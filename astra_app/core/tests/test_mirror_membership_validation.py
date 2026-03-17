import datetime
import socket
from io import StringIO
from typing import override
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from core import signals as astra_signals
from core.membership_notes import CUSTOS
from core.membership_request_workflow import record_membership_request_created, resubmit_membership_request
from core.mirror_membership_validation import (
    InaccessibleMirrorTargetError,
    UnsafeMirrorTargetError,
    ValidationOutcome,
    _validate_almalinux_mirror_network_registration,
    _validate_github_reference,
    finalize_validation,
    mirror_answers_fingerprint,
    mirror_request_answers,
    mirror_request_answers_from_responses,
    schedule_mirror_membership_validation,
)
from core.models import MembershipRequest, MembershipType, MirrorMembershipValidation, Note, Organization
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates


class _FakeResponse:
    def __init__(self, *, status_code: int, body: bytes = b"") -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self._body = body

    @property
    def content(self) -> bytes:
        return self._body

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    def read(self, amt: int | None = None, decode_content: bool = False) -> bytes:
        _ = decode_content
        if amt is None or amt < 0:
            return self._body
        return self._body[:amt]

    def close(self) -> None:
        return None


class _FakeUrllib3Response:
    def __init__(self, *, status: int) -> None:
        self.status = status

    def close(self) -> None:
        return None


class MirrorMembershipValidationTests(TestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        ensure_email_templates()
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
            },
        )

        from core import mirror_membership_validation_receivers

        mirror_membership_validation_receivers.connect_mirror_membership_validation_receivers()
        self.mattermost_patch = patch("core.mattermost_webhooks.dispatch_mattermost_event", autospec=True)
        self.mattermost_patch.start()
        self.addCleanup(self.mattermost_patch.stop)

    def _mirror_responses(
        self,
        *,
        domain: str = "https://mirror.example.org",
        pull_request: str = "https://github.com/AlmaLinux/mirrors/pull/123",
        additional_info: str = "Primary EU mirror",
    ) -> list[dict[str, str]]:
        return [
            {"Domain": domain},
            {"Pull request": pull_request},
            {"Additional information": additional_info},
        ]

    def _create_user_request(
        self,
        *,
        status: str = MembershipRequest.Status.pending,
        responses: list[dict[str, str]] | None = None,
        membership_type_id: str = "mirror",
    ) -> MembershipRequest:
        return MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id=membership_type_id,
            status=status,
            on_hold_at=timezone.now() if status == MembershipRequest.Status.on_hold else None,
            responses=responses or self._mirror_responses(),
        )

    def _create_org_request(
        self,
        *,
        status: str = MembershipRequest.Status.pending,
        responses: list[dict[str, str]] | None = None,
    ) -> MembershipRequest:
        organization = Organization.objects.create(name="Mirror Org", representative="alice")
        return MembershipRequest.objects.create(
            requested_username="",
            requested_organization=organization,
            membership_type_id="mirror",
            status=status,
            on_hold_at=timezone.now() if status == MembershipRequest.Status.on_hold else None,
            responses=responses or self._mirror_responses(),
        )

    def _requests_get(self, url: str, **kwargs) -> _FakeResponse:
        _ = kwargs
        if url == "https://mirror.example.org":
            return _FakeResponse(status_code=200)
        if url == "https://mirror.example.org/almalinux/timestamp.txt":
            return _FakeResponse(status_code=200)
        if url == "https://mirror.example.org/almalinux-kitten/timestamp.txt":
            return _FakeResponse(status_code=404)
        if url == "https://github.com/AlmaLinux/mirrors/pull/123":
            return _FakeResponse(status_code=200)
        if url == "https://github.com/AlmaLinux/mirrors/pull/123.diff":
            return _FakeResponse(
                status_code=200,
                body=(
                    b"diff --git a/mirrors.d/mirror.example.org.yml b/mirrors.d/mirror.example.org.yml\n"
                    b"+++ b/mirrors.d/mirror.example.org.yml\n"
                ),
            )
        if url == self._mirror_network_lookup_url("mirror.example.org"):
            return _FakeResponse(status_code=200)
        if url.startswith("https://raw.githubusercontent.com/AlmaLinux/mirrors/refs/heads/master/mirrors.d/"):
            return _FakeResponse(status_code=404)
        raise AssertionError(f"unexpected URL: {url}")

    def _mirror_network_lookup_url(self, domain_or_hostname: str) -> str:
        hostname = urlsplit(domain_or_hostname).hostname or domain_or_hostname
        return f"https://raw.githubusercontent.com/AlmaLinux/mirrors/refs/heads/master/mirrors.d/{hostname}.yml"

    def _alma_timestamp_text(self, *, age: datetime.timedelta) -> bytes:
        timestamp = timezone.now().astimezone(datetime.UTC) - age
        return timestamp.strftime("%a %b %d %H:%M:%S UTC %Y\n").encode()

    def _bound_http_get(self, url: str) -> _FakeResponse:
        if url == "https://mirror.example.org":
            return _FakeResponse(status_code=200)
        if url == "https://mirror.example.org/timestamp.txt":
            return _FakeResponse(status_code=404)
        if url == "https://mirror.example.org/almalinux/timestamp.txt":
            return _FakeResponse(status_code=200, body=self._alma_timestamp_text(age=datetime.timedelta(hours=2)))
        if url == "https://mirror.example.org/almalinux-kitten/timestamp.txt":
            return _FakeResponse(status_code=404)
        raise AssertionError(f"unexpected bound URL: {url}")

    def test_user_submission_schedules_pending_validation_without_http(self) -> None:
        membership_request = self._create_user_request()

        with (
            patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.pending)
        self.assertEqual(validation.attempt_count, 0)
        self.assertEqual(validation.result, {})
        self.assertIsNotNone(validation.next_run_at)
        get_mock.assert_not_called()

    def test_user_submission_schedules_validation_even_when_receiver_disconnected(self) -> None:
        membership_request = self._create_user_request()
        self.assertFalse(MirrorMembershipValidation.objects.filter(membership_request=membership_request).exists())

        from core.mirror_membership_validation_receivers import schedule_mirror_validation_from_signal
        from core.signal_receivers import safe_receiver

        dispatch_uid = "core.mirror_membership_validation_receivers.membership_request_submitted"
        astra_signals.membership_request_submitted.disconnect(dispatch_uid=dispatch_uid)
        self.addCleanup(
            lambda: astra_signals.membership_request_submitted.connect(
                safe_receiver("membership_request_submitted")(schedule_mirror_validation_from_signal),
                dispatch_uid=dispatch_uid,
            )
        )

        with (
            patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        self.assertTrue(
            MirrorMembershipValidation.objects.filter(membership_request=membership_request).exists(),
            "Mirror validation should be scheduled even if signal receivers are disconnected",
        )
        get_mock.assert_not_called()

    def test_org_submission_schedules_pending_validation_without_http(self) -> None:
        membership_request = self._create_org_request()

        with (
            patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        get_mock.assert_not_called()
        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.pending)

    def test_resubmission_schedules_validation_even_when_receiver_disconnected(self) -> None:
        membership_request = self._create_user_request(status=MembershipRequest.Status.on_hold)
        self.assertFalse(MirrorMembershipValidation.objects.filter(membership_request=membership_request).exists())

        from core.mirror_membership_validation_receivers import schedule_mirror_validation_from_signal
        from core.signal_receivers import safe_receiver

        dispatch_uid = "core.mirror_membership_validation_receivers.membership_rfi_replied"
        astra_signals.membership_rfi_replied.disconnect(dispatch_uid=dispatch_uid)
        self.addCleanup(
            lambda: astra_signals.membership_rfi_replied.connect(
                safe_receiver("membership_rfi_replied")(schedule_mirror_validation_from_signal),
                dispatch_uid=dispatch_uid,
            )
        )

        updated_responses = self._mirror_responses(domain="https://mirror2.example.org")
        with (
            patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            resubmit_membership_request(
                membership_request=membership_request,
                actor_username="alice",
                updated_responses=updated_responses,
            )

        self.assertTrue(
            MirrorMembershipValidation.objects.filter(membership_request=membership_request).exists(),
            "Mirror validation should be scheduled even if RFI-replied receivers are disconnected",
        )
        get_mock.assert_not_called()

    def test_non_mirror_submission_does_not_schedule_validation(self) -> None:
        membership_request = self._create_user_request(membership_type_id="individual", responses=[{"Contributions": "Docs"}])

        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        self.assertFalse(MirrorMembershipValidation.objects.filter(membership_request=membership_request).exists())

    def test_duplicate_submission_signal_is_idempotent(self) -> None:
        membership_request = self._create_user_request()

        astra_signals.membership_request_submitted.send(
            sender=MembershipRequest,
            membership_request=membership_request,
            actor="alice",
        )
        astra_signals.membership_request_submitted.send(
            sender=MembershipRequest,
            membership_request=membership_request,
            actor="alice",
        )

        self.assertEqual(MirrorMembershipValidation.objects.filter(membership_request=membership_request).count(), 1)

    def test_rfi_resubmission_requeues_when_mirror_answers_change(self) -> None:
        membership_request = self._create_user_request(status=MembershipRequest.Status.on_hold)
        validation = MirrorMembershipValidation.objects.create(
            membership_request=membership_request,
            status=MirrorMembershipValidation.Status.completed,
            answer_fingerprint="stale-fingerprint",
            attempt_count=3,
            next_run_at=timezone.now() + datetime.timedelta(days=1),
            result={"domain": {"status": "accessible"}},
            noted_result_fingerprint="stale-note",
        )

        with self.captureOnCommitCallbacks(execute=True):
            resubmit_membership_request(
                membership_request=membership_request,
                actor_username="alice",
                updated_responses=self._mirror_responses(
                    domain="https://mirror2.example.org",
                    pull_request="https://github.com/AlmaLinux/mirrors/pull/999",
                ),
            )

        validation.refresh_from_db()
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.pending)
        self.assertNotEqual(validation.answer_fingerprint, "stale-fingerprint")
        self.assertEqual(validation.attempt_count, 0)
        self.assertEqual(validation.result, {})
        self.assertEqual(validation.noted_result_fingerprint, "")

    def test_org_rfi_resubmission_requeues_when_mirror_answers_change(self) -> None:
        membership_request = self._create_org_request(status=MembershipRequest.Status.on_hold)
        validation = MirrorMembershipValidation.objects.create(
            membership_request=membership_request,
            status=MirrorMembershipValidation.Status.completed,
            answer_fingerprint="stale-fingerprint",
            attempt_count=2,
            next_run_at=timezone.now() + datetime.timedelta(days=1),
            result={"domain": {"status": "reachable"}},
            noted_result_fingerprint="stale-note",
        )

        with self.captureOnCommitCallbacks(execute=True):
            resubmit_membership_request(
                membership_request=membership_request,
                actor_username="alice",
                updated_responses=self._mirror_responses(
                    domain="https://mirror-org.example.org",
                    pull_request="https://github.com/AlmaLinux/mirrors/pull/456",
                    additional_info="Updated org mirror details",
                ),
            )

        validation.refresh_from_db()
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.pending)
        self.assertNotEqual(validation.answer_fingerprint, "stale-fingerprint")
        self.assertEqual(validation.attempt_count, 0)
        self.assertEqual(validation.result, {})
        self.assertEqual(validation.noted_result_fingerprint, "")

    def test_rfi_signal_noops_when_answers_are_unchanged(self) -> None:
        membership_request = self._create_user_request()
        fingerprint = mirror_answers_fingerprint(mirror_request_answers(membership_request))
        validation = MirrorMembershipValidation.objects.create(
            membership_request=membership_request,
            status=MirrorMembershipValidation.Status.completed,
            answer_fingerprint=fingerprint,
            attempt_count=2,
            next_run_at=timezone.now() + datetime.timedelta(days=1),
            result={"domain": {"status": "reachable"}},
            noted_result_fingerprint="kept-note",
        )

        astra_signals.membership_rfi_replied.send(
            sender=MembershipRequest,
            membership_request=membership_request,
            actor="alice",
        )

        validation.refresh_from_db()
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.completed)
        self.assertEqual(validation.answer_fingerprint, fingerprint)
        self.assertEqual(validation.attempt_count, 2)
        self.assertEqual(validation.result, {"domain": {"status": "reachable"}})
        self.assertEqual(validation.noted_result_fingerprint, "kept-note")

    def test_resubmit_rejects_unchanged_normalized_answers(self) -> None:
        membership_request = self._create_user_request(status=MembershipRequest.Status.on_hold)

        with self.assertRaisesMessage(ValidationError, "Please update your request before resubmitting it"):
            resubmit_membership_request(
                membership_request=membership_request,
                actor_username="alice",
                updated_responses=[
                    {" Domain ": "mirror.example.org "},
                    {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/123"},
                    {" Additional info": "Primary EU mirror"},
                ],
            )

        self.assertFalse(MirrorMembershipValidation.objects.filter(membership_request=membership_request).exists())

    def test_resubmit_persists_normalized_responses_from_direct_service_call(self) -> None:
        membership_request = self._create_user_request(status=MembershipRequest.Status.on_hold)

        with self.captureOnCommitCallbacks(execute=True):
            resubmit_membership_request(
                membership_request=membership_request,
                actor_username="alice",
                updated_responses=[
                    {" Domain ": " https://mirror2.example.org "},
                    {"Additional info": "Legacy clarification"},
                    {"Additional information": "Canonical clarification"},
                    {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/999"},
                    {"Empty value": "   "},
                ],
            )

        membership_request.refresh_from_db()
        self.assertEqual(
            membership_request.responses,
            [
                {"Domain": "https://mirror2.example.org"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/999"},
                {"Additional information": "Canonical clarification"},
            ],
        )

    def test_rfi_resubmission_requeues_when_only_alias_clarification_changes(self) -> None:
        membership_request = self._create_user_request(status=MembershipRequest.Status.on_hold)
        fingerprint = mirror_answers_fingerprint(mirror_request_answers(membership_request))
        validation = MirrorMembershipValidation.objects.create(
            membership_request=membership_request,
            status=MirrorMembershipValidation.Status.completed,
            answer_fingerprint=fingerprint,
            attempt_count=2,
            next_run_at=timezone.now() + datetime.timedelta(days=1),
            result={"domain": {"status": "reachable"}},
            noted_result_fingerprint="stale-note",
        )

        with self.captureOnCommitCallbacks(execute=True):
            resubmit_membership_request(
                membership_request=membership_request,
                actor_username="alice",
                updated_responses=[
                    {"Domain": "https://mirror.example.org"},
                    {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/123"},
                    {"Additional info": "Primary EU mirror"},
                    {"Additional information": "Updated clarification"},
                ],
            )

        membership_request.refresh_from_db()
        validation.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.pending)
        self.assertIsNone(membership_request.on_hold_at)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.pending)
        self.assertNotEqual(validation.answer_fingerprint, fingerprint)
        self.assertEqual(validation.result, {})

    def test_mirror_request_answers_prefers_alias_clarification_for_mixed_key_rows(self) -> None:
        membership_type = MembershipType.objects.get(code="mirror")

        answers = mirror_request_answers_from_responses(
            membership_type=membership_type,
            responses=[
                {"Domain": "https://mirror.example.org"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/123"},
                {"Additional info": "Old note"},
                {"Additional information": "Newer note"},
            ],
        )

        assert answers is not None
        self.assertEqual(answers.additional_info, "Newer note")

    def test_mirror_request_answers_fall_back_to_canonical_when_alias_blank(self) -> None:
        membership_type = MembershipType.objects.get(code="mirror")

        answers = mirror_request_answers_from_responses(
            membership_type=membership_type,
            responses=[
                {"Domain": "https://mirror.example.org"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/123"},
                {"Additional info": "Primary EU mirror"},
                {"Additional information": "   "},
            ],
        )

        assert answers is not None
        self.assertEqual(answers.additional_info, "Primary EU mirror")

    def test_normalize_membership_request_responses_collapses_mirror_clarification_precedence(self) -> None:
        from core.membership_response_normalization import (
            ADDITIONAL_INFORMATION_QUESTION,
            normalize_membership_request_responses,
        )

        normalized = normalize_membership_request_responses(
            responses=[
                {" Domain ": " mirror.example.org "},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/123"},
                {"Additional info": "Legacy clarification"},
                {"Additional information": "Canonical clarification"},
                {"Empty value": "   "},
            ],
            is_mirror_membership=True,
        )

        self.assertEqual(
            normalized.as_responses(),
            [
                {"Domain": "mirror.example.org"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/123"},
                {ADDITIONAL_INFORMATION_QUESTION: "Canonical clarification"},
            ],
        )
        self.assertEqual(normalized.get(ADDITIONAL_INFORMATION_QUESTION), "Canonical clarification")

    def test_canonicalize_membership_response_question_normalizes_legacy_aliases(self) -> None:
        from core.membership_response_normalization import (
            ADDITIONAL_INFORMATION_QUESTION,
            canonicalize_membership_response_question,
        )

        self.assertEqual(
            canonicalize_membership_response_question(" Additional info "),
            ADDITIONAL_INFORMATION_QUESTION,
        )
        self.assertEqual(
            canonicalize_membership_response_question(" Additional information "),
            ADDITIONAL_INFORMATION_QUESTION,
        )
        self.assertEqual(
            canonicalize_membership_response_question(" Domain "),
            "Domain",
        )

    def test_normalize_membership_request_responses_prefers_canonical_additional_information_for_non_mirror_rows(
        self,
    ) -> None:
        from core.membership_response_normalization import (
            ADDITIONAL_INFORMATION_QUESTION,
            normalize_membership_request_responses,
        )

        for responses in (
            [
                {"Additional info": "Legacy clarification"},
                {"Additional information": "Canonical clarification"},
            ],
            [
                {"Additional information": "Canonical clarification"},
                {"Additional info": "Legacy clarification"},
            ],
        ):
            with self.subTest(responses=responses):
                normalized = normalize_membership_request_responses(
                    responses=responses,
                    is_mirror_membership=False,
                )

                self.assertEqual(
                    normalized.as_responses(),
                    [{ADDITIONAL_INFORMATION_QUESTION: "Canonical clarification"}],
                )
                self.assertEqual(normalized.get(ADDITIONAL_INFORMATION_QUESTION), "Canonical clarification")

    def test_validate_almalinux_mirror_network_registration_uses_domain_hostname_lookup(self) -> None:
        captured_urls: list[str] = []

        def github_requests_get(url: str, **kwargs) -> _FakeResponse:
            captured_urls.append(url)
            _ = kwargs
            if url == self._mirror_network_lookup_url("linuxsoft.cern.ch"):
                return _FakeResponse(status_code=200)
            raise AssertionError(f"unexpected URL: {url}")

        with patch(
            "core.mirror_membership_validation.requests.get",
            side_effect=github_requests_get,
            autospec=True,
        ):
            result = _validate_almalinux_mirror_network_registration("https://linuxsoft.cern.ch/almalinux")

        self.assertEqual(result["status"], "registered")
        self.assertEqual(
            result["url"],
            self._mirror_network_lookup_url("linuxsoft.cern.ch"),
        )
        self.assertEqual(
            captured_urls,
            [self._mirror_network_lookup_url("linuxsoft.cern.ch")],
        )

    def test_validate_github_reference_checks_pull_diff_for_expected_mirror_file(self) -> None:
        fetched_urls: list[tuple[str, bool]] = []

        def github_requests_get(url: str, **kwargs) -> _FakeResponse:
            fetched_urls.append((url, bool(kwargs.get("allow_redirects"))))
            if url == "https://github.com/AlmaLinux/mirrors/pull/123":
                return _FakeResponse(status_code=200)
            if url == "https://github.com/AlmaLinux/mirrors/pull/123.diff":
                return _FakeResponse(
                    status_code=200,
                    body=(
                        b"diff --git a/mirrors.d/linuxsoft.cern.ch.yml b/mirrors.d/linuxsoft.cern.ch.yml\n"
                        b"+++ b/mirrors.d/linuxsoft.cern.ch.yml\n"
                    ),
                )
            raise AssertionError(f"unexpected URL: {url}")

        with patch(
            "core.mirror_membership_validation.requests.get",
            side_effect=github_requests_get,
            autospec=True,
        ):
            result = _validate_github_reference(
                "https://github.com/AlmaLinux/mirrors/pull/123",
                expected_file_path="mirrors.d/linuxsoft.cern.ch.yml",
            )

        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["expected_file_status"], "matched")
        self.assertEqual(result["expected_file_path"], "mirrors.d/linuxsoft.cern.ch.yml")
        self.assertEqual(result["diff_url"], "https://github.com/AlmaLinux/mirrors/pull/123.diff")
        self.assertEqual(
            fetched_urls,
            [
                ("https://github.com/AlmaLinux/mirrors/pull/123", False),
                ("https://github.com/AlmaLinux/mirrors/pull/123.diff", True),
            ],
        )

    def test_validate_github_reference_rejects_commit_without_expected_mirror_file_change(self) -> None:
        def github_requests_get(url: str, **kwargs) -> _FakeResponse:
            _ = kwargs
            if url == "https://github.com/AlmaLinux/mirrors/commit/abc123":
                return _FakeResponse(status_code=200)
            if url == "https://github.com/AlmaLinux/mirrors/commit/abc123.diff":
                return _FakeResponse(
                    status_code=200,
                    body=(
                        b"diff --git a/mirrors.d/other.example.org.yml b/mirrors.d/other.example.org.yml\n"
                        b"+++ b/mirrors.d/other.example.org.yml\n"
                    ),
                )
            raise AssertionError(f"unexpected URL: {url}")

        with patch(
            "core.mirror_membership_validation.requests.get",
            side_effect=github_requests_get,
            autospec=True,
        ):
            result = _validate_github_reference(
                "https://github.com/AlmaLinux/mirrors/commit/abc123",
                expected_file_path="mirrors.d/mirror.example.org.yml",
            )

        self.assertEqual(result["status"], "invalid_missing_expected_file")
        self.assertEqual(result["expected_file_status"], "missing")
        self.assertEqual(result["expected_file_path"], "mirrors.d/mirror.example.org.yml")
        self.assertEqual(result["diff_url"], "https://github.com/AlmaLinux/mirrors/commit/abc123.diff")

    def test_validate_github_reference_marks_diff_fetch_timeout_retryable(self) -> None:
        def github_requests_get(url: str, **kwargs) -> _FakeResponse:
            _ = kwargs
            if url == "https://github.com/AlmaLinux/mirrors/pull/123":
                return _FakeResponse(status_code=200)
            if url == "https://github.com/AlmaLinux/mirrors/pull/123.diff":
                raise requests.exceptions.Timeout("github diff timed out")
            raise AssertionError(f"unexpected URL: {url}")

        with patch(
            "core.mirror_membership_validation.requests.get",
            side_effect=github_requests_get,
            autospec=True,
        ):
            result = _validate_github_reference(
                "https://github.com/AlmaLinux/mirrors/pull/123",
                expected_file_path="mirrors.d/mirror.example.org.yml",
            )

        self.assertEqual(result["status"], "retryable_upstream_failure")
        self.assertEqual(result["detail"], "timeout")
        self.assertEqual(result["diff_url"], "https://github.com/AlmaLinux/mirrors/pull/123.diff")
        self.assertEqual(result["expected_file_path"], "mirrors.d/mirror.example.org.yml")

    def test_command_writes_terminal_summary_note_once(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.completed)
        self.assertEqual(validation.result["almalinux_mirror_network"]["status"], "registered")
        self.assertEqual(validation.result["github"]["expected_file_status"], "matched")
        notes = list(Note.objects.filter(membership_request=membership_request, username=CUSTOS).order_by("pk"))
        self.assertEqual(len(notes), 1)
        self.assertIn("Mirror validation summary", notes[0].content)
        self.assertIn("Domain: reachable", notes[0].content)
        self.assertIn("Mirror status: up-to-date", notes[0].content)
        self.assertIn("AlmaLinux mirror network: registered", notes[0].content)
        self.assertIn(
            "GitHub pull request: valid; touches mirrors.d/mirror.example.org.yml",
            notes[0].content,
        )

    def test_command_marks_not_registered_mirror_network_as_terminal_failure(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(domain="https://not-registered.example.org"),
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        def bound_http_get(url: str) -> _FakeResponse:
            if url == "https://not-registered.example.org":
                return _FakeResponse(status_code=200)
            if url == "https://not-registered.example.org/timestamp.txt":
                return _FakeResponse(status_code=404)
            if url == "https://not-registered.example.org/almalinux/timestamp.txt":
                return _FakeResponse(status_code=200, body=self._alma_timestamp_text(age=datetime.timedelta(hours=2)))
            if url == "https://not-registered.example.org/almalinux-kitten/timestamp.txt":
                return _FakeResponse(status_code=404)
            raise AssertionError(f"unexpected bound URL: {url}")

        def github_requests_get(url: str, **kwargs) -> _FakeResponse:
            _ = kwargs
            if url == "https://github.com/AlmaLinux/mirrors/pull/123":
                return _FakeResponse(status_code=200)
            if url == "https://github.com/AlmaLinux/mirrors/pull/123.diff":
                return _FakeResponse(
                    status_code=200,
                    body=(
                        b"diff --git a/mirrors.d/not-registered.example.org.yml b/mirrors.d/not-registered.example.org.yml\n"
                        b"+++ b/mirrors.d/not-registered.example.org.yml\n"
                    ),
                )
            if url == self._mirror_network_lookup_url("not-registered.example.org"):
                return _FakeResponse(status_code=404)
            raise AssertionError(f"unexpected URL: {url}")

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=github_requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        self.assertEqual(validation.result["almalinux_mirror_network"]["status"], "not_registered")
        self.assertIn("AlmaLinux mirror network: not registered", note.content)

    def test_command_rejects_github_reference_that_does_not_touch_expected_mirror_file(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        def github_requests_get(url: str, **kwargs) -> _FakeResponse:
            _ = kwargs
            if url == "https://github.com/AlmaLinux/mirrors/pull/123":
                return _FakeResponse(status_code=200)
            if url == "https://github.com/AlmaLinux/mirrors/pull/123.diff":
                return _FakeResponse(
                    status_code=200,
                    body=(
                        b"diff --git a/mirrors.d/other.example.org.yml b/mirrors.d/other.example.org.yml\n"
                        b"+++ b/mirrors.d/other.example.org.yml\n"
                    ),
                )
            if url == self._mirror_network_lookup_url("mirror.example.org"):
                return _FakeResponse(status_code=200)
            raise AssertionError(f"unexpected URL: {url}")

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=github_requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        self.assertEqual(validation.result["github"]["status"], "invalid_missing_expected_file")
        self.assertEqual(validation.result["github"]["expected_file_status"], "missing")
        self.assertIn(
            "GitHub pull request: does not touch mirrors.d/mirror.example.org.yml",
            note.content,
        )

    def test_command_marks_stale_mirror_status_in_summary_note(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        def bound_http_get(url: str) -> _FakeResponse:
            if url == "https://mirror.example.org":
                return _FakeResponse(status_code=200)
            if url == "https://mirror.example.org/timestamp.txt":
                return _FakeResponse(status_code=404)
            if url == "https://mirror.example.org/almalinux/timestamp.txt":
                return _FakeResponse(status_code=200, body=self._alma_timestamp_text(age=datetime.timedelta(days=2)))
            if url == "https://mirror.example.org/almalinux-kitten/timestamp.txt":
                return _FakeResponse(status_code=404)
            raise AssertionError(f"unexpected bound URL: {url}")

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        self.assertEqual(validation.result["timestamp"]["status"], "stale")
        self.assertIn("Mirror status: stale", note.content)

    def test_command_treats_invalid_timestamp_content_as_not_found(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        def bound_http_get(url: str) -> _FakeResponse:
            if url == "https://mirror.example.org":
                return _FakeResponse(status_code=200)
            if url == "https://mirror.example.org/timestamp.txt":
                return _FakeResponse(status_code=404)
            if url == "https://mirror.example.org/almalinux/timestamp.txt":
                return _FakeResponse(status_code=200, body=b"not-a-real-timestamp\n")
            if url == "https://mirror.example.org/almalinux-kitten/timestamp.txt":
                return _FakeResponse(status_code=404)
            if url == "https://mirror.example.org/alma/timestamp.txt":
                return _FakeResponse(status_code=404)
            if url == "https://mirror.example.org/timestamp.txt":
                return _FakeResponse(status_code=404)
            raise AssertionError(f"unexpected bound URL: {url}")

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        self.assertEqual(validation.result["timestamp"]["status"], "not_found")
        self.assertIn("Mirror status: not found", note.content)

    def test_command_accepts_alma_prefix_timestamp_file_when_standard_paths_missing(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(domain="https://almalinux.mirrors.itworxx.de/"),
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        def bound_http_get(url: str) -> _FakeResponse:
            if url == "https://almalinux.mirrors.itworxx.de":
                return _FakeResponse(status_code=200)
            if url == "https://almalinux.mirrors.itworxx.de/timestamp.txt":
                return _FakeResponse(status_code=404)
            if url == "https://almalinux.mirrors.itworxx.de/almalinux/timestamp.txt":
                return _FakeResponse(status_code=404)
            if url == "https://almalinux.mirrors.itworxx.de/almalinux-kitten/timestamp.txt":
                return _FakeResponse(status_code=404)
            if url == "https://almalinux.mirrors.itworxx.de/alma/timestamp.txt":
                return _FakeResponse(status_code=200, body=self._alma_timestamp_text(age=datetime.timedelta(hours=2)))
            raise AssertionError(f"unexpected bound URL: {url}")

        def github_requests_get(url: str, **kwargs) -> _FakeResponse:
            _ = kwargs
            if url == "https://github.com/AlmaLinux/mirrors/pull/123":
                return _FakeResponse(status_code=200)
            if url == "https://github.com/AlmaLinux/mirrors/pull/123.diff":
                return _FakeResponse(
                    status_code=200,
                    body=(
                        b"diff --git a/mirrors.d/almalinux.mirrors.itworxx.de.yml b/mirrors.d/almalinux.mirrors.itworxx.de.yml\n"
                        b"+++ b/mirrors.d/almalinux.mirrors.itworxx.de.yml\n"
                    ),
                )
            if url == self._mirror_network_lookup_url("almalinux.mirrors.itworxx.de"):
                return _FakeResponse(status_code=200)
            raise AssertionError(f"unexpected URL: {url}")

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=github_requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.completed)
        self.assertEqual(validation.result["timestamp"]["status"], "up_to_date")
        self.assertEqual(
            validation.result["timestamp"]["url"],
            "https://almalinux.mirrors.itworxx.de/alma/timestamp.txt",
        )
        self.assertEqual(
            validation.result["timestamp"]["checked_urls"],
            [
                "https://almalinux.mirrors.itworxx.de",
                "https://almalinux.mirrors.itworxx.de/almalinux/timestamp.txt",
                "https://almalinux.mirrors.itworxx.de/almalinux-kitten/timestamp.txt",
                "https://almalinux.mirrors.itworxx.de/alma/timestamp.txt",
            ],
        )
        self.assertIn("Mirror status: up-to-date", note.content)

    def test_command_redacts_userinfo_from_stored_mirror_urls_and_notes(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                domain="https://user:secret@mirror.example.org:8443/private/token123/?token=secret",
            ),
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        sanitized_domain_url = "https://mirror.example.org:8443"
        sanitized_timestamp_url = "https://mirror.example.org:8443/almalinux/timestamp.txt"
        checked_domain_url = "https://mirror.example.org:8443/private/token123"
        checked_root_timestamp_url = "https://mirror.example.org:8443/timestamp.txt"
        checked_timestamp_url = "https://mirror.example.org:8443/almalinux/timestamp.txt"

        def bound_http_get(url: str) -> _FakeResponse:
            if url == checked_domain_url:
                return _FakeResponse(status_code=200)
            if url == checked_root_timestamp_url:
                return _FakeResponse(status_code=404)
            if url == checked_timestamp_url:
                return _FakeResponse(status_code=200, body=self._alma_timestamp_text(age=datetime.timedelta(hours=2)))
            if url == "https://mirror.example.org:8443/almalinux-kitten/timestamp.txt":
                return _FakeResponse(status_code=404)
            return _FakeResponse(status_code=404)

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)

        self.assertEqual(validation.status, MirrorMembershipValidation.Status.completed)
        self.assertEqual(validation.result["domain"]["url"], sanitized_domain_url)
        self.assertEqual(validation.result["timestamp"]["url"], sanitized_timestamp_url)
        self.assertEqual(
            validation.result["timestamp"]["checked_urls"],
            [
                sanitized_domain_url,
                sanitized_timestamp_url,
            ],
        )
        self.assertEqual(validation.result["answers"]["domain_url"], sanitized_domain_url)
        self.assertNotIn("user:secret@", str(validation.result))
        self.assertNotIn("token=secret", str(validation.result))
        self.assertNotIn("private/token123", str(validation.result))
        self.assertNotIn("user:secret@", note.content)
        self.assertNotIn("token=secret", note.content)
        self.assertNotIn("private/token123", note.content)
        self.assertIn("Mirror status: up-to-date", note.content)
        self.assertNotIn(sanitized_timestamp_url, note.content)

    def test_command_rejects_private_target_without_outbound_http(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(domain="https://127.0.0.1", pull_request="https://github.com/AlmaLinux/mirrors/pull/123"),
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        with patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock:
            get_mock.return_value = _FakeResponse(status_code=200)
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)
        self.assertIn("unsafe target", note.content)
        self.assertNotIn("127.0.0.1", note.content)
        get_mock.assert_called_once_with(
            "https://github.com/AlmaLinux/mirrors/pull/123",
            allow_redirects=False,
            stream=True,
            timeout=5,
        )

    def test_command_rejects_rebinding_target_before_timestamp_fetch(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                domain="https://rebind.example.org",
                pull_request="https://github.com/AlmaLinux/mirrors/pull/123",
            ),
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        resolution_count = 0
        bound_hosts: list[tuple[str, str | None, str | None, str]] = []

        def rebinding_getaddrinfo(
            host: str,
            port: int,
            **kwargs,
        ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            _ = kwargs
            nonlocal resolution_count
            if host != "rebind.example.org":
                raise AssertionError(f"unexpected hostname lookup: {host}")
            resolution_count += 1
            if resolution_count == 1:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.10.50", port))]

        def bound_urlopen(
            pool_self,
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            **kwargs,
        ) -> _FakeUrllib3Response:
            _ = kwargs
            bound_hosts.append((pool_self.host, getattr(pool_self, "assert_hostname", None), headers.get("Host") if headers else None, url))
            self.assertEqual(pool_self.host, "93.184.216.34")
            self.assertEqual(getattr(pool_self, "assert_hostname", None), "rebind.example.org")
            self.assertEqual(headers, {"Host": "rebind.example.org"})
            self.assertEqual(method, "GET")
            self.assertEqual(url, "/")
            return _FakeUrllib3Response(status=200)

        def github_only_requests_get(url: str, **kwargs) -> _FakeResponse:
            _ = kwargs
            if url == "https://github.com/AlmaLinux/mirrors/pull/123":
                return _FakeResponse(status_code=200)
            if url == "https://github.com/AlmaLinux/mirrors/pull/123.diff":
                return _FakeResponse(
                    status_code=200,
                    body=(
                        b"diff --git a/mirrors.d/rebind.example.org.yml b/mirrors.d/rebind.example.org.yml\n"
                        b"+++ b/mirrors.d/rebind.example.org.yml\n"
                    ),
                )
            if url == self._mirror_network_lookup_url("rebind.example.org"):
                return _FakeResponse(status_code=404)
            raise AssertionError(f"mirror-domain fetch bypassed bound transport: {url}")

        with (
            patch(
                "core.mirror_membership_validation.socket.getaddrinfo",
                side_effect=rebinding_getaddrinfo,
                autospec=True,
            ),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=github_only_requests_get,
                autospec=True,
            ),
            patch(
                "core.mirror_membership_validation.urllib3.connectionpool.HTTPSConnectionPool.urlopen",
                side_effect=bound_urlopen,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        self.assertEqual(validation.result["domain"]["status"], "reachable")
        self.assertEqual(validation.result["timestamp"]["status"], "unsafe_target")
        self.assertEqual(bound_hosts, [("93.184.216.34", "rebind.example.org", "rebind.example.org", "/")])
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)
        self.assertIn("unsafe target", note.content)
        self.assertNotIn("192.168.10.50", note.content)

    def test_command_marks_malformed_domain_terminally(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                domain="mailto:mirror@example.org",
                pull_request="https://github.com/AlmaLinux/mirrors/pull/123",
            ),
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        with patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock:
            get_mock.return_value = _FakeResponse(status_code=200)
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)
        self.assertIn("invalid_http_url", note.content)
        get_mock.assert_called_once_with(
            "https://github.com/AlmaLinux/mirrors/pull/123",
            allow_redirects=False,
            stream=True,
            timeout=5,
        )

    def test_malformed_credential_bearing_domain_does_not_leak_userinfo(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                domain="https://user:secret@mirror.example.org:badport",
                pull_request="https://github.com/AlmaLinux/mirrors/pull/123",
            ),
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        with patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock:
            get_mock.return_value = _FakeResponse(status_code=200)
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)

        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        self.assertEqual(validation.result["domain"]["status"], "malformed")
        self.assertEqual(validation.result["domain"]["url"], "https://mirror.example.org")
        self.assertEqual(validation.result["answers"]["domain_url"], "https://mirror.example.org")
        self.assertEqual(validation.result["timestamp"]["checked_urls"], [])
        self.assertNotIn("user:secret@", str(validation.result))
        self.assertNotIn("user:secret@", note.content)
        self.assertIn("invalid_http_url", note.content)
        get_mock.assert_called_once_with(
            "https://github.com/AlmaLinux/mirrors/pull/123",
            allow_redirects=False,
            stream=True,
            timeout=5,
        )

    def test_command_classifies_github_wrong_repo(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                pull_request="https://github.com/AlmaLinux/infra/pull/123",
            ),
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        self.assertEqual(validation.result["github"]["status"], "invalid_wrong_repo")
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)
        self.assertIn("invalid wrong repo", note.content)

    def test_command_redacts_userinfo_from_github_urls_in_results_and_notes(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                pull_request="https://user:token@github.com/AlmaLinux/infra/pull/123?token=secret",
            ),
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        with patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)

        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        self.assertEqual(
            validation.result["answers"]["pull_request_url"],
            "https://github.com",
        )
        self.assertEqual(validation.result["github"]["status"], "invalid_wrong_repo")
        self.assertEqual(
            validation.result["github"]["url"],
            "https://github.com",
        )
        self.assertNotIn("user:token@", str(validation.result))
        self.assertNotIn("token=secret", str(validation.result))
        self.assertNotIn("/AlmaLinux/infra/pull/123", str(validation.result))
        self.assertNotIn("user:token@", note.content)
        self.assertNotIn("token=secret", note.content)
        self.assertIn("invalid wrong repo", note.content)

    def test_validate_github_reference_redacts_userinfo_on_error_paths(self) -> None:
        malformed_result = _validate_github_reference("https://user:token@github.com/AlmaLinux?token=secret")
        self.assertEqual(malformed_result["status"], "invalid_malformed")
        self.assertEqual(malformed_result["url"], "https://github.com")

        wrong_repo_result = _validate_github_reference(
            "https://user:token@github.com/AlmaLinux/infra/pull/123?token=secret",
        )
        self.assertEqual(wrong_repo_result["status"], "invalid_wrong_repo")
        self.assertEqual(
            wrong_repo_result["url"],
            "https://github.com",
        )

        with patch(
            "core.mirror_membership_validation.requests.get",
            side_effect=requests.exceptions.Timeout("github timed out"),
            autospec=True,
        ):
            retryable_result = _validate_github_reference(
                "https://user:token@github.com/AlmaLinux/mirrors/pull/123?token=secret",
            )

        self.assertEqual(retryable_result["status"], "retryable_upstream_failure")
        self.assertEqual(
            retryable_result["url"],
            "https://github.com/AlmaLinux/mirrors/pull/123",
        )
        self.assertNotIn("user:token@", str(malformed_result))
        self.assertNotIn("user:token@", str(wrong_repo_result))
        self.assertNotIn("user:token@", str(retryable_result))
        self.assertNotIn("token=secret", str(malformed_result))
        self.assertNotIn("token=secret", str(wrong_repo_result))
        self.assertNotIn("token=secret", str(retryable_result))
        self.assertNotIn("/AlmaLinux", str(malformed_result))
        self.assertNotIn("/AlmaLinux/infra/pull/123", str(wrong_repo_result))

    def test_command_classifies_github_not_found(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        def not_found_requests_get(url: str, **kwargs) -> _FakeResponse:
            _ = kwargs
            if url == "https://github.com/AlmaLinux/mirrors/pull/123":
                return _FakeResponse(status_code=404)
            return self._requests_get(url, **kwargs)

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=not_found_requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        self.assertEqual(validation.result["github"]["status"], "invalid_not_found")
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)
        self.assertIn("not found", note.content)

    def test_command_accepts_github_commit_reference_variants(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                pull_request="http://www.github.com/AlmaLinux/mirrors/commit/abc123/?diff=split#top",
            ),
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        def commit_requests_get(url: str, **kwargs) -> _FakeResponse:
            _ = kwargs
            if url == "https://github.com/AlmaLinux/mirrors/commit/abc123":
                return _FakeResponse(status_code=200)
            if url == "https://github.com/AlmaLinux/mirrors/commit/abc123.diff":
                return _FakeResponse(
                    status_code=200,
                    body=(
                        b"diff --git a/mirrors.d/mirror.example.org.yml b/mirrors.d/mirror.example.org.yml\n"
                        b"+++ b/mirrors.d/mirror.example.org.yml\n"
                    ),
                )
            return self._requests_get(url, **kwargs)

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=commit_requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.completed)
        self.assertEqual(validation.result["github"]["status"], "commit")
        self.assertEqual(
            validation.result["github"]["url"],
            "https://github.com/AlmaLinux/mirrors/commit/abc123",
        )

    def test_closed_requests_are_deleted_regardless_of_validation_status(self) -> None:
        scenario_rows = [
            (MembershipRequest.Status.approved, MirrorMembershipValidation.Status.pending, timezone.now()),
            (
                MembershipRequest.Status.rejected,
                MirrorMembershipValidation.Status.completed,
                timezone.now() + datetime.timedelta(days=1),
            ),
            (
                MembershipRequest.Status.ignored,
                MirrorMembershipValidation.Status.failed_retryable,
                timezone.now() + datetime.timedelta(days=1),
            ),
            (
                MembershipRequest.Status.rescinded,
                MirrorMembershipValidation.Status.failed_terminal,
                timezone.now() + datetime.timedelta(days=1),
            ),
            (
                MembershipRequest.Status.approved,
                MirrorMembershipValidation.Status.skipped,
                timezone.now() + datetime.timedelta(days=1),
            ),
            (
                MembershipRequest.Status.rejected,
                MirrorMembershipValidation.Status.running,
                timezone.now() + datetime.timedelta(days=1),
            ),
        ]

        request_ids: list[int] = []
        validation_ids: list[int] = []
        for request_status, validation_status, next_run_at in scenario_rows:
            membership_request = self._create_user_request()
            with self.captureOnCommitCallbacks(execute=True):
                record_membership_request_created(
                    membership_request=membership_request,
                    actor_username="alice",
                    send_submitted_email=False,
                )

            membership_request.status = request_status
            membership_request.save(update_fields=["status"])

            validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
            validation.status = validation_status
            validation.next_run_at = next_run_at
            validation.attempt_count = 1 if validation_status != MirrorMembershipValidation.Status.pending else 0
            validation.result = {"domain": {"status": "reachable"}} if validation_status != MirrorMembershipValidation.Status.pending else {}
            if validation_status == MirrorMembershipValidation.Status.running:
                validation.claimed_at = timezone.now()
                validation.claim_expires_at = timezone.now() + datetime.timedelta(minutes=5)
                validation.save(
                    update_fields=[
                        "status",
                        "next_run_at",
                        "attempt_count",
                        "result",
                        "claimed_at",
                        "claim_expires_at",
                    ]
                )
            else:
                validation.save(update_fields=["status", "next_run_at", "attempt_count", "result"])

            request_ids.append(membership_request.pk)
            validation_ids.append(validation.pk)

        with patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock:
            call_command("membership_mirror_validation")

        for request_id, validation_id in zip(request_ids, validation_ids, strict=True):
            self.assertFalse(MirrorMembershipValidation.objects.filter(pk=validation_id).exists())
            self.assertFalse(Note.objects.filter(membership_request_id=request_id, username=CUSTOS).exists())
        get_mock.assert_not_called()

    def test_stale_running_result_does_not_overwrite_resubmitted_answers(self) -> None:
        membership_request = self._create_user_request()
        initial_answers = mirror_request_answers(membership_request)
        validation = MirrorMembershipValidation.objects.create(
            membership_request=membership_request,
            status=MirrorMembershipValidation.Status.running,
            answer_fingerprint=mirror_answers_fingerprint(initial_answers),
            next_run_at=timezone.now(),
            claimed_at=timezone.now(),
            claim_expires_at=timezone.now() + datetime.timedelta(minutes=5),
        )
        stale_validation = MirrorMembershipValidation.objects.get(pk=validation.pk)

        membership_request.responses = self._mirror_responses(
            domain="https://mirror2.example.org",
            pull_request="https://github.com/AlmaLinux/mirrors/pull/999",
        )
        membership_request.save(update_fields=["responses"])
        schedule_mirror_membership_validation(membership_request=membership_request)

        note_content = finalize_validation(
            validation=stale_validation,
            outcome=ValidationOutcome(
                overall_status=MirrorMembershipValidation.Status.completed,
                result={
                    "domain": {"status": "reachable", "url": "https://mirror.example.org"},
                    "timestamp": {"status": "up_to_date", "url": "https://mirror.example.org/almalinux/timestamp.txt"},
                    "github": {"status": "valid", "url": "https://github.com/AlmaLinux/mirrors/pull/123"},
                },
                should_retry=False,
            ),
            now=timezone.now(),
        )

        validation.refresh_from_db()
        self.assertIsNone(note_content)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.pending)
        self.assertEqual(
            validation.answer_fingerprint,
            mirror_answers_fingerprint(mirror_request_answers(membership_request)),
        )
        self.assertEqual(validation.result, {})
        self.assertFalse(Note.objects.filter(membership_request=membership_request, username=CUSTOS).exists())

    def test_command_dry_run_does_not_mutate_state(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        stdout = StringIO()

        with patch(
            "core.mirror_membership_validation.requests.get",
            side_effect=self._requests_get,
            autospec=True,
        ):
            call_command("membership_mirror_validation", "--dry-run", stdout=stdout)

        validation.refresh_from_db()
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.pending)
        self.assertEqual(validation.attempt_count, 0)
        self.assertFalse(Note.objects.filter(membership_request=membership_request, username=CUSTOS).exists())
        self.assertIn("dry-run", stdout.getvalue().lower())
        self.assertIn(str(membership_request.pk), stdout.getvalue())

    def test_command_force_processes_retryable_row_before_next_run(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        def retryable_requests_get(url: str, **kwargs) -> _FakeResponse:
            _ = kwargs
            if url == "https://github.com/AlmaLinux/mirrors/pull/123":
                return _FakeResponse(status_code=503)
            return self._requests_get(url, **kwargs)

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=retryable_requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_retryable)
        self.assertGreater(validation.next_run_at, timezone.now())
        self.assertEqual(Note.objects.filter(membership_request=membership_request, username=CUSTOS).count(), 0)

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation", "--force")

        validation.refresh_from_db()
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.completed)
        self.assertEqual(Note.objects.filter(membership_request=membership_request, username=CUSTOS).count(), 1)

    def test_command_reclaims_expired_claims(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        validation.status = MirrorMembershipValidation.Status.running
        validation.claimed_at = timezone.now() - datetime.timedelta(minutes=10)
        validation.claim_expires_at = timezone.now() - datetime.timedelta(minutes=5)
        validation.save(update_fields=["status", "claimed_at", "claim_expires_at"])

        stdout = StringIO()
        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation", stdout=stdout)

        validation.refresh_from_db()
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.completed)
        self.assertIn("reclaimed", stdout.getvalue().lower())

    def test_command_retries_then_terminally_notes_after_retry_exhaustion(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=requests.exceptions.Timeout("github timed out"),
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation", "--force")
            call_command("membership_mirror_validation", "--force")
            call_command("membership_mirror_validation", "--force")

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        note = Note.objects.get(membership_request=membership_request, username=CUSTOS)
        self.assertIn("retry exhausted", note.content)

    def test_command_reports_persisted_status_after_processing(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        stdout = StringIO()
        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command("membership_mirror_validation", stdout=stdout)

        self.assertIn(f"processed request {membership_request.pk} status=completed", stdout.getvalue())

    def test_command_request_id_processes_request_without_existing_validation_row(self) -> None:
        membership_request = self._create_user_request()
        stdout = StringIO()

        self.assertFalse(MirrorMembershipValidation.objects.filter(membership_request=membership_request).exists())

        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=self._bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command(
                "membership_mirror_validation",
                "--request-id",
                str(membership_request.pk),
                stdout=stdout,
            )

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.completed)
        self.assertEqual(validation.attempt_count, 1)
        self.assertIn(f"processing request ID {membership_request.pk} via --request-id", stdout.getvalue())
        self.assertIn(
            "debug: domain target=https://mirror.example.org result=reachable",
            stdout.getvalue(),
        )
        self.assertIn(
            "debug: GitHub target=https://github.com/AlmaLinux/mirrors/pull/123 diff=https://github.com/AlmaLinux/mirrors/pull/123.diff result=valid",
            stdout.getvalue(),
        )

    def test_command_request_id_reruns_completed_validation_with_sanitized_debug_output(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                domain="https://user:secret@mirror.example.org:8443/private/token123/?token=secret",
                pull_request="https://user:secret@github.com/AlmaLinux/mirrors/pull/123?token=secret",
            ),
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        validation.status = MirrorMembershipValidation.Status.completed
        validation.attempt_count = 2
        validation.next_run_at = timezone.now() + datetime.timedelta(days=1)
        validation.result = {"domain": {"status": "reachable", "url": "https://mirror.example.org:8443"}}
        validation.save(update_fields=["status", "attempt_count", "next_run_at", "result"])

        checked_domain_url = "https://mirror.example.org:8443/private/token123"
        checked_root_timestamp_url = "https://mirror.example.org:8443/timestamp.txt"
        checked_timestamp_url = "https://mirror.example.org:8443/almalinux/timestamp.txt"

        def bound_http_get(url: str) -> _FakeResponse:
            if url == checked_domain_url:
                return _FakeResponse(status_code=200)
            if url == checked_root_timestamp_url:
                return _FakeResponse(status_code=404)
            if url == checked_timestamp_url:
                return _FakeResponse(status_code=200, body=self._alma_timestamp_text(age=datetime.timedelta(hours=2)))
            if url == "https://mirror.example.org:8443/almalinux-kitten/timestamp.txt":
                return _FakeResponse(status_code=404)
            raise AssertionError(f"unexpected bound URL: {url}")

        stdout = StringIO()
        with (
            patch("core.mirror_membership_validation._bound_http_get", side_effect=bound_http_get, autospec=True),
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command(
                "membership_mirror_validation",
                "--request-id",
                str(membership_request.pk),
                stdout=stdout,
            )

        validation.refresh_from_db()
        command_output = stdout.getvalue()
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.completed)
        self.assertEqual(validation.attempt_count, 3)
        self.assertIn(
            "debug: domain target=https://mirror.example.org:8443 result=reachable",
            command_output,
        )
        self.assertIn(
            "debug: mirror targets=https://mirror.example.org:8443, https://mirror.example.org:8443/almalinux/timestamp.txt result=up_to_date",
            command_output,
        )
        self.assertIn(
            "debug: AlmaLinux mirror network target=https://raw.githubusercontent.com/AlmaLinux/mirrors/refs/heads/master/mirrors.d/mirror.example.org.yml result=registered",
            command_output,
        )
        self.assertIn(
            "debug: GitHub target=https://github.com/AlmaLinux/mirrors/pull/123 diff=https://github.com/AlmaLinux/mirrors/pull/123.diff result=valid",
            command_output,
        )
        self.assertNotIn("user:secret@", command_output)
        self.assertNotIn("token=secret", command_output)
        self.assertNotIn("/private/token123", command_output)

    def test_command_request_id_deletes_closed_request_validation_without_note_or_debug_output(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        membership_request.status = MembershipRequest.Status.approved
        membership_request.save(update_fields=["status"])

        stdout = StringIO()
        with (
            patch("core.mirror_membership_validation._bound_http_get", autospec=True) as bound_get_mock,
            patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock,
        ):
            call_command(
                "membership_mirror_validation",
                "--request-id",
                str(membership_request.pk),
                stdout=stdout,
            )

        output = stdout.getvalue()
        self.assertFalse(MirrorMembershipValidation.objects.filter(pk=validation.pk).exists())
        self.assertFalse(Note.objects.filter(membership_request=membership_request, username=CUSTOS).exists())
        self.assertIn(
            f"deleted closed-request validation for request ID {membership_request.pk} via --request-id",
            output,
        )
        self.assertNotIn("debug:", output)
        bound_get_mock.assert_not_called()
        get_mock.assert_not_called()

    def test_command_request_id_closed_request_without_row_does_not_recreate_validation(self) -> None:
        membership_request = self._create_user_request(status=MembershipRequest.Status.approved)

        stdout = StringIO()
        with (
            patch("core.mirror_membership_validation._bound_http_get", autospec=True) as bound_get_mock,
            patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock,
        ):
            call_command(
                "membership_mirror_validation",
                "--request-id",
                str(membership_request.pk),
                stdout=stdout,
            )

        output = stdout.getvalue()
        self.assertFalse(MirrorMembershipValidation.objects.filter(membership_request=membership_request).exists())
        self.assertFalse(Note.objects.filter(membership_request=membership_request, username=CUSTOS).exists())
        self.assertIn(
            f"request ID {membership_request.pk} is closed; no validation row to delete via --request-id",
            output,
        )
        self.assertNotIn("debug:", output)
        bound_get_mock.assert_not_called()
        get_mock.assert_not_called()

    def test_command_request_id_dry_run_does_not_mutate_state_or_emit_debug_output(self) -> None:
        membership_request = self._create_user_request()
        with self.captureOnCommitCallbacks(execute=True):
            record_membership_request_created(
                membership_request=membership_request,
                actor_username="alice",
                send_submitted_email=False,
            )

        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        validation.status = MirrorMembershipValidation.Status.completed
        validation.attempt_count = 2
        validation.next_run_at = timezone.now() + datetime.timedelta(days=1)
        validation.result = {"domain": {"status": "reachable", "url": "https://mirror.example.org"}}
        validation.save(update_fields=["status", "attempt_count", "next_run_at", "result"])

        stdout = StringIO()
        with (
            patch("core.mirror_membership_validation._bound_http_get", autospec=True) as bound_get_mock,
            patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock,
        ):
            call_command(
                "membership_mirror_validation",
                "--request-id",
                str(membership_request.pk),
                "--dry-run",
                stdout=stdout,
            )

        validation.refresh_from_db()
        output = stdout.getvalue()
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.completed)
        self.assertEqual(validation.attempt_count, 2)
        self.assertFalse(Note.objects.filter(membership_request=membership_request, username=CUSTOS).exists())
        self.assertIn(
            f"dry-run: would validate request ID {membership_request.pk} via --request-id",
            output,
        )
        self.assertNotIn("debug:", output)
        bound_get_mock.assert_not_called()
        get_mock.assert_not_called()

    def test_command_request_id_rejects_nonexistent_request_id(self) -> None:
        with self.assertRaisesMessage(CommandError, "membership request ID 999999 does not exist"):
            call_command("membership_mirror_validation", "--request-id", "999999")

    def test_command_request_id_rejects_non_mirror_request_id(self) -> None:
        membership_request = self._create_user_request(
            membership_type_id="individual",
            responses=[{"Contributions": "Docs"}],
        )

        with self.assertRaisesMessage(
            CommandError,
            f"membership request ID {membership_request.pk} is not a mirror membership request",
        ):
            call_command("membership_mirror_validation", "--request-id", str(membership_request.pk))

    def test_command_request_id_redacts_failure_path_debug_output(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                domain="https://user:secret@mirror.example.org:badport/private/token123/?token=secret",
                pull_request="https://user:secret@github.com/AlmaLinux/infra/pull/123/private?token=secret",
            ),
        )
        stdout = StringIO()

        with (
            patch("core.mirror_membership_validation._bound_http_get", autospec=True) as bound_get_mock,
            patch("core.mirror_membership_validation.requests.get", autospec=True) as get_mock,
        ):
            call_command(
                "membership_mirror_validation",
                "--request-id",
                str(membership_request.pk),
                stdout=stdout,
            )

        output = stdout.getvalue()
        validation = MirrorMembershipValidation.objects.get(membership_request=membership_request)
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.failed_terminal)
        self.assertIn(
            "debug: domain target=https://mirror.example.org result=malformed detail=invalid_http_url",
            output,
        )
        self.assertIn(
            "debug: mirror targets=none result=not_checked detail=domain status was malformed",
            output,
        )
        self.assertIn(
            "debug: AlmaLinux mirror network target=none result=not_checked detail=domain status was malformed",
            output,
        )
        self.assertIn(
            "debug: GitHub target=https://github.com result=invalid_wrong_repo detail=wrong_github_repo",
            output,
        )
        self.assertNotIn("user:secret@", output)
        self.assertNotIn("token=secret", output)
        self.assertNotIn("/private/token123", output)
        self.assertNotIn("/AlmaLinux/infra/pull/123/private", output)
        bound_get_mock.assert_not_called()
        get_mock.assert_not_called()

    def test_command_request_id_redacts_unsafe_target_domain_debug_output(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                domain="https://user:secret@mirror.example.org/private/token123/?token=secret",
            ),
        )
        stdout = StringIO()

        with (
            patch(
                "core.mirror_membership_validation._bound_http_get",
                side_effect=UnsafeMirrorTargetError(category="private_address"),
                autospec=True,
            ) as bound_get_mock,
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command(
                "membership_mirror_validation",
                "--request-id",
                str(membership_request.pk),
                stdout=stdout,
            )

        output = stdout.getvalue()
        self.assertIn(
            "debug: domain target=https://mirror.example.org result=unsafe_target detail=private_address",
            output,
        )
        self.assertIn(
            "debug: mirror targets=none result=not_checked detail=domain status was unsafe_target",
            output,
        )
        self.assertIn(
            "debug: AlmaLinux mirror network target=none result=not_checked detail=domain status was unsafe_target",
            output,
        )
        self.assertNotIn("user:secret@", output)
        self.assertNotIn("token=secret", output)
        self.assertNotIn("/private/token123", output)
        bound_get_mock.assert_called_once()

    def test_command_request_id_redacts_inaccessible_domain_debug_output(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                domain="https://user:secret@mirror.example.org/private/token123/?token=secret",
            ),
        )
        stdout = StringIO()

        with (
            patch(
                "core.mirror_membership_validation._bound_http_get",
                side_effect=InaccessibleMirrorTargetError(category="dns_lookup_failed"),
                autospec=True,
            ) as bound_get_mock,
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command(
                "membership_mirror_validation",
                "--request-id",
                str(membership_request.pk),
                stdout=stdout,
            )

        output = stdout.getvalue()
        self.assertIn(
            "debug: domain target=https://mirror.example.org result=inaccessible detail=dns_lookup_failed",
            output,
        )
        self.assertIn(
            "debug: mirror targets=none result=not_checked detail=domain status was inaccessible",
            output,
        )
        self.assertIn(
            "debug: AlmaLinux mirror network target=none result=not_checked detail=domain status was inaccessible",
            output,
        )
        self.assertNotIn("user:secret@", output)
        self.assertNotIn("token=secret", output)
        self.assertNotIn("/private/token123", output)
        bound_get_mock.assert_called_once()

    def test_command_request_id_redacts_retryable_domain_debug_output(self) -> None:
        membership_request = self._create_user_request(
            responses=self._mirror_responses(
                domain="https://user:secret@mirror.example.org/private/token123/?token=secret",
            ),
        )
        stdout = StringIO()

        with (
            patch(
                "core.mirror_membership_validation._bound_http_get",
                side_effect=requests.exceptions.Timeout(),
                autospec=True,
            ) as bound_get_mock,
            patch(
                "core.mirror_membership_validation.requests.get",
                side_effect=self._requests_get,
                autospec=True,
            ),
        ):
            call_command(
                "membership_mirror_validation",
                "--request-id",
                str(membership_request.pk),
                stdout=stdout,
            )

        output = stdout.getvalue()
        self.assertIn(
            "debug: domain target=https://mirror.example.org result=retryable_failure detail=timeout",
            output,
        )
        self.assertIn(
            "debug: mirror targets=none result=not_checked detail=domain status was retryable_failure",
            output,
        )
        self.assertIn(
            "debug: AlmaLinux mirror network target=none result=not_checked detail=domain status was retryable_failure",
            output,
        )
        self.assertNotIn("user:secret@", output)
        self.assertNotIn("token=secret", output)
        self.assertNotIn("/private/token123", output)
        bound_get_mock.assert_called_once()

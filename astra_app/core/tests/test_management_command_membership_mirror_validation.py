from __future__ import annotations

import datetime
from typing import override
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.mirror_membership_validation import ValidationOutcome
from core.models import MembershipRequest, MembershipType, MirrorMembershipValidation
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates


class MembershipMirrorValidationCommandTests(TestCase):
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

    def _mirror_responses(self) -> list[dict[str, str]]:
        return [
            {"Domain": "https://mirror.example.org"},
            {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/123"},
            {"Additional information": "Primary EU mirror"},
        ]

    def _create_mirror_request(
        self,
        *,
        username: str,
        status: str = MembershipRequest.Status.pending,
    ) -> MembershipRequest:
        membership_type = MembershipType.objects.get(code="mirror")
        return MembershipRequest.objects.create(
            requested_username=username,
            membership_type=membership_type,
            status=status,
            responses=self._mirror_responses(),
        )

    def _fake_validation_outcome(self) -> ValidationOutcome:
        return ValidationOutcome(
            overall_status=MirrorMembershipValidation.Status.completed,
            result={
                "domain": {"status": "not_checked", "detail": "test"},
                "timestamp": {"status": "not_checked", "detail": "test"},
                "almalinux_mirror_network": {"status": "not_checked", "detail": "test"},
                "github": {"status": "not_checked", "detail": "test"},
            },
            should_retry=False,
        )

    def test_dry_run_reports_open_mirror_requests_missing_validation_row(self) -> None:
        membership_request = self._create_mirror_request(username="alice")
        self.assertFalse(MirrorMembershipValidation.objects.filter(membership_request=membership_request).exists())

        with self.assertLogs("core.management.commands.membership_mirror_validation", level="INFO") as logs:
            call_command("membership_mirror_validation", "--dry-run")

        self.assertIn(
            f"dry-run: missing mirror validation row for request {membership_request.pk}",
            "\n".join(logs.output),
        )

    def test_fix_creates_validation_row_for_missing_open_request(self) -> None:
        membership_request = self._create_mirror_request(username="bob")
        self.assertFalse(MirrorMembershipValidation.objects.filter(membership_request=membership_request).exists())

        with (
            self.assertLogs("core.management.commands.membership_mirror_validation", level="INFO") as captured,
            patch(
                "core.management.commands.membership_mirror_validation.run_validation",
                autospec=True,
                return_value=self._fake_validation_outcome(),
            ),
        ):
            call_command("membership_mirror_validation", "--fix")

        self.assertTrue(MirrorMembershipValidation.objects.filter(membership_request=membership_request).exists())
        self.assertTrue(
            any(
                f"ensured mirror validation row exists for request {membership_request.pk}" in record.getMessage()
                for record in captured.records
            ),
            "expected --fix to emit an INFO log about the ensured validation row",
        )

    def test_fix_does_not_modify_existing_validation_rows(self) -> None:
        membership_request = self._create_mirror_request(username="carol")
        validation = MirrorMembershipValidation.objects.create(
            membership_request=membership_request,
            status=MirrorMembershipValidation.Status.completed,
            answer_fingerprint="existing",
            next_run_at=timezone.now() + datetime.timedelta(days=1),
        )

        with self.assertLogs("core.management.commands.membership_mirror_validation", level="INFO") as logs:
            call_command("membership_mirror_validation", "--fix")

        validation.refresh_from_db()
        self.assertEqual(validation.status, MirrorMembershipValidation.Status.completed)
        self.assertEqual(validation.answer_fingerprint, "existing")
        self.assertTrue(
            any("mirror_validation.none_due" in line or "processed" in line for line in logs.output),
            f"Expected fix mode to log its work, got: {logs.output}",
        )

    def test_closed_requests_are_ignored_by_detection_and_fix(self) -> None:
        closed_request = self._create_mirror_request(
            username="dave",
            status=MembershipRequest.Status.approved,
        )
        self.assertFalse(MirrorMembershipValidation.objects.filter(membership_request=closed_request).exists())

        with self.assertLogs("core.management.commands.membership_mirror_validation", level="INFO") as logs:
            call_command("membership_mirror_validation", "--dry-run")
        self.assertTrue(
            any("dry-run: no mirror validation rows are due." in line for line in logs.output),
            f"Expected a dry-run summary log, got: {logs.output}",
        )

        with patch(
            "core.management.commands.membership_mirror_validation.run_validation",
            autospec=True,
            return_value=self._fake_validation_outcome(),
        ):
            call_command("membership_mirror_validation", "--fix")

        self.assertFalse(MirrorMembershipValidation.objects.filter(membership_request=closed_request).exists())

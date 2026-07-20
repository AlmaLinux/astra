from types import SimpleNamespace
from typing import override
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from core import signals as astra_signals
from core.freeipa.cache import _user_cache_key
from core.freeipa.client import clear_current_viewer_username, set_current_viewer_username
from core.freeipa.user import FreeIPAUser
from core.membership_notes import CUSTOS
from core.models import MembershipRequest, MembershipType, MembershipTypeCategory, Note, Organization
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates


@override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["IR", "CU"])
class MembershipNotesReceiversEmbargoedTests(TestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 0},
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="organization",
            defaults={"is_individual": False, "is_organization": True, "sort_order": 1},
        )
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="organization",
            defaults={
                "name": "Organization",
                "group_cn": "almalinux-organization",
                "category_id": "organization",
                "sort_order": 1,
                "enabled": True,
            },
        )

        from core import membership_notes_receivers

        membership_notes_receivers.connect_membership_notes_receivers()
        self.mattermost_patch = patch("core.mattermost_webhooks.dispatch_mattermost_event", autospec=True)
        self.mattermost_patch.start()
        self.addCleanup(self.mattermost_patch.stop)

    def _create_user_request(self, *, username: str = "alice") -> MembershipRequest:
        return MembershipRequest.objects.create(
            requested_username=username,
            membership_type_id="individual",
        )

    def _create_org_request(
        self,
        *,
        representative: str = "orgrep",
        country_code: str = "US",
    ) -> MembershipRequest:
        organization = Organization.objects.create(
            name="Example Org",
            representative=representative,
            country_code=country_code,
        )
        return MembershipRequest.objects.create(
            requested_username="",
            requested_organization=organization,
            membership_type_id="organization",
        )

    def _count_receivers_with_dispatch_uid(self, *, signal: object, dispatch_uid: str) -> int:
        signal_receivers = getattr(signal, "receivers")
        return sum(1 for receiver_entry in signal_receivers if receiver_entry[0][0] == dispatch_uid)

    def test_user_signal_embargo_match_creates_note(self) -> None:
        request = self._create_user_request(username="alice")

        with (
            patch(
                "core.membership_notes_receivers.FreeIPAUser.get",
                return_value=SimpleNamespace(_user_data={}),
            ),
            patch(
                "core.membership_notes_receivers.embargoed_country_match_from_user_data",
                return_value=SimpleNamespace(label="Cuba"),
            ),
        ):
            astra_signals.membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="alice",
            )

        notes = list(Note.objects.filter(membership_request=request, username=CUSTOS).order_by("pk"))
        self.assertEqual(len(notes), 1)
        self.assertEqual(
            notes[0].content,
            "This user's declared country, Cuba, is on the list of embargoed countries.",
        )

    def test_user_signal_embargo_note_created_for_private_applicant_non_self_viewer(self) -> None:
        """Compliance embargo checks must bypass fasIsPrivate redaction.

        A private applicant's country attribute is stripped by anonymize() for
        any viewer other than the applicant. If the request is submitted by
        someone else (committee/admin), the compliance receiver must still see
        the declared country and create the warning note.
        """
        request = self._create_user_request(username="kinan-wehba")

        cache_key = _user_cache_key("kinan-wehba")
        cache.set(
            cache_key,
            {
                "uid": ["kinan-wehba"],
                settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR: ["IR"],
                "fasIsPrivate": ["TRUE"],
            },
        )
        self.addCleanup(cache.delete, cache_key)

        set_current_viewer_username("committee-admin")
        try:
            astra_signals.membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="committee-admin",
            )
        finally:
            clear_current_viewer_username()

        notes = list(Note.objects.filter(membership_request=request, username=CUSTOS).order_by("pk"))
        self.assertEqual(len(notes), 1)
        self.assertEqual(
            notes[0].content,
            "This user's declared country, Iran (IR), is on the list of embargoed countries.",
        )

    def test_org_signal_rep_embargo_note_created_for_private_rep_non_self_viewer(self) -> None:
        """Org representative embargo checks must also bypass privacy redaction."""
        request = self._create_org_request(representative="kinan-wehba", country_code="US")
        organization = request.requested_organization
        assert organization is not None

        cache_key = _user_cache_key("kinan-wehba")
        cache.set(
            cache_key,
            {
                "uid": ["kinan-wehba"],
                settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR: ["IR"],
                "fasIsPrivate": ["TRUE"],
            },
        )
        self.addCleanup(cache.delete, cache_key)

        set_current_viewer_username("committee-admin")
        try:
            astra_signals.organization_membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="committee-admin",
                organization_id=organization.pk,
                organization_display_name=organization.name,
            )
        finally:
            clear_current_viewer_username()

        notes = list(Note.objects.filter(membership_request=request, username=CUSTOS).order_by("pk"))
        self.assertEqual(len(notes), 1)
        self.assertEqual(
            notes[0].content,
            "This organization's representative's declared country, Iran (IR), is on the list of embargoed countries.",
        )

    def test_user_signal_without_match_creates_no_note(self) -> None:
        request = self._create_user_request(username="alice")

        with (
            patch(
                "core.membership_notes_receivers.FreeIPAUser.get",
                return_value=SimpleNamespace(_user_data={}),
            ),
            patch(
                "core.membership_notes_receivers.embargoed_country_match_from_user_data",
                return_value=None,
            ),
        ):
            astra_signals.membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="alice",
            )

        self.assertEqual(Note.objects.filter(membership_request=request, username=CUSTOS).count(), 0)

    def test_user_signal_freeipa_returns_none_creates_no_note(self) -> None:
        request = self._create_user_request(username="alice")

        with patch("core.membership_notes_receivers.FreeIPAUser.get", return_value=None):
            astra_signals.membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="alice",
            )

        self.assertEqual(Note.objects.filter(membership_request=request, username=CUSTOS).count(), 0)

    def test_user_signal_freeipa_raises_logs_and_does_not_crash(self) -> None:
        request = self._create_user_request(username="alice")

        with (
            patch(
                "core.membership_notes_receivers.FreeIPAUser.get",
                side_effect=RuntimeError("freeipa user lookup failed"),
            ),
            self.assertLogs("core.membership_notes_receivers", level="ERROR") as captured,
        ):
            astra_signals.membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="alice",
            )

        self.assertEqual(Note.objects.filter(membership_request=request, username=CUSTOS).count(), 0)
        self.assertTrue(
            any(record.getMessage() == "membership_request.note.error" for record in captured.records),
        )

    def test_org_signal_org_country_match_creates_org_note(self) -> None:
        request = self._create_org_request(representative="", country_code="CU")
        organization = request.requested_organization
        assert organization is not None

        with (
            patch("core.membership_notes_receivers.Organization.objects.get", return_value=organization),
            patch(
                "core.membership_notes_receivers.embargoed_country_match_from_country_code",
                return_value=SimpleNamespace(label="Cuba"),
            ),
        ):
            astra_signals.organization_membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="alice",
                organization_id=organization.pk,
                organization_display_name=organization.name,
            )

        notes = list(Note.objects.filter(membership_request=request, username=CUSTOS).order_by("pk"))
        self.assertEqual(len(notes), 1)
        self.assertEqual(
            notes[0].content,
            "This organization's declared country, Cuba, is on the list of embargoed countries.",
        )

    def test_org_signal_representative_country_match_creates_rep_note(self) -> None:
        request = self._create_org_request(representative="orgrep", country_code="US")
        organization = request.requested_organization
        assert organization is not None

        with (
            patch(
                "core.membership_notes_receivers.FreeIPAUser.get",
                return_value=SimpleNamespace(_user_data={}),
            ),
            patch(
                "core.membership_notes_receivers.embargoed_country_match_from_user_data",
                return_value=SimpleNamespace(label="Cuba"),
            ),
        ):
            astra_signals.organization_membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="alice",
                organization_id=organization.pk,
                organization_display_name=organization.name,
            )

        notes = list(Note.objects.filter(membership_request=request, username=CUSTOS).order_by("pk"))
        self.assertEqual(len(notes), 1)
        self.assertEqual(
            notes[0].content,
            "This organization's representative's declared country, Cuba, is on the list of embargoed countries.",
        )

    def test_org_signal_without_representative_skips_rep_check(self) -> None:
        request = self._create_org_request(representative="", country_code="US")
        organization = request.requested_organization
        assert organization is not None

        with patch("core.membership_notes_receivers.FreeIPAUser.get", autospec=True) as get_mock:
            astra_signals.organization_membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="alice",
                organization_id=organization.pk,
                organization_display_name=organization.name,
            )

        get_mock.assert_not_called()
        self.assertEqual(Note.objects.filter(membership_request=request, username=CUSTOS).count(), 0)

    def test_org_signal_rep_lookup_returns_none_creates_no_rep_note(self) -> None:
        request = self._create_org_request(representative="orgrep", country_code="US")
        organization = request.requested_organization
        assert organization is not None

        with patch("core.membership_notes_receivers.FreeIPAUser.get", return_value=None):
            astra_signals.organization_membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="alice",
                organization_id=organization.pk,
                organization_display_name=organization.name,
            )

        self.assertEqual(Note.objects.filter(membership_request=request, username=CUSTOS).count(), 0)

    def test_org_signal_rep_lookup_raises_logs_and_does_not_crash(self) -> None:
        request = self._create_org_request(representative="orgrep", country_code="US")
        organization = request.requested_organization
        assert organization is not None

        with (
            patch(
                "core.membership_notes_receivers.FreeIPAUser.get",
                side_effect=RuntimeError("freeipa org rep lookup failed"),
            ),
            self.assertLogs("core.membership_notes_receivers", level="ERROR") as captured,
        ):
            astra_signals.organization_membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="alice",
                organization_id=organization.pk,
                organization_display_name=organization.name,
            )

        self.assertEqual(Note.objects.filter(membership_request=request, username=CUSTOS).count(), 0)
        self.assertTrue(
            any(record.getMessage() == "membership_request.note.error" for record in captured.records),
        )

    def test_org_signal_missing_organization_logs_warning_and_creates_no_notes(self) -> None:
        request = self._create_org_request(representative="orgrep", country_code="US")
        organization = request.requested_organization
        assert organization is not None

        with (
            patch(
                "core.membership_notes_receivers.Organization.objects.get",
                side_effect=Organization.DoesNotExist,
            ),
            self.assertLogs("core.membership_notes_receivers", level="WARNING") as captured,
        ):
            astra_signals.organization_membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="alice",
                organization_id=organization.pk,
                organization_display_name=organization.name,
            )

        self.assertEqual(Note.objects.filter(membership_request=request, username=CUSTOS).count(), 0)
        self.assertTrue(
            any(record.getMessage() == "membership_request.organization.not_found" for record in captured.records),
        )

    def test_org_signal_missing_org_id_logs_debug_and_creates_no_notes(self) -> None:
        request = self._create_user_request(username="alice")

        with self.assertLogs("core.membership_notes_receivers", level="DEBUG") as captured:
            astra_signals.organization_membership_request_submitted.send(
                sender=MembershipRequest,
                membership_request=request,
                actor="alice",
                organization_id=None,
                organization_display_name="Example Org",
            )

        self.assertEqual(Note.objects.filter(membership_request=request, username=CUSTOS).count(), 0)
        self.assertTrue(
            any(record.getMessage() == "membership_request.organization.missing_id" for record in captured.records),
        )

    def test_connect_membership_notes_receivers_is_idempotent_for_all_dispatch_uids(self) -> None:
        from core import membership_notes_receivers

        membership_notes_receivers.connect_membership_notes_receivers()
        membership_notes_receivers.connect_membership_notes_receivers()

        self.assertEqual(
            self._count_receivers_with_dispatch_uid(
                signal=astra_signals.user_country_changed,
                dispatch_uid="core.membership_notes_receivers.user_country_changed",
            ),
            1,
        )
        self.assertEqual(
            self._count_receivers_with_dispatch_uid(
                signal=astra_signals.membership_request_submitted,
                dispatch_uid="core.membership_notes_receivers.membership_request_submitted",
            ),
            1,
        )
        self.assertEqual(
            self._count_receivers_with_dispatch_uid(
                signal=astra_signals.organization_membership_request_submitted,
                dispatch_uid="core.membership_notes_receivers.organization_membership_request_submitted",
            ),
            1,
        )


class MembershipRequestViewRegressionTests(TestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        ensure_email_templates()
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_membership_request_view_does_not_call_add_note_inline(self) -> None:
        self._login_as_freeipa_user("alice")
        freeipa_user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
                "fasstatusnote": ["US"],
                "memberof_group": [],
            },
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=freeipa_user),
            patch("core.views_membership.user.block_action_without_coc", return_value=None),
            patch("core.views_membership.user.block_action_without_country_code", return_value=None),
            patch("core.mattermost_webhooks.dispatch_mattermost_event", autospec=True),
            patch("core.membership_notes.add_note", autospec=True) as add_note_mock,
        ):
            response = self.client.post(
                reverse("membership-request"),
                data={
                    "membership_type": "individual",
                    "q_contributions": "I contributed tests for embargoed-country receivers.",
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            MembershipRequest.objects.filter(
                requested_username="alice",
                membership_type_id="individual",
                status=MembershipRequest.Status.pending,
            ).exists()
        )
        add_note_mock.assert_not_called()

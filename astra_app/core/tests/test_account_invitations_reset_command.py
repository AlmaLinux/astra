import io
import json

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from post_office.models import Email, EmailTemplate

from core.account_invitations import find_account_invitation_matches
from core.freeipa.e2e_registry import E2E_FREEIPA_REGULAR_USERNAMES
from core.models import AccountInvitation, AccountInvitationSend, FreeIPAPermissionGrant, Organization
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.rate_limit import _rate_limit_cache_key, allow_request
from core.tests.utils_test_data import ensure_core_categories


class AccountInvitationsResetCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        ensure_core_categories()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def setUp(self) -> None:
        super().setUp()
        EmailTemplate.objects.update_or_create(
            name=settings.ACCOUNT_INVITE_EMAIL_TEMPLATE_NAME,
            defaults={
                "subject": "Account invitation",
                "content": "Hello {{ email }}",
                "html_content": "<p>Hello {{ email }}</p>",
            },
        )

    @override_settings(ASTRA_E2E_MODE=False, ASTRA_E2E_FAKE_FREEIPA_ENABLED=False)
    def test_command_rejects_runs_outside_fake_freeipa_e2e_mode(self) -> None:
        with self.assertRaisesMessage(CommandError, "ASTRA_E2E_FAKE_FREEIPA_ENABLED"):
            call_command("account_invitations_reset")

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_wave3_invitation_scenarios_idempotently(self) -> None:
        stdout_first = io.StringIO()
        stdout_second = io.StringIO()

        call_command("account_invitations_reset", stdout=stdout_first)
        first_payload = json.loads(stdout_first.getvalue())

        call_command("account_invitations_reset", stdout=stdout_second)
        second_payload = json.loads(stdout_second.getvalue())

        self.assertEqual(first_payload["scenario"], "membership-invitations")
        self.assertEqual(second_payload["scenario"], "membership-invitations")
        self.assertEqual(set(first_payload["actors"].keys()), {"invitation_operator"})
        self.assertEqual(first_payload["actors"].keys(), second_payload["actors"].keys())

        operator_payload = first_payload["actors"]["invitation_operator"]
        alias_payload = second_payload["actors"]["invitation_operator"]["invitation_aliases"]
        scenario_aliases = first_payload["scenarios"]

        self.assertEqual(operator_payload["username"], "regular01")
        self.assertEqual(
            set(alias_payload.keys()),
            {
                "pending_shell_observer",
                "accepted_shell_observer",
                "pending_row_resend",
                "pending_row_dismiss",
                "pending_refresh_acceptance",
                "pending_bulk_resend_primary",
                "pending_bulk_resend_secondary",
                "pending_bulk_resend_extra",
                "pending_bulk_dismiss_primary",
                "pending_bulk_dismiss_secondary",
                "accepted_single_dismiss",
                "accepted_bulk_dismiss_primary",
                "accepted_bulk_dismiss_secondary",
                "accepted_bulk_dismiss_extra",
                "accepted_multi_match_inspection",
            },
        )
        self.assertEqual(
            set(scenario_aliases.keys()),
            {
                "invitations-list-shell",
                "invitations-refresh-now",
                "invitations-pending-row-actions",
                "invitations-pending-bulk-resend",
                "invitations-pending-bulk-dismiss",
                "invitations-accepted-bulk-dismiss",
                "invitations-accepted-single-dismiss",
                "invitations-accepted-inspection",
            },
        )
        self.assertEqual(
            scenario_aliases["invitations-pending-bulk-resend"]["aliases"],
            [
                "pending_bulk_resend_primary",
                "pending_bulk_resend_secondary",
                "pending_bulk_resend_extra",
            ],
        )
        self.assertTrue(scenario_aliases["invitations-pending-bulk-resend"]["destructive"])
        self.assertFalse(scenario_aliases["invitations-list-shell"]["destructive"])

        pending_emails = {
            alias: metadata["email"]
            for alias, metadata in alias_payload.items()
            if metadata["resend_eligibility"] == "pending_non_matching"
        }
        self.assertEqual(
            set(pending_emails.keys()),
            {
                "pending_shell_observer",
                "pending_row_resend",
                "pending_row_dismiss",
                "pending_bulk_resend_primary",
                "pending_bulk_resend_secondary",
                "pending_bulk_resend_extra",
                "pending_bulk_dismiss_primary",
                "pending_bulk_dismiss_secondary",
            },
        )
        for email in pending_emails.values():
            self.assertTrue(email.endswith("@membership-invitations.invalid"))
            self.assertEqual(find_account_invitation_matches(email), [])

        fake_freeipa_emails = {f"{username}@example.test" for username in E2E_FREEIPA_REGULAR_USERNAMES}
        self.assertTrue(fake_freeipa_emails.isdisjoint(set(pending_emails.values())))

        accepted_aliases = {
            alias: metadata
            for alias, metadata in alias_payload.items()
            if metadata["resend_eligibility"] == "accepted_preseeded"
        }
        self.assertEqual(
            set(accepted_aliases.keys()),
            {
                "accepted_shell_observer",
                "accepted_bulk_dismiss_primary",
                "accepted_bulk_dismiss_secondary",
                "accepted_bulk_dismiss_extra",
                "accepted_single_dismiss",
                "accepted_multi_match_inspection",
            },
        )
        self.assertTrue(all(metadata["accepted_username"] for metadata in accepted_aliases.values()))
        self.assertTrue(all(metadata["freeipa_matched_usernames"] for metadata in accepted_aliases.values()))

        pending_page_ids = set(
            AccountInvitation.objects.filter(accepted_at__isnull=True, dismissed_at__isnull=True)
            .order_by("-invited_at", "email")
            .values_list("pk", flat=True)[:25]
        )
        accepted_page_ids = set(
            AccountInvitation.objects.filter(accepted_at__isnull=False, dismissed_at__isnull=True)
            .order_by("-accepted_at", "email")
            .values_list("pk", flat=True)[:25]
        )

        self.assertGreaterEqual(AccountInvitation.objects.filter(accepted_at__isnull=True, dismissed_at__isnull=True).count(), 26)
        self.assertGreaterEqual(AccountInvitation.objects.filter(accepted_at__isnull=False, dismissed_at__isnull=True).count(), 26)
        self.assertTrue(
            {
                alias_payload["pending_shell_observer"]["invitation_id"],
                alias_payload["pending_row_resend"]["invitation_id"],
                alias_payload["pending_row_dismiss"]["invitation_id"],
            }.issubset(pending_page_ids)
        )
        self.assertTrue(
            {
                alias_payload["accepted_shell_observer"]["invitation_id"],
                alias_payload["accepted_bulk_dismiss_primary"]["invitation_id"],
                alias_payload["accepted_bulk_dismiss_secondary"]["invitation_id"],
            }.issubset(accepted_page_ids)
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_refresh_multi_match_and_pending_dismiss_aliases_for_wave7_operator_coverage(self) -> None:
        stdout = io.StringIO()

        call_command("account_invitations_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        invitation_aliases = payload["actors"]["invitation_operator"]["invitation_aliases"]

        self.assertTrue(
            {
                "pending_refresh_acceptance",
                "accepted_single_dismiss",
                "pending_bulk_dismiss_primary",
                "pending_bulk_dismiss_secondary",
                "accepted_multi_match_inspection",
            }.issubset(set(invitation_aliases.keys()))
        )

        self.assertEqual(
            invitation_aliases["pending_refresh_acceptance"]["resend_eligibility"],
            "pending_refresh_match",
        )
        self.assertEqual(
            invitation_aliases["accepted_multi_match_inspection"]["accepted_username"],
            "regular06",
        )
        self.assertEqual(
            invitation_aliases["accepted_multi_match_inspection"]["freeipa_matched_usernames"],
            ["regular06", "regular16"],
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_organization_link_targets_for_pending_and_accepted_shell_rows(self) -> None:
        stdout = io.StringIO()

        call_command("account_invitations_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        invitation_aliases = payload["actors"]["invitation_operator"]["invitation_aliases"]
        pending_shell = invitation_aliases["pending_shell_observer"]
        accepted_shell = invitation_aliases["accepted_shell_observer"]

        self.assertEqual(pending_shell["organization_name"], "Wave 7 Pending Invitation Org")
        self.assertIsInstance(pending_shell["organization_id"], int)
        self.assertEqual(accepted_shell["organization_name"], "Wave 7 Accepted Invitation Org")
        self.assertIsInstance(accepted_shell["organization_id"], int)

        pending_invitation = AccountInvitation.objects.get(pk=pending_shell["invitation_id"])
        accepted_invitation = AccountInvitation.objects.get(pk=accepted_shell["invitation_id"])

        self.assertEqual(pending_invitation.organization_id, pending_shell["organization_id"])
        self.assertEqual(accepted_invitation.organization_id, accepted_shell["organization_id"])
        self.assertTrue(
            Organization.objects.filter(
                pk=pending_shell["organization_id"],
                name="Wave 7 Pending Invitation Org",
            ).exists()
        )
        self.assertTrue(
            Organization.objects.filter(
                pk=accepted_shell["organization_id"],
                name="Wave 7 Accepted Invitation Org",
            ).exists()
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_clears_wave3_resend_side_effects_before_reseeding(self) -> None:
        stdout = io.StringIO()
        call_command("account_invitations_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        operator_payload = payload["actors"]["invitation_operator"]
        resend_pk = operator_payload["invitation_aliases"]["pending_row_resend"]["invitation_id"]
        resend_invitation = AccountInvitation.objects.get(pk=resend_pk)

        queued_email = Email.objects.create(
            to=[resend_invitation.email],
            from_email=settings.DEFAULT_FROM_EMAIL,
            subject="Wave 3 resend artifact",
            message="queued",
        )
        AccountInvitationSend.objects.create(
            invitation=resend_invitation,
            sent_by_username="regular01",
            sent_at=timezone.now(),
            template_name=settings.ACCOUNT_INVITE_EMAIL_TEMPLATE_NAME,
            post_office_email_id=queued_email.pk,
            result=AccountInvitationSend.Result.queued,
        )
        resend_invitation.last_sent_at = timezone.now()
        resend_invitation.send_count = 2
        resend_invitation.dismissed_at = timezone.now()
        resend_invitation.dismissed_by_username = "regular01"
        resend_invitation.save(
            update_fields=["last_sent_at", "send_count", "dismissed_at", "dismissed_by_username"]
        )

        row_limit = settings.ACCOUNT_INVITATION_RESEND_LIMIT
        while allow_request(
            scope="account_invitation_resend",
            key_parts=["regular01", str(resend_invitation.pk)],
            limit=row_limit,
            window_seconds=settings.ACCOUNT_INVITATION_RESEND_WINDOW_SECONDS,
        ):
            pass

        bulk_limit = settings.ACCOUNT_INVITATION_RESEND_LIMIT
        while allow_request(
            scope="account_invitation_bulk_resend",
            key_parts=["regular01"],
            limit=bulk_limit,
            window_seconds=settings.ACCOUNT_INVITATION_BULK_SEND_WINDOW_SECONDS,
        ):
            pass

        row_cache_key = _rate_limit_cache_key("account_invitation_resend", ["regular01", str(resend_invitation.pk)])
        bulk_cache_key = _rate_limit_cache_key("account_invitation_bulk_resend", ["regular01"])
        self.assertIsNotNone(cache.get(row_cache_key))
        self.assertIsNotNone(cache.get(bulk_cache_key))

        call_command("account_invitations_reset")

        resend_invitation = AccountInvitation.objects.get(email=resend_invitation.email)
        self.assertEqual(resend_invitation.send_count, 0)
        self.assertIsNone(resend_invitation.last_sent_at)
        self.assertIsNone(resend_invitation.dismissed_at)
        self.assertEqual(resend_invitation.dismissed_by_username, "")
        self.assertEqual(resend_invitation.email_template_name, settings.ACCOUNT_INVITE_EMAIL_TEMPLATE_NAME)
        self.assertFalse(AccountInvitationSend.objects.filter(invitation=resend_invitation).exists())
        self.assertFalse(Email.objects.filter(pk=queued_email.pk).exists())
        self.assertIsNone(cache.get(row_cache_key))
        self.assertIsNone(cache.get(bulk_cache_key))
        self.assertTrue(
            allow_request(
                scope="account_invitation_resend",
                key_parts=["regular01", str(resend_invitation.pk)],
                limit=row_limit,
                window_seconds=settings.ACCOUNT_INVITATION_RESEND_WINDOW_SECONDS,
            )
        )
        self.assertTrue(
            allow_request(
                scope="account_invitation_bulk_resend",
                key_parts=["regular01"],
                limit=bulk_limit,
                window_seconds=settings.ACCOUNT_INVITATION_BULK_SEND_WINDOW_SECONDS,
            )
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_requires_existing_invitation_template_prerequisite(self) -> None:
        EmailTemplate.objects.filter(name=settings.ACCOUNT_INVITE_EMAIL_TEMPLATE_NAME).delete()

        with self.assertRaisesMessage(CommandError, settings.ACCOUNT_INVITE_EMAIL_TEMPLATE_NAME):
            call_command("account_invitations_reset")
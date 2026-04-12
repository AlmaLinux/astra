
import datetime
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
from core.models import Membership, MembershipRequest, MembershipType, Organization


class FreeIPAMembershipReconcileCommandTests(TestCase):
    def _group(self, cn: str, members: list[str]) -> FreeIPAGroup:
        return FreeIPAGroup(
            cn,
            {
                "cn": [cn],
                "member_user": members,
            },
        )

    def test_report_mode_alerts_and_does_not_mutate(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        Membership.objects.create(target_username="alice", membership_type=membership_type)

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        target_group = self._group("almalinux-individual", [])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-individual":
                return target_group
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", return_value=admin_user),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.add_member") as add_mock,
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.remove_member") as remove_mock,
            self.assertLogs("core.management.commands.freeipa_membership_reconcile", level="INFO") as logs,
        ):
            call_command("freeipa_membership_reconcile")

        add_mock.assert_not_called()
        remove_mock.assert_not_called()

        from post_office.models import Email

        self.assertTrue(
            Email.objects.filter(
                to="admin@example.com",
                template__name=settings.FREEIPA_MEMBERSHIP_RECONCILE_ALERT_EMAIL_TEMPLATE_NAME,
            ).exists()
        )
        self.assertTrue(
            any("Reconciliation complete" in line for line in logs.output),
            f"Expected a reconciliation summary log, got: {logs.output}",
        )

    def test_fix_mode_applies_changes(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        Membership.objects.create(target_username="alice", membership_type=membership_type)

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        target_group = self._group("almalinux-individual", ["bob"])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-individual":
                return target_group
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", return_value=admin_user),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.add_member") as add_mock,
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.remove_member") as remove_mock,
        ):
            call_command("freeipa_membership_reconcile", "--fix")

        add_mock.assert_called_once_with("alice")
        remove_mock.assert_called_once_with("bob")

    def test_targeted_mode_requires_selector(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            "Targeted mode requires exactly one selector: provide --request-id or --username.",
        ):
            call_command("freeipa_membership_reconcile", "--targeted")

    def test_targeted_mode_rejects_multiple_selectors(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            "Targeted mode requires exactly one selector: use either --request-id or --username.",
        ):
            call_command(
                "freeipa_membership_reconcile",
                "--targeted",
                "--request-id",
                "1",
                "--username",
                "alice",
            )

    def test_request_id_mode_requires_approved_status(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        Membership.objects.create(target_username="alice", membership_type=membership_type)
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.pending,
        )

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        target_group = self._group("almalinux-individual", ["bob"])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-individual":
                return target_group
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", return_value=admin_user),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.add_member") as add_mock,
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.remove_member") as remove_mock,
            self.assertRaisesMessage(
                CommandError,
                f"membership request ID {membership_request.pk} must be approved for targeted reconcile; status=pending",
            ),
        ):
            call_command(
                "freeipa_membership_reconcile",
                "--fix",
                "--request-id",
                str(membership_request.pk),
            )

        add_mock.assert_not_called()
        remove_mock.assert_not_called()

    def test_request_id_targeted_fix_adds_only_selected_identity(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        Membership.objects.create(target_username="alice", membership_type=membership_type)
        Membership.objects.create(target_username="charlie", membership_type=membership_type)
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
        )

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        target_group = self._group("almalinux-individual", ["bob"])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-individual":
                return target_group
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", return_value=admin_user),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.add_member") as add_mock,
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.remove_member") as remove_mock,
            self.assertLogs("core.management.commands.freeipa_membership_reconcile", level="INFO") as logs,
        ):
            call_command(
                "freeipa_membership_reconcile",
                "--fix",
                "--request-id",
                str(membership_request.pk),
            )

        add_mock.assert_called_once_with("alice")
        remove_mock.assert_not_called()
        self.assertTrue(
            any(
                "selector_type=request_id" in line
                and f"selector_value={membership_request.pk}" in line
                and "target=alice" in line
                and "group=almalinux-individual" in line
                and "mode=fix" in line
                and "outcome=mutated_add_only" in line
                for line in logs.output
            ),
            f"Expected targeted selector context fields, got: {logs.output}",
        )

    def test_request_id_targeted_fix_noops_when_target_not_expected(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        Membership.objects.create(
            target_username="alice",
            membership_type=membership_type,
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
        )

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        target_group = self._group("almalinux-individual", ["bob"])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-individual":
                return target_group
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", return_value=admin_user),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.add_member") as add_mock,
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.remove_member") as remove_mock,
            self.assertLogs("core.management.commands.freeipa_membership_reconcile", level="INFO") as logs,
        ):
            call_command(
                "freeipa_membership_reconcile",
                "--fix",
                "--request-id",
                str(membership_request.pk),
            )

        add_mock.assert_not_called()
        remove_mock.assert_not_called()
        self.assertTrue(
            any(
                "selector_type=request_id" in line
                and f"selector_value={membership_request.pk}" in line
                and "target=alice" in line
                and "group=almalinux-individual" in line
                and "mode=fix" in line
                and "outcome=noop_target_not_expected" in line
                for line in logs.output
            ),
            f"Expected targeted no-op selector context fields, got: {logs.output}",
        )

    def test_request_id_targeted_report_marks_drift_not_in_sync(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        Membership.objects.create(target_username="alice", membership_type=membership_type)
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
        )

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        target_group = self._group("almalinux-individual", [])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-individual":
                return target_group
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", return_value=admin_user),
            self.assertLogs("core.management.commands.freeipa_membership_reconcile", level="INFO") as logs,
        ):
            call_command(
                "freeipa_membership_reconcile",
                "--report",
                "--request-id",
                str(membership_request.pk),
            )

        self.assertTrue(
            any(
                "selector_type=request_id" in line
                and f"selector_value={membership_request.pk}" in line
                and "target=alice" in line
                and "group=almalinux-individual" in line
                and "mode=report" in line
                and "outcome=reported_drift" in line
                for line in logs.output
            ),
            f"Expected targeted report drift outcome fields, got: {logs.output}",
        )
        self.assertFalse(
            any("outcome=noop_already_in_sync" in line for line in logs.output),
            f"Did not expect in-sync no-op outcome when drift exists, got: {logs.output}",
        )

    def test_request_id_targeted_dry_run_marks_drift_not_in_sync(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        Membership.objects.create(target_username="alice", membership_type=membership_type)
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
        )

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        target_group = self._group("almalinux-individual", [])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-individual":
                return target_group
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", return_value=admin_user),
            self.assertLogs("core.management.commands.freeipa_membership_reconcile", level="INFO") as logs,
        ):
            call_command(
                "freeipa_membership_reconcile",
                "--fix",
                "--dry-run",
                "--request-id",
                str(membership_request.pk),
            )

        self.assertTrue(
            any(
                "selector_type=request_id" in line
                and f"selector_value={membership_request.pk}" in line
                and "target=alice" in line
                and "group=almalinux-individual" in line
                and "mode=report" in line
                and "outcome=reported_drift" in line
                for line in logs.output
            ),
            f"Expected targeted dry-run drift outcome fields, got: {logs.output}",
        )
        self.assertFalse(
            any("outcome=noop_already_in_sync" in line for line in logs.output),
            f"Did not expect in-sync no-op outcome when drift exists, got: {logs.output}",
        )

    def test_request_id_mode_evaluates_disabled_membership_type_group(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": False,
            },
        )
        Membership.objects.create(target_username="alice", membership_type=membership_type)
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
        )

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        target_group = self._group("almalinux-individual", [])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-individual":
                return target_group
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", return_value=admin_user),
            self.assertLogs("core.management.commands.freeipa_membership_reconcile", level="INFO") as logs,
        ):
            call_command(
                "freeipa_membership_reconcile",
                "--report",
                "--request-id",
                str(membership_request.pk),
            )

        self.assertTrue(
            any(
                "group_diff" in line
                and "group=almalinux-individual" in line
                and "mode=report" in line
                and "missing=1" in line
                for line in logs.output
            ),
            f"Expected disabled request-id group to be evaluated for drift, got: {logs.output}",
        )
        self.assertFalse(
            any("no_drift" in line for line in logs.output),
            f"Did not expect no_drift when request-id target is missing in FreeIPA, got: {logs.output}",
        )

    def test_request_id_mode_resolves_org_representative_and_group(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        org = Organization.objects.create(name="Acme", representative="repuser")
        Membership.objects.create(target_organization=org, membership_type=membership_type)
        membership_request = MembershipRequest.objects.create(
            requested_organization=org,
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
        )

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        gold_group = self._group("almalinux-gold", [])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-gold":
                return gold_group
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", return_value=admin_user),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.add_member") as add_mock,
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.remove_member") as remove_mock,
        ):
            call_command(
                "freeipa_membership_reconcile",
                "--fix",
                "--request-id",
                str(membership_request.pk),
            )

        add_mock.assert_called_once_with("repuser")
        remove_mock.assert_not_called()

    def test_sponsorship_divergence_is_logged(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor",
                "group_cn": "almalinux-silver",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="Acme",
            representative="bob",
        )
        # Expired sponsorship — triggers the divergence warning.
        Membership.objects.create(
            target_organization=org,
            membership_type_id="silver",
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        gold_group = self._group("almalinux-gold", ["bob"])
        silver_group = self._group("almalinux-silver", [])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-gold":
                return gold_group
            if cn == "almalinux-silver":
                return silver_group
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", return_value=admin_user),
            self.assertLogs("core.management.commands.freeipa_membership_reconcile", level="WARNING") as logs,
        ):
            call_command("freeipa_membership_reconcile")

        self.assertTrue(
            any("sponsorship_divergence" in line for line in logs.output),
            f"Expected divergence warning, got: {logs.output}",
        )

    def test_dry_run_does_not_queue_alert_email(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        Membership.objects.create(target_username="alice", membership_type=membership_type)

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        target_group = self._group("almalinux-individual", [])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-individual":
                return target_group
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", return_value=admin_user),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.add_member") as add_mock,
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.remove_member") as remove_mock,
        ):
            call_command("freeipa_membership_reconcile", "--dry-run")

        add_mock.assert_not_called()
        remove_mock.assert_not_called()

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                to="admin@example.com",
                template__name=settings.FREEIPA_MEMBERSHIP_RECONCILE_ALERT_EMAIL_TEMPLATE_NAME,
            ).exists()
        )

    def test_fix_mode_skips_missing_freeipa_users_instead_of_attempting_add(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        Membership.objects.create(target_username="ghost", membership_type=membership_type)

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        target_group = self._group("almalinux-individual", [])
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        def _get_group(cn: str) -> FreeIPAGroup | None:
            if cn == settings.FREEIPA_ADMIN_GROUP:
                return admin_group
            if cn == "almalinux-individual":
                return target_group
            return None

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "admin":
                return admin_user
            if username == "ghost":
                return None
            return None

        with (
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.get", side_effect=_get_group),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAUser.get", side_effect=_get_user),
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.add_member") as add_mock,
            patch("core.management.commands.freeipa_membership_reconcile.FreeIPAGroup.remove_member") as remove_mock,
            self.assertLogs("core.management.commands.freeipa_membership_reconcile", level="WARNING") as logs,
        ):
            call_command("freeipa_membership_reconcile", "--fix")

        add_mock.assert_not_called()
        remove_mock.assert_not_called()
        self.assertTrue(
            any("expected_user_missing" in line and "ghost" in line for line in logs.output),
            f"Expected missing-user warning, got: {logs.output}",
        )

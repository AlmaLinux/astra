from __future__ import annotations

import datetime
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.backends import FreeIPAGroup, FreeIPAUser
from core.models import Membership, MembershipType, Organization, OrganizationSponsorship


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
                "isIndividual": True,
                "isOrganization": False,
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

    def test_fix_mode_applies_changes(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
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

    def test_sponsorship_divergence_is_logged(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "almalinux-gold",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor",
                "group_cn": "almalinux-silver",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="Acme",
            membership_level_id="gold",
            representative="bob",
        )
        OrganizationSponsorship.objects.create(
            organization=org,
            membership_type_id="silver",
            expires_at=timezone.now() + datetime.timedelta(days=5),
        )

        admin_group = self._group(settings.FREEIPA_ADMIN_GROUP, ["admin"])
        target_group = self._group("almalinux-gold", ["bob"])
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
                return target_group
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
                "isIndividual": True,
                "isOrganization": False,
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

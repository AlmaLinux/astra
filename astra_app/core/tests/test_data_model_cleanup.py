import datetime

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (
    AuditLogEntry,
    Election,
    Membership,
    MembershipLog,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)


class OrganizationStatusTests(TestCase):
    def test_status_follows_representative_even_for_queryset_updates(self) -> None:
        organization = Organization.objects.create(name="Acme")
        self.assertEqual(organization.status, Organization.Status.unclaimed)

        Organization.objects.filter(pk=organization.pk).update(representative="bob")

        self.assertEqual(Organization.objects.get(pk=organization.pk).status, Organization.Status.active)


class OrganizationAuditLogTargetTests(TestCase):
    def setUp(self) -> None:
        self.organization = Organization.objects.create(name="Acme", representative="rep")

    def test_deleting_organization_keeps_its_audit_entries_with_a_snapshot(self) -> None:
        entry = AuditLogEntry.objects.create(
            organization=self.organization,
            event_type="organization_edited",
            payload={"changed_fields": ["name"], "actor_username": "rep"},
        )
        organization_id = self.organization.pk

        self.organization.delete()

        entry.refresh_from_db()
        self.assertIsNone(entry.organization_id)
        self.assertEqual((entry.deleted_organization_id, entry.deleted_organization_name), (organization_id, "Acme"))

    def test_audit_entry_needs_exactly_one_target(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Board",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=1),
        )
        for targets in ({}, {"election": election, "organization": self.organization}):
            with self.subTest(targets=sorted(targets)):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    AuditLogEntry.objects.create(event_type="probe", **targets)


class MembershipExpiryRequiredTests(TestCase):
    def setUp(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            name="individual",
            defaults={"is_individual": True, "is_organization": False},
        )
        self.membership_type, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={"name": "Individual", "category_id": "individual", "enabled": True},
        )

    def test_membership_without_expiry_is_rejected(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(target_username="alice", membership_type=self.membership_type)

    def test_term_actions_without_expiry_are_rejected(self) -> None:
        for action in (MembershipLog.Action.approved, MembershipLog.Action.expiry_changed):
            with self.subTest(action=action):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    MembershipLog.objects.create(
                        actor_username="reviewer",
                        target_username="alice",
                        membership_type=self.membership_type,
                        action=action,
                    )

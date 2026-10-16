from django.test import TestCase

from core.membership_request_workflow import ignore_membership_request
from core.models import (
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)
from core.tests.utils_test_data import FAR_FUTURE


class DeletedOrganizationSnapshotTests(TestCase):
    def setUp(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            name="sponsorship",
            defaults={"is_individual": False, "is_organization": True},
        )
        self.membership_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "sponsor-group",
            },
        )
        self.organization = Organization.objects.create(name="Acme", representative="rep")

    def _create_org_rows(self) -> tuple[MembershipRequest, MembershipLog]:
        request = MembershipRequest.objects.create(
            requested_organization=self.organization,
            membership_type=self.membership_type,
            status=MembershipRequest.Status.approved,
        )
        log = MembershipLog.objects.create(
            actor_username="reviewer",
            action=MembershipLog.Action.approved,
            expires_at=FAR_FUTURE,
            membership_type=self.membership_type,
            target_organization=self.organization,
            membership_request=request,
        )
        return request, log

    def test_renamed_organization_shows_current_name_everywhere(self) -> None:
        request, log = self._create_org_rows()
        membership = Membership.objects.create(
            target_organization=self.organization,
            membership_type=self.membership_type,
            expires_at=FAR_FUTURE,
        )

        Organization.objects.filter(pk=self.organization.pk).update(name="Acme Renamed")

        for row in (
            MembershipRequest.objects.get(pk=request.pk),
            MembershipLog.objects.get(pk=log.pk),
            Membership.objects.get(pk=membership.pk),
        ):
            with self.subTest(model=type(row).__name__):
                self.assertEqual(row.organization_display_name, "Acme Renamed")
                self.assertEqual(row.target_identity.organization_id, self.organization.pk)

    def test_deleting_organization_snapshots_its_id_and_name_at_deletion(self) -> None:
        request, log = self._create_org_rows()
        organization_id = self.organization.pk
        self.organization.name = "Acme Renamed"
        self.organization.save(update_fields=["name"])

        Organization.objects.get(pk=organization_id).delete()

        for row in (MembershipRequest.objects.get(pk=request.pk), MembershipLog.objects.get(pk=log.pk)):
            with self.subTest(model=type(row).__name__):
                self.assertEqual(row.deleted_organization_id, organization_id)
                self.assertEqual(row.deleted_organization_name, "Acme Renamed")
                self.assertEqual(row.target_kind, "organization")
                self.assertEqual(row.organization_display_name, "Acme Renamed")
                self.assertEqual(row.target_identity.organization_id, organization_id)
        self.assertIsNone(MembershipRequest.objects.get(pk=request.pk).requested_organization_id)
        self.assertIsNone(MembershipLog.objects.get(pk=log.pk).target_organization_id)

    def test_bulk_organization_delete_snapshots_each_organization(self) -> None:
        other = Organization.objects.create(name="Other", representative="rep2")
        acme_log = MembershipLog.objects.create(
            actor_username="reviewer",
            action=MembershipLog.Action.approved,
            expires_at=FAR_FUTURE,
            membership_type=self.membership_type,
            target_organization=self.organization,
        )
        other_log = MembershipLog.objects.create(
            actor_username="reviewer",
            action=MembershipLog.Action.approved,
            expires_at=FAR_FUTURE,
            membership_type=self.membership_type,
            target_organization=other,
        )
        expected = {acme_log.pk: (self.organization.pk, "Acme"), other_log.pk: (other.pk, "Other")}

        Organization.objects.filter(pk__in=[self.organization.pk, other.pk]).delete()

        actual = {
            log.pk: (log.deleted_organization_id, log.deleted_organization_name)
            for log in MembershipLog.objects.filter(pk__in=expected)
        }
        self.assertEqual(actual, expected)

    def test_status_change_on_request_of_deleted_organization_logs_the_snapshot(self) -> None:
        request = MembershipRequest.objects.create(
            requested_organization=self.organization,
            membership_type=self.membership_type,
        )
        organization_id = self.organization.pk
        self.organization.delete()

        log = ignore_membership_request(
            membership_request=MembershipRequest.objects.get(pk=request.pk),
            actor_username="reviewer",
        )

        log.refresh_from_db()
        self.assertIsNone(log.target_organization_id)
        self.assertEqual((log.deleted_organization_id, log.deleted_organization_name), (organization_id, "Acme"))

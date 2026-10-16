import datetime
from typing import override

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from core.tests.utils_test_data import FAR_FUTURE, migrate_to_latest

GRANTED_AT = datetime.datetime(2025, 3, 1, tzinfo=datetime.UTC)


class MembershipRequestBackfillMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0105_membership_dues_usd")]
    migrate_to = [("core", "0106_backfill_requests_for_direct_memberships")]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(migrate_to_latest)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        old_apps = executor.loader.project_state(self.migrate_from).apps
        category = old_apps.get_model("core", "MembershipTypeCategory").objects.get_or_create(
            name="sponsorship", defaults={"is_organization": True},
        )[0]
        membership_type = old_apps.get_model("core", "MembershipType").objects.update_or_create(
            code="gold", defaults={"name": "Gold Sponsor Member", "category": category, "annual_dues_usd": 20000},
        )[0]
        organization_model = old_apps.get_model("core", "Organization")
        membership_model = old_apps.get_model("core", "Membership")
        request_model = old_apps.get_model("core", "MembershipRequest")

        # Granted in the Django admin, before grants recorded a request.
        self.granted = organization_model.objects.create(name="Granted Org", status="active", representative="rep1")
        membership = membership_model.objects.create(expires_at=FAR_FUTURE, target_organization=self.granted, membership_type=membership_type)
        membership_model.objects.filter(pk=membership.pk).update(created_at=GRANTED_AT)
        old_apps.get_model("core", "MembershipLog").objects.create(
            actor_username="admin-alice",
            target_organization=self.granted,
            target_organization_code=str(self.granted.pk),
            target_organization_name=self.granted.name,
            membership_type=membership_type,
            requested_group_cn="",
            action="approved",
            expires_at=FAR_FUTURE,
        )
        membership_model.objects.create(expires_at=FAR_FUTURE, target_username="bob", membership_type=membership_type)

        # Applied for through the normal flow.
        self.requested = organization_model.objects.create(name="Requested Org", status="active", representative="rep2")
        membership_model.objects.create(expires_at=FAR_FUTURE, target_organization=self.requested, membership_type=membership_type)
        request_model.objects.create(
            requested_organization=self.requested,
            requested_organization_code=str(self.requested.pk),
            requested_organization_name=self.requested.name,
            membership_type=membership_type,
            status="approved",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_memberships_granted_without_a_request_get_an_approved_one(self) -> None:
        request_model = self.apps.get_model("core", "MembershipRequest")

        backfilled = request_model.objects.get(requested_organization_id=self.granted.pk)
        self.assertEqual(backfilled.status, "approved")
        self.assertEqual(backfilled.membership_type_id, "gold")
        self.assertEqual((backfilled.requested_organization_code, backfilled.requested_organization_name), (str(self.granted.pk), "Granted Org"))
        self.assertEqual((backfilled.requested_at, backfilled.decided_at), (GRANTED_AT, GRANTED_AT))
        self.assertEqual(backfilled.decided_by_username, "admin-alice")
        [note] = self.apps.get_model("core", "Note").objects.filter(membership_request=backfilled)
        self.assertIn("granted directly", note.content)

        user_request = request_model.objects.get(requested_username="bob")
        self.assertEqual((user_request.status, user_request.decided_by_username), ("approved", ""))

        self.assertEqual(request_model.objects.filter(requested_organization_id=self.requested.pk).count(), 1)

from typing import override

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from core.tests.utils_test_data import migrate_to_latest

DELETED_ORG_ID = 424242


class DeletedOrganizationSnapshotMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0107_backfill_lifecycle_log_requests")]
    migrate_to = [("core", "0108_deleted_organization_snapshot")]

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
            code="gold", defaults={"name": "Gold Sponsor Member", "category": category},
        )[0]
        organization_model = old_apps.get_model("core", "Organization")
        request_model = old_apps.get_model("core", "MembershipRequest")
        log_model = old_apps.get_model("core", "MembershipLog")

        self.live = organization_model.objects.create(name="Live Org", status="active", representative="rep1")
        live_snapshot = {"code": str(self.live.pk), "name": "Old Live Name"}
        self.live_request = request_model.objects.create(
            requested_organization=self.live,
            requested_organization_code=live_snapshot["code"],
            requested_organization_name=live_snapshot["name"],
            membership_type=membership_type,
            status="approved",
        )
        self.live_log = log_model.objects.create(
            actor_username="reviewer",
            target_organization=self.live,
            target_organization_code=live_snapshot["code"],
            target_organization_name=live_snapshot["name"],
            membership_type=membership_type,
            requested_group_cn="",
            action="approved",
        )
        self.live_membership = old_apps.get_model("core", "Membership").objects.create(
            target_organization=self.live,
            target_organization_code=live_snapshot["code"],
            target_organization_name=live_snapshot["name"],
            membership_type=membership_type,
        )

        self.deleted_request = request_model.objects.create(
            requested_organization_code=str(DELETED_ORG_ID),
            requested_organization_name="Gone Org",
            membership_type=membership_type,
            status="ignored",
        )
        self.deleted_log = log_model.objects.create(
            actor_username="reviewer",
            target_organization_code=str(DELETED_ORG_ID),
            target_organization_name="Gone Org",
            membership_type=membership_type,
            requested_group_cn="",
            action="terminated",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_only_rows_of_deleted_organizations_keep_a_snapshot(self) -> None:
        request_model = self.apps.get_model("core", "MembershipRequest")
        log_model = self.apps.get_model("core", "MembershipLog")

        for model, live_pk, deleted_pk in (
            (request_model, self.live_request.pk, self.deleted_request.pk),
            (log_model, self.live_log.pk, self.deleted_log.pk),
        ):
            with self.subTest(model=model.__name__):
                live = model.objects.get(pk=live_pk)
                self.assertEqual((live.deleted_organization_id, live.deleted_organization_name), (None, ""))
                deleted = model.objects.get(pk=deleted_pk)
                self.assertEqual(
                    (deleted.deleted_organization_id, deleted.deleted_organization_name),
                    (DELETED_ORG_ID, "Gone Org"),
                )

    def test_reverse_migration_restores_codes_and_names(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        request_model = old_apps.get_model("core", "MembershipRequest")
        log_model = old_apps.get_model("core", "MembershipLog")
        membership_model = old_apps.get_model("core", "Membership")

        for model, pk, prefix, expected in (
            (request_model, self.live_request.pk, "requested", (str(self.live.pk), "Live Org")),
            (log_model, self.live_log.pk, "target", (str(self.live.pk), "Live Org")),
            (membership_model, self.live_membership.pk, "target", (str(self.live.pk), "Live Org")),
            (request_model, self.deleted_request.pk, "requested", (str(DELETED_ORG_ID), "Gone Org")),
            (log_model, self.deleted_log.pk, "target", (str(DELETED_ORG_ID), "Gone Org")),
        ):
            with self.subTest(model=model.__name__, pk=pk):
                row = model.objects.values_list(f"{prefix}_organization_code", f"{prefix}_organization_name").get(pk=pk)
                self.assertEqual(row, expected)

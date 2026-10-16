import datetime
from typing import override

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from core.tests.utils_test_data import migrate_to_latest

FIRST_TERM_AT = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
SECOND_TERM_AT = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


class MembershipLogTermRequestMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0106_backfill_requests_for_direct_memberships")]
    migrate_to = [("core", "0107_backfill_lifecycle_log_requests")]

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
        gold = old_apps.get_model("core", "MembershipType").objects.update_or_create(
            code="gold", defaults={"name": "Gold Sponsor Member", "category": category, "annual_dues_usd": 20000},
        )[0]
        organization_model = old_apps.get_model("core", "Organization")
        acme = organization_model.objects.create(name="Acme Corp", status="active", representative="rep1")
        gone = organization_model.objects.create(name="Gone Corp", status="active", representative="rep2")
        request_model = old_apps.get_model("core", "MembershipRequest")
        log_model = old_apps.get_model("core", "MembershipLog")

        def term_request(*, decided_at: datetime.datetime | None, status: str = "approved", organization=acme):
            return request_model.objects.create(
                requested_organization=organization,
                requested_organization_code=str(organization.pk),
                requested_organization_name=organization.name,
                membership_type=gold,
                status=status,
                decided_at=decided_at,
            )

        self.first_term = term_request(decided_at=FIRST_TERM_AT)
        self.second_term = term_request(decided_at=SECOND_TERM_AT)
        # Still pending, so it never granted a term.
        term_request(decided_at=None, status="pending")

        def log(action: str, *, at: datetime.datetime, membership_request=None, organization=acme) -> int:
            entry = log_model.objects.create(
                actor_username="committee",
                target_organization=organization,
                target_organization_code=str(organization.pk),
                target_organization_name=organization.name,
                membership_type=gold,
                membership_request=membership_request,
                requested_group_cn="",
                action=action,
                # 0111 requires an expiry on expiry_changed rows once the suite migrates to latest.
                expires_at=at,
            )
            # created_at is auto_now_add, so it can only be backdated after creation.
            log_model.objects.filter(pk=entry.pk).update(created_at=at)
            return entry.pk

        self.first_term_expiry = log("expiry_changed", at=FIRST_TERM_AT + datetime.timedelta(days=30))
        self.first_term_termination = log("terminated", at=FIRST_TERM_AT + datetime.timedelta(days=60))
        self.second_term_dues = log("dues_changed", at=SECOND_TERM_AT + datetime.timedelta(days=1))
        self.second_term_expiry = log("expiry_changed", at=SECOND_TERM_AT + datetime.timedelta(days=30))
        self.before_any_term = log("expiry_changed", at=FIRST_TERM_AT - datetime.timedelta(days=1))
        self.already_linked = log(
            "expiry_changed",
            at=SECOND_TERM_AT + datetime.timedelta(days=40),
            membership_request=self.first_term,
        )

        # A sponsor deleted since: the log and its request keep only the org code.
        self.deleted_org_term = term_request(decided_at=FIRST_TERM_AT, organization=gone)
        self.deleted_org_termination = log("terminated", at=SECOND_TERM_AT, organization=gone)
        gone.delete()

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.log_model = executor.loader.project_state(self.migrate_to).apps.get_model("core", "MembershipLog")

    def _request_id(self, log_pk: int) -> int | None:
        return self.log_model.objects.get(pk=log_pk).membership_request_id

    def test_lifecycle_rows_are_linked_to_the_term_in_force_when_they_happened(self) -> None:
        self.assertEqual(self._request_id(self.first_term_expiry), self.first_term.pk)
        self.assertEqual(self._request_id(self.first_term_termination), self.first_term.pk)
        self.assertEqual(self._request_id(self.second_term_dues), self.second_term.pk)
        self.assertEqual(self._request_id(self.second_term_expiry), self.second_term.pk)
        self.assertEqual(self._request_id(self.deleted_org_termination), self.deleted_org_term.pk)

    def test_rows_without_a_matching_term_or_already_linked_are_left_alone(self) -> None:
        self.assertIsNone(self._request_id(self.before_any_term))
        self.assertEqual(self._request_id(self.already_linked), self.first_term.pk)

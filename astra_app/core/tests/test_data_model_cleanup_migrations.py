import datetime
from typing import override

from django.apps.registry import Apps
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from core.tests.utils_test_data import migrate_to_latest

NOW = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)


def _migrate(target: list[tuple[str, str]]) -> Apps:
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    return executor.loader.project_state(target).apps


def _gold_membership_type(apps: Apps) -> object:
    category = apps.get_model("core", "MembershipTypeCategory").objects.get_or_create(
        name="sponsorship", defaults={"is_organization": True},
    )[0]
    return apps.get_model("core", "MembershipType").objects.update_or_create(
        code="gold", defaults={"name": "Gold Sponsor Member", "category": category, "group_cn": "sponsor-gold"},
    )[0]


class DropDerivedColumnsMigrationTests(TransactionTestCase):
    before = [("core", "0108_deleted_organization_snapshot")]
    after = [("core", "0109_drop_derived_columns")]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(migrate_to_latest)
        apps = _migrate(self.before)
        membership_type = _gold_membership_type(apps)

        organization_model = apps.get_model("core", "Organization")
        self.unclaimed = organization_model.objects.create(name="Unclaimed", status="unclaimed", claim_secret="s")
        self.active = organization_model.objects.create(name="Active", status="active", representative="rep")

        self.log = apps.get_model("core", "MembershipLog").objects.create(
            actor_username="alice",
            target_username="alice",
            membership_type=membership_type,
            requested_group_cn="sponsor-gold",
            action="terminated",
        )
        apps.get_model("core", "MembershipTerminationFeedback").objects.create(
            membership_log=self.log,
            username="alice",
            membership_type=membership_type,
            reason_category="other",
        )

        invitation = apps.get_model("core", "AccountInvitation").objects.create(
            email="invitee@example.com",
            invitation_token="token",
            invited_by_username="committee",
            send_count=2,
            last_sent_at=NOW,
        )
        send_model = apps.get_model("core", "AccountInvitationSend")
        for sent_at, result in ((NOW - datetime.timedelta(days=1), "queued"), (NOW, "queued"), (NOW, "failed")):
            send_model.objects.create(
                invitation=invitation, sent_by_username="committee", sent_at=sent_at, template_name="t", result=result,
            )
        self.invitation_pk = invitation.pk

        election = apps.get_model("core", "Election").objects.create(
            name="Board", start_datetime=NOW, end_datetime=NOW + datetime.timedelta(days=1),
        )
        ballot_model = apps.get_model("core", "Ballot")
        ballot_fields = {"election": election, "credential_public_id": "cred", "previous_chain_hash": "0", "chain_hash": "0"}
        first = ballot_model.objects.create(ballot_hash="first", is_counted=True, **ballot_fields)
        # Mirror submit_ballot(): the deferred supersession trigger checks the final state at commit.
        with transaction.atomic():
            second = ballot_model.objects.create(ballot_hash="second", superseded_by=first, is_counted=False, **ballot_fields)
            ballot_model.objects.filter(pk=first.pk).update(superseded_by=second, is_counted=False)
            ballot_model.objects.filter(pk=second.pk).update(superseded_by=None, is_counted=True)
        self.ballot_pks = (first.pk, second.pk)

        _migrate(self.after)

    def test_reverse_migration_rebuilds_every_dropped_column(self) -> None:
        apps = _migrate(self.before)

        organization_model = apps.get_model("core", "Organization")
        self.assertEqual(organization_model.objects.get(pk=self.unclaimed.pk).status, "unclaimed")
        self.assertEqual(organization_model.objects.get(pk=self.active.pk).status, "active")

        feedback = apps.get_model("core", "MembershipTerminationFeedback").objects.get(membership_log_id=self.log.pk)
        self.assertEqual((feedback.username, feedback.membership_type_id), ("alice", "gold"))

        self.assertEqual(
            apps.get_model("core", "MembershipLog").objects.get(pk=self.log.pk).requested_group_cn, "sponsor-gold",
        )

        invitation = apps.get_model("core", "AccountInvitation").objects.get(pk=self.invitation_pk)
        self.assertEqual((invitation.send_count, invitation.last_sent_at), (2, NOW))

        ballot_model = apps.get_model("core", "Ballot")
        counted = dict(ballot_model.objects.filter(pk__in=self.ballot_pks).values_list("pk", "is_counted"))
        self.assertEqual(counted, {self.ballot_pks[0]: False, self.ballot_pks[1]: True})


class AuditLogOrganizationSnapshotMigrationTests(TransactionTestCase):
    before = [("core", "0109_drop_derived_columns")]
    after = [("core", "0110_audit_log_organization_snapshot")]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(migrate_to_latest)
        apps = _migrate(self.before)
        organization = apps.get_model("core", "Organization").objects.create(name="Acme", representative="rep")
        election = apps.get_model("core", "Election").objects.create(
            name="Board", start_datetime=NOW, end_datetime=NOW + datetime.timedelta(days=1),
        )
        entry_model = apps.get_model("core", "AuditLogEntry")
        self.org_entry = entry_model.objects.create(
            organization=organization,
            event_type="organization_representative_changed",
            payload={"old": "a", "new": "b", "actor": "reviewer"},
        )
        self.election_entry = entry_model.objects.create(
            election=election, event_type="election_started", payload={"actor": "reviewer"},
        )
        _migrate(self.after)

    def test_only_organization_entries_move_to_actor_username(self) -> None:
        entry_model = _migrate(self.after).get_model("core", "AuditLogEntry")
        self.assertEqual(
            entry_model.objects.get(pk=self.org_entry.pk).payload,
            {"old": "a", "new": "b", "actor_username": "reviewer"},
        )
        self.assertEqual(entry_model.objects.get(pk=self.election_entry.pk).payload, {"actor": "reviewer"})

    def test_reverse_migration_restores_actor(self) -> None:
        entry_model = _migrate(self.before).get_model("core", "AuditLogEntry")
        self.assertEqual(entry_model.objects.get(pk=self.org_entry.pk).payload["actor"], "reviewer")


class MembershipExpiresAtRequiredMigrationTests(TransactionTestCase):
    before = [("core", "0110_audit_log_organization_snapshot")]
    after = [("core", "0111_membership_expires_at_required")]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(migrate_to_latest)
        apps = _migrate(self.before)
        self.membership_model = apps.get_model("core", "Membership")
        self.membership = self.membership_model.objects.create(
            target_username="alice", membership_type=_gold_membership_type(apps),
        )

    def test_migration_refuses_to_invent_expiry_dates(self) -> None:
        with self.assertRaisesMessage(RuntimeError, "without expires_at"):
            _migrate(self.after)

        self.membership_model.objects.filter(pk=self.membership.pk).update(expires_at=timezone.now())
        _migrate(self.after)


class OrganizationAuditEntriesMoveMigrationTests(TransactionTestCase):
    before = [("core", "0116_membershiplog_representative_change")]
    after = [("core", "0117_move_organization_audit_entries")]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(migrate_to_latest)
        apps = _migrate(self.before)
        gold = _gold_membership_type(apps)
        organization_model = apps.get_model("core", "Organization")
        request_model = apps.get_model("core", "MembershipRequest")
        log_model = apps.get_model("core", "MembershipLog")
        entry_model = apps.get_model("core", "AuditLogEntry")

        def backdate(model: type, obj: object, field: str, when: datetime.datetime) -> None:
            model.objects.filter(pk=obj.pk).update(**{field: when})

        self.acme = organization_model.objects.create(name="Acme", representative="bob")
        self.term_request = request_model.objects.create(
            requested_organization=self.acme, membership_type=gold, status="approved", decided_at=NOW - datetime.timedelta(days=30),
        )
        approval = log_model.objects.create(
            actor_username="committee", target_organization=self.acme, membership_type=gold,
            membership_request=self.term_request, action="approved", expires_at=NOW + datetime.timedelta(days=300),
        )
        backdate(log_model, approval, "created_at", NOW - datetime.timedelta(days=30))

        invoice = entry_model.objects.create(
            organization=self.acme,
            event_type="sponsorship_invoice_generated",
            payload={
                "invoice_number": "0001-2026-123456", "term": "current", "membership_type": "gold",
                "amount_due_usd": 20000, "regenerated": True, "actor_username": "rep1",
            },
        )
        backdate(entry_model, invoice, "timestamp", NOW)
        gone_invoice = entry_model.objects.create(
            deleted_organization_id=999,
            deleted_organization_name="Gone",
            event_type="sponsorship_invoice_generated",
            payload={
                "invoice_number": "0999-2026-654321", "term": "renewal", "membership_type": "gold",
                "amount_due_usd": 20000, "regenerated": False, "actor_username": "-",
            },
        )
        backdate(entry_model, gone_invoice, "timestamp", NOW)

        # The edit view used to log open requests itself, without old/new, just before auditing.
        changed_at = NOW + datetime.timedelta(days=1)
        self.open_request = request_model.objects.create(requested_organization=self.acme, membership_type=gold, status="pending")
        open_log = log_model.objects.create(
            actor_username="committee", target_organization=self.acme, membership_type=gold,
            membership_request=self.open_request, action="representative_changed",
        )
        backdate(log_model, open_log, "created_at", changed_at)
        rep_change = entry_model.objects.create(
            organization=self.acme,
            event_type="organization_representative_changed",
            payload={"old": "carol", "new": "bob", "actor_username": "committee"},
        )
        backdate(entry_model, rep_change, "timestamp", changed_at + datetime.timedelta(milliseconds=5))

        bare = organization_model.objects.create(name="Bare", representative="dave")
        entry_model.objects.create(
            organization=bare,
            event_type="organization_representative_changed",
            payload={"old": "erin", "new": "dave", "actor_username": "committee"},
        )
        entry_model.objects.create(
            organization=self.acme,
            event_type="organization_edited",
            payload={"changed_fields": ["name"], "actor_username": "bob"},
        )
        election = apps.get_model("core", "Election").objects.create(
            name="Board", start_datetime=NOW, end_datetime=NOW + datetime.timedelta(days=1),
        )
        self.election_entry = entry_model.objects.create(election=election, event_type="election_started", payload={})

        self.apps = _migrate(self.after)

    def test_invoices_and_representative_changes_move_to_the_membership_log(self) -> None:
        log_model = self.apps.get_model("core", "MembershipLog")

        self.assertCountEqual(
            log_model.objects.filter(action__in=["invoice_generated", "invoice_regenerated"]).values_list(
                "action", "target_organization_id", "deleted_organization_id", "deleted_organization_name",
                "membership_type_id", "membership_request_id", "actor_username", "invoice_number", "created_at",
            ),
            [
                ("invoice_regenerated", self.acme.pk, None, "", "gold", self.term_request.pk, "rep1", "0001-2026-123456", NOW),
                ("invoice_generated", None, 999, "Gone", "gold", None, "-", "0999-2026-654321", NOW),
            ],
        )
        # The open request's row gains old/new; the active term gets the row today's code would write.
        self.assertCountEqual(
            log_model.objects.filter(action="representative_changed").values_list(
                "target_organization_id", "membership_request_id", "actor_username", "old_representative", "new_representative",
            ),
            [
                (self.acme.pk, self.open_request.pk, "committee", "carol", "bob"),
                (self.acme.pk, self.term_request.pk, "committee", "carol", "bob"),
            ],
        )

    def test_only_election_entries_remain(self) -> None:
        entry_model = self.apps.get_model("core", "AuditLogEntry")
        self.assertEqual(list(entry_model.objects.values_list("pk", flat=True)), [self.election_entry.pk])

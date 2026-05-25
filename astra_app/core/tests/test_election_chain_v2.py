import datetime
import hashlib
import json
import queue
import threading
import uuid

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.election_chain import (
    build_config_manifest,
    canonical_config_manifest_bytes,
    config_manifest_sha256,
    load_locked_manifest_source_rows,
)
from core.elections_services import build_public_ballots_export, submit_ballot
from core.models import (
    Ballot,
    Candidate,
    Election,
    ExclusionGroup,
    ExclusionGroupCandidate,
    VotingCredential,
)
from core.tokens import election_chain_anchor_hash
from core.views_elections import edit as election_edit_view


class ElectionChainV2Tests(TestCase):
    def test_build_config_manifest_matches_pinned_contract(self) -> None:
        start_datetime = datetime.datetime(2026, 5, 1, 12, 30, tzinfo=datetime.UTC)
        election = Election.objects.create(
            name="Chain v2 election",
            description="Immutable config test",
            url="https://example.com/elections/chain-v2",
            start_datetime=start_datetime,
            end_datetime=start_datetime + datetime.timedelta(days=1),
            number_of_seats=2,
            quorum=25,
            eligible_group_cn="council",
            status=Election.Status.draft,
            chain_version=2,
        )
        candidate_b = Candidate.objects.create(
            election=election,
            freeipa_username="bravo",
            nominated_by="nominator-b",
            description="Bravo candidate",
            url="https://example.com/candidates/bravo",
            tiebreak_uuid=uuid.UUID("00000000-0000-0000-0000-0000000000b2"),
        )
        candidate_a = Candidate.objects.create(
            election=election,
            freeipa_username="alpha",
            nominated_by="nominator-a",
            description="Alpha candidate",
            url="https://example.com/candidates/alpha",
            tiebreak_uuid=uuid.UUID("00000000-0000-0000-0000-0000000000a1"),
        )
        group = ExclusionGroup.objects.create(
            election=election,
            name="Employees",
            max_elected=1,
            public_id=uuid.UUID("10000000-0000-0000-0000-000000000001"),
        )
        ExclusionGroupCandidate.objects.create(exclusion_group=group, candidate=candidate_b)
        ExclusionGroupCandidate.objects.create(exclusion_group=group, candidate=candidate_a)

        manifest = build_config_manifest(election=election)
        self.maxDiff = None
        expected_manifest = {
            "version": 1,
            "election": {
                "id": election.id,
                "name": "Chain v2 election",
                "start_datetime": "2026-05-01T12:30:00Z",
                "number_of_seats": 2,
                "quorum": 25,
                "eligible_group_cn": "council",
            },
            "tally_rule": {
                "algorithm": "Meek STV (High-Precision Variant)",
                "algorithm_version": "1.0",
                "spec_identity": "docs/runbooks/meek-stv-elections.md",
                "epsilon": "1E-28",
                "max_iterations": 200,
            },
            "candidates": [
                {
                    "id": candidate_b.id,
                    "freeipa_username": "bravo",
                    "nominated_by": "nominator-b",
                    "tiebreak_uuid": "00000000-0000-0000-0000-0000000000b2",
                },
                {
                    "id": candidate_a.id,
                    "freeipa_username": "alpha",
                    "nominated_by": "nominator-a",
                    "tiebreak_uuid": "00000000-0000-0000-0000-0000000000a1",
                },
            ],
            "exclusion_groups": [
                {
                    "public_id": "10000000-0000-0000-0000-000000000001",
                    "name": "Employees",
                    "max_elected": 1,
                    "candidate_ids": [candidate_b.id, candidate_a.id],
                }
            ],
        }
        self.assertEqual(manifest, expected_manifest)

        expected_bytes = json.dumps(expected_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected_digest = hashlib.sha256(expected_bytes).hexdigest()

        self.assertEqual(canonical_config_manifest_bytes(manifest), expected_bytes)
        self.assertEqual(config_manifest_sha256(manifest), expected_digest)
        self.assertEqual(
            election_chain_anchor_hash(election_id=election.id, config_manifest_sha256=expected_digest),
            hashlib.sha256(
                (
                    f"election-v2:{election.id}:{expected_digest}. "
                    "alex estuvo aquí, dejándose el alma."
                ).encode()
            ).hexdigest(),
        )

    def test_build_public_ballots_export_uses_v2_root_and_stored_manifest_rankings(self) -> None:
        start_datetime = timezone.now() - datetime.timedelta(days=2)
        election = Election.objects.create(
            name="Artifact v2 election",
            description="",
            url="https://example.com/elections/artifact-v2",
            start_datetime=start_datetime,
            end_datetime=start_datetime + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=10,
            eligible_group_cn="voters",
            status=Election.Status.draft,
            chain_version=2,
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
            tiebreak_uuid=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        )
        manifest = build_config_manifest(election=election)
        manifest_digest = config_manifest_sha256(manifest)
        anchor_hash = election_chain_anchor_hash(
            election_id=election.id,
            config_manifest_sha256=manifest_digest,
        )
        Election.objects.filter(pk=election.pk).update(
            status=Election.Status.closed,
            config_manifest_version=1,
            config_manifest=manifest,
            config_manifest_sha256=manifest_digest,
            chain_anchor_hash=anchor_hash,
        )
        election.refresh_from_db()
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[candidate.id],
            weight=1,
            nonce="0" * 32,
        )
        chain_hash = hashlib.sha256(f"{anchor_hash}:{ballot_hash}".encode()).hexdigest()
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[candidate.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=anchor_hash,
            chain_hash=chain_hash,
            is_counted=True,
        )
        Candidate.objects.filter(pk=candidate.pk).update(freeipa_username="mallory")

        payload = build_public_ballots_export(election=election)

        self.assertEqual(payload["chain_version"], 2)
        self.assertEqual(payload["chain_root_kind"], "config_anchor_v2")
        self.assertNotIn("chain_root_hash", payload)
        self.assertNotIn("chain_anchor_hash", payload)
        self.assertEqual(payload["genesis_hash"], anchor_hash)
        self.assertEqual(payload["config_manifest_version"], 1)
        self.assertEqual(payload["config_manifest_sha256"], manifest_digest)
        self.assertEqual(payload["chain_head"], chain_hash)
        self.assertEqual(payload["ballots"][0]["ranking"], ["alice"])

    def test_submit_ballot_uses_chain_anchor_for_first_v2_ballot(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Open v2 election",
            description="",
            url="",
            start_datetime=now - datetime.timedelta(hours=1),
            end_datetime=now + datetime.timedelta(hours=1),
            number_of_seats=1,
            quorum=10,
            eligible_group_cn="",
            status=Election.Status.draft,
            chain_version=2,
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        manifest = build_config_manifest(election=election)
        manifest_digest = config_manifest_sha256(manifest)
        anchor_hash = election_chain_anchor_hash(
            election_id=election.id,
            config_manifest_sha256=manifest_digest,
        )
        Election.objects.filter(pk=election.pk).update(
            status=Election.Status.open,
            config_manifest_version=1,
            config_manifest=manifest,
            config_manifest_sha256=manifest_digest,
            chain_anchor_hash=anchor_hash,
        )
        election.refresh_from_db()
        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="alice",
            weight=1,
        )

        receipt = submit_ballot(election=election, credential_public_id="cred-1", ranking=[candidate.id])

        self.assertEqual(receipt.ballot.previous_chain_hash, anchor_hash)

    def test_build_public_ballots_export_uses_anchor_as_chain_head_when_v2_has_no_ballots(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Empty v2 election",
            description="",
            url="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now,
            number_of_seats=1,
            quorum=0,
            eligible_group_cn="",
            status=Election.Status.draft,
            chain_version=2,
        )
        manifest = build_config_manifest(election=election)
        manifest_digest = config_manifest_sha256(manifest)
        anchor_hash = election_chain_anchor_hash(
            election_id=election.id,
            config_manifest_sha256=manifest_digest,
        )
        Election.objects.filter(pk=election.pk).update(
            status=Election.Status.closed,
            config_manifest_version=1,
            config_manifest=manifest,
            config_manifest_sha256=manifest_digest,
            chain_anchor_hash=anchor_hash,
        )
        election.refresh_from_db()

        payload = build_public_ballots_export(election=election)

        self.assertEqual(payload["chain_head"], anchor_hash)
        self.assertNotIn("chain_root_hash", payload)
        self.assertNotIn("chain_anchor_hash", payload)
        self.assertEqual(payload["genesis_hash"], anchor_hash)

    def test_load_locked_manifest_source_rows_locks_candidate_and_exclusion_tables(self) -> None:
        start_datetime = timezone.now() - datetime.timedelta(days=2)
        election = Election.objects.create(
            name="Locked manifest source election",
            description="",
            url="",
            start_datetime=start_datetime,
            end_datetime=start_datetime + datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=1,
            eligible_group_cn="voters",
            status=Election.Status.draft,
            chain_version=2,
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        group = ExclusionGroup.objects.create(election=election, name="Employees", max_elected=1)
        ExclusionGroupCandidate.objects.create(exclusion_group=group, candidate=candidate)

        with transaction.atomic():
            with CaptureQueriesContext(connection) as ctx:
                candidate_rows, groups, memberships = load_locked_manifest_source_rows(election=election)

        self.assertEqual(len(candidate_rows), 1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(memberships), 1)

        sql_statements = [query["sql"] for query in ctx.captured_queries if "FOR UPDATE" in query["sql"]]
        self.assertTrue(any("core_candidate" in sql for sql in sql_statements), sql_statements)
        self.assertTrue(any("core_exclusiongroup" in sql for sql in sql_statements), sql_statements)
        self.assertTrue(any("core_exclusiongroupcandidate" in sql for sql in sql_statements), sql_statements)

    def test_save_candidates_and_groups_rejects_stale_started_v2_membership_reset(self) -> None:
        election = Election.objects.create(
            name="Stale draft election",
            description="",
            url="https://example.com/elections/stale-draft",
            start_datetime=timezone.now() + datetime.timedelta(days=1),
            end_datetime=timezone.now() + datetime.timedelta(days=2),
            number_of_seats=1,
            quorum=1,
            eligible_group_cn="voters",
            status=Election.Status.draft,
            chain_version=2,
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        group = ExclusionGroup.objects.create(
            election=election,
            name="Employees",
            max_elected=1,
        )
        membership = ExclusionGroupCandidate.objects.create(exclusion_group=group, candidate=candidate)

        stale_election = Election.objects.get(pk=election.pk)
        stale_group = ExclusionGroup.objects.get(pk=group.pk)

        Election.objects.filter(pk=election.pk).update(status=Election.Status.open)

        class _FakeForm:
            def __init__(self, *, instance, cleaned_data: dict[str, object]) -> None:
                self.instance = instance
                self.cleaned_data = cleaned_data

            def save(self, *, commit: bool = False):
                del commit
                return self.instance

        class _FakeFormSet:
            def __init__(self, forms: list[object]) -> None:
                self.forms = forms

        with self.assertRaises(ValidationError):
            election_edit_view._save_candidates_and_groups(
                stale_election,
                _FakeFormSet([]),
                _FakeFormSet(
                    [
                        _FakeForm(
                            instance=stale_group,
                            cleaned_data={
                                "DELETE": False,
                                "name": stale_group.name,
                                "candidate_usernames": [],
                            },
                        )
                    ]
                ),
            )

        self.assertTrue(ExclusionGroupCandidate.objects.filter(pk=membership.pk).exists())


class ElectionChainV2TransactionTests(TransactionTestCase):
    def test_candidate_insert_waits_for_start_lock_and_rechecks_open_status(self) -> None:
        if not connection.features.has_select_for_update:
            self.skipTest("database does not support select_for_update")

        election = Election.objects.create(
            name="Concurrent start election",
            description="",
            url="https://example.com/elections/concurrent-start",
            start_datetime=timezone.now() + datetime.timedelta(days=1),
            end_datetime=timezone.now() + datetime.timedelta(days=2),
            number_of_seats=1,
            quorum=1,
            eligible_group_cn="voters",
            status=Election.Status.draft,
            chain_version=2,
        )

        started = threading.Event()
        finished = threading.Event()
        errors: queue.Queue[BaseException] = queue.Queue()

        def run() -> None:
            close_old_connections()
            started.set()
            try:
                Candidate.objects.create(
                    election_id=election.id,
                    freeipa_username="alice",
                    nominated_by="nominator",
                )
            except BaseException as exc:  # pragma: no cover - surfaced by assertions below
                errors.put(exc)
            finally:
                finished.set()
                close_old_connections()

        with transaction.atomic():
            locked = Election.objects.select_for_update().get(pk=election.pk)

            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(started.wait(timeout=1), "worker thread did not start")
            self.assertFalse(
                finished.wait(timeout=0.2),
                "candidate insert completed before the start-transition election lock was released",
            )

            locked.status = Election.Status.open
            locked.save(update_fields=["status", "updated_at"])

        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "worker thread did not finish")
        self.assertFalse(Candidate.objects.filter(election=election).exists())
        self.assertFalse(errors.empty(), "candidate insert unexpectedly succeeded after election opened")
        self.assertIsInstance(errors.get(), ValidationError)
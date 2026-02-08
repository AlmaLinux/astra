
import datetime

from django.test import TestCase
from django.utils import timezone

from core.elections_services import close_election, submit_ballot
from core.models import AuditLogEntry, Ballot, Candidate, Election, VotingCredential
from core.tests.ballot_chain import compute_chain_hash
from core.tokens import election_genesis_chain_hash


def _export_ballots_for_audit(*, election: Election) -> list[dict[str, object]]:
    """Return ballot rows in the same order/shape as the public ballots export.

    We intentionally unit-test tamper evidence by operating on this export-like
    representation: it's what independent auditors can actually download.
    """

    rows = list(
        Ballot.objects.filter(election=election)
        .order_by("created_at", "id")
        .values(
            "ranking",
            "weight",
            "ballot_hash",
            "is_counted",
            "chain_hash",
            "previous_chain_hash",
            "superseded_by__ballot_hash",
        )
    )
    for row in rows:
        row["superseded_by"] = row.pop("superseded_by__ballot_hash")
    return rows


def _published_chain_head(*, election: Election) -> str:
    entry = AuditLogEntry.objects.filter(election=election, event_type="election_closed", is_public=True).first()
    assert entry is not None
    assert isinstance(entry.payload, dict)
    chain_head = str(entry.payload.get("chain_head") or "")
    assert len(chain_head) == 64
    return chain_head


def _verify_chain(
    *,
    ballots: list[dict[str, object]],
    published_chain_head: str | None,
    genesis_hash: str,
) -> tuple[int | None, str]:
    """Verify the per-election commitment chain.

    Returns a tuple: (mismatch_index_1_based | None, computed_chain_head).
    """

    prev = genesis_hash
    computed_chain_head = genesis_hash

    for idx, row in enumerate(ballots, start=1):
        ballot_hash = str(row.get("ballot_hash") or "")
        previous_chain_hash = str(row.get("previous_chain_hash") or "")
        chain_hash = str(row.get("chain_hash") or "")

        if previous_chain_hash != prev:
            return idx, computed_chain_head

        expected_chain_hash = compute_chain_hash(previous_chain_hash=prev, ballot_hash=ballot_hash)
        if chain_hash != expected_chain_hash:
            return idx, computed_chain_head

        prev = chain_hash
        computed_chain_head = chain_hash

    if published_chain_head is not None and computed_chain_head != published_chain_head:
        # Anchor mismatch: the chain is internally consistent, but it does not
        # match the published chain head.
        return len(ballots) or 1, computed_chain_head

    return None, computed_chain_head


def _rewrite_chain_fields(*, ballots: list[dict[str, object]], genesis_hash: str) -> list[dict[str, object]]:
    """Simulate an attacker rewriting chain fields pre-publication.

    This represents a malicious actor with direct DB access (bypassing triggers)
    who rewrites history before any chain head is published.
    """

    rewritten: list[dict[str, object]] = []
    prev = genesis_hash
    for row in ballots:
        ballot_hash = str(row.get("ballot_hash") or "")
        chain_hash = compute_chain_hash(previous_chain_hash=prev, ballot_hash=ballot_hash)
        new_row = dict(row)
        new_row["previous_chain_hash"] = prev
        new_row["chain_hash"] = chain_hash
        rewritten.append(new_row)
        prev = chain_hash
    return rewritten


class MeekVotingAttackTests(TestCase):
    def _create_open_election(self) -> Election:
        now = timezone.now()
        return Election.objects.create(
            name="Attack test election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=2,
            status=Election.Status.open,
        )

    def _create_candidates(self, *, election: Election) -> tuple[Candidate, Candidate, Candidate]:
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="n")
        c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="n")
        c3 = Candidate.objects.create(election=election, freeipa_username="carol", nominated_by="n")
        return c1, c2, c3

    def _create_credential(self, *, election: Election, public_id: str, username: str) -> VotingCredential:
        return VotingCredential.objects.create(
            election=election,
            public_id=public_id,
            freeipa_username=username,
            weight=1,
        )

    def test_attack_1_silent_ballot_deletion_after_publication_detected_by_chain_mismatch(self) -> None:
        election = self._create_open_election()
        c1, c2, _c3 = self._create_candidates(election=election)

        cred_a = self._create_credential(election=election, public_id="cred-a", username="voter-a")
        cred_b = self._create_credential(election=election, public_id="cred-b", username="voter-b")
        cred_c = self._create_credential(election=election, public_id="cred-c", username="voter-c")

        submit_ballot(election=election, credential_public_id=cred_a.public_id, ranking=[c1.id])
        submit_ballot(election=election, credential_public_id=cred_b.public_id, ranking=[c2.id])
        submit_ballot(election=election, credential_public_id=cred_c.public_id, ranking=[c1.id, c2.id])

        close_election(election=election)
        published_head = _published_chain_head(election=election)

        ballots = _export_ballots_for_audit(election=election)
        mismatch_idx, computed_head = _verify_chain(
            ballots=ballots,
            published_chain_head=published_head,
            genesis_hash=election_genesis_chain_hash(election.id),
        )
        self.assertIsNone(mismatch_idx)
        self.assertEqual(computed_head, published_head)

        # Malicious admin deletes ballot B after the chain head is published.
        tampered = list(ballots)
        tampered.pop(1)  # index 2 (1-based) in the original sequence

        mismatch_idx2, computed_head2 = _verify_chain(
            ballots=tampered,
            published_chain_head=published_head,
            genesis_hash=election_genesis_chain_hash(election.id),
        )
        self.assertEqual(mismatch_idx2, 2)
        self.assertNotEqual(computed_head2, published_head)

    def test_attack_2_ballot_modification_detectable_via_receipt_nonce_against_public_export(self) -> None:
        """A ballot content change can be detected by a voter who saved nonce + receipt.

        Note: the commitment chain commits to the receipt code (ballot_hash), not the
        human-readable ranking payload. A malicious rewrite of ranking alone will not
        change chain hashes, but it will fail voter verification against nonce.
        """

        election = self._create_open_election()
        c1, c2, _c3 = self._create_candidates(election=election)

        cred = self._create_credential(election=election, public_id="cred-1", username="voter")
        submitted_ranking = [c1.id, c2.id]
        receipt = submit_ballot(election=election, credential_public_id=cred.public_id, ranking=submitted_ranking)
        close_election(election=election)
        published_head = _published_chain_head(election=election)

        ballots = _export_ballots_for_audit(election=election)
        self.assertEqual(len(ballots), 1)
        row = ballots[0]
        self.assertEqual(str(row.get("ballot_hash")), receipt.ballot.ballot_hash)

        # Malicious admin changes the ranking in the published export but leaves the receipt.
        tampered_ranking = [c2.id, c1.id]
        tampered = [dict(row, ranking=tampered_ranking)]

        mismatch_idx, computed_head = _verify_chain(
            ballots=tampered,
            published_chain_head=published_head,
            genesis_hash=election_genesis_chain_hash(election.id),
        )
        self.assertIsNone(mismatch_idx)
        self.assertEqual(computed_head, published_head)

        # Voter verification (receipt + nonce) fails against the tampered ballot content.
        recomputed_from_tampered = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id=cred.public_id,
            ranking=tampered_ranking,
            weight=1,
            nonce=receipt.nonce,
        )
        self.assertNotEqual(recomputed_from_tampered, receipt.ballot.ballot_hash)
        self.assertNotEqual(tampered_ranking, submitted_ranking)

    def test_attack_3_reordering_ballots_breaks_chain(self) -> None:
        election = self._create_open_election()
        c1, c2, _c3 = self._create_candidates(election=election)

        cred_a = self._create_credential(election=election, public_id="cred-a", username="voter-a")
        cred_b = self._create_credential(election=election, public_id="cred-b", username="voter-b")

        submit_ballot(election=election, credential_public_id=cred_a.public_id, ranking=[c1.id])
        submit_ballot(election=election, credential_public_id=cred_b.public_id, ranking=[c2.id])

        close_election(election=election)
        published_head = _published_chain_head(election=election)

        ballots = _export_ballots_for_audit(election=election)
        self.assertEqual(len(ballots), 2)

        tampered = [ballots[1], ballots[0]]
        mismatch_idx, _computed_head = _verify_chain(
            ballots=tampered,
            published_chain_head=published_head,
            genesis_hash=election_genesis_chain_hash(election.id),
        )
        self.assertEqual(mismatch_idx, 1)

    def test_attack_4_superseded_ballot_removal_detected_and_receipt_missing(self) -> None:
        election = self._create_open_election()
        c1, c2, _c3 = self._create_candidates(election=election)

        cred = self._create_credential(election=election, public_id="cred-1", username="voter")
        cred_other = self._create_credential(election=election, public_id="cred-2", username="voter2")

        receipt1 = submit_ballot(election=election, credential_public_id=cred.public_id, ranking=[c1.id])
        receipt2 = submit_ballot(election=election, credential_public_id=cred.public_id, ranking=[c2.id])
        submit_ballot(election=election, credential_public_id=cred_other.public_id, ranking=[c1.id, c2.id])

        self.assertNotEqual(receipt1.ballot.ballot_hash, receipt2.ballot.ballot_hash)
        close_election(election=election)
        published_head = _published_chain_head(election=election)

        ballots = _export_ballots_for_audit(election=election)
        self.assertEqual(len(ballots), 3)

        superseded_hash = receipt1.ballot.ballot_hash
        tampered = [row for row in ballots if str(row.get("ballot_hash")) != superseded_hash]
        self.assertEqual(len(tampered), 2)

        mismatch_idx, _computed_head = _verify_chain(
            ballots=tampered,
            published_chain_head=published_head,
            genesis_hash=election_genesis_chain_hash(election.id),
        )
        self.assertIsNotNone(mismatch_idx)

        # Auditor detection: a ballot submission audit event exists for the receipt,
        # but the published ballot export is missing that receipt.
        audit_hashes = {
            str(payload.get("ballot_hash") or "")
            for payload in AuditLogEntry.objects.filter(election=election, event_type="ballot_submitted")
            .values_list("payload", flat=True)
            if isinstance(payload, dict)
        }
        export_hashes = {str(row.get("ballot_hash") or "") for row in ballots}
        tampered_hashes = {str(row.get("ballot_hash") or "") for row in tampered}
        self.assertIn(superseded_hash, audit_hashes)
        self.assertNotIn(superseded_hash, tampered_hashes)
        self.assertEqual(export_hashes - tampered_hashes, {superseded_hash})

    def test_attack_5_receipt_collision_attempt_same_payload_twice_produces_distinct_receipts(self) -> None:
        election = self._create_open_election()
        c1, c2, _c3 = self._create_candidates(election=election)

        cred = self._create_credential(election=election, public_id="cred-1", username="voter")
        ranking = [c1.id, c2.id]

        receipt1 = submit_ballot(election=election, credential_public_id=cred.public_id, ranking=ranking)
        receipt2 = submit_ballot(election=election, credential_public_id=cred.public_id, ranking=ranking)

        self.assertNotEqual(receipt1.ballot.ballot_hash, receipt2.ballot.ballot_hash)
        self.assertEqual(Ballot.objects.filter(election=election).count(), 2)

        b1 = Ballot.objects.get(pk=receipt1.ballot.pk)
        b2 = Ballot.objects.get(pk=receipt2.ballot.pk)
        b1.refresh_from_db(fields=["superseded_by_id", "is_counted"])
        b2.refresh_from_db(fields=["superseded_by_id", "is_counted"])

        self.assertEqual(b1.superseded_by_id, b2.id)
        self.assertFalse(b1.is_counted)
        self.assertIsNone(b2.superseded_by_id)
        self.assertTrue(b2.is_counted)

    def test_attack_6_pre_publication_chain_rewrite_is_not_detectable_without_anchor(self) -> None:
        """Known limitation: without a published chain head, history can be rewritten.

        If an attacker can rewrite ballots and their chain hashes *before* a chain head
        is published (i.e., before election close emits the public `chain_head`), then
        an auditor has no external anchor to detect the rewrite.
        """

        election = self._create_open_election()
        c1, c2, _c3 = self._create_candidates(election=election)

        cred_a = self._create_credential(election=election, public_id="cred-a", username="voter-a")
        cred_b = self._create_credential(election=election, public_id="cred-b", username="voter-b")
        cred_c = self._create_credential(election=election, public_id="cred-c", username="voter-c")

        submit_ballot(election=election, credential_public_id=cred_a.public_id, ranking=[c1.id])
        submit_ballot(election=election, credential_public_id=cred_b.public_id, ranking=[c2.id])
        submit_ballot(election=election, credential_public_id=cred_c.public_id, ranking=[c1.id, c2.id])

        ballots = _export_ballots_for_audit(election=election)
        self.assertEqual(len(ballots), 3)

        # Attacker deletes ballot B and rewrites the remaining chain fields.
        tampered = [ballots[0], ballots[2]]
        rewritten = _rewrite_chain_fields(
            ballots=tampered,
            genesis_hash=election_genesis_chain_hash(election.id),
        )

        # Without a published anchor (chain head), internal verification passes.
        mismatch_idx, _computed_head = _verify_chain(
            ballots=rewritten,
            published_chain_head=None,
            genesis_hash=election_genesis_chain_hash(election.id),
        )
        self.assertIsNone(mismatch_idx)


import datetime
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Ballot, Candidate, Election
from core.tests.ballot_chain import compute_chain_hash
from core.tokens import election_genesis_chain_hash


class BallotVerificationPageTests(TestCase):
    def _create_ballot(
        self,
        *,
        election: Election,
        credential_public_id: str,
        ranking: list[int],
        weight: int,
        previous_chain_hash: str,
        created_at: datetime.datetime,
        nonce: str = "0" * 32,
        is_counted: bool = True,
        superseded_by: Ballot | None = None,
    ) -> Ballot:
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id=credential_public_id,
            ranking=ranking,
            weight=weight,
            nonce=nonce,
        )
        chain_hash = compute_chain_hash(previous_chain_hash=previous_chain_hash, ballot_hash=ballot_hash)

        # Ballot rows are append-only (DB trigger). To make created_at deterministic,
        # freeze django.utils.timezone.now during insert (auto_now_add).
        with patch("django.utils.timezone.now", return_value=created_at):
            return Ballot.objects.create(
                election=election,
                credential_public_id=credential_public_id,
                ranking=ranking,
                weight=weight,
                ballot_hash=ballot_hash,
                previous_chain_hash=previous_chain_hash,
                chain_hash=chain_hash,
                is_counted=is_counted,
                superseded_by=superseded_by,
            )

    def test_verify_page_renders_and_rejects_invalid_receipt_format(self) -> None:
        url = reverse("ballot-verify")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "verify")

        resp2 = self.client.get(url, data={"receipt": "not-a-hash"})
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, "Invalid receipt")

    def test_verify_page_not_found_does_not_leak_election_info(self) -> None:
        url = reverse("ballot-verify")
        unknown = "a" * 64
        resp = self.client.get(url, data={"receipt": unknown})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No ballot with this receipt")
        self.assertNotContains(resp, "Election status")
        self.assertNotContains(resp, "Election name")

    def test_verify_page_open_election_never_says_counted(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Open election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="n")

        created_at = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 34, 56))
        ballot = self._create_ballot(
            election=election,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            previous_chain_hash=election_genesis_chain_hash(election.id),
            created_at=created_at,
        )

        url = reverse("ballot-verify")
        resp = self.client.get(url, data={"receipt": ballot.ballot_hash})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Yes")
        self.assertContains(resp, election.name)
        self.assertContains(resp, "open")
        self.assertContains(resp, "2026-01-02")
        self.assertNotContains(resp, "12:34")
        self.assertNotContains(resp, "counted")
        self.assertNotContains(resp, "ranking")

    def test_verify_page_closed_election_indicates_locked_and_upcoming_tally(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Closed election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="n")

        created_at = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 34, 56))
        ballot = self._create_ballot(
            election=election,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            previous_chain_hash=election_genesis_chain_hash(election.id),
            created_at=created_at,
        )

        url = reverse("ballot-verify")
        resp = self.client.get(url, data={"receipt": ballot.ballot_hash})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "recorded and locked")
        self.assertContains(resp, "upcoming tally")
        self.assertNotContains(resp, "counted")

    def test_verify_page_tallied_election_links_to_public_exports(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Tallied election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            tally_result={"quota": "1", "elected": [], "eliminated": [], "forced_excluded": [], "rounds": []},
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="n")

        created_at = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 34, 56))
        ballot = self._create_ballot(
            election=election,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            previous_chain_hash=election_genesis_chain_hash(election.id),
            created_at=created_at,
        )

        url = reverse("ballot-verify")
        resp = self.client.get(url, data={"receipt": ballot.ballot_hash})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "included in the final")
        self.assertContains(resp, reverse("election-public-ballots", args=[election.id]))
        self.assertContains(resp, reverse("election-audit-log", args=[election.id]))

    def test_verify_page_superseded_ballot_does_not_reveal_replacement_receipt(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Superseded election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            tally_result={"quota": "1", "elected": [], "eliminated": [], "forced_excluded": [], "rounds": []},
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="n")
        c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="n")

        created_at1 = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 34, 56))
        created_at2 = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 40, 0))

        # Mirror the election submission logic:
        # - Only one ballot may be "final" (superseded_by IS NULL)
        # - Only one ballot may be counted (is_counted=True)
        # - superseded_by must point forward (higher ballot id)
        ballot1 = self._create_ballot(
            election=election,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            previous_chain_hash=election_genesis_chain_hash(election.id),
            created_at=created_at1,
            is_counted=True,
        )
        ballot1.is_counted = False
        ballot1.save(update_fields=["is_counted"])

        ballot2 = self._create_ballot(
            election=election,
            credential_public_id="cred-1",
            ranking=[c2.id],
            weight=1,
            previous_chain_hash=election_genesis_chain_hash(election.id),
            created_at=created_at2,
            is_counted=True,
            superseded_by=ballot1,
        )
        ballot1.superseded_by = ballot2
        ballot1.save(update_fields=["superseded_by"])
        ballot2.superseded_by = None
        ballot2.save(update_fields=["superseded_by"])

        url = reverse("ballot-verify")
        resp = self.client.get(url, data={"receipt": ballot1.ballot_hash})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "replaced")
        # Must not leak the replacement receipt.
        self.assertNotContains(resp, ballot2.ballot_hash)

    def test_ballot_verify_does_not_expose_credential_to_unauthenticated(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Open election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="n")
        c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="n")

        created_at = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 34, 56))
        ballot = self._create_ballot(
            election=election,
            credential_public_id="cred-1",
            ranking=[c1.id, c2.id],
            weight=1,
            previous_chain_hash=election_genesis_chain_hash(election.id),
            created_at=created_at,
        )

        url = reverse("ballot-verify")
        resp = self.client.get(url, data={"receipt": ballot.ballot_hash})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "verify-ballot-hash.py")
        self.assertContains(resp, "Copy/paste")
        self.assertContains(resp, f"election_id = {election.id}")
        self.assertNotContains(resp, "credential_public_id =")
        self.assertNotContains(resp, "cred-1")
        self.assertContains(resp, f"&quot;alice&quot;: {c1.id}")
        self.assertContains(resp, f"&quot;bob&quot;: {c2.id}")

    def test_ballot_verify_does_not_expose_credential_to_authenticated_non_owner(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Open election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="n")
        Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="n")

        created_at = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 34, 56))
        ballot = self._create_ballot(
            election=election,
            credential_public_id="cred-owner-1",
            ranking=[c1.id],
            weight=1,
            previous_chain_hash=election_genesis_chain_hash(election.id),
            created_at=created_at,
        )

        session = self.client.session
        session["_freeipa_username"] = "other-user"
        session.save()

        url = reverse("ballot-verify")
        resp = self.client.get(url, data={"receipt": ballot.ballot_hash})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "credential_public_id =")
        self.assertNotContains(resp, "cred-owner-1")

    @override_settings(
        ELECTION_RATE_LIMIT_BALLOT_VERIFY_LIMIT=1,
        ELECTION_RATE_LIMIT_BALLOT_VERIFY_WINDOW_SECONDS=60,
    )
    def test_verify_page_rate_limits_repeated_queries(self) -> None:
        cache.clear()

        url = reverse("ballot-verify")
        unknown = "a" * 64

        resp1 = self.client.get(url, data={"receipt": unknown})
        self.assertEqual(resp1.status_code, 200)

        resp2 = self.client.get(url, data={"receipt": unknown})
        self.assertEqual(resp2.status_code, 429)
        self.assertContains(resp2, "Too many", status_code=429)


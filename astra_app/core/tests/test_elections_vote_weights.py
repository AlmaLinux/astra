
import datetime
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser
from core.elections_eligibility import (
    VoteWeightLine,
    eligible_vote_weight_for_username,
    eligible_voters_from_memberships,
    vote_weight_breakdown_for_username,
)
from core.elections_services import (
    close_election,
    issue_voting_credentials_from_memberships,
    submit_ballot,
    tally_election,
)
from core.models import (
    Ballot,
    Candidate,
    Election,
    Membership,
    MembershipType,
    Organization,
    VotingCredential,
)


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionVoteWeightsTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._coc_patcher = patch("core.views_elections.vote.has_signed_coc", return_value=True)
        self._coc_patcher.start()
        self.addCleanup(self._coc_patcher.stop)
        self._coc_block_patcher = patch("core.views_elections.vote.block_action_without_coc", return_value=None)
        self._coc_block_patcher.start()
        self.addCleanup(self._coc_block_patcher.stop)

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _make_weighted_voter(self, *, election: Election, username: str) -> int:
        now = timezone.now()

        mt_a = MembershipType.objects.create(
            code="voter_a",
            name="Voter A",
            votes=2,
            category_id="individual",
            enabled=True,
        )
        mt_b = MembershipType.objects.create(
            code="voter_b",
            name="Voter B",
            votes=3,
            category_id="individual",
            enabled=True,
        )
        mt_org = MembershipType.objects.create(
            code="org_sponsor",
            name="Org sponsor",
            votes=5,
            category_id="sponsorship",
            enabled=True,
        )

        m1 = Membership.objects.create(
            target_username=username,
            membership_type=mt_a,
            expires_at=now + datetime.timedelta(days=365),
        )
        m2 = Membership.objects.create(
            target_username=username,
            membership_type=mt_b,
            expires_at=now + datetime.timedelta(days=365),
        )

        created_at = election.start_datetime - datetime.timedelta(days=10)
        Membership.objects.filter(pk=m1.pk).update(created_at=created_at)
        Membership.objects.filter(pk=m2.pk).update(created_at=created_at)

        org = Organization.objects.create(
            name="ACME",
            representative=username,
        )
        sponsorship = Membership.objects.create(
            target_organization=org,
            membership_type=mt_org,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=sponsorship.pk).update(created_at=created_at)

        return 2 + 3 + 5

    def test_eligible_voters_sum_multiple_memberships_and_org_sponsorship(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Weights election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        expected = self._make_weighted_voter(election=election, username="voter1")

        eligible = eligible_voters_from_memberships(election=election)
        eligible_by_username = {v.username: v.weight for v in eligible}
        self.assertEqual(eligible_by_username.get("voter1"), expected)

    def test_ballot_weight_and_meek_quota_use_combined_weight(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Tally weight election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="nominator")

        expected = self._make_weighted_voter(election=election, username="voter1")

        issued = issue_voting_credentials_from_memberships(election=election)
        cred = next(c for c in issued if c.freeipa_username == "voter1")
        self.assertEqual(int(cred.weight), expected)

        receipt = submit_ballot(
            election=election,
            credential_public_id=str(cred.public_id),
            ranking=[c1.id, c2.id],
        )
        ballot = receipt.ballot
        self.assertEqual(int(ballot.weight), expected)

        election.status = Election.Status.closed
        election.save(update_fields=["status"])

        result = tally_election(election=election)
        # quota is a stringified Decimal.
        # Droop quota: floor(votes / (seats + 1)) + 1. With seats=1, it is floor(votes/2) + 1.
        self.assertEqual(Decimal(str(result["quota"])), Decimal(expected // 2 + 1))

    def test_vote_submit_uses_credential_weight_even_if_memberships_removed_after_issuance(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Credential snapshot election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="nominator")

        expected = self._make_weighted_voter(election=election, username="voter1")

        issued = issue_voting_credentials_from_memberships(election=election)
        cred = next(c for c in issued if c.freeipa_username == "voter1")
        self.assertEqual(int(cred.weight), expected)

        # Memberships and sponsorship responsibilities can change while the election is open.
        # Once a credential is issued, vote submission must rely on the credential weight,
        # not re-check current memberships.
        Membership.objects.filter(target_username="voter1").delete()
        Membership.objects.filter(target_organization__isnull=False).delete()
        Organization.objects.all().delete()

        self._login_as_freeipa_user("voter1")
        voter1 = FreeIPAUser(
            "voter1",
            {
                "uid": ["voter1"],
                "givenname": ["Voter"],
                "sn": ["One"],
                "displayname": ["Voter One"],
                "memberof_group": [],
            },
        )
        with patch("core.backends.FreeIPAUser.get", return_value=voter1):
            resp = self.client.post(
                reverse("election-vote-submit", args=[election.id]),
                data=json.dumps({"credential_public_id": str(cred.public_id), "ranking": [c1.id, c2.id]}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))

        ballot = Ballot.objects.get(election=election, credential_public_id=str(cred.public_id))
        self.assertEqual(int(ballot.weight), expected)

        election.status = Election.Status.closed
        election.save(update_fields=["status"])

        result = tally_election(election=election)
        self.assertEqual(Decimal(str(result["quota"])), Decimal(expected // 2 + 1))

    def test_vote_submit_returns_nonce_and_chain_hashes(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Vote receipt details election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="nominator")

        self._make_weighted_voter(election=election, username="voter1")

        issued = issue_voting_credentials_from_memberships(election=election)
        cred = next(c for c in issued if c.freeipa_username == "voter1")

        self._login_as_freeipa_user("voter1")
        voter1 = FreeIPAUser(
            "voter1",
            {
                "uid": ["voter1"],
                "givenname": ["Voter"],
                "sn": ["One"],
                "displayname": ["Voter One"],
                "memberof_group": [],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=voter1):
            resp = self.client.post(
                reverse("election-vote-submit", args=[election.id]),
                data=json.dumps({"credential_public_id": str(cred.public_id), "ranking": [c1.id, c2.id]}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        payload = resp.json()
        self.assertTrue(payload.get("ok"))

        ballot_hash = str(payload.get("ballot_hash") or "")
        nonce = str(payload.get("nonce") or "")
        previous_chain_hash = str(payload.get("previous_chain_hash") or "")
        chain_hash = str(payload.get("chain_hash") or "")

        self.assertEqual(len(ballot_hash), 64)
        self.assertEqual(len(nonce), 32)
        self.assertEqual(len(previous_chain_hash), 64)
        self.assertEqual(len(chain_hash), 64)

    def test_tally_uses_ballot_weight_after_election_anonymized(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Anonymized credential election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        c1 = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        c2 = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="nominator")

        expected = self._make_weighted_voter(election=election, username="voter1")
        issued = issue_voting_credentials_from_memberships(election=election)
        cred = next(c for c in issued if c.freeipa_username == "voter1")

        receipt = submit_ballot(
            election=election,
            credential_public_id=str(cred.public_id),
            ranking=[c1.id, c2.id],
        )
        ballot = receipt.ballot
        self.assertEqual(int(ballot.weight), expected)

        # Closing an election anonymizes credentials. Tally must not rely on the
        # credential/user identity at this stage.
        close_election(election=election)
        cred_refreshed = VotingCredential.objects.get(election=election, public_id=str(cred.public_id))
        self.assertIsNone(cred_refreshed.freeipa_username)

        # Even if membership data is later removed, the tally should still use the
        # stored ballot weights.
        Membership.objects.all().delete()
        Organization.objects.all().delete()

        result = tally_election(election=election)
        self.assertEqual(Decimal(str(result["quota"])), Decimal(expected // 2 + 1))

    def test_vote_page_shows_user_vote_count_under_credential_field(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Vote page weights election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        expected = self._make_weighted_voter(election=election, username="voter1")

        issued = issue_voting_credentials_from_memberships(election=election)
        cred = next(c for c in issued if c.freeipa_username == "voter1")
        self.assertEqual(int(cred.weight), expected)

        self._login_as_freeipa_user("voter1")

        voter1 = FreeIPAUser(
            "voter1",
            {
                "uid": ["voter1"],
                "givenname": ["Voter"],
                "sn": ["One"],
                "displayname": ["Voter One"],
                "memberof_group": [],
            },
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "displayname": ["Alice User"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "voter1":
                return voter1
            if username == "alice":
                return alice
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("election-vote", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "You have")
        self.assertContains(resp, f"<strong>{expected}</strong>")
        self.assertContains(resp, "votes for this election")

    def test_vote_weight_breakdown_returns_per_membership_lines(self) -> None:
        """vote_weight_breakdown_for_username returns one line per contributing membership."""
        now = timezone.now()
        election = Election.objects.create(
            name="Breakdown test",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        self._make_weighted_voter(election=election, username="voter1")

        breakdown = vote_weight_breakdown_for_username(election=election, username="voter1")

        # Should have 3 lines: two individual + one org
        self.assertEqual(len(breakdown), 3)
        total = sum(line.votes for line in breakdown)
        # total should match the weight from _make_weighted_voter (2 + 3 + 5 = 10)
        self.assertEqual(total, 10)

        # Individual lines should have no org_name
        individual_lines = [line for line in breakdown if not line.org_name]
        self.assertEqual(len(individual_lines), 2)

        # Org line should have an org_name
        org_lines = [line for line in breakdown if line.org_name]
        self.assertEqual(len(org_lines), 1)
        self.assertEqual(org_lines[0].votes, 5)
        self.assertEqual(org_lines[0].org_name, "ACME")

    @override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=14)
    def test_breakdown_uses_election_start_reference_for_membership_age(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Breakdown reference datetime election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        too_new_type = MembershipType.objects.create(
            code="recent_weight",
            name="Recent weight",
            votes=2,
            category_id="individual",
            enabled=True,
        )
        old_enough_type = MembershipType.objects.create(
            code="old_weight",
            name="Old weight",
            votes=3,
            category_id="individual",
            enabled=True,
        )

        too_new_membership = Membership.objects.create(
            target_username="alice",
            membership_type=too_new_type,
            expires_at=now + datetime.timedelta(days=365),
        )
        old_enough_membership = Membership.objects.create(
            target_username="alice",
            membership_type=old_enough_type,
            expires_at=now + datetime.timedelta(days=365),
        )

        # Age at vote time is not authoritative; age at election start is.
        Membership.objects.filter(pk=too_new_membership.pk).update(created_at=now - datetime.timedelta(days=20))
        Membership.objects.filter(pk=old_enough_membership.pk).update(created_at=now - datetime.timedelta(days=30))

        breakdown = vote_weight_breakdown_for_username(election=election, username="alice")

        self.assertEqual([line.label for line in breakdown], ["Old weight"])
        self.assertEqual(sum(line.votes for line in breakdown), 3)

    def test_eligible_vote_weight_for_username_delegates_to_breakdown_sum(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Delegation vote weight election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        with patch(
            "core.elections_eligibility.vote_weight_breakdown_for_username",
            return_value=[
                VoteWeightLine(label="A", org_name="", votes=2),
                VoteWeightLine(label="B", org_name="Org", votes=3),
            ],
        ) as breakdown_mock, patch(
            "core.elections_eligibility._eligible_voters_from_memberships",
            side_effect=AssertionError("_eligible_voters_from_memberships should not be called"),
        ):
            weight = eligible_vote_weight_for_username(election=election, username="alice")

        self.assertEqual(weight, 5)
        breakdown_mock.assert_called_once_with(election=election, username="alice")

    def test_eligible_vote_weight_matches_breakdown_sum_for_real_memberships(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Real membership sum election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        membership_type = MembershipType.objects.create(
            code="real_sum_weight",
            name="Real sum weight",
            votes=4,
            category_id="individual",
            enabled=True,
        )
        membership = Membership.objects.create(
            target_username="alice",
            membership_type=membership_type,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=30))

        breakdown = vote_weight_breakdown_for_username(election=election, username="alice")
        weight = eligible_vote_weight_for_username(election=election, username="alice")

        self.assertEqual(weight, sum(line.votes for line in breakdown))
        self.assertEqual(weight, 4)

    def test_vote_page_shows_breakdown_tooltip_icon(self) -> None:
        """Vote page renders the info icon with breakdown tooltip when voter has memberships."""
        now = timezone.now()
        election = Election.objects.create(
            name="Tooltip test election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        self._make_weighted_voter(election=election, username="voter1")
        issue_voting_credentials_from_memberships(election=election)

        self._login_as_freeipa_user("voter1")
        voter1_ipa = FreeIPAUser("voter1", {"uid": ["voter1"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=voter1_ipa):
            resp = self.client.get(reverse("election-vote", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        # Info icon should be present
        self.assertContains(resp, "fa-info-circle")
        # Tooltip should reference individual membership names or "ACME"
        self.assertContains(resp, "ACME")

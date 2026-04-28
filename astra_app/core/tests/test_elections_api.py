import datetime
import json
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import elections_services
from core.elections_eligibility import VoteWeightLine
from core.freeipa.user import FreeIPAUser
from core.models import (
    AuditLogEntry,
    Ballot,
    Candidate,
    Election,
    ExclusionGroup,
    FreeIPAPermissionGrant,
    Membership,
    MembershipType,
    MembershipTypeCategory,
    Organization,
    VotingCredential,
)
from core.permissions import ASTRA_ADD_ELECTION
from core.tests.ballot_chain import compute_chain_hash
from core.tests.utils_test_data import ensure_core_categories
from core.tokens import election_genesis_chain_hash
from core.views_elections.vote import _parse_vote_payload


class ElectionsApiTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_vote_payload_accepts_json_username_ranking_fallback(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="JSON fallback election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        request = RequestFactory().post(
            reverse("api-election-vote-submit", args=[election.id]),
            data=json.dumps({"credential_public_id": "cred-1", "ranking": [], "ranking_usernames": "alice"}),
            content_type="application/json",
        )

        credential_public_id, ranking = _parse_vote_payload(request, election=election)

        self.assertEqual(credential_public_id, "cred-1")
        self.assertEqual(ranking, [candidate.id])

    def test_elections_api_returns_visible_elections_for_non_manager(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        open_election = Election.objects.create(
            name="Published election",
            description="Visible to everyone",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=2,
            status=Election.Status.open,
        )
        Election.objects.create(
            name="Draft election",
            description="Managers only",
            start_datetime=now + datetime.timedelta(days=2),
            end_datetime=now + datetime.timedelta(days=3),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(reverse("api-elections"), HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertFalse(payload["can_manage_elections"])
        self.assertEqual([item["name"] for item in payload["items"]], ["Published election"])
        self.assertEqual(payload["items"][0]["id"], open_election.id)
        self.assertEqual(payload["items"][0]["status"], Election.Status.open)
        self.assertNotIn("detail_url", payload["items"][0])
        self.assertNotIn("edit_url", payload["items"][0])
        self.assertEqual(payload["pagination"]["page"], 1)

    def test_election_detail_info_api_returns_data_only_for_draft_manager(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Board election",
            description="Elect the board",
            url="https://example.com/elections/board",
            start_datetime=now + datetime.timedelta(days=2),
            end_datetime=now + datetime.timedelta(days=3),
            number_of_seats=2,
            quorum=25,
            eligible_group_cn="board-voters",
            status=Election.Status.draft,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            response = self.client.get(
                reverse("api-election-detail-info", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["election"]["id"], election.id)
        self.assertEqual(payload["election"]["name"], "Board election")
        self.assertEqual(payload["election"]["status"], Election.Status.draft)
        self.assertEqual(payload["election"]["eligible_group_cn"], "board-voters")
        self.assertEqual(payload["election"]["number_of_seats"], 2)
        self.assertEqual(payload["election"]["quorum"], 25)
        self.assertFalse(payload["election"]["can_vote"])
        self.assertNotIn("can_manage_elections", payload["election"])
        self.assertNotIn("detail_url", payload["election"])
        self.assertNotIn("vote_url", payload["election"])
        self.assertNotIn("membership_request_url", payload["election"])
        self.assertNotIn("edit_url", payload["election"])
        self.assertNotIn("audit_log_url", payload["election"])
        self.assertNotIn("public_ballots_url", payload["election"])
        self.assertNotIn("public_audit_url", payload["election"])
        self.assertNotIn("extend_end_api_url", payload["election"])
        self.assertNotIn("conclude_api_url", payload["election"])

    def test_election_detail_info_api_omits_open_manager_lifecycle_api_urls(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Lifecycle election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            response = self.client.get(
                reverse("api-election-detail-info", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertNotIn("extend_end_api_url", payload["election"])
        self.assertNotIn("conclude_api_url", payload["election"])

    def test_election_detail_info_api_includes_action_card_state_for_open_voter(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Open voter election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        voter_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        membership = Membership.objects.create(target_username="viewer", membership_type=voter_type, expires_at=None)
        Membership.objects.filter(pk=membership.pk).update(created_at=election.start_datetime - datetime.timedelta(days=1))

        issued_at = now - datetime.timedelta(hours=1)
        VotingCredential.objects.create(
            election=election,
            public_id="cred-viewer",
            freeipa_username="viewer",
            weight=1,
            created_at=issued_at,
        )

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(
                reverse("api-election-detail-info", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["election"]["viewer_email"], "viewer@example.com")
        self.assertNotIn("vote_url", payload["election"])
        self.assertNotIn("membership_request_url", payload["election"])
        self.assertEqual(
            payload["election"]["eligibility_min_membership_age_days"],
            settings.ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS,
        )
        self.assertTrue(payload["election"]["credential_issued_at"])

    def test_election_detail_info_api_omits_public_artifact_urls(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Finished election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )
        election.public_ballots_file.save(
            "ballots.json",
            SimpleUploadedFile("ballots.json", b"{}", content_type="application/json"),
            save=False,
        )
        election.public_audit_file.save(
            "audit.json",
            SimpleUploadedFile("audit.json", b"{}", content_type="application/json"),
            save=False,
        )
        election.save(update_fields=["public_ballots_file", "public_audit_file"])

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(
                reverse("api-election-detail-info", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertNotIn("public_ballots_url", payload["election"])
        self.assertNotIn("public_audit_url", payload["election"])

    def test_election_detail_info_api_includes_exclusion_and_tally_summary(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Summary election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=2,
            status=Election.Status.tallied,
            tally_result={"elected": [], "rounds": []},
        )
        elected_candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
            description="",
            url="",
        )
        non_elected_candidate = Candidate.objects.create(
            election=election,
            freeipa_username="bob",
            nominated_by="nominator",
            description="",
            url="",
        )
        exclusion_group = ExclusionGroup.objects.create(
            election=election,
            name="Employees of X",
            max_elected=1,
        )
        exclusion_group.candidates.add(elected_candidate, non_elected_candidate)
        election.tally_result = {"elected": [elected_candidate.id], "rounds": []}
        election.save(update_fields=["tally_result"])

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "displayname": ["Alice User"], "memberof_group": []},
        )
        bob = FreeIPAUser(
            "bob",
            {"uid": ["bob"], "displayname": ["Bob User"], "memberof_group": []},
        )
        nominator = FreeIPAUser("nominator", {"uid": ["nominator"], "memberof_group": []})

        def _get_user(username: str):
            if username == "admin":
                return admin
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            if username == "nominator":
                return nominator
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            response = self.client.get(
                reverse("api-election-detail-info", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["election"]["election_is_finished"])
        self.assertEqual(payload["election"]["empty_seats"], 1)
        self.assertEqual(payload["election"]["tally_winners"][0]["username"], "alice")
        self.assertEqual(payload["election"]["tally_winners"][0]["full_name"], "Alice User")
        self.assertEqual(len(payload["election"]["exclusion_group_messages"]), 1)
        self.assertIn("Employees of X", payload["election"]["exclusion_group_messages"][0])
        self.assertIn("only 1", payload["election"]["exclusion_group_messages"][0])
        self.assertTrue(payload["election"]["show_turnout_chart"])
        self.assertIn("participating_voter_count", payload["election"]["turnout_stats"])
        self.assertEqual(set(payload["election"]["turnout_chart_data"]), {"labels", "counts"})

    def test_election_detail_candidates_api_returns_paginated_candidate_cards(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Board election",
            description="Elect the board",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=2,
            status=Election.Status.open,
        )
        organization = Organization.objects.create(name="Infra Foundation", representative="")
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
            description="A short bio.",
            url="https://example.com/~alice",
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="bob",
            nominated_by=f"org:{organization.id}",
            description="",
            url="",
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
        nominator = FreeIPAUser(
            "nominator",
            {
                "uid": ["nominator"],
                "givenname": ["Nominator"],
                "sn": ["Person"],
                "displayname": ["Nominator Person"],
                "memberof_group": [],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "sn": ["User"],
                "displayname": ["Bob User"],
                "memberof_group": [],
            },
        )
        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            if username == "nominator":
                return nominator
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.views_elections.detail.resolve_avatar_urls_for_users", return_value=({"alice": "/avatars/alice.png"}, 1, 0)),
        ):
            response = self.client.get(
                reverse("api-election-detail-candidates", args=[election.id]),
                {"page": "1"},
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["candidates"]["pagination"]["page"], 1)
        self.assertEqual(payload["candidates"]["pagination"]["count"], 2)
        self.assertEqual(payload["candidates"]["items"][0]["avatar_url"], "/avatars/alice.png")

    def test_election_detail_page_api_returns_data_only_payload(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Board election",
            description="Elect the board",
            start_datetime=now - datetime.timedelta(days=3),
            end_datetime=now + datetime.timedelta(days=2),
            number_of_seats=2,
            quorum=25,
            eligible_group_cn="board-voters",
            status=Election.Status.tallied,
        )
        alice_candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="bob",
            nominated_by="nominator",
        )
        election.tally_result = {
            "quota": "1",
            "elected": [alice_candidate.id],
            "eliminated": [],
            "forced_excluded": [],
            "rounds": [],
        }
        election.save(update_fields=["tally_result"])
        turnout_entry = AuditLogEntry.objects.create(
            election=election,
            event_type="ballot_submitted",
            payload={},
            is_public=False,
        )
        AuditLogEntry.objects.filter(pk=turnout_entry.pk).update(timestamp=now - datetime.timedelta(days=1))
        exclusion_group = ExclusionGroup.objects.create(
            election=election,
            name="Employees of X",
            max_elected=1,
        )
        exclusion_group.candidates.add(*Candidate.objects.filter(election=election))

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "displayname": ["Alice User"], "memberof_group": []},
        )
        bob = FreeIPAUser(
            "bob",
            {"uid": ["bob"], "displayname": ["Bob User"], "memberof_group": []},
        )

        def _get_user(username: str):
            if username == "admin":
                return admin
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            response = self.client.get(
                reverse("api-election-detail-page", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["election"]
        self.assertEqual(payload["id"], election.id)
        self.assertEqual(payload["start_datetime"], election.start_datetime.isoformat())
        self.assertEqual(payload["end_datetime"], election.end_datetime.isoformat())
        self.assertNotIn("start_datetime_display", payload)
        self.assertNotIn("end_datetime_display", payload)
        self.assertNotIn("exclusion_group_messages", payload)
        self.assertEqual(
            payload["turnout_rows"],
            [
                {"day": (now - datetime.timedelta(days=1)).date().isoformat(), "count": 1},
            ],
        )
        self.assertNotIn("turnout_chart_data", payload)
        self.assertEqual(
            payload["exclusion_groups"],
            [
                {
                    "name": "Employees of X",
                    "max_elected": 1,
                    "candidates": [
                        {"username": "alice", "full_name": "Alice User"},
                        {"username": "bob", "full_name": "Bob User"},
                    ],
                }
            ],
        )

    def test_election_eligible_voters_api_returns_filtered_paginated_user_items_for_manager(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Eligible voters API election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        voter_type = MembershipType.objects.get(code="voter")

        alice = Membership.objects.create(target_username="alice", membership_type=voter_type, expires_at=None)
        bob = Membership.objects.create(target_username="bob", membership_type=voter_type, expires_at=None)
        Membership.objects.filter(pk__in=[alice.pk, bob.pk]).update(created_at=election.start_datetime - datetime.timedelta(days=10))

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def _get_user(username: str, respect_privacy: bool = True):
            if username == "admin":
                return admin
            if username == "alice":
                return FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice Example"], "memberof_group": []})
            if username == "bob":
                return FreeIPAUser("bob", {"uid": ["bob"], "displayname": ["Bob Example"], "memberof_group": []})
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.views_groups.resolve_avatar_urls_for_users", return_value=({"alice": "/avatars/alice.png"}, 1, 0)),
        ):
            response = self.client.get(
                reverse("api-election-detail-eligible-voters", args=[election.id]),
                {"q": "ali", "page": 1},
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["eligible_voters"]["items"], [
            {
                "username": "alice",
                "full_name": "Alice Example",
                "avatar_url": "/avatars/alice.png",
            }
        ])
        self.assertEqual(payload["eligible_voters"]["pagination"]["page"], 1)
        self.assertEqual(payload["eligible_voters"]["pagination"]["count"], 1)
        self.assertNotIn("ineligible_voters", payload)

    def test_election_eligible_voters_api_does_not_compute_ineligible_voters(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Eligible voters split API election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        membership = Membership.objects.create(
            target_username="alice",
            membership_type_id="voter",
            expires_at=None,
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=election.start_datetime - datetime.timedelta(days=10))

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        alice = FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice Example"], "memberof_group": []})

        def _get_user(username: str, respect_privacy: bool = True):
            if username == "admin":
                return admin
            if username == "alice":
                return alice
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch(
                "core.views_elections.detail.elections_eligibility.ineligible_voters_with_reasons",
                side_effect=AssertionError("eligible voters endpoint must not compute ineligible voters"),
            ),
            patch("core.views_groups.resolve_avatar_urls_for_users", return_value=({}, 0, 0)),
        ):
            response = self.client.get(
                reverse("api-election-detail-eligible-voters", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["username"] for item in payload["eligible_voters"]["items"]], ["alice"])
        self.assertNotIn("ineligible_voters", payload)

    @override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=30)
    def test_election_ineligible_voters_api_returns_filtered_paginated_user_items_for_manager(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 2, 1, 12, 0, 0))
        start_dt = timezone.make_aware(datetime.datetime(2026, 2, 10, 12, 0, 0))
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        election = Election.objects.create(
            name="Ineligible voters split API election",
            description="",
            start_datetime=start_dt,
            end_datetime=start_dt + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.open,
        )
        MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        bob = Membership.objects.create(
            target_username="bob",
            membership_type_id="voter",
            expires_at=start_dt + datetime.timedelta(days=365),
        )
        alice = Membership.objects.create(
            target_username="alice",
            membership_type_id="voter",
            expires_at=start_dt + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk__in=[bob.pk, alice.pk]).update(created_at=now)

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        freeipa_users = [
            FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []}),
            FreeIPAUser("bob", {"uid": ["bob"], "displayname": ["Bob Example"], "memberof_group": []}),
        ]

        def _get_user(username: str, respect_privacy: bool = True):
            if username == "admin":
                return admin
            if username == "bob":
                return freeipa_users[1]
            if username == "alice":
                return freeipa_users[0]
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.elections_eligibility.snapshot_freeipa_users", return_value=freeipa_users),
            patch("core.views_groups.resolve_avatar_urls_for_users", return_value=({"bob": "/avatars/bob.png"}, 1, 0)),
        ):
            response = self.client.get(
                reverse("api-election-detail-ineligible-voters", args=[election.id]),
                {"q": "bo", "page": 1},
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["ineligible_voters"]["items"], [
            {
                "username": "bob",
                "full_name": "Bob Example",
                "avatar_url": "/avatars/bob.png",
            }
        ])
        self.assertEqual(payload["ineligible_voters"]["details_by_username"]["bob"]["reason"], "too_new")
        self.assertEqual(payload["ineligible_voters"]["pagination"]["page"], 1)
        self.assertEqual(payload["ineligible_voters"]["pagination"]["count"], 1)
        self.assertNotIn("eligible_voters", payload)

    def test_election_vote_api_returns_bootstrap_for_open_election(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Vote page election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=2,
            status=Election.Status.open,
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="viewer",
            weight=2,
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        voter_type, _created = MembershipType.objects.update_or_create(
            code="voter",
            defaults={
                "name": "Voter",
                "votes": 2,
                "category_id": "individual",
                "enabled": True,
            },
        )
        Membership.objects.create(target_username="viewer", membership_type=voter_type, expires_at=None)

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "displayname": ["Viewer User"],
                "memberof_group": [],
            },
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "displayname": ["Alice User"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch(
                "core.views_elections.vote.vote_weight_breakdown_for_username",
                return_value=[VoteWeightLine(votes=2, label="Individual", org_name="")],
            ),
            patch("core.views_elections.vote.block_action_without_coc", return_value=None),
        ):
            response = self.client.get(
                reverse("api-election-vote", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["election"]["id"], election.id)
        self.assertEqual(payload["election"]["name"], "Vote page election")
        self.assertTrue(payload["election"]["can_submit_vote"])
        self.assertEqual(payload["election"]["voter_votes"], 2)
        self.assertNotIn("detail_url", payload["election"])
        self.assertNotIn("verify_url", payload["election"])
        self.assertTrue(payload["election"]["submit_url"].endswith(reverse("api-election-vote-submit", args=[election.id])))
        self.assertEqual(payload["candidates"][0]["id"], candidate.id)
        self.assertIn("Alice User", payload["candidates"][0]["label"])
        self.assertEqual(
            payload["vote_weight_breakdown"],
            [{"votes": 2, "label": "Individual", "org_name": None}],
        )

    def test_ballot_verify_api_returns_tallied_verification_details(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Tallied verification election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            tally_result={"quota": "1", "elected": [], "eliminated": [], "forced_excluded": [], "rounds": []},
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="n",
        )

        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[candidate.id],
            weight=1,
            nonce="0" * 32,
        )
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[candidate.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=election_genesis_chain_hash(election.id),
            chain_hash=compute_chain_hash(
                previous_chain_hash=election_genesis_chain_hash(election.id),
                ballot_hash=ballot_hash,
            ),
            is_counted=True,
        )

        response = self.client.get(
            reverse("api-ballot-verify"),
            {"receipt": ballot_hash},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["has_query"])
        self.assertTrue(payload["is_valid_receipt"])
        self.assertTrue(payload["found"])
        self.assertFalse(payload["rate_limited"])
        self.assertEqual(payload["election"]["id"], election.id)
        self.assertEqual(payload["election"]["name"], election.name)
        self.assertEqual(payload["election_status"], Election.Status.tallied)
        self.assertFalse(payload["is_superseded"])
        self.assertTrue(payload["is_final_ballot"])
        self.assertTrue(payload["public_ballots_url"].endswith(reverse("election-public-ballots", args=[election.id])))
        self.assertNotIn("audit_log_url", payload)
        self.assertIn(f"election_id = {election.id}", payload["verification_snippet"])

    def test_election_vote_submit_api_alias_uses_existing_submission_contract(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Vote submit API election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        response = self.client.post(
            reverse("api-election-vote-submit", args=[election.id]),
            data=json.dumps({"credential_public_id": "cred-1", "ranking": [1]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content), {"ok": False, "error": "Authentication required."})

    def test_elections_turnout_report_api_returns_manager_report_payload(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Turnout election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=2,
            status=Election.Status.open,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-turnout-1",
            freeipa_username="voter1",
            weight=3,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            response = self.client.get(
                reverse("api-elections-turnout-report"),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["rows"][0]["election"]["id"], election.id)
        self.assertEqual(payload["rows"][0]["eligible_weight"], 3)
        self.assertFalse(payload["rows"][0]["credentials_issued"] is False)
        self.assertEqual(payload["chart_data"]["labels"][0].split(": ", 1)[1], election.name)
        self.assertEqual(payload["chart_data"]["weight_turnout"][0], 0.0)

    def test_elections_turnout_report_detail_api_returns_data_only_rows(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Turnout election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=2,
            status=Election.Status.open,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-turnout-1",
            freeipa_username="voter1",
            weight=3,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            response = self.client.get(
                reverse("api-elections-turnout-report-detail"),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("chart_data", payload)
        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["rows"][0]["election"]["id"], election.id)
        self.assertEqual(payload["rows"][0]["election"]["name"], election.name)
        self.assertEqual(payload["rows"][0]["election"]["start_datetime"], election.start_datetime.isoformat())
        self.assertNotIn("start_date", payload["rows"][0]["election"])

    def test_election_extend_end_api_updates_open_election_for_manager(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="API extend election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        new_end = timezone.localtime(election.end_datetime + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            response = self.client.post(
                reverse("api-election-extend-end", args=[election.id]),
                data=json.dumps({"confirm": election.name, "end_datetime": new_end}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["ok"])
        election.refresh_from_db()
        self.assertEqual(timezone.localtime(election.end_datetime).strftime("%Y-%m-%dT%H:%M"), new_end)

    def test_election_conclude_api_closes_open_election_for_manager(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="API conclude election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            response = self.client.post(
                reverse("api-election-conclude", args=[election.id]),
                data=json.dumps({"confirm": election.name, "skip_tally": True}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["election"]["status"], Election.Status.closed)
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.closed)

    def test_election_conclude_api_returns_closed_status_when_tally_fails_after_close(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="API conclude election partial success",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def _close_election(*, election: Election, actor: str | None = None) -> None:
            election.status = Election.Status.closed
            election.save(update_fields=["status"])

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.lifecycle.elections_services.close_election", side_effect=_close_election),
            patch(
                "core.views_elections.lifecycle.elections_services.tally_election",
                side_effect=elections_services.ElectionError("tally exploded"),
            ),
        ):
            response = self.client.post(
                reverse("api-election-conclude", args=[election.id]),
                data=json.dumps({"confirm": election.name, "skip_tally": False}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["tally_failed"])
        self.assertEqual(payload["election"]["status"], Election.Status.closed)
        self.assertContains(response, "Election closed, but tally failed", status_code=200)

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.closed)

    def test_election_tally_api_tallies_closed_election_for_manager(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="API tally election",
            description="",
            start_datetime=now - datetime.timedelta(days=3),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def _tally_election(*, election: Election, actor: str | None = None) -> None:
            election.status = Election.Status.tallied
            election.tally_result = {"elected": []}
            election.save(update_fields=["status", "tally_result"])

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin),
            patch("core.views_elections.lifecycle.elections_services.tally_election", side_effect=_tally_election),
        ):
            response = self.client.post(
                reverse("api-election-tally", args=[election.id]),
                data=json.dumps({"confirm": election.name}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["message"], "Election tallied.")
        self.assertEqual(payload["election"]["status"], Election.Status.tallied)

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.tallied)

    def test_election_tally_api_rejects_non_closed_election(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="API tally election invalid state",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            response = self.client.post(
                reverse("api-election-tally", args=[election.id]),
                data=json.dumps({"confirm": election.name}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"], ["Only closed elections can be tallied."])

    def test_election_tally_api_denied_without_permission(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="API tally election denied",
            description="",
            start_datetime=now - datetime.timedelta(days=3),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.post(
                reverse("api-election-tally", args=[election.id]),
                data=json.dumps({"confirm": election.name}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 403)

    def test_election_tally_api_retries_after_skip_tally_close_for_committee_group_member(self) -> None:
        self._login_as_freeipa_user("committee")
        FreeIPAPermissionGrant.objects.get_or_create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="API retry tally election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(hours=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-committee",
            ranking=[candidate.id],
            weight=1,
            nonce="1" * 32,
        )
        genesis_hash = election_genesis_chain_hash(election.id)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-committee",
            ranking=[candidate.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash),
        )

        committee_user = FreeIPAUser(
            "committee",
            {
                "uid": ["committee"],
                "memberof_group": [settings.FREEIPA_ELECTION_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=committee_user):
            conclude_response = self.client.post(
                reverse("api-election-conclude", args=[election.id]),
                data=json.dumps({"confirm": election.name, "skip_tally": True}),
                content_type="application/json",
            )

            detail_response = self.client.get(reverse("election-detail", args=[election.id]))

            election.refresh_from_db()
            self.assertEqual(election.status, Election.Status.closed)
            tally_response = self.client.post(
                reverse("api-election-tally", args=[election.id]),
                data=json.dumps({"confirm": election.name}),
                content_type="application/json",
            )

        self.assertEqual(conclude_response.status_code, 200)
        conclude_payload = json.loads(conclude_response.content)
        self.assertTrue(conclude_payload["ok"])
        self.assertEqual(conclude_payload["election"]["status"], Election.Status.closed)

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "data-election-tally-action-root")
        self.assertContains(detail_response, reverse("api-election-tally", args=[election.id]))

        self.assertEqual(tally_response.status_code, 200)
        tally_payload = json.loads(tally_response.content)
        self.assertTrue(tally_payload["ok"])
        self.assertEqual(tally_payload["election"]["status"], Election.Status.tallied)

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.tallied)
        self.assertEqual(election.tally_result["elected"], [candidate.id])

    def test_election_send_mail_credentials_api_returns_send_mail_redirect_and_payload(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Reminder API election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-alice",
            freeipa_username="alice",
            weight=1,
        )

        def _get_user(username: str, respect_privacy: bool = True):
            return FreeIPAUser(
                username,
                {"uid": [username], "mail": [f"{username}@example.com"], "memberof_group": []},
            )

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            response = self.client.post(
                reverse("api-election-send-mail-credentials", args=[election.id]),
                data=json.dumps({"username": "alice"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["ok"])
        self.assertIn(reverse("send-mail"), payload["redirect_url"])
        self.assertEqual(payload["recipient_count"], 1)

        raw_csv_payload = self.client.session.get("send_mail_csv_payload_v1")
        self.assertTrue(raw_csv_payload)
        session_payload = json.loads(str(raw_csv_payload))
        self.assertEqual(len(session_payload["recipients"]), 1)
        self.assertEqual(session_payload["recipients"][0]["username"], "alice")
        self.assertEqual(session_payload["recipients"][0]["credential_public_id"], "cred-alice")

    @override_settings(
        ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_LIMIT=1,
        ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_WINDOW_SECONDS=60,
    )
    def test_election_send_mail_credentials_api_applies_rate_limit(self) -> None:
        cache.clear()
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Rate limited reminder API election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-alice",
            freeipa_username="alice",
            weight=1,
        )

        def _get_user(username: str, respect_privacy: bool = True):
            return FreeIPAUser(
                username,
                {"uid": [username], "mail": [f"{username}@example.com"], "memberof_group": []},
            )

        payload = json.dumps({"username": "alice"})
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            first = self.client.post(
                reverse("api-election-send-mail-credentials", args=[election.id]),
                data=payload,
                content_type="application/json",
            )
            second = self.client.post(
                reverse("api-election-send-mail-credentials", args=[election.id]),
                data=payload,
                content_type="application/json",
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        second_payload = json.loads(second.content)
        self.assertFalse(second_payload["ok"])
        self.assertEqual(second_payload["errors"], ["Too many resend attempts. Please try again later."])

    def test_election_audit_log_api_hides_quorum_reached_for_non_managers(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Quorum privacy election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        AuditLogEntry.objects.create(
            election=election,
            event_type="quorum_reached",
            payload={"quorum_percent": 50},
            is_public=True,
        )
        AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload={"chain_head": "b" * 64},
            is_public=True,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(
                reverse("api-election-audit-log", args=[election.id]),
                {"page": "1"},
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertNotIn("can_manage_elections", payload)
        self.assertEqual(payload["audit_log"]["pagination"]["page"], 1)
        self.assertEqual([item["event_type"] for item in payload["audit_log"]["items"]], ["election_closed"])
        self.assertEqual(payload["audit_log"]["items"][0]["title"], "Election closed")

    def test_election_audit_log_api_does_not_expose_raw_closed_payload_to_non_managers(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Closed payload privacy election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload={
                "actor": "admin",
                "chain_head": "c" * 64,
                "credentials_affected": 9,
                "emails_scrubbed": 8,
            },
            is_public=True,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(
                reverse("api-election-audit-log", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        event_payload = payload["audit_log"]["items"][0]["payload"]
        self.assertEqual(
            event_payload,
            {
                "chain_head": "c" * 64,
                "credentials_affected": True,
                "emails_scrubbed": True,
            },
        )
        self.assertNotIn("admin", json.dumps(payload))
        self.assertNotEqual(event_payload["credentials_affected"], 9)
        self.assertNotEqual(event_payload["emails_scrubbed"], 8)

    def test_election_audit_log_api_omits_closed_scrub_fields_for_managers(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Closed manager payload election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload={
                "actor": "admin",
                "chain_head": "d" * 64,
                "credentials_affected": 12,
                "emails_scrubbed": 10,
            },
            is_public=True,
        )

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            response = self.client.get(
                reverse("api-election-audit-log", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        event_payload = payload["audit_log"]["items"][0]["payload"]
        self.assertEqual(event_payload, {"chain_head": "d" * 64})
        self.assertNotIn("admin", json.dumps(payload))
        self.assertNotIn("credentials_affected", json.dumps(payload))
        self.assertNotIn("emails_scrubbed", json.dumps(payload))

    def test_election_audit_log_api_exposes_start_tiebreak_data_without_operational_counts(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Started payload audit election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        AuditLogEntry.objects.create(
            election=election,
            event_type="election_started",
            payload={
                "actor": "admin",
                "eligible_voters": 12,
                "emailed": 10,
                "skipped": 1,
                "failures": 1,
                "genesis_chain_hash": "e" * 64,
                "candidates": [
                    {
                        "id": 1,
                        "freeipa_username": "alice",
                        "tiebreak_uuid": "00000000-0000-0000-0000-000000000001",
                    },
                ],
            },
            is_public=True,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(
                reverse("api-election-audit-log", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        event_payload = payload["audit_log"]["items"][0]["payload"]
        self.assertEqual(
            event_payload,
            {
                "genesis_chain_hash": "e" * 64,
                "candidates": [
                    {
                        "id": 1,
                        "freeipa_username": "alice",
                        "tiebreak_uuid": "00000000-0000-0000-0000-000000000001",
                    },
                ],
            },
        )
        self.assertNotIn("admin", json.dumps(payload))
        self.assertNotIn("eligible_voters", json.dumps(payload))
        self.assertNotIn("failures", json.dumps(payload))

    def test_election_audit_log_api_groups_ballot_submissions_for_managers(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Long election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )

        day1 = (now - datetime.timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
        day2 = (now - datetime.timedelta(days=2)).replace(hour=11, minute=0, second=0, microsecond=0)

        e1 = AuditLogEntry.objects.create(
            election=election,
            event_type="ballot_submitted",
            payload={"ballot_hash": "hash-1"},
            is_public=False,
        )
        e2 = AuditLogEntry.objects.create(
            election=election,
            event_type="ballot_submitted",
            payload={"ballot_hash": "hash-2"},
            is_public=False,
        )
        e3 = AuditLogEntry.objects.create(
            election=election,
            event_type="ballot_submitted",
            payload={"ballot_hash": "hash-3"},
            is_public=False,
        )

        AuditLogEntry.objects.filter(id=e1.id).update(timestamp=day1)
        AuditLogEntry.objects.filter(id=e2.id).update(timestamp=day1 + datetime.timedelta(hours=1))
        AuditLogEntry.objects.filter(id=e3.id).update(timestamp=day2)

        admin = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin):
            response = self.client.get(
                reverse("api-election-audit-log", args=[election.id]),
                {"page": "1"},
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertNotIn("can_manage_elections", payload)
        summary_events = [item for item in payload["audit_log"]["items"] if item["event_type"] == "ballots_submitted_summary"]
        self.assertEqual(len(summary_events), 2)
        self.assertEqual(summary_events[0]["ballots_count"], 1)
        self.assertTrue(summary_events[0]["first_timestamp"])
        self.assertTrue(summary_events[0]["last_timestamp"])
        self.assertEqual(summary_events[0]["ballot_entries"][0]["ballot_hash"], "hash-3")
        self.assertEqual(summary_events[1]["ballots_count"], 2)
        self.assertTrue(summary_events[1]["first_timestamp"])
        self.assertTrue(summary_events[1]["last_timestamp"])
        self.assertEqual(summary_events[1]["ballot_entries"][0]["ballot_hash"], "hash-1")
        self.assertEqual(summary_events[1]["ballot_entries"][1]["ballot_hash"], "hash-2")

    def test_election_audit_log_api_returns_timeline_data_only(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Timeline-only election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            tally_result={"elected": [], "rounds": []},
        )
        AuditLogEntry.objects.create(
            election=election,
            event_type="tally_completed",
            payload={"elected": []},
            is_public=True,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(
                reverse("api-election-audit-log", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(set(payload), {"audit_log"})
        self.assertNotIn("can_manage_elections", payload)
        self.assertNotIn("summary", payload)
        self.assertNotIn("newer_url", payload["audit_log"]["pagination"])
        self.assertNotIn("older_url", payload["audit_log"]["pagination"])
        self.assertNotIn("profile_url", json.dumps(payload))
        self.assertNotIn("/elections/", json.dumps(payload))

    def test_election_audit_log_api_uses_shared_pagination_serializer(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Pagination helper election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )
        AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload={"chain_head": "f" * 64},
            is_public=True,
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer),
            patch(
                "core.views_elections.audit.serialize_pagination",
                wraps=lambda page_ctx: {"count": 456},
                create=True,
            ) as serialize_mock,
        ):
            response = self.client.get(
                reverse("api-election-audit-log", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["audit_log"]["pagination"], {"count": 456})
        serialize_mock.assert_called_once()

    def test_election_audit_summary_api_returns_summary_data_only(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Summary election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            tally_result={"elected": [], "rounds": []},
        )

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            response = self.client.get(
                reverse("api-election-audit-summary", args=[election.id]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(set(payload), {"summary"})
        self.assertEqual(payload["summary"]["ballots_cast"], 0)
        self.assertEqual(payload["summary"]["votes_cast"], 0)
        self.assertNotIn("can_manage_elections", payload)
        self.assertNotIn("audit_log", payload)
        self.assertNotIn("profile_url", json.dumps(payload))
        self.assertNotIn("/user/", json.dumps(payload))

    def test_versioned_public_audit_endpoints_alias_existing_exports(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact endpoints election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        genesis_hash = election_genesis_chain_hash(election.id)
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[candidate.id],
            weight=1,
            nonce="0" * 32,
        )
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[candidate.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

        elections_services.tally_election(election=election)
        election.refresh_from_db()

        ballots_resp = self.client.get(reverse("api-election-public-ballots", args=[election.id]))
        audit_resp = self.client.get(reverse("api-election-public-audit", args=[election.id]))

        self.assertIn(ballots_resp.status_code, {200, 302})
        self.assertNotIn("/login", str(ballots_resp.get("Location", "")))
        self.assertIn(audit_resp.status_code, {200, 302})
        self.assertNotIn("/login", str(audit_resp.get("Location", "")))
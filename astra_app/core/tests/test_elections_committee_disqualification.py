
import datetime
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.elections_eligibility import ElectionEligibilityError, election_committee_disqualification
from core.freeipa.exceptions import FreeIPAMisconfiguredError, FreeIPAUnavailableError
from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
from core.models import Election, FreeIPAPermissionGrant, Membership, MembershipType
from core.permissions import ASTRA_ADD_ELECTION
from core.tests.utils_test_data import ensure_core_categories


class _CoreCategoriesTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()


class ElectionCommitteeDisqualificationUnitTests(_CoreCategoriesTestCase):
    def test_disqualification_detects_committee_candidates_and_nominators(self) -> None:
        committee_group = FreeIPAGroup(
            settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            {"member_user": ["alice", "nina"]},
        )
        def _get_group(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
            if cn != committee_group.cn:
                raise FreeIPAMisconfiguredError("Unknown group")
            return committee_group

        with patch("core.elections_eligibility.get_freeipa_group_for_elections", side_effect=_get_group):
            candidates = ["alice", "bob"]
            nominators = ["nina", "sara"]
            candidate_conflicts, nominator_conflicts = election_committee_disqualification(
                candidate_usernames=candidates,
                nominator_usernames=nominators,
            )

        self.assertEqual(candidate_conflicts, {"alice"})
        self.assertEqual(nominator_conflicts, {"nina"})

    def test_disqualification_blocks_when_committee_group_missing(self) -> None:
        with patch(
            "core.elections_eligibility.get_freeipa_group_for_elections",
            side_effect=FreeIPAMisconfiguredError("missing"),
        ):
            with self.assertRaises(ElectionEligibilityError) as exc:
                election_committee_disqualification(
                    candidate_usernames=["alice"],
                    nominator_usernames=["bob"],
                )

        self.assertIn("committee", str(exc.exception).lower())

    def test_disqualification_returns_empty_when_committee_group_empty(self) -> None:
        committee_group = FreeIPAGroup(
            settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            {"member_user": []},
        )
        def _get_group(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
            if cn != committee_group.cn:
                raise FreeIPAMisconfiguredError("Unknown group")
            return committee_group

        with patch("core.elections_eligibility.get_freeipa_group_for_elections", side_effect=_get_group):
            candidate_conflicts, nominator_conflicts = election_committee_disqualification(
                candidate_usernames=["alice"],
                nominator_usernames=["bob"],
            )

        self.assertEqual(candidate_conflicts, set())
        self.assertEqual(nominator_conflicts, set())

    def test_disqualification_is_case_insensitive(self) -> None:
        committee_group = FreeIPAGroup(
            settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            {"member_user": ["ALICE"]},
        )
        def _get_group(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
            if cn != committee_group.cn:
                raise FreeIPAMisconfiguredError("Unknown group")
            return committee_group

        with patch("core.elections_eligibility.get_freeipa_group_for_elections", side_effect=_get_group):
            candidate_conflicts, nominator_conflicts = election_committee_disqualification(
                candidate_usernames=["alice"],
                nominator_usernames=["aLiCe"],
            )

        self.assertEqual(candidate_conflicts, {"alice"})
        self.assertEqual(nominator_conflicts, {"aLiCe"})


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionCommitteeDisqualificationSearchTests(_CoreCategoriesTestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_manage_permission(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def _create_membership(self, *, username: str, now: datetime.datetime) -> None:
        mt, _created = MembershipType.objects.update_or_create(
            code="voter",
            defaults={
                "name": "Voter",
                "votes": 1,
                "category_id": "individual",
                "enabled": True,
            },
        )
        membership = Membership.objects.create(
            target_username=username,
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=30))

    def test_candidate_search_excludes_committee_members(self) -> None:
        now = timezone.now()
        self._create_membership(username="alice", now=now)
        self._create_membership(username="bob", now=now)

        election = Election.objects.create(
            name="Draft election",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=5),
            end_datetime=now + datetime.timedelta(days=6),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")

        committee_group = FreeIPAGroup(
            settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            {"member_user": ["alice"]},
        )

        def _get_group(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
            if cn != committee_group.cn:
                raise FreeIPAMisconfiguredError("Unknown group")
            return committee_group

        def _get_user(username: str) -> FreeIPAUser:
            return FreeIPAUser(username, {"uid": [username], "memberof_group": []})

        with (
            patch("core.elections_eligibility.get_freeipa_group_for_elections", side_effect=_get_group),
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
        ):
            resp = self.client.get(reverse("election-eligible-users-search", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        results = [r.get("id") for r in data.get("results", [])]
        self.assertIn("bob", results)
        self.assertNotIn("alice", results)

    def test_nomination_search_excludes_committee_members(self) -> None:
        now = timezone.now()
        self._create_membership(username="alice", now=now)
        self._create_membership(username="bob", now=now)

        election = Election.objects.create(
            name="Draft election",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=5),
            end_datetime=now + datetime.timedelta(days=6),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")

        committee_group = FreeIPAGroup(
            settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            {"member_user": ["alice"]},
        )

        def _get_group(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
            if cn != committee_group.cn:
                raise FreeIPAMisconfiguredError("Unknown group")
            return committee_group

        def _get_user(username: str) -> FreeIPAUser:
            return FreeIPAUser(username, {"uid": [username], "memberof_group": []})

        with (
            patch("core.elections_eligibility.get_freeipa_group_for_elections", side_effect=_get_group),
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
        ):
            self.client.get(reverse("election-nomination-users-search", args=[election.id]))

    def test_candidate_search_blocks_when_freeipa_unavailable(self) -> None:
        now = timezone.now()
        self._create_membership(username="alice", now=now)

        election = Election.objects.create(
            name="Draft election",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=5),
            end_datetime=now + datetime.timedelta(days=6),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        self._login_as_freeipa_user("admin")
        self._grant_manage_permission("admin")

        def _get_user(username: str) -> FreeIPAUser:
            return FreeIPAUser(username, {"uid": [username], "memberof_group": []})

        with (
            patch(
                "core.elections_eligibility.get_freeipa_group_for_elections",
                side_effect=FreeIPAUnavailableError("unavailable"),
            ),
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
        ):
            resp = self.client.get(reverse("election-eligible-users-search", args=[election.id]))

        self.assertEqual(resp.status_code, 503)
        self.assertIn("freeipa", str(resp.json().get("error", "")).lower())

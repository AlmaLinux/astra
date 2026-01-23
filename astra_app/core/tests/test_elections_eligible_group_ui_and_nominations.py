from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAMisconfiguredError
from core.models import Candidate, Election, FreeIPAPermissionGrant, Membership, MembershipType
from core.permissions import ASTRA_ADD_ELECTION


def _group_lookup_by_cn(*groups: SimpleNamespace):
    by_cn = {str(group.cn): group for group in groups}

    def _get_group(*, cn: str, require_fresh: bool = False) -> SimpleNamespace:
        group = by_cn.get(str(cn))
        if group is None:
            raise FreeIPAMisconfiguredError("Unknown group")
        return group

    return _get_group


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionEligibleGroupUiAndNominationTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_election_manager(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def _make_membership(self, *, election: Election, username: str) -> None:
        mt = MembershipType.objects.create(
            code=f"mt_{username}",
            name=f"MT {username}",
            votes=1,
            isIndividual=True,
            enabled=True,
        )
        m = Membership.objects.create(
            target_username=username,
            membership_type=mt,
            expires_at=timezone.now() + datetime.timedelta(days=365),
        )
        created_at = election.start_datetime - datetime.timedelta(days=10)
        Membership.objects.filter(pk=m.pk).update(created_at=created_at)

    def test_eligible_users_search_count_only_respects_eligible_group_override(self) -> None:
        self._login_as_freeipa_user("admin")
        self._grant_election_manager("admin")

        now = timezone.now()
        election = Election.objects.create(
            name="Group override election",
            description="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            quorum=0,
            status=Election.Status.draft,
            eligible_group_cn="",
        )

        self._make_membership(election=election, username="alice")
        self._make_membership(election=election, username="bob")

        restricted_group = SimpleNamespace(
            cn="restricted",
            description="",
            members=["alice"],
            member_groups=[],
            fas_group=False,
        )
        committee_group = SimpleNamespace(
            cn=settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            description="",
            members=[],
            member_groups=[],
            fas_group=False,
        )

        with patch(
            "core.backends.get_freeipa_group_for_elections",
            side_effect=_group_lookup_by_cn(restricted_group, committee_group),
        ):
            url = reverse("election-eligible-users-search", args=[election.id])
            resp = self.client.get(url, {"count_only": "1", "eligible_group_cn": "restricted"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"count": 1})

    def test_eligible_users_search_count_only_allows_clearing_group_override(self) -> None:
        self._login_as_freeipa_user("admin")
        self._grant_election_manager("admin")

        now = timezone.now()
        election = Election.objects.create(
            name="Clear override election",
            description="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            quorum=0,
            status=Election.Status.draft,
            eligible_group_cn="restricted",
        )

        self._make_membership(election=election, username="alice")
        self._make_membership(election=election, username="bob")

        restricted_group = SimpleNamespace(
            cn="restricted",
            description="",
            members=["alice"],
            member_groups=[],
            fas_group=False,
        )
        committee_group = SimpleNamespace(
            cn=settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            description="",
            members=[],
            member_groups=[],
            fas_group=False,
        )

        with patch(
            "core.backends.get_freeipa_group_for_elections",
            side_effect=_group_lookup_by_cn(restricted_group, committee_group),
        ):
            url = reverse("election-eligible-users-search", args=[election.id])
            resp = self.client.get(url, {"count_only": "1", "eligible_group_cn": ""})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"count": 2})

    def test_save_draft_allows_nominations_outside_voting_group(self) -> None:
        self._login_as_freeipa_user("admin")
        self._grant_election_manager("admin")

        now = timezone.now()
        election = Election.objects.create(
            name="Draft election",
            description="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            quorum=0,
            status=Election.Status.draft,
            eligible_group_cn="restricted",
        )

        # Alice is membership-eligible and in the voting group.
        self._make_membership(election=election, username="alice")
        # Bob is membership-eligible but not in the voting group; he can still nominate.
        self._make_membership(election=election, username="bob")

        restricted_group = SimpleNamespace(
            cn="restricted",
            description="",
            members=["alice"],
            member_groups=[],
            fas_group=False,
        )
        committee_group = SimpleNamespace(
            cn=settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            description="",
            members=[],
            member_groups=[],
            fas_group=False,
        )

        start_local = timezone.localtime(election.start_datetime).replace(tzinfo=None)
        end_local = timezone.localtime(election.end_datetime).replace(tzinfo=None)

        post_data: dict[str, str] = {
            "action": "save_draft",
            "name": election.name,
            "description": "",
            "url": "",
            "start_datetime": start_local.strftime("%Y-%m-%dT%H:%M"),
            "end_datetime": end_local.strftime("%Y-%m-%dT%H:%M"),
            "number_of_seats": "1",
            "quorum": "0",
            "eligible_group_cn": "restricted",
            "subject": "",
            "html_content": "",
            "text_content": "",
            "candidates-TOTAL_FORMS": "1",
            "candidates-INITIAL_FORMS": "0",
            "candidates-MIN_NUM_FORMS": "0",
            "candidates-MAX_NUM_FORMS": "1000",
            "candidates-0-freeipa_username": "alice",
            "candidates-0-nominated_by": "bob",
            "candidates-0-description": "",
            "candidates-0-url": "",
            "groups-TOTAL_FORMS": "0",
            "groups-INITIAL_FORMS": "0",
            "groups-MIN_NUM_FORMS": "0",
            "groups-MAX_NUM_FORMS": "1000",
        }

        with patch(
            "core.backends.get_freeipa_group_for_elections",
            side_effect=_group_lookup_by_cn(restricted_group, committee_group),
        ):
            resp = self.client.post(reverse("election-edit", args=[election.id]), data=post_data)

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Candidate.objects.filter(election=election, freeipa_username="alice", nominated_by="bob").exists()
        )

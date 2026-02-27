
import datetime
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Membership, MembershipType
from core.permissions import ASTRA_ADD_ELECTION
from core.tests.utils_test_data import ensure_core_categories


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=90)
class ElectionEditEligibleUsersSearchTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_manage_elections(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def test_create_mode_search_returns_usernames_even_if_freeipa_lookup_fails(self) -> None:
        now = timezone.now()

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        membership = Membership.objects.create(
            target_username="alice",
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=200))

        self._login_as_freeipa_user("admin")
        self._grant_manage_elections("admin")

        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def get_user(username: str):
            if username == "admin":
                return admin_user
            raise RuntimeError("FreeIPA is down")

        start_dt = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user):
            resp = self.client.get(
                reverse("election-eligible-users-search", args=[0]),
                data={"q": "ali", "start_datetime": start_dt},
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["results"], [{"id": "alice", "text": "alice"}])

    def test_create_mode_search_defaults_start_datetime_to_now(self) -> None:
        now = timezone.now()

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        membership = Membership.objects.create(
            target_username="alice",
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=200))

        self._login_as_freeipa_user("admin")
        self._grant_manage_elections("admin")

        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def get_user(username: str):
            if username == "admin":
                return admin_user
            raise RuntimeError("FreeIPA is down")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user):
            resp = self.client.get(
                reverse("election-eligible-users-search", args=[0]),
                data={"q": "ali"},
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["results"], [{"id": "alice", "text": "alice"}])

    def test_create_mode_search_with_blank_query_returns_first_results(self) -> None:
        now = timezone.now()

        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            category_id="individual",
            enabled=True,
        )
        membership = Membership.objects.create(
            target_username="alice",
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=200))

        self._login_as_freeipa_user("admin")
        self._grant_manage_elections("admin")

        admin_user = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})

        def get_user(username: str):
            if username == "admin":
                return admin_user
            raise RuntimeError("FreeIPA is down")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user):
            resp = self.client.get(
                reverse("election-eligible-users-search", args=[0]),
                data={"q": ""},
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["results"], [{"id": "alice", "text": "alice"}])

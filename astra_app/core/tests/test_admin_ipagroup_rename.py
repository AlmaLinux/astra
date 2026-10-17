import datetime
from unittest.mock import patch

import requests
from django.contrib.admin.models import CHANGE, LogEntry
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from python_freeipa import exceptions

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
from core.models import Election, FreeIPAPermissionGrant, MembershipType
from core.tests.utils_test_data import ensure_core_categories


class _FakeClient:
    def __init__(self) -> None:
        self.group_mod_calls: list[tuple[str, dict[str, object]]] = []
        # Call index -> exception raised by that group_mod call.
        self.errors: dict[int, Exception] = {}

    def group_mod(self, cn: str, **kwargs: object) -> dict[str, object]:
        index = len(self.group_mod_calls)
        self.group_mod_calls.append((cn, dict(kwargs)))
        if index in self.errors:
            raise self.errors[index]
        return {"result": {}}


RENAME = ("marketing-sig", {"o_rename": "promo-sig"})
REVERT = ("promo-sig", {"o_rename": "marketing-sig"})


def _fake_retry(fake_client: _FakeClient):
    return patch(
        "core.freeipa.group._with_freeipa_service_client_retry",
        side_effect=lambda _get_client, fn: fn(fake_client),
    )


class FreeIPAGroupRenameTests(SimpleTestCase):
    def test_rename_calls_group_mod_and_invalidates_related_caches(self) -> None:
        group = FreeIPAGroup(
            "marketing-sig",
            {
                "cn": ["marketing-sig"],
                "member_user": ["bob"],
                "member_group": ["marketing-sig-core"],
                "membermanager_user": ["carol"],
                "membermanager_group": ["sig-leads"],
            },
        )
        # Fresh listing after the rename: these groups reference the new name.
        parent = FreeIPAGroup("sigs", {"cn": ["sigs"], "member_group": ["promo-sig"]})
        sponsored = FreeIPAGroup("artwork", {"cn": ["artwork"], "membermanager_group": ["promo-sig"]})
        unrelated = FreeIPAGroup("infra", {"cn": ["infra"], "member_group": ["other"]})
        fake_client = _FakeClient()

        with (
            _fake_retry(fake_client),
            patch.object(group, "member_usernames_recursive", return_value={"bob", "dave"}),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[parent, sponsored, unrelated]),
            patch("core.freeipa.group._invalidate_group_cache") as invalidate_group,
            patch("core.freeipa.group._invalidate_user_cache") as invalidate_user,
            patch("core.freeipa.group._invalidate_groups_list_cache") as invalidate_list,
            patch("core.freeipa.group._invalidate_users_list_cache") as invalidate_users_list,
        ):
            group.rename("promo-sig")

        self.assertEqual(group.cn, "promo-sig")
        self.assertEqual(fake_client.group_mod_calls, [RENAME])
        self.assertEqual(
            {c.args[0] for c in invalidate_group.call_args_list},
            {"marketing-sig", "promo-sig", "marketing-sig-core", "sigs", "artwork"},
        )
        self.assertEqual({c.args[0] for c in invalidate_user.call_args_list}, {"bob", "dave", "carol"})
        invalidate_list.assert_called()
        # The cached user listing embeds every user's group names.
        invalidate_users_list.assert_called()

    def test_rename_rejected_by_freeipa_keeps_old_name(self) -> None:
        group = FreeIPAGroup("marketing-sig", {"cn": ["marketing-sig"]})
        fake_client = _FakeClient()
        fake_client.errors[0] = exceptions.DuplicateEntry('group with name "promo-sig" already exists')

        with (
            _fake_retry(fake_client),
            patch.object(group, "member_usernames_recursive", return_value=set()),
            patch("core.freeipa.group._invalidate_group_cache") as invalidate_group,
            self.assertRaises(exceptions.DuplicateEntry),
        ):
            group.rename("promo-sig")

        # The view relies on cn changing only once FreeIPA accepted the rename.
        self.assertEqual(group.cn, "marketing-sig")
        invalidate_group.assert_not_called()


class AdminIPAGroupRenameTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        self.admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        self.group = FreeIPAGroup(
            "marketing-sig",
            {"cn": ["marketing-sig"], "description": ["Marketing SIG"], "member_user": ["bob"]},
        )
        self.fake_client = _FakeClient()
        self._login("alice")

    def _login(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _request(self, method: str, group: FreeIPAGroup, data: dict[str, str] | None = None, *, viewer: FreeIPAUser | None = None):
        old_cn = group.cn
        url = reverse("admin:auth_ipagroup_rename", args=[old_cn])
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer or self.admin_user),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[group]),
            patch("core.freeipa.group.FreeIPAGroup.get", side_effect=lambda cn: group if cn == old_cn else None),
            patch.object(group, "member_usernames_recursive", return_value={"bob"}),
            _fake_retry(self.fake_client),
        ):
            if method == "get":
                return self.client.get(url)
            return self.client.post(url, data=data or {})

    def _create_references(self) -> tuple[MembershipType, list[Election], FreeIPAPermissionGrant]:
        membership_type, _ = MembershipType.objects.update_or_create(
            code="marketing_rename_test",
            defaults={
                "name": "Marketing",
                "group_cn": "marketing-sig",
                "votes": 0,
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        now = timezone.now()
        elections = [
            Election.objects.create(
                name=f"{status} election",
                start_datetime=now,
                end_datetime=now + datetime.timedelta(days=7),
                status=status,
                eligible_group_cn="marketing-sig",
            )
            for status in (Election.Status.draft, Election.Status.tallied)
        ]
        grant = FreeIPAPermissionGrant.objects.create(
            permission="astra.view_membership",
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name="marketing-sig",
        )
        return membership_type, elections, grant

    def test_get_shows_reference_counts(self) -> None:
        self._create_references()

        resp = self._request("get", self.group)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.context["reference_counts"],
            {"membership_types": 1, "elections": 2, "permission_grants": 1},
        )

    def test_rename_updates_freeipa_and_local_references(self) -> None:
        membership_type, elections, grant = self._create_references()
        unrelated_election = Election.objects.create(
            name="Other election",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + datetime.timedelta(days=7),
            eligible_group_cn="infra-sig",
        )
        user_grant = FreeIPAPermissionGrant.objects.create(
            permission="astra.view_membership",
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="marketing-sig",
        )

        # FreeIPA lowercases group names, so local references must match.
        resp = self._request("post", self.group, {"new_cn": " Promo-SIG "})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("admin:auth_ipagroup_change", args=["promo-sig"]))
        self.assertEqual(self.fake_client.group_mod_calls, [RENAME])

        membership_type.refresh_from_db()
        self.assertEqual(membership_type.group_cn, "promo-sig")
        for election in elections:
            election.refresh_from_db()
            self.assertEqual(election.eligible_group_cn, "promo-sig")
        grant.refresh_from_db()
        self.assertEqual(grant.principal_name, "promo-sig")

        unrelated_election.refresh_from_db()
        self.assertEqual(unrelated_election.eligible_group_cn, "infra-sig")
        user_grant.refresh_from_db()
        self.assertEqual(user_grant.principal_name, "marketing-sig")

        entry = LogEntry.objects.get(action_flag=CHANGE, object_id="promo-sig")
        self.assertIn("marketing-sig", entry.get_change_message())

    def _assert_nothing_renamed_locally(self, membership_type: MembershipType, elections: list[Election], grant: FreeIPAPermissionGrant) -> None:
        membership_type.refresh_from_db()
        self.assertEqual(membership_type.group_cn, "marketing-sig")
        for election in elections:
            election.refresh_from_db()
            self.assertEqual(election.eligible_group_cn, "marketing-sig")
        grant.refresh_from_db()
        self.assertEqual(grant.principal_name, "marketing-sig")
        self.assertFalse(LogEntry.objects.filter(action_flag=CHANGE).exists())

    def test_rename_refused_for_group_configured_in_settings(self) -> None:
        group = FreeIPAGroup("membership-committee", {"cn": ["membership-committee"], "description": ["d"]})

        resp = self._request("post", group, {"new_cn": "renamed-committee"})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "cannot be renamed")
        self.assertEqual(self.fake_client.group_mod_calls, [])

    @override_settings(FREEIPA_MEMBERSHIP_COMMITTEE_GROUP="Membership-Committee")
    def test_settings_group_match_ignores_case(self) -> None:
        # FreeIPA lowercases names, but settings come from env vars as typed.
        group = FreeIPAGroup("membership-committee", {"cn": ["membership-committee"], "description": ["d"]})

        resp = self._request("post", group, {"new_cn": "renamed-committee"})

        self.assertContains(resp, "cannot be renamed")
        self.assertEqual(self.fake_client.group_mod_calls, [])

    def test_rejects_new_name_configured_in_settings(self) -> None:
        # Taking over a configured name would hand the group that role, e.g. the
        # team-leads sync would rewrite its members.
        for new_cn in ("team_leads", "Membership-Committee"):
            with self.subTest(new_cn=new_cn):
                resp = self._request("post", self.group, {"new_cn": new_cn})

                self.assertEqual(resp.status_code, 200)
                self.assertIn("new_cn", resp.context["form"].errors)
        self.assertEqual(self.fake_client.group_mod_calls, [])

    def test_rejects_new_name_already_referenced_in_astra(self) -> None:
        # Leftover references (e.g. grants for a deleted group) would silently
        # attach to the renamed group.
        stale_grant = FreeIPAPermissionGrant.objects.create(
            permission="astra.add_membership",
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name="promo-sig",
        )

        resp = self._request("post", self.group, {"new_cn": "promo-sig"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("new_cn", resp.context["form"].errors)
        self.assertEqual(self.fake_client.group_mod_calls, [])
        self.assertTrue(FreeIPAPermissionGrant.objects.filter(pk=stale_grant.pk, principal_name="promo-sig").exists())

    def test_freeipa_rejection_rolls_back_local_references(self) -> None:
        references = self._create_references()
        self.fake_client.errors[0] = exceptions.DuplicateEntry('group with name "promo-sig" already exists')

        with self.assertLogs("core.admin", level="ERROR"):
            resp = self._request("post", self.group, {"new_cn": "promo-sig"})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")
        self.assertEqual(self.fake_client.group_mod_calls, [RENAME])
        self._assert_nothing_renamed_locally(*references)

    def test_freeipa_timeout_warns_rename_state_is_unknown(self) -> None:
        # A timeout may arrive after FreeIPA applied the rename, so the admin must
        # check FreeIPA instead of assuming nothing happened.
        references = self._create_references()
        self.fake_client.errors[0] = requests.exceptions.ReadTimeout("read timed out")

        with self.assertLogs("core.admin", level="ERROR"):
            resp = self._request("post", self.group, {"new_cn": "promo-sig"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.fake_client.group_mod_calls, [RENAME])
        self.assertContains(resp, "Check FreeIPA")
        self._assert_nothing_renamed_locally(*references)

    def test_failure_after_freeipa_rename_reverts_freeipa(self) -> None:
        # Stands in for anything failing after FreeIPA accepted the rename (cache
        # writes, the commit itself): local references roll back, so FreeIPA must too.
        references = self._create_references()

        with (
            patch(
                "core.freeipa.group._invalidate_groups_list_cache",
                side_effect=[DatabaseError("cache table locked"), None],
            ),
            self.assertLogs("core.admin", level="ERROR"),
        ):
            resp = self._request("post", self.group, {"new_cn": "promo-sig"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.fake_client.group_mod_calls, [RENAME, REVERT])
        self.assertContains(resp, "reverted")
        self.assertContains(resp, "cache table locked")
        self._assert_nothing_renamed_locally(*references)

    def test_failed_revert_reports_manual_fix(self) -> None:
        references = self._create_references()
        self.fake_client.errors[1] = exceptions.FreeIPAError("connection reset")

        with (
            patch(
                "core.freeipa.group._invalidate_groups_list_cache",
                side_effect=DatabaseError("cache table locked"),
            ),
            self.assertLogs("core.admin", level="CRITICAL"),
        ):
            resp = self._request("post", self.group, {"new_cn": "promo-sig"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.fake_client.group_mod_calls, [RENAME, REVERT])
        self.assertContains(resp, "manually")
        self._assert_nothing_renamed_locally(*references)

    def test_rejects_empty_or_unchanged_name(self) -> None:
        for new_cn in ("", "  ", "Marketing-SIG"):
            with self.subTest(new_cn=new_cn):
                resp = self._request("post", self.group, {"new_cn": new_cn})

                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.context["form"].errors)
        self.assertEqual(self.fake_client.group_mod_calls, [])

    def test_non_admin_cannot_rename(self) -> None:
        self._login("bob")
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})

        resp = self._request("post", self.group, {"new_cn": "promo-sig"}, viewer=bob)

        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("admin:login"), resp["Location"])
        self.assertEqual(self.fake_client.group_mod_calls, [])

    def _get_change_form(self, group: FreeIPAGroup):
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self.admin_user),
            patch("core.admin.FreeIPAUser.all", return_value=[self.admin_user]),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[group]),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            return self.client.get(reverse("admin:auth_ipagroup_change", args=[group.cn]))

    def test_change_form_links_to_rename(self) -> None:
        resp = self._get_change_form(self.group)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("admin:auth_ipagroup_rename", args=["marketing-sig"]))

    def test_change_form_disables_rename_for_group_configured_in_settings(self) -> None:
        group = FreeIPAGroup("membership-committee", {"cn": ["membership-committee"], "description": ["d"]})

        resp = self._get_change_form(group)

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, reverse("admin:auth_ipagroup_rename", args=["membership-committee"]))
        self.assertContains(resp, "configured in settings and cannot be renamed")
        self.assertContains(resp, 'aria-disabled="true"')

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core import views_users
from core.freeipa.client import clear_current_viewer_username, set_current_viewer_username
from core.freeipa.user import FreeIPAUser
from core.permissions import ASTRA_VIEW_MEMBERSHIP


class FASIsPrivateAnonymizeTests(TestCase):
    def test_private_user_anonymizes_when_attr_is_fasisprivate(self) -> None:
        set_current_viewer_username("alice")
        try:
            bob = FreeIPAUser(
                "bob",
                {
                    "uid": ["bob"],
                    "givenname": ["Bob"],
                    "sn": ["User"],
                    "mail": ["bob@example.org"],
                    "fasPronoun": ["they/them"],
                    "fasisprivate": ["TRUE"],
                },
            )
        finally:
            clear_current_viewer_username()

        self.assertEqual(bob.full_name, "bob")
        self.assertNotIn("fasPronoun", bob._user_data)

    def test_private_user_cache_is_not_poisoned_by_other_viewer(self) -> None:
        cache_key = "freeipa_user_bob"
        full_data: dict[str, object] = {
            "uid": ["bob"],
            "givenname": ["Bob"],
            "sn": ["User"],
            "mail": ["bob@example.org"],
            "fasIsPrivate": ["TRUE"],
        }
        cache.set(cache_key, full_data)

        try:
            set_current_viewer_username("alice")
            try:
                bob_for_alice = FreeIPAUser.get("bob")
                self.assertIsNotNone(bob_for_alice)
                assert bob_for_alice is not None
                self.assertEqual(bob_for_alice.full_name, "bob")
            finally:
                clear_current_viewer_username()

            set_current_viewer_username("bob")
            try:
                bob_for_bob = FreeIPAUser.get("bob")
                self.assertIsNotNone(bob_for_bob)
                assert bob_for_bob is not None
                self.assertEqual(bob_for_bob.full_name, "Bob User")
            finally:
                clear_current_viewer_username()
        finally:
            cache.delete(cache_key)

    def test_user_profile_detail_anonymizes_private_user_for_non_self_viewer(self) -> None:
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["bob"]))
        request.user = SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: "alice",
            groups_list=[],
        )

        set_current_viewer_username("alice")
        try:
            bob = FreeIPAUser(
                "bob",
                {
                    "uid": ["bob"],
                    "givenname": ["Bob"],
                    "sn": ["User"],
                    "mail": ["bob@example.org"],
                    "fasPronoun": ["they/them"],
                    "fasWebsiteUrl": ["https://example.invalid"],
                    "fasIsPrivate": ["TRUE"],
                    "memberof_group": ["packagers"],
                },
            )
        finally:
            clear_current_viewer_username()

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=bob),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch(
                "core.views_users.membership_review_permissions",
                autospec=True,
                return_value={
                    "membership_can_view": False,
                    "membership_can_add": False,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
        ):
            resp = views_users.user_profile_detail_api(request, "bob")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertEqual(payload["summary"]["username"], "bob")
        self.assertNotEqual(payload["summary"]["fullName"], "Bob User")
        self.assertEqual(payload["summary"]["email"], "")
        self.assertEqual(payload["summary"]["pronouns"], "")
        self.assertEqual(payload["summary"]["websiteUrls"], [])
        self.assertEqual(payload["summary"]["socialProfiles"], [])
        self.assertFalse(payload["membership"]["showCard"])

    def test_user_profile_detail_redacts_private_fields_from_full_loader_for_non_reviewer(self) -> None:
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["bob"]))
        request.user = SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: "alice",
            groups_list=[],
        )

        full_user_data = {
            "uid": ["bob"],
            "givenname": ["Bob"],
            "sn": ["User"],
            "mail": ["bob@example.org"],
            "fasPronoun": ["they/them"],
            "fasWebsiteUrl": ["https://example.invalid"],
            "fasGitHubUsername": ["bobgh"],
            "fasIsPrivate": ["TRUE"],
            "memberof_group": ["packagers"],
        }

        def load_user(username: str, *, respect_privacy: bool = False) -> FreeIPAUser:
            return FreeIPAUser(username, full_user_data, respect_privacy=respect_privacy)

        set_current_viewer_username("alice")
        try:
            with (
                patch("core.views_users._get_full_user", side_effect=load_user),
                patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
                patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
                patch(
                    "core.views_users.membership_review_permissions",
                    autospec=True,
                    return_value={
                        "membership_can_view": False,
                        "membership_can_add": False,
                        "membership_can_change": False,
                        "membership_can_delete": False,
                    },
                ),
            ):
                response = views_users.user_profile_detail_api(request, "bob")
        finally:
            clear_current_viewer_username()

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["summary"]["fullName"], "bob")
        self.assertEqual(payload["summary"]["email"], "")
        self.assertEqual(payload["summary"]["pronouns"], "")
        self.assertEqual(payload["summary"]["websiteUrls"], [])
        self.assertEqual(payload["summary"]["githubUsername"], "")

    def test_private_profile_membership_card_uses_effective_membership_permission(self) -> None:
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["bob"]))
        request.user = SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: "reviewer",
            groups_list=["astra_admins"],
            has_perm=lambda permission: permission == ASTRA_VIEW_MEMBERSHIP,
        )

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "sn": ["User"],
                "mail": ["bob@example.org"],
                "fasIsPrivate": ["TRUE"],
                "memberof_group": ["packagers"],
            },
            respect_privacy=False,
        )

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=bob),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
        ):
            response = views_users.user_profile_detail_api(request, "bob")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["summary"]["viewerIsMembershipCommittee"])
        self.assertTrue(payload["membership"]["showCard"])
        self.assertTrue(payload["membership"]["canViewHistory"])

    def test_private_profile_membership_card_denies_committee_group_without_effective_permission(self) -> None:
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["bob"]))
        request.user = SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: "reviewer",
            groups_list=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            has_perm=lambda _permission: False,
        )

        set_current_viewer_username("reviewer")
        try:
            bob = FreeIPAUser(
                "bob",
                {
                    "uid": ["bob"],
                    "givenname": ["Bob"],
                    "sn": ["User"],
                    "mail": ["bob@example.org"],
                    "fasIsPrivate": ["TRUE"],
                    "memberof_group": ["packagers"],
                },
            )
        finally:
            clear_current_viewer_username()

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=bob),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
        ):
            response = views_users.user_profile_detail_api(request, "bob")

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertFalse(payload["summary"]["viewerIsMembershipCommittee"])
        self.assertFalse(payload["membership"]["showCard"])
        self.assertFalse(payload["membership"]["canViewHistory"])

    def test_user_profile_detail_shows_private_user_fields_for_self_viewer(self) -> None:
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["bob"]))
        request.user = SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: "bob",
            groups_list=[],
        )

        set_current_viewer_username("bob")
        try:
            bob = FreeIPAUser(
                "bob",
                {
                    "uid": ["bob"],
                    "givenname": ["Bob"],
                    "sn": ["User"],
                    "mail": ["bob@example.org"],
                    "fasPronoun": ["they/them"],
                    "fasWebsiteUrl": ["example.invalid/path"],
                    "fasIsPrivate": ["TRUE"],
                    "memberof_group": ["packagers"],
                },
            )
        finally:
            clear_current_viewer_username()

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=bob),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch(
                "core.views_users.membership_review_permissions",
                autospec=True,
                return_value={
                    "membership_can_view": False,
                    "membership_can_add": False,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
        ):
            resp = views_users.user_profile_detail_api(request, "bob")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertEqual(payload["summary"]["fullName"], "Bob User")
        self.assertEqual(payload["summary"]["email"], "bob@example.org")
        self.assertEqual(payload["summary"]["pronouns"], "they/them")
        self.assertEqual(payload["summary"]["websiteUrls"], ["example.invalid/path"])
        self.assertTrue(payload["membership"]["showCard"])

    def test_user_profile_detail_restores_committee_visible_private_fields_only(self) -> None:
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["bob"]))
        request.user = SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: "reviewer",
            groups_list=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        set_current_viewer_username("reviewer")
        try:
            bob = FreeIPAUser(
                "bob",
                {
                    "uid": ["bob"],
                    "givenname": ["Bob"],
                    "sn": ["User"],
                    "mail": ["bob@example.org"],
                    "fasPronoun": ["they/them"],
                    "fasWebsiteUrl": ["example.invalid/path"],
                    "fasCountryCode": ["US"],
                    "fasIsPrivate": ["TRUE"],
                    "memberof_group": ["packagers"],
                },
            )
        finally:
            clear_current_viewer_username()

        full_profile = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "sn": ["User"],
                "mail": ["bob@example.org"],
                "fasPronoun": ["they/them"],
                "fasWebsiteUrl": ["example.invalid/path"],
                "fasCountryCode": ["US"],
                "fasIsPrivate": ["TRUE"],
                "memberof_group": ["packagers"],
            },
        )

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=bob),
            patch("core.views_users.FreeIPAUser.get", autospec=True, return_value=full_profile),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch(
                "core.views_users.membership_review_permissions",
                autospec=True,
                return_value={
                    "membership_can_view": True,
                    "membership_can_add": False,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
        ):
            resp = views_users.user_profile_detail_api(request, "bob")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertEqual(payload["summary"]["fullName"], "bob")
        self.assertEqual(payload["summary"]["email"], "bob@example.org")
        self.assertEqual(payload["summary"]["websiteUrls"], [])
        self.assertEqual(payload["summary"]["socialProfiles"], [])
        self.assertTrue(payload["membership"]["showCard"])

    def test_user_profile_detail_shows_membership_card_for_private_profile_committee_viewer_without_groups_list(self) -> None:
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["bob"]))
        request.user = SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: "reviewer",
            username="reviewer",
        )

        set_current_viewer_username("reviewer")
        try:
            bob = FreeIPAUser(
                "bob",
                {
                    "uid": ["bob"],
                    "givenname": ["Bob"],
                    "sn": ["User"],
                    "mail": ["bob@example.org"],
                    "fasIsPrivate": ["TRUE"],
                    "memberof_group": ["packagers"],
                },
            )
        finally:
            clear_current_viewer_username()

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=bob),
            patch(
                "core.views_users.FreeIPAUser.get",
                autospec=True,
                side_effect=lambda username, **_kwargs: reviewer if username == "reviewer" else None,
            ),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch(
                "core.views_users.membership_review_permissions",
                autospec=True,
                return_value={
                    "membership_can_view": True,
                    "membership_can_add": False,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
        ):
            resp = views_users.user_profile_detail_api(request, "bob")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload["membership"]["showCard"])

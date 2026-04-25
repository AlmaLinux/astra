import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from core import views_users
from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType, Note
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class UserProfileFreeIPARequestFanoutTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

        self.factory = RequestFactory()

    def tearDown(self) -> None:
        cache.clear()
        super().tearDown()

    def _reviewer_user(self) -> FreeIPAUser:
        return FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

    def _target_user(self, username: str = "alice") -> FreeIPAUser:
        return FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": [username.title()],
                "sn": ["Example"],
                "displayname": [f"{username.title()} Example"],
                "mail": [f"{username}@example.com"],
                "memberof_group": [],
                "fasIsPrivate": ["FALSE"],
            },
        )

    def _request_for_user(self, user: object, *, username: str = "alice"):
        request = self.factory.get(f"/user/{username}/")
        request.user = user
        viewer_username = ""
        if hasattr(user, "username"):
            viewer_username = str(user.username or "").strip()
        request.session = {"_freeipa_username": viewer_username}
        return request

    def _viewer_request_user(self, *, include_email: bool) -> SimpleNamespace:
        viewer = SimpleNamespace(
            username="viewer",
            is_authenticated=True,
            get_username=lambda: "viewer",
            groups_list=[],
            has_perm=lambda permission: permission == ASTRA_VIEW_MEMBERSHIP,
        )
        if include_email:
            viewer.email = "viewer@example.com"
        return viewer

    def _create_aggregate_profile_notes(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=membership_request,
            username="alice",
            content="Target note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="viewer",
            content="Viewer note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="ghost-user",
            content="Ghost note",
            action={},
        )

    def _render_profile_with_aggregate_trace(
        self,
        *,
        request_user: SimpleNamespace,
        lightweight_users_by_username: dict[str, FreeIPAUser],
    ) -> tuple[object, list[str], list[tuple[str, ...]], dict[str, object]]:
        request = self._request_for_user(request_user)
        target_fetches: list[str] = []
        aggregate_lookup_usernames: list[tuple[str, ...]] = []

        def _record_target_fetch(username: str) -> FreeIPAUser:
            target_fetches.append(username)
            return self._target_user(username)

        def _record_lightweight_lookup(usernames: set[str]) -> dict[str, FreeIPAUser]:
            normalized = tuple(sorted(usernames))
            aggregate_lookup_usernames.append(normalized)
            return {
                username: lightweight_users_by_username[username]
                for username in usernames
                if username in lightweight_users_by_username
            }

        with (
            patch("core.views_users._get_full_user", side_effect=_record_target_fetch),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.resolve_avatar_urls_for_users", return_value=({}, 0, 0)),
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=_record_lightweight_lookup,
            ),
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.get",
                side_effect=AssertionError("profile aggregate note rendering must stay on the shared lightweight author-lookup path"),
            ),
        ):
            response = views_users.user_profile_api(request, "alice")

        return response, target_fetches, aggregate_lookup_usernames, json.loads(response.content)

    def test_reviewer_profile_render_cold_cache_avoids_badge_username_fanout(self) -> None:
        MembershipRequest.objects.create(
            requested_username="alpha",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="beta",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
        )

        request = self._request_for_user(self._reviewer_user())

        with (
            patch(
                "core.membership.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=AssertionError("cold-cache profile render must not fan out badge usernames"),
            ),
            patch(
                "core.membership.FreeIPAUser.get",
                side_effect=AssertionError("cold-cache profile render must not use badge FreeIPA get lookups"),
            ),
            patch(
                "core.membership.FreeIPAUser.all",
                side_effect=AssertionError("cold-cache profile render must not use badge FreeIPA all lookups"),
            ),
            patch("core.views_users._get_full_user", return_value=self._target_user("alice")),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
        ):
            response = views_users.user_profile(request, "alice")

        self.assertEqual(response.status_code, 200)

    def test_reviewer_profile_render_after_badge_cache_expiry_avoids_badge_username_fanout(self) -> None:
        from core.membership import get_membership_review_badge_counts

        MembershipRequest.objects.create(
            requested_username="alpha",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="beta",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
        )

        warmed_counts = get_membership_review_badge_counts()
        self.assertEqual(warmed_counts, {"pending_count": 1, "on_hold_count": 1})
        cache.delete("membership_review_badge_counts:v1")

        request = self._request_for_user(self._reviewer_user())

        with (
            patch(
                "core.membership.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=AssertionError("expired-cache profile render must not fan out badge usernames"),
            ),
            patch(
                "core.membership.FreeIPAUser.get",
                side_effect=AssertionError("expired-cache profile render must not use badge FreeIPA get lookups"),
            ),
            patch(
                "core.membership.FreeIPAUser.all",
                side_effect=AssertionError("expired-cache profile render must not use badge FreeIPA all lookups"),
            ),
            patch("core.views_users._get_full_user", return_value=self._target_user("alice")),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
        ):
            response = views_users.user_profile(request, "alice")

        self.assertEqual(response.status_code, 200)

    def test_reviewer_profile_render_when_badge_cache_backend_fails_avoids_badge_username_fanout(self) -> None:
        MembershipRequest.objects.create(
            requested_username="alpha",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )

        request = self._request_for_user(self._reviewer_user())
        failing_cache = Mock()
        failing_cache.get.side_effect = RuntimeError("cache down")
        failing_cache.set.side_effect = RuntimeError("cache down")

        with (
            patch("core.membership.cache", failing_cache),
            patch(
                "core.membership.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=AssertionError("cache-failure profile render must not fan out badge usernames"),
            ),
            patch(
                "core.membership.FreeIPAUser.get",
                side_effect=AssertionError("cache-failure profile render must not use badge FreeIPA get lookups"),
            ),
            patch(
                "core.membership.FreeIPAUser.all",
                side_effect=AssertionError("cache-failure profile render must not use badge FreeIPA all lookups"),
            ),
            patch("core.views_users._get_full_user", return_value=self._target_user("alice")),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
        ):
            response = views_users.user_profile(request, "alice")

        self.assertEqual(response.status_code, 200)

    def test_membership_view_only_profile_render_does_not_compute_badge_counts(self) -> None:
        viewer = SimpleNamespace(
            username="viewer",
            is_authenticated=True,
            get_username=lambda: "viewer",
            groups_list=[],
            has_perm=lambda permission: permission == ASTRA_VIEW_MEMBERSHIP,
        )
        request = self._request_for_user(viewer)

        with (
            patch(
                "core.context_processors.get_membership_review_badge_counts",
                side_effect=AssertionError("view-only profile render must not compute reviewer badge counts"),
            ),
            patch("core.views_users._get_full_user", return_value=self._target_user("alice")),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
        ):
            response = views_users.user_profile(request, "alice")

        self.assertEqual(response.status_code, 200)

    def test_committee_fallback_stays_cold_when_request_user_groups_are_present(self) -> None:
        reviewer = SimpleNamespace(
            username="reviewer",
            is_authenticated=True,
            get_username=lambda: "reviewer",
            groups_list=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )
        request = self._request_for_user(reviewer)

        with (
            patch(
                "core.views_users.FreeIPAUser.get",
                side_effect=AssertionError("groups_list should keep the committee fallback cold"),
            ),
            patch("core.views_users._get_full_user", return_value=self._target_user("alice")),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.resolve_avatar_urls_for_users", return_value=({}, 0, 0)),
        ):
            response = views_users.user_profile_api(request, "alice")

        self.assertEqual(response.status_code, 200)

    def test_committee_fallback_fetches_viewer_once_when_groups_are_missing(self) -> None:
        reviewer = self._reviewer_user()
        request_user = SimpleNamespace(
            username="reviewer",
            is_authenticated=True,
            get_username=lambda: "reviewer",
        )
        request = self._request_for_user(request_user)

        with (
            patch("core.views_users.FreeIPAUser.get", return_value=reviewer) as get_mock,
            patch("core.views_users._get_full_user", return_value=self._target_user("alice")),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.resolve_avatar_urls_for_users", return_value=({}, 0, 0)),
        ):
            response = views_users.user_profile_api(request, "alice")

        self.assertEqual(response.status_code, 200)
        get_mock.assert_called_once_with("reviewer")

    def test_profile_route_reuses_target_and_avatar_safe_viewer_for_aggregate_notes(self) -> None:
        self._create_aggregate_profile_notes()

        response, target_fetches, aggregate_lookup_usernames, payload = self._render_profile_with_aggregate_trace(
            request_user=self._viewer_request_user(include_email=True),
            lightweight_users_by_username={},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(target_fetches, ["alice"])
        self.assertEqual(aggregate_lookup_usernames, [])
        notes = payload["membership"]["notes"]
        self.assertEqual(notes["targetType"], "user")
        self.assertEqual(notes["target"], "alice")
        self.assertTrue(notes["canView"])
        self.assertFalse(notes["canWrite"])
        encoded_payload = json.dumps(payload)
        self.assertNotIn("Target note", encoded_payload)
        self.assertNotIn("Viewer note", encoded_payload)
        self.assertNotIn("Ghost note", encoded_payload)

    def test_profile_route_skips_username_only_viewer_reuse_for_aggregate_notes(self) -> None:
        self._create_aggregate_profile_notes()
        viewer_from_lookup = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "displayname": ["Viewer Example"],
                "mail": ["viewer@example.com"],
            },
        )

        response, target_fetches, aggregate_lookup_usernames, payload = self._render_profile_with_aggregate_trace(
            request_user=self._viewer_request_user(include_email=False),
            lightweight_users_by_username={"viewer": viewer_from_lookup},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(target_fetches, ["alice"])
        self.assertEqual(aggregate_lookup_usernames, [])
        notes = payload["membership"]["notes"]
        self.assertEqual(notes["targetType"], "user")
        self.assertEqual(notes["target"], "alice")
        self.assertTrue(notes["canView"])
        self.assertFalse(notes["canWrite"])
        encoded_payload = json.dumps(payload)
        self.assertNotIn("Target note", encoded_payload)
        self.assertNotIn("Viewer note", encoded_payload)
        self.assertNotIn("Ghost note", encoded_payload)
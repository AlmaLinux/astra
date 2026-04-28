
import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.membership_log_side_effects import apply_membership_log_side_effects
from core.models import FreeIPAPermissionGrant
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
    ASTRA_VIEW_USER_DIRECTORY,
)
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates


class MembershipProfileSidebarAndRequestsTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        ensure_email_templates()

        self._coc_patcher = patch("core.views_membership.user.block_action_without_coc", return_value=None)
        self._coc_patcher.start()
        self.addCleanup(self._coc_patcher.stop)
        self._country_patcher = patch("core.views_membership.user.block_action_without_country_code", return_value=None)
        self._country_patcher.start()
        self.addCleanup(self._country_patcher.stop)

        for perm in (
            ASTRA_ADD_MEMBERSHIP,
            ASTRA_CHANGE_MEMBERSHIP,
            ASTRA_DELETE_MEMBERSHIP,
            ASTRA_VIEW_MEMBERSHIP,
            ASTRA_VIEW_USER_DIRECTORY,
        ):
            FreeIPAPermissionGrant.objects.get_or_create(
                permission=perm,
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _make_user(self, username: str, *, full_name: str = "", groups: list[str] | None = None) -> FreeIPAUser:
        givenname = ""
        sn = ""
        if full_name and " " in full_name:
            givenname, sn = full_name.split(" ", 1)

        # Membership requests/renewals and settings changes are gated by a valid country.
        # Use the configured attribute name so tests stay aligned with settings.
        country_attr = settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR
        return FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": [givenname] if givenname else [],
                "sn": [sn] if sn else [],
                "cn": [full_name] if full_name else [],
                "displayname": [full_name] if full_name else [],
                "mail": [f"{username}@example.com"],
                "memberof_group": list(groups or []),
                country_attr: ["US"],
            },
        )

    def _create_membership_log_with_side_effects(self, **kwargs):
        from core.models import MembershipLog

        log = MembershipLog.objects.create(**kwargs)
        apply_membership_log_side_effects(log=log)
        return log

    def _audit_log_datatables_query(self, *, start: int = 0, length: int = 50) -> dict[str, str]:
        return {
            "draw": "1",
            "start": str(start),
            "length": str(length),
            "search[value]": "",
            "search[regex]": "false",
            "order[0][column]": "0",
            "order[0][dir]": "desc",
            "order[0][name]": "created_at",
            "columns[0][data]": "log_id",
            "columns[0][name]": "created_at",
            "columns[0][searchable]": "true",
            "columns[0][orderable]": "true",
            "columns[0][search][value]": "",
            "columns[0][search][regex]": "false",
        }

    def test_profile_shows_request_link_when_no_membership(self) -> None:
        from core.models import MembershipType

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

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        membership = resp.json()["membership"]
        self.assertTrue(membership["showCard"])
        self.assertTrue(membership["canRequestAny"])
        self.assertNotIn("requestUrl", membership)
        self.assertEqual(membership["entries"], [])
        self.assertEqual(membership["pendingEntries"], [])

    def test_profile_shows_pending_membership_request_greyed_out(self) -> None:
        from core.models import MembershipRequest, MembershipType

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
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["pendingEntries"]
        self.assertEqual(entry["badge"]["label"], "Under review")
        self.assertEqual(entry["membershipType"]["name"], "Individual")
        self.assertEqual(entry["requestId"], req.pk)
        self.assertNotIn("requestUrl", entry)
        self.assertNotIn("url", entry["badge"])

    def test_committee_viewer_sees_in_review_badge_linked_to_request(self) -> None:
        from core.models import MembershipRequest, MembershipType

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
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        alice = self._make_user("alice", full_name="Alice User")

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["pendingEntries"]
        self.assertEqual(entry["badge"]["label"], "Under review")
        self.assertEqual(entry["membershipType"]["name"], "Individual")
        self.assertEqual(entry["requestId"], req.pk)
        self.assertNotIn("requestUrl", entry)
        self.assertNotIn("url", entry["badge"])

    def test_committee_viewer_sees_active_badge_linked_to_request(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType

        mt, _created = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id=mt.code,
            status=MembershipRequest.Status.approved,
            decided_at=timezone.now(),
            decided_by_username="reviewer",
        )

        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id=mt.code,
            membership_request=req,
            requested_group_cn=mt.group_cn,
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        alice = self._make_user("alice", full_name="Alice User")

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["entries"]
        self.assertIn("alx-status-badge--active", entry["badge"]["className"])
        self.assertEqual(entry["membershipType"]["name"], "Individual")
        self.assertEqual(entry["requestId"], req.pk)
        self.assertNotIn("url", entry["badge"])

    def test_membership_badge_renders_active_without_expiry_label(self) -> None:
        expires_at = timezone.now() + datetime.timedelta(days=30)
        membership = SimpleNamespace(
            membership_type=SimpleNamespace(name="Gold"),
            expires_at=expires_at,
        )

        html = render_to_string(
            "core/_membership_badge.html",
            {
                "membership": membership,
                "membership_can_view": False,
            },
        )

        self.assertIn("Gold", html)
        self.assertNotIn("expires", html)

    def test_membership_badge_ignores_expired_state(self) -> None:
        expired_at = timezone.now() - datetime.timedelta(days=1)
        membership = SimpleNamespace(
            membership_type=SimpleNamespace(name="Gold"),
            expires_at=expired_at,
        )

        html = render_to_string(
            "core/_membership_badge.html",
            {
                "membership": membership,
                "membership_can_view": False,
            },
        )

        self.assertIn("Gold", html)
        self.assertNotIn("expired", html)

    def test_committee_profile_renders_expiry_and_terminate_modals(self) -> None:
        from core.models import MembershipLog, MembershipType

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

        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP])
        alice = self._make_user("alice", full_name="Alice User")

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        with patch("core.views_users.resolve_avatar_urls_for_users", autospec=True, return_value=({}, 0, 0)):
                            resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        entry = payload["membership"]["entries"][0]
        management = entry["management"]

        set_expiry_url = reverse(
            "membership-set-expiry",
            kwargs={"username": "alice", "membership_type_code": "individual"},
        )
        terminate_url = reverse(
            "membership-terminate",
            kwargs={"username": "alice", "membership_type_code": "individual"},
        )

        self.assertEqual(entry["membershipType"]["name"], "Individual")
        self.assertEqual(management["modalId"], "expiry-modal-1")
        self.assertEqual(management["inputId"], "expires-on-1")
        self.assertEqual(management["expiryActionUrl"], set_expiry_url)
        self.assertEqual(management["terminateActionUrl"], terminate_url)
        self.assertEqual(management["terminator"], "alice")
        self.assertIn("csrfToken", management)
        self.assertEqual(management["nextUrl"], reverse("api-user-profile", args=["alice"]))
        self.assertIn("Current expiration:", management["currentText"])
        self.assertNotContains(resp, "function setDisabled(btn, disabled) {")
        self.assertNotContains(resp, "data-expiry-modal-state")
        self.assertNotContains(resp, 'data-expiry-action="go-confirm-terminate"')
        self.assertNotContains(resp, 'data-expiry-action="back-to-edit"')

    def test_profile_shows_all_pending_membership_requests(self) -> None:
        from core.models import MembershipRequest, MembershipType

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
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        MembershipRequest.objects.create(requested_username="alice", membership_type_id="mirror")

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        pending_names = [entry["membershipType"]["name"] for entry in resp.json()["membership"]["pendingEntries"]]
        pending_badges = [entry["badge"]["label"] for entry in resp.json()["membership"]["pendingEntries"]]
        self.assertEqual(pending_names, ["Individual", "Mirror"])
        self.assertEqual(pending_badges, ["Under review", "Under review"])

    def test_profile_shows_extend_button_when_membership_expires_soon(self) -> None:
        from core.models import MembershipLog, MembershipType

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

        now = timezone.now()
        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=now + datetime.timedelta(days=50),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["entries"]
        self.assertEqual(entry["membershipType"]["name"], "Individual")
        self.assertTrue(entry["canRenew"])
        self.assertFalse(entry["canRequestTierChange"])
        self.assertNotIn("renewUrl", entry)
        self.assertNotIn("tierChangeUrl", entry)

        # Verify prefill: visiting with ?membership_type=individual should select that option.
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp_request = self.client.get(
                reverse("membership-request") + "?membership_type=individual"
            )
            api_resp_request = self.client.get(
                reverse("api-membership-request-form-detail") + "?membership_type=individual"
            )
        self.assertEqual(resp_request.status_code, 200)
        self.assertContains(resp_request, 'data-membership-request-form-root=""')
        self.assertContains(
            resp_request,
            f'data-membership-request-form-api-url="{reverse("api-membership-request-form-detail")}"',
        )
        self.assertEqual(api_resp_request.status_code, 200)
        self.assertEqual(api_resp_request.json()["form"]["fields"][0]["value"], "individual")

    def test_profile_shows_change_tier_button_for_multi_type_category(self) -> None:
        from core.models import MembershipLog, MembershipType

        # Keep deterministic with --keepdb: only same-category individual tiers.
        MembershipType.objects.update(enabled=False)

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
        MembershipType.objects.update_or_create(
            code="individual_plus",
            defaults={
                "name": "Individual Plus",
                "group_cn": "almalinux-individual-plus",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
            },
        )

        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        membership = resp.json()["membership"]
        [entry] = membership["entries"]
        self.assertEqual(entry["membershipType"]["name"], "Individual")
        self.assertFalse(entry["canRenew"])
        self.assertTrue(entry["canRequestTierChange"])
        self.assertNotIn("renewUrl", entry)
        self.assertNotIn("tierChangeUrl", entry)
        self.assertFalse(membership["canRequestAny"])

    def test_membership_request_prefills_mirror_type(self) -> None:
        """When ?membership_type=mirror is passed, the mirror option should be selected."""
        from core.models import MembershipType

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
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 10,
                "enabled": True,
            },
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(
                reverse("membership-request") + "?membership_type=mirror"
            )
            api_resp = self.client.get(
                reverse("api-membership-request-form-detail") + "?membership_type=mirror"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-membership-request-form-root=""')
        self.assertContains(
            resp,
            f'data-membership-request-form-api-url="{reverse("api-membership-request-form-detail")}"',
        )
        self.assertEqual(api_resp.status_code, 200)
        self.assertEqual(api_resp.json()["form"]["fields"][0]["value"], "mirror")

    def test_terminated_membership_does_not_count_as_active(self) -> None:
        import datetime

        from django.utils import timezone

        from core.membership import get_valid_memberships
        from core.models import MembershipLog, MembershipType

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

        now = timezone.now()
        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=now + datetime.timedelta(days=200),
        )
        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.terminated,
            expires_at=now,
        )

        valid = get_valid_memberships(username="alice")
        self.assertEqual(valid, [])

    def test_user_cannot_request_membership_type_if_already_valid(self) -> None:
        import datetime

        from django.utils import timezone

        from core.models import MembershipLog, MembershipRequest, MembershipType

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

        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp_get = self.client.get(reverse("membership-request"))
        self.assertEqual(resp_get.status_code, 200)
        self.assertNotContains(resp_get, 'value="individual"')

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp_post = self.client.post(
                reverse("membership-request"),
                data={"membership_type": "individual"},
            )

        self.assertEqual(resp_post.status_code, 200)
        self.assertFalse(
            MembershipRequest.objects.filter(
                requested_username="alice",
                status=MembershipRequest.Status.pending,
            ).exists()
        )

    def test_profile_disables_request_button_when_no_membership_types_available(self) -> None:
        from core.models import MembershipLog, MembershipType

        # Keep the test deterministic even with --keepdb: ensure no other enabled
        # requestable membership types exist.
        MembershipType.objects.update(enabled=False)

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

        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["membership"]["canRequestAny"])

    def test_committee_can_terminate_membership_early_and_it_is_logged(self) -> None:
        from core.models import MembershipLog, MembershipType

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

        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        alice = self._make_user("alice", full_name="Alice User")

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock:
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    resp = self.client.post(
                        reverse(
                            "membership-terminate",
                            kwargs={"username": "alice", "membership_type_code": "individual"},
                        ),
                        follow=False,
                    )

        self.assertEqual(resp.status_code, 302)
        remove_mock.assert_not_called()
        self.assertTrue(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.terminated,
            ).exists()
        )

        send_mock.assert_not_called()

    def test_committee_can_change_membership_expiration_date_and_it_is_logged(self) -> None:
        from core.models import MembershipLog, MembershipType

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

        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        alice = self._make_user("alice", full_name="Alice User")

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.post(
                reverse(
                    "membership-set-expiry",
                    kwargs={"username": "alice", "membership_type_code": "individual"},
                ),
                data={"expires_on": "2030-01-02"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        log = (
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.expiry_changed,
            )
            .order_by("-created_at")
            .first()
        )
        self.assertIsNotNone(log)
        assert log is not None
        self.assertIsNotNone(log.expires_at)
        assert log.expires_at is not None
        self.assertEqual(log.expires_at.tzinfo, datetime.UTC)
        self.assertEqual(log.expires_at, datetime.datetime(2030, 1, 2, 23, 59, 59, tzinfo=datetime.UTC))

    def test_committee_sidebar_link_has_badge_green_when_zero_red_when_nonzero(self) -> None:
        from core.models import MembershipRequest, MembershipType

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

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", autospec=True, return_value=[reviewer, alice]):
                resp0 = self.client.get(reverse("users"))

        self.assertEqual(resp0.status_code, 200)
        self.assertContains(resp0, reverse("membership-requests"))
        self.assertContains(resp0, "badge-success")

        MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        cache.clear()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.freeipa.user.FreeIPAUser.all", autospec=True, return_value=[reviewer, alice]):
                resp1 = self.client.get(reverse("users"))

        self.assertEqual(resp1.status_code, 200)
        self.assertContains(resp1, reverse("membership-requests"))
        self.assertContains(resp1, "badge-danger")

    def test_committee_sidebar_has_audit_log_link_to_all_users(self) -> None:
        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.views_users.FreeIPAUser.all", autospec=True, return_value=[]):
                resp = self.client.get(reverse("users"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("membership-audit-log"))

    def test_committee_sidebar_shows_organizations_link(self) -> None:
        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            with patch("core.views_users.FreeIPAUser.all", autospec=True, return_value=[]):
                resp = self.client.get(reverse("users"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("organizations"))

    def test_regular_user_sidebar_shows_organizations_link(self) -> None:
        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("organizations"))


    def test_membership_request_detail_shows_deleted_user(self) -> None:
        from core.models import MembershipRequest, MembershipType

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
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])

        def _get_user(username: str, *, respect_privacy: bool = True) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            page_response = self.client.get(reverse("membership-request-detail", args=[req.pk]))
            api_response = self.client.get(reverse("api-membership-request-detail", args=[req.pk]))

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, 'data-membership-request-detail-root=""')
        self.assertContains(
            page_response,
            f'data-membership-request-detail-api-url="{reverse("api-membership-request-detail", args=[req.pk])}"',
        )
        self.assertNotContains(page_response, "Request responses")

        self.assertEqual(api_response.status_code, 200)
        payload = api_response.json()
        self.assertEqual(payload["viewer"]["mode"], "committee")
        self.assertFalse(payload["request"]["requested_by"]["show"])
        self.assertEqual(
            payload["request"]["requested_for"],
            {
                "show": True,
                "kind": "user",
                "label": req.requested_username,
                "username": req.requested_username,
                "organization_id": None,
                "deleted": True,
            },
        )

    def test_profile_shows_status_note_to_membership_viewer(self) -> None:
        from core.models import MembershipRequest, MembershipType, Note

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
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(membership_request=req, username="reviewer", content="Needs manual review")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
                "fasstatusnote": ["Needs manual review"],
            },
        )

        self._login_as_freeipa_user("reviewer")

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.views_users._get_full_user", return_value=alice),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch("core.views_users.resolve_avatar_urls_for_users", autospec=True, return_value=({}, 0, 0)),
        ):
            resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        notes = payload["membership"]["notes"]
        self.assertEqual(notes["summaryUrl"], reverse("api-membership-notes-aggregate-summary") + f"?target_type=user&target={alice.username}")
        self.assertEqual(notes["detailUrl"], reverse("api-membership-notes-aggregate") + f"?target_type=user&target={alice.username}")
        self.assertEqual(notes["addUrl"], reverse("api-membership-notes-aggregate-add"))
        self.assertTrue(notes["canView"])
        self.assertTrue(notes["canWrite"])
        self.assertNotIn("Needs manual review", str(payload))
        self.assertNotIn(f"(req. #{req.pk})", str(payload))

    def test_profile_hides_status_note_without_membership_view_perm(self) -> None:
        from core.models import MembershipRequest, MembershipType, Note

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
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(membership_request=req, username="reviewer", content="Hidden note")

        viewer = self._make_user("viewer", full_name="Viewer Person", groups=[])
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
                "fasstatusnote": ["Hidden note"],
            },
        )

        self._login_as_freeipa_user("viewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer),
            patch("core.views_users._get_full_user", return_value=alice),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch("core.views_users.resolve_avatar_urls_for_users", autospec=True, return_value=({}, 0, 0)),
        ):
            resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIsNone(payload["membership"]["notes"])
        self.assertNotIn("Hidden note", str(payload))

    def test_profile_aggregate_notes_read_only_hides_compose_and_denies_post(self) -> None:
        from core.models import MembershipRequest, MembershipType, Note

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

        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
            },
        )

        req1 = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        req2 = MembershipRequest.objects.create(requested_username="alice", membership_type_id="mirror")
        Note.objects.create(membership_request=req1, username="reviewer", content="Older note")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_USER_DIRECTORY,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[])
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
                "fasstatusnote": ["Older note"],
            },
        )

        self._login_as_freeipa_user("reviewer")

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            return None

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.views_users._get_full_user", return_value=alice),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch("core.views_users.resolve_avatar_urls_for_users", autospec=True, return_value=({}, 0, 0)),
        ):
            resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        notes = payload["membership"]["notes"]
        self.assertEqual(notes["summaryUrl"], reverse("api-membership-notes-aggregate-summary") + "?target_type=user&target=alice")
        self.assertEqual(notes["detailUrl"], reverse("api-membership-notes-aggregate") + "?target_type=user&target=alice")
        self.assertEqual(notes["addUrl"], reverse("api-membership-notes-aggregate-add"))
        self.assertTrue(notes["canView"])
        self.assertFalse(notes["canWrite"])
        self.assertNotIn("Older note", str(payload))

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": "alice",
                    "note_action": "message",
                    "message": "Hello from aggregate",
                    "compact": "1",
                    "next": reverse("user-profile", kwargs={"username": "alice"}),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        payload = resp.json()
        self.assertEqual(payload, {"error": "Permission denied."})

        self.assertFalse(
            Note.objects.filter(
                membership_request=req2,
                username="reviewer",
                content="Hello from aggregate",
            ).exists()
        )

    def test_membership_request_note_add_creates_message_note(self) -> None:
        from core.models import MembershipRequest, MembershipType, Note

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
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello committee",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "message": "Note added."})
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                content="Hello committee",
            ).exists()
        )

    def test_membership_request_note_add_creates_vote_note(self) -> None:
        from core.models import MembershipRequest, MembershipType, Note

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
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "vote_approve",
                    "message": "LGTM",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "message": "Recorded approve vote."})
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action={"type": "vote", "value": "approve"},
            ).exists()
        )

    def test_membership_request_note_add_redirects_to_next(self) -> None:
        from core.models import MembershipRequest, MembershipType

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
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        next_url = reverse("membership-requests")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Updated",
                    "next": next_url,
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "message": "Note added."})

    def test_membership_request_allows_individual_and_mirror_membership_types(self) -> None:
        from core.models import MembershipRequest, MembershipType

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
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp_get = self.client.get(reverse("membership-request"))
            api_resp_get = self.client.get(reverse("api-membership-request-form-detail"))

        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, 'data-membership-request-form-root=""')
        self.assertContains(
            resp_get,
            f'data-membership-request-form-api-url="{reverse("api-membership-request-form-detail")}"',
        )
        option_values = [
            option["value"]
            for group in api_resp_get.json()["form"]["fields"][0]["option_groups"]
            for option in group["options"]
        ]
        self.assertEqual(option_values, ["individual", "mirror"])

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp_post_invalid = self.client.post(
                reverse("membership-request"),
                data={"membership_type": "mirror"},
            )

        self.assertEqual(resp_post_invalid.status_code, 200)
        self.assertFalse(
            MembershipRequest.objects.filter(
                requested_username="alice",
                status=MembershipRequest.Status.pending,
            ).exists()
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp_post_valid = self.client.post(
                reverse("membership-request"),
                data={
                    "membership_type": "mirror",
                    "q_domain": "example.com",
                    "q_pull_request": "https://github.com/example/repo/pull/123",
                    "q_additional_information": "Extra details",
                },
            )

        self.assertEqual(resp_post_valid.status_code, 302)
        req = MembershipRequest.objects.get(
            requested_username="alice",
            status=MembershipRequest.Status.pending,
        )
        self.assertEqual(req.membership_type_id, "mirror")
        self.assertEqual(
            req.responses,
            [
                {"Domain": "https://example.com"},
                {"Pull request": "https://github.com/example/repo/pull/123"},
                {"Additional information": "Extra details"},
            ],
        )

    def test_membership_request_blocks_category_with_pending_request(self) -> None:
        from core.models import MembershipRequest, MembershipType, MembershipTypeCategory

        MembershipType.objects.update(enabled=False)
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 1},
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
        MembershipType.objects.update_or_create(
            code="community",
            defaults={
                "name": "Community",
                "group_cn": "almalinux-community",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
            },
        )

        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("membership-request"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'value="individual"')
        self.assertNotContains(resp, 'value="community"')

    def test_membership_request_blocks_category_with_on_hold_request(self) -> None:
        from core.models import MembershipRequest, MembershipType, MembershipTypeCategory

        MembershipType.objects.update(enabled=False)
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 1},
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
        MembershipType.objects.update_or_create(
            code="community",
            defaults={
                "name": "Community",
                "group_cn": "almalinux-community",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
            },
        )

        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("membership-request"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'value="individual"')
        self.assertNotContains(resp, 'value="community"')

    def test_membership_request_allows_other_type_when_active(self) -> None:
        from core.models import MembershipLog, MembershipType, MembershipTypeCategory

        MembershipType.objects.update(enabled=False)
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 1},
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
        MembershipType.objects.update_or_create(
            code="community",
            defaults={
                "name": "Community",
                "group_cn": "almalinux-community",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
            },
        )

        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("membership-request"))
            api_resp = self.client.get(reverse("api-membership-request-form-detail"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-membership-request-form-root=""')
        option_values = [
            option["value"]
            for group in api_resp.json()["form"]["fields"][0]["option_groups"]
            for option in group["options"]
        ]
        self.assertNotIn("individual", option_values)
        self.assertIn("community", option_values)

    def test_membership_request_allows_renewal_when_expiring_soon(self) -> None:
        from core.models import MembershipLog, MembershipType, MembershipTypeCategory

        MembershipType.objects.update(enabled=False)
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 1},
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

        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("membership-request"))
            api_resp = self.client.get(reverse("api-membership-request-form-detail"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-membership-request-form-root=""')
        option_values = [
            option["value"]
            for group in api_resp.json()["form"]["fields"][0]["option_groups"]
            for option in group["options"]
        ]
        self.assertIn("individual", option_values)

    def test_membership_request_renders_sponsorship_question(self) -> None:
        from core.models import MembershipType, MembershipTypeCategory

        MembershipType.objects.update(enabled=False)
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 1},
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

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("membership-request"))
            api_resp = self.client.get(reverse("api-membership-request-form-detail"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-membership-request-form-root=""')
        payload = api_resp.json()
        sponsorship_field = next(field for field in payload["form"]["fields"] if field["name"] == "q_sponsorship_details")
        self.assertEqual(
            sponsorship_field["label"],
            "Please describe your organization's sponsorship goals and planned community participation.",
        )
        self.assertEqual(sponsorship_field["id"], "id_q_sponsorship_details")

    def test_membership_request_mirror_url_fields_are_validated(self) -> None:
        from core.models import MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp_post_invalid = self.client.post(
                reverse("membership-request"),
                data={
                    "membership_type": "mirror",
                    "q_domain": "not a domain",
                    "q_pull_request": "not a url",
                },
            )

        self.assertEqual(resp_post_invalid.status_code, 200)
        self.assertContains(resp_post_invalid, "Enter a valid URL")
        self.assertFalse(
            MembershipRequest.objects.filter(
                requested_username="alice",
                status=MembershipRequest.Status.pending,
            ).exists()
        )

    def test_user_profile_template_uses_vue_shell_without_membership_include(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "templates" / "core" / "user_profile.html"
        source = template_path.read_text(encoding="utf-8")
        self.assertIn("data-user-profile-root", source)
        self.assertIn("data-user-profile-api-url", source)
        self.assertNotIn("{% include 'core/_membership_profile_section.html'", source)

        section_template_path = (
            Path(__file__).resolve().parents[1] / "templates" / "core" / "_membership_profile_section.html"
        )
        self.assertFalse(section_template_path.exists())

    def test_membership_request_detail_linkifies_mirror_url_responses(self) -> None:
        from core.models import MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="mirror",
            responses=[
                {"Domain": "mirror.example.org"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/1"},
            ],
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("api-membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        responses = resp.json()["request"]["responses"]
        self.assertEqual(
            responses[0]["segments"],
            [{"kind": "link", "text": "mirror.example.org", "url": "https://mirror.example.org"}],
        )
        self.assertEqual(
            responses[1]["segments"],
            [{
                "kind": "link",
                "text": "https://github.com/AlmaLinux/mirrors/pull/1",
                "url": "https://github.com/AlmaLinux/mirrors/pull/1",
            }],
        )

    def test_membership_audit_log_is_paginated_50_per_page(self) -> None:
        import datetime

        from core.models import MembershipLog, MembershipType

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
        mt = MembershipType.objects.get(code="individual")

        base_time = timezone.now()
        for i in range(51):
            self._create_membership_log_with_side_effects(
                actor_username="reviewer",
                target_username=f"user{i}",
                membership_type=mt,
                requested_group_cn=mt.group_cn,
                action=MembershipLog.Action.requested,
                created_at=base_time + datetime.timedelta(seconds=i),
            )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp_page_1 = self.client.get(
                reverse("api-membership-audit-log"),
                data=self._audit_log_datatables_query(start=0, length=50),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp_page_1.status_code, 200)
        payload_page_1 = resp_page_1.json()
        self.assertEqual(payload_page_1["recordsFiltered"], 51)
        self.assertEqual(payload_page_1["data"][0]["target"]["label"], "user50")
        self.assertEqual(payload_page_1["data"][-1]["target"]["label"], "user1")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp_page_2 = self.client.get(
                reverse("api-membership-audit-log"),
                data=self._audit_log_datatables_query(start=50, length=50),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp_page_2.status_code, 200)
        payload_page_2 = resp_page_2.json()
        self.assertEqual(payload_page_2["recordsFiltered"], 51)
        self.assertEqual(len(payload_page_2["data"]), 1)
        self.assertEqual(payload_page_2["data"][0]["target"]["label"], "user0")

    def test_committee_can_view_membership_audit_log_all_and_by_user_filter(self) -> None:
        from core.models import MembershipLog, MembershipType

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

        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_VALIDITY_DAYS),
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp_all = self.client.get(reverse("membership-audit-log"))

        self.assertEqual(resp_all.status_code, 200)
        self.assertContains(resp_all, "Membership Audit Log")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            payload_all = self.client.get(
                reverse("api-membership-audit-log"),
                data=self._audit_log_datatables_query(),
                HTTP_ACCEPT="application/json",
            ).json()
        self.assertEqual(payload_all["recordsFiltered"], 1)
        self.assertEqual(payload_all["data"][0]["target"]["label"], "alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp_user = self.client.get(f"{reverse('membership-audit-log')}?username=alice")

        self.assertEqual(resp_user.status_code, 200)
        self.assertContains(resp_user, "Membership Audit Log")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            payload_user = self.client.get(
                reverse("api-membership-audit-log"),
                data={**self._audit_log_datatables_query(), "username": "alice"},
                HTTP_ACCEPT="application/json",
            ).json()
        self.assertEqual(payload_user["recordsFiltered"], 1)
        self.assertEqual(payload_user["data"][0]["target"]["label"], "alice")

    def test_membership_audit_log_shows_linked_request_responses(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType

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
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[{"Contributions": "Patch submissions"}],
        )
        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.requested,
            membership_request=req,
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(
                reverse("api-membership-audit-log"),
                data=self._audit_log_datatables_query(),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["recordsFiltered"], 1)
        request_payload = payload["data"][0]["request"]
        self.assertEqual(request_payload["request_id"], req.pk)
        self.assertNotIn("url", request_payload)
        self.assertEqual(request_payload["responses"][0]["question"], "Contributions")
        self.assertIn("Patch submissions", request_payload["responses"][0]["answer_html"])

    def test_membership_management_menu_stays_open_on_child_pages(self) -> None:
        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-audit-log"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Membership Management")
        self.assertContains(resp, "menu-open")

    def test_profile_shows_membership_audit_log_button_for_committee_viewer(self) -> None:
        from core.models import MembershipType

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

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        alice = self._make_user("alice", full_name="Alice User")

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        membership = resp.json()["membership"]
        self.assertTrue(membership["canViewHistory"])
        self.assertNotIn("historyUrl", membership)

    def test_profile_hides_renewal_button_when_pending_request_exists_for_same_type(self) -> None:
        """When an expiring-soon membership already has a pending renewal request,
        the 'Request renewal' button must not appear."""
        from core.models import MembershipLog, MembershipRequest, MembershipType

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

        now = timezone.now()
        # Membership expires in 50 days — within the "expiring soon" window.
        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=now + datetime.timedelta(days=50),
        )

        # A pending renewal request already exists for the same membership type.
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["entries"]
        self.assertEqual(entry["membershipType"]["name"], "Individual")
        self.assertFalse(entry["canRenew"])
        self.assertNotIn("renewUrl", entry)

    def test_profile_hides_renewal_button_when_on_hold_request_exists_for_same_type(self) -> None:
        """Same as above, but with an on_hold request instead of pending."""
        from core.models import MembershipLog, MembershipRequest, MembershipType

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

        now = timezone.now()
        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=now + datetime.timedelta(days=50),
        )

        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["entries"]
        self.assertEqual(entry["membershipType"]["name"], "Individual")
        self.assertFalse(entry["canRenew"])
        self.assertNotIn("renewUrl", entry)

    def test_profile_shows_renewal_button_when_no_pending_request_for_type(self) -> None:
        """A pending request for a DIFFERENT type should not suppress the button."""
        from core.models import MembershipLog, MembershipRequest, MembershipType

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
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        now = timezone.now()
        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=now + datetime.timedelta(days=50),
        )

        # Pending request for a DIFFERENT type — should NOT suppress the individual renewal button.
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="mirror",
            status=MembershipRequest.Status.pending,
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["entries"]
        self.assertTrue(entry["canRenew"])
        self.assertFalse(entry["canRequestTierChange"])
        self.assertNotIn("renewUrl", entry)

    def test_profile_shows_expiry_in_users_timezone(self) -> None:
        import datetime

        from core.models import MembershipLog, MembershipType

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

        expires_at_utc = timezone.now() + datetime.timedelta(days=2)
        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=expires_at_utc,
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
                "fasTimezone": ["Australia/Brisbane"],
            },
        )
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alice":
                return alice
            if username == "reviewer":
                return reviewer
            return None

        self._login_as_freeipa_user("alice")
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["entries"]
        self.assertIn("(Australia/Brisbane)", entry["expiresLabel"])

    def test_membership_request_shows_no_types_message_when_all_types_blocked(self) -> None:
        """When a user already holds all available individual membership types, the
        request page should thank them and not show the form."""
        from core.models import MembershipLog, MembershipType

        MembershipType.objects.update(enabled=False)

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
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Membership",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        # Alice holds active, non-expiring memberships in both categories.
        for mt_code, group_cn in [("individual", "almalinux-individual"), ("mirror", "almalinux-mirror")]:
            self._create_membership_log_with_side_effects(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id=mt_code,
                requested_group_cn=group_cn,
                action=MembershipLog.Action.approved,
                expires_at=timezone.now() + datetime.timedelta(days=365),
            )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("membership-request"))
            api_resp = self.client.get(reverse("api-membership-request-form-detail"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-membership-request-form-root=""')
        payload = api_resp.json()
        self.assertTrue(payload["no_types_available"])
        self.assertEqual(payload["prefill_type_unavailable_name"], None)
        option_values = [
            option["value"]
            for group in payload["form"]["fields"][0]["option_groups"]
            for option in group["options"]
        ]
        self.assertEqual(option_values, [])

    def test_membership_request_type_param_preselects_form(self) -> None:
        """?type=individual should pre-select the individual option in the form."""
        from core.models import MembershipType

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
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Membership",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("membership-request") + "?membership_type=individual")
            api_resp = self.client.get(
                reverse("api-membership-request-form-detail") + "?membership_type=individual"
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-membership-request-form-root=""')
        self.assertContains(
            resp,
            f'data-membership-request-form-api-url="{reverse("api-membership-request-form-detail")}"',
        )
        self.assertEqual(api_resp.status_code, 200)
        self.assertEqual(api_resp.json()["form"]["fields"][0]["value"], "individual")

    def test_membership_request_type_param_shows_message_when_unavailable(self) -> None:
        """When ?type=individual but user already holds an active individual
        membership, show an informational message and still render the form
        with other available types."""
        from core.models import MembershipLog, MembershipType

        MembershipType.objects.update(enabled=False)

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
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Membership",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        self._create_membership_log_with_side_effects(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=365),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("membership-request") + "?membership_type=individual")
            api_resp = self.client.get(
                reverse("api-membership-request-form-detail") + "?membership_type=individual"
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-membership-request-form-root=""')
        self.assertContains(
            resp,
            f'data-membership-request-form-api-url="{reverse("api-membership-request-form-detail")}"',
        )
        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        option_values = [
            option["value"]
            for group in payload["form"]["fields"][0]["option_groups"]
            for option in group["options"]
        ]
        self.assertEqual(payload["prefill_type_unavailable_name"], "Individual")
        self.assertEqual(option_values, ["mirror"])

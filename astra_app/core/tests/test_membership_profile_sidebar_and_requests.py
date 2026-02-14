
import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
)
from core.tests.utils_test_data import ensure_core_categories


class MembershipProfileSidebarAndRequestsTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

        self._coc_patcher = patch("core.views_membership.block_action_without_coc", return_value=None)
        self._coc_patcher.start()
        self.addCleanup(self._coc_patcher.stop)
        self._country_patcher = patch("core.views_membership.block_action_without_country_code", return_value=None)
        self._country_patcher.start()
        self.addCleanup(self._country_patcher.stop)

        for perm in (ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP):
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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Membership")
        self.assertContains(resp, reverse("membership-request"))

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
        MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Under review")
        self.assertContains(resp, "Individual")

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

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Under review")
        self.assertContains(resp, f'href="{reverse("membership-request-detail", args=[req.pk])}"')

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

        MembershipLog.objects.create(
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

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "alx-status-badge--active")
        self.assertContains(resp, "Individual")
        self.assertContains(resp, f'href="{reverse("membership-request-detail", args=[req.pk])}"')

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

        MembershipLog.objects.create(
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

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)

        set_expiry_url = reverse(
            "membership-set-expiry",
            kwargs={"username": "alice", "membership_type_code": "individual"},
        )
        terminate_url = reverse(
            "membership-terminate",
            kwargs={"username": "alice", "membership_type_code": "individual"},
        )

        self.assertContains(resp, 'data-target="#expiry-modal-1"')
        self.assertContains(resp, 'id="expiry-modal-1"')
        self.assertContains(resp, f'action="{set_expiry_url}"')
        self.assertContains(resp, "Edit expiration")

        self.assertNotContains(resp, 'data-target="#terminate-modal-1"')
        self.assertNotContains(resp, 'id="terminate-modal-1"')
        self.assertContains(resp, f'action="{terminate_url}"')
        self.assertContains(resp, "Manage membership: Individual for alice")
        self.assertNotContains(resp, "Target:")
        self.assertContains(resp, "Expiration date")
        self.assertContains(resp, "Expiration is an end-of-day date in UTC.")
        self.assertContains(resp, "Save expiration")
        self.assertContains(resp, "Danger zone")
        self.assertContains(resp, "Ends this membership early.")
        self.assertContains(resp, "Terminate membership&hellip;", html=True)
        self.assertContains(resp, 'data-target="#expiry-modal-1-terminate-collapse"')
        self.assertContains(resp, 'id="expiry-modal-1-terminate-collapse"')
        self.assertContains(resp, "This will end the membership early and cannot be undone.")
        self.assertContains(resp, "Type the name to confirm")
        self.assertContains(resp, 'placeholder="alice"')
        self.assertContains(resp, 'data-terminate-target="alice"')
        self.assertContains(resp, "Does not match. Type the name to enable termination (case-insensitive).")
        self.assertContains(resp, 'data-terminate-action="cancel"')
        self.assertContains(resp, "Cancel termination")
        self.assertContains(resp, 'id="expiry-modal-1-terminate-submit"')
        self.assertContains(resp, "disabled")
        self.assertContains(resp, "aria-disabled=\"true\"")
        self.assertContains(resp, "attr('data-terminate-target')")
        self.assertContains(resp, "var modalId = 'expiry\\u002Dmodal\\u002D1';")
        self.assertContains(resp, "var inputId = modalId + '-terminate-confirm-input';")
        self.assertContains(resp, "var submitId = modalId + '-terminate-submit';")
        self.assertContains(resp, "jq(document).on('input', '#' + inputId, function() {")
        self.assertContains(resp, "var $input = jq(this);")
        self.assertContains(resp, "jq(document).on('click', '[data-terminate-action=\"cancel\"]', function() {")
        self.assertContains(resp, "jq(collapseSel).on('shown.bs.collapse', function() {")
        self.assertContains(resp, "jq(collapseSel).on('hidden.bs.collapse', function() {")
        self.assertContains(resp, "jq(modalSel).on('hidden.bs.modal', function() {")
        self.assertContains(resp, "$submit.prop('disabled', !matches).attr('aria-disabled', !matches ? 'true' : 'false');")
        self.assertNotContains(resp, "$input.off('input.terminate');")
        self.assertNotContains(resp, "$input.on('input.terminate', updateConfirmState);")
        self.assertNotContains(resp, "jq(collapseSel).on('click', '[data-terminate-action=\"cancel\"]', function () {")
        self.assertNotContains(resp, "jq(modalSel).on('shown.bs.modal hidden.bs.modal', function () {")
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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Under review")
        self.assertContains(resp, "Individual")
        self.assertContains(resp, "Mirror")

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
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=now + datetime.timedelta(days=50),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Request renewal")
        self.assertContains(resp, reverse("membership-request") + "?membership_type=individual")

        # Verify prefill: visiting with ?membership_type=individual should select that option.
        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp_request = self.client.get(
                reverse("membership-request") + "?membership_type=individual"
            )
        self.assertEqual(resp_request.status_code, 200)
        self.assertContains(resp_request, 'value="individual" selected')

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

        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Change tier")
        self.assertContains(resp, reverse("membership-request") + "?membership_type=individual")
        self.assertNotContains(resp, "Request membership")

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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(
                reverse("membership-request") + "?membership_type=mirror"
            )
        self.assertEqual(resp.status_code, 200)
        # Mirror should be selected, individual should NOT be selected.
        self.assertContains(resp, 'value="mirror" selected')
        self.assertNotContains(resp, 'value="individual" selected')

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
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=now + datetime.timedelta(days=200),
        )
        MembershipLog.objects.create(
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

        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp_get = self.client.get(reverse("membership-request"))
        self.assertEqual(resp_get.status_code, 200)
        self.assertNotContains(resp_get, 'value="individual"')

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
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

        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'class="btn btn-sm btn-outline-primary">Request</a>')

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

        MembershipLog.objects.create(
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

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
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

        MembershipLog.objects.create(
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

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
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
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            with patch("core.views_users.FreeIPAUser.all", autospec=True, return_value=[]):
                resp0 = self.client.get(reverse("users"))

        self.assertEqual(resp0.status_code, 200)
        self.assertContains(resp0, reverse("membership-requests"))
        self.assertContains(resp0, "badge-success")

        MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            with patch("core.views_users.FreeIPAUser.all", autospec=True, return_value=[]):
                resp1 = self.client.get(reverse("users"))

        self.assertEqual(resp1.status_code, 200)
        self.assertContains(resp1, reverse("membership-requests"))
        self.assertContains(resp1, "badge-danger")

    def test_committee_sidebar_has_audit_log_link_to_all_users(self) -> None:
        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            with patch("core.views_users.FreeIPAUser.all", autospec=True, return_value=[]):
                resp = self.client.get(reverse("users"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("membership-audit-log"))

    def test_committee_sidebar_shows_organizations_link(self) -> None:
        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            with patch("core.views_users.FreeIPAUser.all", autospec=True, return_value=[]):
                resp = self.client.get(reverse("users"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("organizations"))

    def test_requests_list_links_to_profile_and_shows_full_name(self) -> None:
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

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("user-profile", kwargs={"username": req.requested_username}))
        self.assertContains(resp, "Alice User")

    def test_membership_requests_list_hides_deleted_user_request(self) -> None:
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

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            # Simulate the target user having been deleted from FreeIPA.
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, reverse("membership-request-detail", args=[req.pk]))
        self.assertNotContains(resp, req.requested_username)
        self.assertContains(resp, "No pending requests.")

    def test_membership_requests_list_hides_deleted_org_request(self) -> None:
        from core.models import MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )

        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=None,
            requested_organization_code="acme",
            requested_organization_name="Acme",
            membership_type_id="gold",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, reverse("membership-request-detail", args=[req.pk]))
        self.assertNotContains(resp, "Acme")
        self.assertContains(resp, "No pending requests.")

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

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, req.requested_username)

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
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.views_users._get_full_user", return_value=alice),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
        ):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Membership Committee Notes")
        self.assertContains(resp, "Needs manual review")
        self.assertContains(resp, f"(req. #{req.pk})")
        self.assertContains(resp, f'href="{reverse("membership-request-detail", args=[req.pk])}"')

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
            patch("core.backends.FreeIPAUser.get", return_value=viewer),
            patch("core.views_users._get_full_user", return_value=alice),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
        ):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Membership Committee Notes")
        self.assertNotContains(resp, "Hidden note")

    def test_profile_aggregate_notes_allows_posting_but_hides_vote_buttons(self) -> None:
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
                "fasstatusnote": ["Older note"],
            },
        )

        self._login_as_freeipa_user("reviewer")

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.views_users._get_full_user", return_value=alice),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
        ):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Membership Committee Notes")
        self.assertContains(resp, 'placeholder="Type a note..."')
        self.assertNotContains(resp, 'data-note-action="vote_approve"')
        self.assertNotContains(resp, 'data-note-action="vote_disapprove"')

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.post(
                reverse("membership-notes-aggregate-note-add"),
                data={
                    "aggregate_target_type": "user",
                    "aggregate_target": "alice",
                    "note_action": "message",
                    "message": "Hello from aggregate",
                    "compact": "1",
                    "next": reverse("user-profile", kwargs={"username": "alice"}),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("Hello from aggregate", payload.get("html") or "")

        self.assertTrue(
            Note.objects.filter(
                membership_request=req2,
                username="reviewer",
                content="Hello from aggregate",
            ).exists()
        )

    def test_requests_list_includes_collapsible_status_note(self) -> None:
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
        Note.objects.create(membership_request=req, username="reviewer", content="Request note")

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
                "fasstatusnote": ["Request note"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Membership Committee Notes")
        self.assertContains(resp, "Request note")

    def test_requests_list_shows_request_responses_in_collapsible_section(self) -> None:
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
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[{"Contributions": "I did docs and CI."}],
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

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Request responses")
        self.assertContains(resp, "Contributions")
        self.assertContains(resp, "I did docs and CI.")

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

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello committee",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
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

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "vote_approve",
                    "message": "LGTM",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
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
        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-request-note-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Updated",
                    "next": next_url,
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], next_url)

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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp_get = self.client.get(reverse("membership-request"))

        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, 'value="individual"')
        self.assertContains(resp_get, 'value="mirror"')

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp_post_valid = self.client.post(
                reverse("membership-request"),
                data={
                    "membership_type": "mirror",
                    "q_domain": "example.com",
                    "q_pull_request": "https://github.com/example/repo/pull/123",
                    "q_additional_info": "Extra details",
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
                {"Additional info": "Extra details"},
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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
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

        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("membership-request"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'value="individual"')
        self.assertContains(resp, 'value="community"')

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

        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1),
        )

        alice = self._make_user("alice", full_name="Alice User")
        self._login_as_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("membership-request"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="individual"')

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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("membership-request"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sponsorship")
        self.assertContains(resp, "sponsorship goals and planned community participation")
        self.assertContains(resp, 'id="id_q_sponsorship_details"')

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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
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

    def test_user_profile_template_uses_pending_request_badge_include(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "templates" / "core" / "user_profile.html"
        source = template_path.read_text(encoding="utf-8")
        self.assertIn("{% include 'core/_membership_profile_section.html'", source)

        section_template_path = (
            Path(__file__).resolve().parents[1] / "templates" / "core" / "_membership_profile_section.html"
        )
        section_source = section_template_path.read_text(encoding="utf-8")
        self.assertIn("{% include 'core/_membership_badge.html'", section_source)

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

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'href="https://mirror.example.org"')
        self.assertContains(resp, 'href="https://github.com/AlmaLinux/mirrors/pull/1"')

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
            MembershipLog.objects.create(
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

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp_page_1 = self.client.get(reverse("membership-audit-log"))
        self.assertEqual(resp_page_1.status_code, 200)
        self.assertContains(resp_page_1, "user50")
        self.assertNotContains(resp_page_1, "user0")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp_page_2 = self.client.get(reverse("membership-audit-log") + "?page=2")
        self.assertEqual(resp_page_2.status_code, 200)
        self.assertContains(resp_page_2, "user0")

    def test_committee_can_view_membership_audit_log_all_and_by_user(self) -> None:
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

        MembershipLog.objects.create(
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

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp_all = self.client.get(reverse("membership-audit-log"))

        self.assertEqual(resp_all.status_code, 200)
        self.assertContains(resp_all, "Membership Audit Log")
        self.assertContains(resp_all, "alice")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp_user = self.client.get(reverse("membership-audit-log-user", kwargs={"username": "alice"}))

        self.assertEqual(resp_user.status_code, 200)
        self.assertContains(resp_user, "Membership Audit Log")
        self.assertContains(resp_user, "alice")

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
        MembershipLog.objects.create(
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

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-audit-log"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'href="{reverse("membership-request-detail", args=[req.pk])}"')
        self.assertContains(resp, f"Request #{req.pk}")
        self.assertContains(resp, "Request responses")
        self.assertContains(resp, "Contributions")
        self.assertContains(resp, "Patch submissions")

    def test_membership_management_menu_stays_open_on_child_pages(self) -> None:
        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = self._make_user("reviewer", full_name="Reviewer Person", groups=[committee_cn])
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
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
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("membership-audit-log-user", kwargs={"username": "alice"}))

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
        MembershipLog.objects.create(
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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        # The renewal button must NOT appear — they already requested it.
        self.assertNotContains(resp, "Request renewal")
        # But the membership should still be listed.
        self.assertContains(resp, "Individual")

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
        MembershipLog.objects.create(
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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Request renewal")
        self.assertContains(resp, "Individual")

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
        MembershipLog.objects.create(
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

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Request renewal")

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
        MembershipLog.objects.create(
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
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("user-profile", kwargs={"username": "alice"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "(Australia/Brisbane)")

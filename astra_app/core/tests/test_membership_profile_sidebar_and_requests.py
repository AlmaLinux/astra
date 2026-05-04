
import datetime
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
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

    def test_user_profile_detail_shows_request_capability_when_no_membership(self) -> None:
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
                        resp = self.client.get(reverse("api-user-profile-detail", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        membership = resp.json()["membership"]
        self.assertTrue(membership["showCard"])
        self.assertTrue(membership["canRequestAny"])
        self.assertEqual(membership["entries"], [])
        self.assertEqual(membership["pendingEntries"], [])

    def test_user_profile_detail_shows_pending_membership_request_entry(self) -> None:
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            with patch("core.views_users._get_full_user", return_value=alice):
                with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                    with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                        resp = self.client.get(reverse("api-user-profile-detail", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["pendingEntries"]
        self.assertEqual(entry["status"], MembershipRequest.Status.pending)

    def test_user_profile_detail_shows_change_tier_capability_for_multi_type_category(self) -> None:
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
                        resp = self.client.get(reverse("api-user-profile-detail", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        membership = resp.json()["membership"]
        [entry] = membership["entries"]
        self.assertEqual(entry["membershipType"]["name"], "Individual")
        self.assertFalse(entry["canRenew"])
        self.assertTrue(entry["canRequestTierChange"])
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

    def test_user_profile_page_renders_membership_notes_bootstrap_for_membership_viewer(self) -> None:
        reviewer = self._make_user("reviewer", full_name="Reviewer Person")
        self._login_as_freeipa_user("reviewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.views_users.membership_review_permissions",
                return_value={
                    "membership_can_view": True,
                    "membership_can_add": True,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
        ):
            resp = self.client.get(reverse("user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp,
            f'data-user-profile-membership-notes-summary-url="{reverse("api-membership-notes-aggregate-summary")}?target_type=user&amp;target=alice"',
        )
        self.assertContains(resp, 'data-user-profile-membership-notes-can-view="true"')
        self.assertContains(resp, 'data-user-profile-membership-notes-can-write="true"')

    def test_user_profile_page_hides_membership_notes_bootstrap_without_membership_view_perm(self) -> None:
        viewer = self._make_user("viewer", full_name="Viewer Person")
        self._login_as_freeipa_user("viewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer),
            patch(
                "core.views_users.membership_review_permissions",
                return_value={
                    "membership_can_view": False,
                    "membership_can_add": False,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
        ):
            resp = self.client.get(reverse("user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-user-profile-membership-notes-can-view="false"')
        self.assertContains(resp, 'data-user-profile-membership-notes-can-write="false"')

    def test_user_profile_page_renders_read_only_membership_notes_bootstrap(self) -> None:
        reviewer = self._make_user("reviewer", full_name="Reviewer Person")
        self._login_as_freeipa_user("reviewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.views_users.membership_review_permissions",
                return_value={
                    "membership_can_view": True,
                    "membership_can_add": False,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
        ):
            resp = self.client.get(reverse("user-profile", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-user-profile-membership-notes-can-view="true"')
        self.assertContains(resp, 'data-user-profile-membership-notes-can-write="false"')

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

    def test_user_profile_detail_shows_membership_audit_log_capability_for_committee_viewer(self) -> None:
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
                        resp = self.client.get(reverse("api-user-profile-detail", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        membership = resp.json()["membership"]
        self.assertTrue(membership["canViewHistory"])

    def test_user_profile_detail_hides_renewal_when_pending_request_exists_for_same_type(self) -> None:
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
                        resp = self.client.get(reverse("api-user-profile-detail", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["entries"]
        self.assertEqual(entry["membershipType"]["name"], "Individual")
        self.assertFalse(entry["canRenew"])

    def test_user_profile_detail_hides_renewal_when_on_hold_request_exists_for_same_type(self) -> None:
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
                        resp = self.client.get(reverse("api-user-profile-detail", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["entries"]
        self.assertEqual(entry["membershipType"]["name"], "Individual")
        self.assertFalse(entry["canRenew"])

    def test_user_profile_detail_shows_renewal_when_no_pending_request_exists_for_type(self) -> None:
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
                        resp = self.client.get(reverse("api-user-profile-detail", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        [entry] = resp.json()["membership"]["entries"]
        self.assertTrue(entry["canRenew"])
        self.assertFalse(entry["canRequestTierChange"])

    def test_user_profile_detail_preserves_expiry_timestamp_for_users_timezone(self) -> None:
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
                        resp = self.client.get(reverse("api-user-profile-detail", args=["alice"]))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["summary"]["timezoneName"], "Australia/Brisbane")
        [entry] = resp.json()["membership"]["entries"]
        self.assertEqual(entry["expiresAt"], expires_at_utc.isoformat())

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

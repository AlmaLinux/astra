
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import MembershipRequest, MembershipType, MembershipTypeCategory


class UserProfileMembershipCanRequestAnyTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 0},
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="mirror",
            defaults={"is_organization": True, "sort_order": 1},
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_membership_can_request_any_false_when_all_eligible_types_have_open_requests(self) -> None:
        # Ensure the test is deterministic: only two membership types are requestable.
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
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        MembershipRequest.objects.create(
            requested_username="alex",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="alex",
            membership_type_id="mirror",
            status=MembershipRequest.Status.on_hold,
        )

        alex = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.com"],
                "memberof_group": [],
                "givenname": ["Alex"],
                "sn": ["User"],
            },
        )

        self._login_as_freeipa_user("alex")
        with (
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.backends.FreeIPAUser.get", return_value=alex),
        ):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "alex"}))

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(bool(resp.context["membership_can_request_any"]))
        self.assertNotContains(resp, 'title="No additional membership types available"')
        self.assertNotContains(resp, "btn btn-sm btn-outline-primary disabled")

    def test_user_profile_does_not_recommend_request_after_rejection(self) -> None:
        # Ensure the test is deterministic: only one membership type is requestable.
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

        # The user previously opened a request and it was rejected.
        MembershipRequest.objects.create(
            requested_username="alex",
            membership_type_id="individual",
            status=MembershipRequest.Status.rejected,
        )

        alex = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.com"],
                "memberof_group": [],
                "givenname": ["Alex"],
                "sn": ["User"],
                "c": ["US"],
            },
        )

        self._login_as_freeipa_user("alex")
        with (
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.backends.FreeIPAUser.get", return_value=alex),
        ):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "alex"}))

        self.assertEqual(resp.status_code, 200)
        recommended = list(resp.context.get("account_setup_recommended_actions") or [])
        self.assertFalse(
            any(a.get("id") == "membership-request-recommended-alert" for a in recommended),
            "Did not expect a membership request recommendation after a rejected request.",
        )

    def test_user_profile_required_actions_use_context_aware_settings_links(self) -> None:
        MembershipType.objects.update(enabled=False)

        alex = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "mail": ["alex@example.com"],
                "memberof_group": [],
                "givenname": ["Alex"],
                "sn": ["User"],
            },
        )

        agreement = type("Agreement", (), {"cn": "coc", "enabled": True, "signed": False})

        self._login_as_freeipa_user("alex")
        with (
            patch("core.views_users.has_enabled_agreements", return_value=True),
            patch("core.views_users.list_agreements_for_user", return_value=[agreement]),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.backends.FreeIPAUser.get", return_value=alex),
        ):
            resp = self.client.get(reverse("user-profile", kwargs={"username": "alex"}))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '/settings/?tab=agreements')
        self.assertContains(resp, 'return=profile')
        self.assertContains(resp, 'href="/settings/?tab=profile&amp;highlight=country_code"')

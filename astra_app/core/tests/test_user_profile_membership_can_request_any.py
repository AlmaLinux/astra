from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import MembershipRequest, MembershipType


class UserProfileMembershipCanRequestAnyTests(TestCase):
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
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "isIndividual": False,
                "isOrganization": False,
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

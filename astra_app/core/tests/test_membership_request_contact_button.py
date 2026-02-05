from __future__ import annotations

from unittest.mock import patch
from urllib.parse import urlencode

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType, Organization
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_ADD_SEND_MAIL, ASTRA_VIEW_MEMBERSHIP


class MembershipRequestRfiButtonTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        for perm in (ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP, ASTRA_ADD_SEND_MAIL):
            FreeIPAPermissionGrant.objects.get_or_create(
                permission=perm,
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name=committee_cn,
            )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_rfi_button_opens_modal_for_user_request(self) -> None:
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

        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )
        alice = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []})

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, ">RFI<")
        self.assertContains(resp, "Request for Information")
        self.assertContains(resp, f'data-target="#rfi-modal-{req.pk}"')
        self.assertContains(resp, f'id="rfi-modal-{req.pk}"')
        self.assertContains(resp, f'action="{reverse("membership-request-rfi", args=[req.pk])}"')
        self.assertContains(resp, 'name="rfi_message"')

    def test_rfi_modal_includes_message_preset(self) -> None:
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

        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )
        alice = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []})

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp,
            "Please provide additional details about your involvement with the AlmaLinux community, including any "
            "contributions, participation, or other relevant activities.",
        )

    def test_contact_button_links_to_send_mail_for_user_request(self) -> None:
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

        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )
        alice = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []})

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, ">Contact<")

        expected_href = f"{reverse('send-mail')}?{urlencode({'type': 'users', 'to': 'alice', 'template': '', 'membership_request_id': str(req.pk), 'reply_to': settings.MEMBERSHIP_COMMITTEE_EMAIL})}"
        expected_href_html = expected_href.replace("&", "&amp;")
        self.assertContains(resp, f'href="{expected_href_html}"')

    def test_rfi_button_opens_modal_for_org_representative(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "group_cn": "almalinux-gold",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Example Org", representative="orgrep")
        req = MembershipRequest.objects.create(requested_username="", requested_organization=org, membership_type_id="gold")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )

        self._login_as_freeipa_user("reviewer")
        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, ">RFI<")
        self.assertContains(resp, "Request for Information")
        self.assertContains(resp, f'data-target="#rfi-modal-{req.pk}"')
        self.assertContains(resp, f'id="rfi-modal-{req.pk}"')
        self.assertContains(resp, f'action="{reverse("membership-request-rfi", args=[req.pk])}"')
        self.assertContains(resp, 'name="rfi_message"')

    def test_contact_button_links_to_send_mail_for_org_representative(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "group_cn": "almalinux-gold",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Example Org", representative="orgrep")
        req = MembershipRequest.objects.create(requested_username="", requested_organization=org, membership_type_id="gold")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )
        orgrep = FreeIPAUser("orgrep", {"uid": ["orgrep"], "mail": ["orgrep@example.com"], "memberof_group": []})

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "orgrep":
                return orgrep
            return None

        self._login_as_freeipa_user("reviewer")
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("membership-request-detail", args=[req.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, ">Contact<")

        expected_href = f"{reverse('send-mail')}?{urlencode({'type': 'users', 'to': 'orgrep', 'template': '', 'membership_request_id': str(req.pk), 'reply_to': settings.MEMBERSHIP_COMMITTEE_EMAIL})}"
        expected_href_html = expected_href.replace("&", "&amp;")
        self.assertContains(resp, f'href="{expected_href_html}"')

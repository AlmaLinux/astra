from __future__ import annotations

from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_ADD_SEND_MAIL


class MembershipReasonEmailUnescapingTests(TestCase):
    def setUp(self) -> None:
        super().setUp()

        self._freeipa_users: dict[str, FreeIPAUser] = {}
        patcher = patch("core.backends.FreeIPAUser.get", side_effect=self._get_freeipa_user)
        patcher.start()
        self.addCleanup(patcher.stop)

        for perm in (ASTRA_ADD_MEMBERSHIP, ASTRA_ADD_SEND_MAIL):
            FreeIPAPermissionGrant.objects.get_or_create(
                permission=perm,
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _get_freeipa_user(self, username: str) -> FreeIPAUser | None:
        return self._freeipa_users.get(str(username))

    def _add_freeipa_user(self, *, username: str, email: str, groups: list[str]) -> None:
        self._freeipa_users[username] = FreeIPAUser(
            username,
            {
                "uid": [username],
                "mail": [email],
                "memberof_group": list(groups),
            },
        )

    def test_rfi_message_is_not_html_entity_escaped_in_email_bodies(self) -> None:
        from post_office.models import Email

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

        self._add_freeipa_user(username="reviewer", email="reviewer@example.com", groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP])
        self._add_freeipa_user(username="alice", email="alice@example.com", groups=[])

        self._login_as_freeipa_user("reviewer")

        before = Email.objects.count()
        message = "What's missing from your application?"
        resp = self.client.post(
            reverse("membership-request-rfi", args=[req.pk]),
            data={"rfi_message": message},
            follow=False,
        )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Email.objects.count(), before + 1)

        email = Email.objects.latest("created")
        self.assertEqual(email.template.name, settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME)
        self.assertIn(message, str(email.message or ""))
        self.assertIn(message, str(email.html_message or ""))
        self.assertNotIn("&#x27;", str(email.message or ""))
        self.assertNotIn("&#x27;", str(email.html_message or ""))

    def test_rejection_reason_is_not_html_entity_escaped_in_email_bodies(self) -> None:
        from post_office.models import Email

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

        self._add_freeipa_user(username="reviewer", email="reviewer@example.com", groups=[settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP])
        self._add_freeipa_user(username="alice", email="alice@example.com", groups=[])

        self._login_as_freeipa_user("reviewer")

        before = Email.objects.count()
        reason = "It's not enough to approve yet."
        resp = self.client.post(
            reverse("membership-request-reject", args=[req.pk]),
            data={"reason": reason},
            follow=False,
        )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Email.objects.count(), before + 1)

        email = Email.objects.latest("created")
        self.assertEqual(email.template.name, settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME)
        self.assertIn(reason, str(email.message or ""))
        self.assertIn(reason, str(email.html_message or ""))
        self.assertNotIn("&#x27;", str(email.message or ""))
        self.assertNotIn("&#x27;", str(email.html_message or ""))

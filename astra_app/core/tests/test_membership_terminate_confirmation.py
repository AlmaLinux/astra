import datetime
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Membership, MembershipType
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP
from core.tests.test_membership_dues import MembershipDuesTestMixin


class TerminateConfirmationTests(MembershipDuesTestMixin, TestCase):
    """The typed-name confirmation is checked by the server, not only by the browser."""

    def setUp(self) -> None:
        super().setUp()
        for permission in (ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP):
            FreeIPAPermissionGrant.objects.create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name="committee",
            )
        users = {
            username: FreeIPAUser(username, {"uid": [username], "mail": [f"{username}@example.com"], "memberof_group": []})
            for username in ("committee", "rep1")
        }
        patcher = patch("core.freeipa.user.FreeIPAUser.get", side_effect=lambda username, **_kwargs: users.get(username))
        patcher.start()
        self.addCleanup(patcher.stop)
        session = self.client.session
        session["_freeipa_username"] = "committee"
        session.save()

    def _messages(self, response) -> list[str]:
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_sponsorship_termination_needs_the_organization_name(self) -> None:
        url = reverse("membership-terminate", args=[self._active_gold().pk])

        for confirm in ("", "Some Other Org"):
            with self.subTest(confirm=confirm):
                response = self.client.post(url, data={"confirm": confirm})
                self.assertIn("Type the organization name to confirm the termination.", self._messages(response))
                self.assertTrue(Membership.objects.filter(target_organization=self.organization).exists())

        self.client.post(url, data={"confirm": f"  {self.organization.name.upper()} "})
        self.assertFalse(Membership.objects.filter(target_organization=self.organization).exists())

    def test_user_membership_termination_needs_the_username(self) -> None:
        individual, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={"name": "Individual", "group_cn": "almalinux-individual", "category_id": "individual", "enabled": True},
        )
        membership = Membership.objects.create(
            target_username="rep1", membership_type=individual, expires_at=timezone.now() + datetime.timedelta(days=30)
        )
        url = reverse("membership-terminate", args=[membership.pk])

        response = self.client.post(url)
        self.assertIn("Type the username to confirm the termination.", self._messages(response))
        self.assertTrue(Membership.objects.filter(target_username="rep1").exists())

        self.client.post(url, data={"confirm": "REP1"})
        self.assertFalse(Membership.objects.filter(target_username="rep1").exists())

    def test_a_member_missing_from_freeipa_can_still_be_terminated(self) -> None:
        individual, _ = MembershipType.objects.update_or_create(
            code="individual",
            defaults={"name": "Individual", "group_cn": "almalinux-individual", "category_id": "individual", "enabled": True},
        )
        # With no FreeIPA account there is no group access left to revoke.
        membership = Membership.objects.create(
            target_username="ghost", membership_type=individual, expires_at=timezone.now() + datetime.timedelta(days=30)
        )

        response = self.client.post(reverse("membership-terminate", args=[membership.pk]), data={"confirm": "ghost"})

        self.assertIn("Membership terminated.", self._messages(response))
        self.assertFalse(Membership.objects.filter(target_username="ghost").exists())

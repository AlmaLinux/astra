
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
from core.models import MembershipType
from core.tests.utils_test_data import ensure_core_categories


class AdminProtectedIPAGroupTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_cannot_delete_group_referenced_by_settings(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        protected_group = FreeIPAGroup("admins", {"cn": ["admins"], "description": ["d"], "member": []})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[protected_group]),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=protected_group),
            patch.object(protected_group, "delete") as delete_mock,
        ):
            url = reverse("admin:auth_ipagroup_changelist")
            resp = self.client.post(
                url,
                data={
                    "action": "delete_selected",
                    "_selected_action": ["admins"],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        delete_mock.assert_not_called()
        self.assertContains(resp, "cannot be deleted")

    def test_cannot_delete_group_referenced_by_membership_type(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})

        MembershipType.objects.update_or_create(
            code="individual_protected_group",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "votes": 1,
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

        protected_group = FreeIPAGroup(
            "almalinux-individual",
            {"cn": ["almalinux-individual"], "description": ["d"], "member": []},
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[protected_group]),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=protected_group),
            patch.object(protected_group, "delete") as delete_mock,
        ):
            url = reverse("admin:auth_ipagroup_changelist")
            resp = self.client.post(
                url,
                data={
                    "action": "delete_selected",
                    "_selected_action": ["almalinux-individual"],
                    "post": "yes",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        delete_mock.assert_not_called()
        self.assertContains(resp, "cannot be deleted")

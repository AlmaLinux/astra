
from unittest.mock import patch

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
from core.tests.utils_test_data import ensure_core_categories


class AdminMembershipTypeCRUDTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_admin_can_create_membership_type_and_is_logged(self) -> None:
        from core.models import MembershipType

        self._login_as_freeipa_admin("alice")
        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            url = reverse("admin:core_membershiptype_add")
            resp = self.client.post(
                url,
                data={
                    "code": "partner",
                    "name": "Partner",
                    "category": "sponsorship",
                    "sort_order": 70,
                    "enabled": True,
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(MembershipType.objects.filter(code="partner").exists())

        ContentType.objects.clear_cache()
        ContentType.objects.get_for_model(MembershipType)

        from django.contrib.auth import get_user_model

        shadow_user = get_user_model().objects.get(username="alice")
        entry = LogEntry.objects.order_by("-action_time").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.user_id, shadow_user.pk)
        self.assertEqual(entry.action_flag, ADDITION)
        self.assertEqual(entry.object_id, "partner")

    def test_admin_cannot_edit_code(self) -> None:
        from core.models import MembershipType

        MembershipType.objects.create(
            code="partner",
            name="Partner",
            category_id="sponsorship",
            sort_order=70,
            enabled=True,
        )

        self._login_as_freeipa_admin("alice")
        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            url = reverse("admin:core_membershiptype_change", args=["partner"])
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        # When a field is marked readonly_fields, Django admin renders it as a
        # non-input element (no name="code" input).
        self.assertNotContains(resp, 'name="code"')

    def test_admin_can_set_group(self) -> None:
        from core.models import MembershipType

        self._login_as_freeipa_admin("alice")
        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        groups = [
            FreeIPAGroup("sponsors", {"cn": ["sponsors"], "description": ["Sponsors"]}),
            FreeIPAGroup("mirror-admins", {"cn": ["mirror-admins"], "description": ["Mirror admins"]}),
        ]

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAGroup.all", return_value=groups),
        ):
            url = reverse("admin:core_membershiptype_add")
            resp = self.client.post(
                url,
                data={
                    "code": "partner",
                    "name": "Partner",
                    "group_cn": "mirror-admins",
                    "category": "sponsorship",
                    "sort_order": 70,
                    "enabled": True,
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        obj = MembershipType.objects.get(code="partner")
        self.assertEqual(obj.group_cn, "mirror-admins")

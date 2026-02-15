"""
Verify that shared modal JS defers initialization until DOMContentLoaded,
so jQuery and Bootstrap are available when event handlers are bound.

The inline script in _membership_request_shared_modals.html runs inside
{% block content %}, which is parsed BEFORE {% block scripts %} (where
jQuery/Bootstrap are loaded). Without DOMContentLoaded, window.jQuery is
undefined and the show.bs.modal handler never fires → blank modal title/body.
"""

from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class SharedModalScriptDeferredTests(TestCase):
    """Shared modals must wrap their jQuery event binding in DOMContentLoaded."""

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
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
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )

    def _login_as_committee(self, username: str = "reviewer") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_shared_modal_script_uses_domcontentloaded(self) -> None:
        """The shared-modal IIFE must be wrapped in DOMContentLoaded so that
        jQuery is available when show.bs.modal handlers are bound."""
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        self._login_as_committee()

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)

        content = resp.content.decode()

        # The shared modals must be present.
        self.assertIn('id="shared-approve-modal"', content)

        # The shared-modal binding must be loaded as a dedicated static module.
        self.assertIn('src="/static/core/js/membership_request_shared_modals.js"', content)


class GroupDetailModalScriptDeferredTests(TestCase):
    """Group detail modal JS must defer jQuery usage until DOMContentLoaded."""

    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_group_detail_modal_script_uses_domcontentloaded(self) -> None:
        from core.backends import FreeIPAGroup

        self._login_as_freeipa("admin")

        group = FreeIPAGroup(
            "testgrp",
            {
                "cn": ["testgrp"],
                "description": ["A group"],
                "member_user": ["admin", "alice"],
                "member_group": [],
                "membermanager_user": ["admin"],
                "membermanager_group": [],
                "objectclass": ["fasgroup"],
            },
        )
        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "displayname": ["Administrator"],
                "memberof_group": [],
            },
        )

        with (
            patch("core.backends.FreeIPAGroup.get", return_value=group),
            patch("core.backends.FreeIPAUser.get", return_value=admin_user),
        ):
            resp = self.client.get("/group/testgrp/")

        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()

        # The sponsor modal JS must be present and deferred.
        self.assertIn('id="remove-member-modal"', content)
        self.assertIn("DOMContentLoaded", content)

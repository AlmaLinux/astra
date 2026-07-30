"""Lock the two membership-panel payloads to one shape.

The user profile and the organization detail page render the same membership
panel. They used to serialize it independently, and the organization side
shipped for months without a renewal CTA because the capability flag simply was
not in its payload -- nothing compared the two. These tests fail if the shapes
drift again.
"""

import datetime
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.membership_payloads import (
    MEMBERSHIP_ENTRY_CAPABILITY_KEYS,
    serialize_membership_entry,
    serialize_pending_membership_entry,
)
from core.models import (
    FreeIPAPermissionGrant,
    Membership,
    MembershipRequest,
    MembershipType,
    Organization,
)
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories


class MembershipPayloadContractTests(TestCase):
    def setUp(self) -> None:
        ensure_core_categories()
        self._country_code_patcher = patch(
            "core.views_membership.user.block_action_without_country_code",
            return_value=None,
        )
        self._country_code_patcher.start()
        self.addCleanup(self._country_code_patcher.stop)

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
                "group_cn": "almalinux-gold",
            },
        )
        MembershipType.objects.update_or_create(
            code="platinum",
            defaults={
                "name": "Platinum Sponsor Member",
                "description": "Platinum Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-platinum",
            },
        )
        # Individual tiers for the user profile surface: an organization cannot
        # hold an individual membership and vice versa, so each surface needs a
        # two-tier category of its own to exercise the tier-change suggestion.
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "description": "Individual",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-individual",
            },
        )
        MembershipType.objects.update_or_create(
            code="individual_plus",
            defaults={
                "name": "Individual Plus",
                "description": "Individual Plus",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
                "group_cn": "almalinux-individual-plus",
            },
        )
        # Keep the suggestion deterministic against migration-seeded tiers.
        MembershipType.objects.exclude(
            code__in=["gold", "platinum", "individual", "individual_plus"]
        ).update(enabled=False)

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _organization_membership_entry(self, *, username: str) -> dict:
        organization = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=organization,
            membership_type_id="gold",
            expires_at=timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1),
        )

        actor = FreeIPAUser(username, {"uid": [username], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user(username)
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=actor):
            response = self.client.get(
                reverse("api-organization-detail-page", args=[organization.pk]),
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        memberships = response.json()["organization"]["memberships"]
        self.assertTrue(memberships, "expected an active membership in the organization payload")
        return memberships[0]

    def _user_profile_membership_entry(self, *, username: str) -> dict:
        Membership.objects.create(
            target_username=username,
            membership_type_id="individual",
            expires_at=timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1),
        )

        actor = FreeIPAUser(username, {"uid": [username], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user(username)
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=actor):
            response = self.client.get(
                reverse("api-user-profile-detail", args=[username]),
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        entries = response.json()["membership"]["entries"]
        self.assertTrue(entries, "expected an active membership in the user profile payload")
        return entries[0]

    def test_both_surfaces_emit_the_same_membership_entry_keys(self) -> None:
        organization_entry = self._organization_membership_entry(username="bob")
        profile_entry = self._user_profile_membership_entry(username="carol")

        self.assertEqual(
            set(organization_entry.keys()),
            set(profile_entry.keys()),
            "membership entry shapes drifted between the organization and user profile payloads",
        )

    def test_both_surfaces_emit_every_declared_capability_flag(self) -> None:
        organization_entry = self._organization_membership_entry(username="bob")
        profile_entry = self._user_profile_membership_entry(username="carol")

        for surface, entry in (("organization", organization_entry), ("user profile", profile_entry)):
            missing = MEMBERSHIP_ENTRY_CAPABILITY_KEYS - set(entry.keys())
            self.assertEqual(missing, set(), f"{surface} payload is missing capability flags: {sorted(missing)}")
            for key in MEMBERSHIP_ENTRY_CAPABILITY_KEYS:
                self.assertIsInstance(entry[key], bool, f"{surface} payload flag {key} must be a bool")

    def test_declared_capability_keys_match_the_serializer_output(self) -> None:
        """Guard the declaration itself: a new `can*` flag must join the frozenset."""

        entry = serialize_membership_entry(
            membership_type=MembershipType.objects.get(code="gold"),
            created_at=timezone.now(),
            expires_at=timezone.now(),
            request_id=None,
            is_expiring_soon=True,
            has_pending_request_in_category=False,
            can_request_tier_change=True,
            tier_change_membership_type_code="platinum",
            can_act=True,
            can_view=True,
            can_manage=True,
        )
        boolean_capability_keys = {
            key for key, value in entry.items() if key.startswith("can") and isinstance(value, bool)
        }
        self.assertEqual(
            boolean_capability_keys,
            set(MEMBERSHIP_ENTRY_CAPABILITY_KEYS),
            "MEMBERSHIP_ENTRY_CAPABILITY_KEYS is out of sync with serialize_membership_entry",
        )

    def test_both_surfaces_emit_the_same_pending_entry_keys(self) -> None:
        shared = set(
            serialize_pending_membership_entry(
                membership_type=MembershipType.objects.get(code="gold"),
                request_id=7,
                status="pending",
            ).keys()
        )

        organization = Organization.objects.create(name="Acme Pending", representative="bob")
        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=organization,
            membership_type_id="gold",
            status=MembershipRequest.Status.pending,
        )
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=bob):
            response = self.client.get(
                reverse("api-organization-detail-page", args=[organization.pk]),
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        pending = response.json()["organization"]["pending_memberships"]
        self.assertTrue(pending)
        self.assertEqual(set(pending[0].keys()), shared)

    def test_renewal_targets_the_held_tier_on_both_surfaces(self) -> None:
        """Renewal must re-request the held tier, never the tier-change suggestion."""

        organization_entry = self._organization_membership_entry(username="bob")
        profile_entry = self._user_profile_membership_entry(username="carol")

        for surface, entry in (("organization", organization_entry), ("user profile", profile_entry)):
            self.assertTrue(entry["canRenew"], f"{surface} entry should be renewable")
            self.assertEqual(
                entry["renewalMembershipTypeCode"],
                entry["membershipType"]["code"],
                f"{surface} renewal CTA must target the held tier",
            )

    def test_tier_change_suggests_a_different_tier_on_both_surfaces(self) -> None:
        """A "Change tier" CTA that pre-fills the held tier is a no-op for the user."""

        organization_entry = self._organization_membership_entry(username="bob")
        profile_entry = self._user_profile_membership_entry(username="carol")

        expected = (
            ("organization", organization_entry, "platinum"),
            ("user profile", profile_entry, "individual_plus"),
        )
        for surface, entry, suggested_code in expected:
            self.assertTrue(entry["canRequestTierChange"], f"{surface} entry should offer a tier change")
            self.assertEqual(
                entry["tierChangeMembershipTypeCode"],
                suggested_code,
                f"{surface} tier-change CTA should suggest the neighbouring tier",
            )
            self.assertNotEqual(
                entry["tierChangeMembershipTypeCode"],
                entry["membershipType"]["code"],
                f"{surface} tier-change CTA must not pre-fill the tier already held",
            )

    def test_capability_flags_are_withheld_from_a_read_only_reviewer(self) -> None:
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="dave",
        )

        entry = self._organization_membership_entry(username="dave")

        self.assertTrue(entry["isExpiringSoon"])
        self.assertFalse(entry["canRenew"])
        self.assertEqual(entry["renewalMembershipTypeCode"], "")
        self.assertFalse(entry["canRequestTierChange"])
        self.assertEqual(entry["tierChangeMembershipTypeCode"], "")

    def test_capability_flags_are_granted_to_a_committee_member(self) -> None:
        for permission in (ASTRA_ADD_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP):
            FreeIPAPermissionGrant.objects.create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name="erin",
            )

        entry = self._organization_membership_entry(username="erin")

        self.assertTrue(entry["canRenew"])
        self.assertEqual(entry["renewalMembershipTypeCode"], "gold")

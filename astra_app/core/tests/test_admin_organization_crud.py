
from unittest.mock import patch

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.tests.utils_test_data import ensure_core_categories


class AdminOrganizationCRUDTests(TestCase):
    def setUp(self) -> None:
        ensure_core_categories()

    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _organization_change_payload(
        self,
        *,
        organization,
        representative: str,
        inline_rows: list[dict[str, str]],
    ) -> dict[str, str]:
        payload = {
            "name": organization.name,
            "business_contact_name": organization.business_contact_name,
            "business_contact_email": organization.business_contact_email,
            "business_contact_phone": organization.business_contact_phone,
            "pr_marketing_contact_name": organization.pr_marketing_contact_name,
            "pr_marketing_contact_email": organization.pr_marketing_contact_email,
            "pr_marketing_contact_phone": organization.pr_marketing_contact_phone,
            "technical_contact_name": organization.technical_contact_name,
            "technical_contact_email": organization.technical_contact_email,
            "technical_contact_phone": organization.technical_contact_phone,
            "website_logo": organization.website_logo,
            "website": organization.website,
            "street": organization.street,
            "city": organization.city,
            "state": organization.state,
            "postal_code": organization.postal_code,
            "country_code": organization.country_code,
            "status": organization.status,
            "representative": representative,
            "memberships-TOTAL_FORMS": str(len(inline_rows)),
            "memberships-INITIAL_FORMS": str(len(inline_rows)),
            "memberships-MIN_NUM_FORMS": "0",
            "memberships-MAX_NUM_FORMS": "1000",
            "_save": "Save",
        }

        for index, row in enumerate(inline_rows):
            for key, value in row.items():
                payload[f"memberships-{index}-{key}"] = value

        return payload

    def test_admin_can_create_organization_with_representatives_and_is_logged(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        rep_user = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAUser.all", return_value=[admin_user, rep_user]),
        ):
            url = reverse("admin:core_organization_add")
            resp = self.client.post(
                url,
                data={
                    "name": "AlmaLinux",
                    "business_contact_name": "Business Person",
                    "business_contact_email": "contact@almalinux.org",
                    "pr_marketing_contact_name": "PR Person",
                    "pr_marketing_contact_email": "pr@almalinux.org",
                    "technical_contact_name": "Tech Person",
                    "technical_contact_email": "tech@almalinux.org",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://almalinux.org/",
                    "notes": "Internal notes",
                    "representative": "bob",
                    # MembershipInline management form (no inline rows).
                    "memberships-TOTAL_FORMS": "0",
                    "memberships-INITIAL_FORMS": "0",
                    "memberships-MIN_NUM_FORMS": "0",
                    "memberships-MAX_NUM_FORMS": "1000",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        org = Organization.objects.get(name="AlmaLinux")
        self.assertEqual(org.name, "AlmaLinux")
        self.assertEqual(org.representative, "bob")

        ContentType.objects.clear_cache()
        ContentType.objects.get_for_model(Organization)

        from django.contrib.auth import get_user_model

        shadow_user = get_user_model().objects.get(username="alice")
        entry = LogEntry.objects.order_by("-action_time").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.user_id, shadow_user.pk)
        self.assertEqual(entry.action_flag, ADDITION)
        self.assertEqual(entry.object_id, str(org.pk))

    def test_admin_can_create_unclaimed_organization_without_representative(self) -> None:
        from core.models import Organization

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user),
            patch("core.admin.FreeIPAUser.all", return_value=[admin_user]),
        ):
            response = self.client.post(
                reverse("admin:core_organization_add"),
                data={
                    "name": "Admin Unclaimed Org",
                    "business_contact_name": "Business Person",
                    "business_contact_email": "contact@example.org",
                    "pr_marketing_contact_name": "PR Person",
                    "pr_marketing_contact_email": "pr@example.org",
                    "technical_contact_name": "Tech Person",
                    "technical_contact_email": "tech@example.org",
                    "website_logo": "https://example.org/logo-options",
                    "website": "https://example.org/",
                    "notes": "Internal notes",
                    "status": Organization.Status.unclaimed,
                    "representative": "",
                    # MembershipInline management form (no inline rows).
                    "memberships-TOTAL_FORMS": "0",
                    "memberships-INITIAL_FORMS": "0",
                    "memberships-MIN_NUM_FORMS": "0",
                    "memberships-MAX_NUM_FORMS": "1000",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        organization = Organization.objects.get(name="Admin Unclaimed Org")
        self.assertEqual(organization.representative, "")
        self.assertEqual(organization.status, Organization.Status.unclaimed)
        self.assertTrue(organization.claim_secret)

    def test_admin_add_form_hides_legacy_additional_information_field(self) -> None:
        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            resp = self.client.get(reverse("admin:core_organization_add"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="id_additional_information"')

    def test_admin_generate_claim_url_action_for_unclaimed_org(self) -> None:
        from core.models import Organization

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            organization = Organization.objects.create(name="Unclaimed Org")

            response = self.client.post(
                reverse("admin:core_organization_changelist"),
                data={
                    "action": "generate_claim_url",
                    "_selected_action": [str(organization.pk)],
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/organizations/claim/")

    def test_admin_change_representative_transfers_active_sponsorship_groups(self) -> None:
        from core.models import Membership, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-gold",
            },
        )

        organization = Organization.objects.create(
            name="Admin Existing Org",
            representative="bob",
            country_code="US",
            business_contact_name="Business Person",
            business_contact_email="contact@example.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@example.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@example.org",
            website_logo="https://example.org/logo-options",
            website="https://example.org/",
        )
        membership = Membership.objects.create(
            target_organization=organization,
            membership_type_id="gold",
        )

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        old_rep = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": ["almalinux-gold"]})
        new_rep = FreeIPAUser("carol", {"uid": ["carol"], "memberof_group": []})

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alice":
                return admin_user
            if username == "bob":
                return old_rep
            if username == "carol":
                return new_rep
            return None

        payload = self._organization_change_payload(
            organization=organization,
            representative="carol",
            inline_rows=[
                {
                    "id": str(membership.pk),
                    "target_organization": str(organization.pk),
                    "membership_type": "gold",
                    "expires_at": "",
                }
            ],
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.admin.FreeIPAUser.all", return_value=[admin_user, old_rep, new_rep]),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock,
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock,
        ):
            response = self.client.post(
                reverse("admin:core_organization_change", args=[organization.pk]),
                data=payload,
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        organization.refresh_from_db()
        self.assertEqual(organization.representative, "carol")
        remove_mock.assert_called_once()
        _, remove_kwargs = remove_mock.call_args
        self.assertEqual(remove_kwargs["group_name"], "almalinux-gold")
        add_mock.assert_called_once()
        _, add_kwargs = add_mock.call_args
        self.assertEqual(add_kwargs["group_name"], "almalinux-gold")

    def test_admin_change_representative_and_sponsorship_inline_requires_separate_saves(self) -> None:
        from core.models import Membership, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-gold",
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
                "group_cn": "almalinux-silver",
            },
        )

        organization = Organization.objects.create(
            name="Admin Mixed Save Org",
            representative="bob",
            country_code="US",
            business_contact_name="Business Person",
            business_contact_email="contact@example.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@example.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@example.org",
            website_logo="https://example.org/logo-options",
            website="https://example.org/",
        )
        membership = Membership.objects.create(
            target_organization=organization,
            membership_type_id="gold",
        )

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        old_rep = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": ["almalinux-gold"]})
        new_rep = FreeIPAUser("carol", {"uid": ["carol"], "memberof_group": []})

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alice":
                return admin_user
            if username == "bob":
                return old_rep
            if username == "carol":
                return new_rep
            return None

        payload = self._organization_change_payload(
            organization=organization,
            representative="carol",
            inline_rows=[
                {
                    "id": str(membership.pk),
                    "target_organization": str(organization.pk),
                    "membership_type": "silver",
                    "expires_at": "",
                }
            ],
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.admin.FreeIPAUser.all", return_value=[admin_user, old_rep, new_rep]),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock,
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock,
        ):
            response = self.client.post(
                reverse("admin:core_organization_change", args=[organization.pk]),
                data=payload,
                follow=False,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Change the representative and sponsorship memberships in separate saves.")
        organization.refresh_from_db()
        membership.refresh_from_db()
        self.assertEqual(organization.representative, "bob")
        self.assertEqual(membership.membership_type_id, "gold")
        remove_mock.assert_not_called()
        add_mock.assert_not_called()

    def test_admin_inline_failure_after_representative_change_does_not_sync_freeipa(self) -> None:
        from core.models import Membership, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-gold",
            },
        )
        MembershipType.objects.update_or_create(
            code="contributor-basic",
            defaults={
                "name": "Contributor Basic",
                "category_id": "contributor",
                "sort_order": 2,
                "enabled": True,
                "group_cn": "",
            },
        )
        MembershipType.objects.update_or_create(
            code="contributor-plus",
            defaults={
                "name": "Contributor Plus",
                "category_id": "contributor",
                "sort_order": 3,
                "enabled": True,
                "group_cn": "",
            },
        )

        organization = Organization.objects.create(
            name="Admin Inline Failure Org",
            representative="bob",
            country_code="US",
            business_contact_name="Business Person",
            business_contact_email="contact@example.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@example.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@example.org",
            website_logo="https://example.org/logo-options",
            website="https://example.org/",
        )
        sponsorship_membership = Membership.objects.create(
            target_organization=organization,
            membership_type_id="gold",
        )
        contributor_membership = Membership.objects.create(
            target_organization=organization,
            membership_type_id="contributor-basic",
        )

        self._login_as_freeipa_admin("alice")

        admin_user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        old_rep = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": ["almalinux-gold"]})
        new_rep = FreeIPAUser("carol", {"uid": ["carol"], "memberof_group": []})

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "alice":
                return admin_user
            if username == "bob":
                return old_rep
            if username == "carol":
                return new_rep
            return None

        payload = self._organization_change_payload(
            organization=organization,
            representative="carol",
            inline_rows=[
                {
                    "id": str(sponsorship_membership.pk),
                    "target_organization": str(organization.pk),
                    "membership_type": "gold",
                    "expires_at": "",
                },
                {
                    "id": str(contributor_membership.pk),
                    "target_organization": str(organization.pk),
                    "membership_type": "contributor-basic",
                    "expires_at": "",
                },
                {
                    "id": "",
                    "target_organization": str(organization.pk),
                    "membership_type": "contributor-plus",
                    "expires_at": "",
                },
            ],
        )
        payload["memberships-TOTAL_FORMS"] = "3"
        payload["memberships-INITIAL_FORMS"] = "2"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.admin.FreeIPAUser.all", return_value=[admin_user, old_rep, new_rep]),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock,
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock,
        ):
            response = self.client.post(
                reverse("admin:core_organization_change", args=[organization.pk]),
                data=payload,
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        organization.refresh_from_db()
        self.assertEqual(organization.representative, "bob")
        self.assertEqual(
            Membership.objects.filter(
                target_organization=organization,
                membership_type_id="contributor-plus",
            ).count(),
            0,
        )
        remove_mock.assert_not_called()
        add_mock.assert_not_called()

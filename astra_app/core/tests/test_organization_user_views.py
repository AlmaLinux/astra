from __future__ import annotations

import datetime
from io import BytesIO
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import patch
from urllib.parse import quote

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP


class OrganizationUserViewsTests(TestCase):
    _test_media_root = Path(mkdtemp(prefix="alx_test_media_"))

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _valid_org_payload(self, *, name: str) -> dict[str, str]:
        return {
            "name": name,
            "business_contact_name": "Business",
            "business_contact_email": "business@example.com",
            "business_contact_phone": "",
            "pr_marketing_contact_name": "Marketing",
            "pr_marketing_contact_email": "marketing@example.com",
            "pr_marketing_contact_phone": "",
            "technical_contact_name": "Tech",
            "technical_contact_email": "tech@example.com",
            "technical_contact_phone": "",
            "website_logo": "https://example.com/logo-options",
            "website": "https://example.com/",
            "additional_information": "",
        }

    def test_non_committee_representative_cannot_create_second_org(self) -> None:
        from core.models import Organization

        Organization.objects.create(
            name="Existing Org",
            representative="bob",
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-create"), follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("organizations"))

        payload = self._valid_org_payload(name="Second Org")
        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("organizations"))
        self.assertFalse(Organization.objects.filter(name="Second Org").exists())

    def test_organization_create_requires_signed_coc(self) -> None:
        from core.backends import FreeIPAFASAgreement
        from core.models import Organization

        self._login_as_freeipa_user("alice")

        coc = FreeIPAFASAgreement(
            settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN,
            {
                "cn": [settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN],
                "ipaenabledflag": ["TRUE"],
                "memberuser_user": [],
            },
        )

        payload = self._valid_org_payload(name="Blocked Org")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", autospec=True, return_value=alice):
            with patch("core.views_utils.FreeIPAFASAgreement.get", autospec=True, return_value=coc):
                resp = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(resp.status_code, 302)
        expected = (
            f"{reverse('settings')}?agreement={quote(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN)}#agreements"
        )
        self.assertEqual(resp["Location"], expected)
        self.assertFalse(Organization.objects.filter(name="Blocked Org").exists())

    def test_committee_can_create_org_for_other_rep_even_if_already_rep(self) -> None:
        from core.models import Organization

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        Organization.objects.create(
            name="Reviewer Org",
            representative="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": []})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})

        def fake_get(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")

        payload = self._valid_org_payload(name="New Org")
        payload["representative"] = "bob"

        with patch("core.backends.FreeIPAUser.get", side_effect=fake_get):
            resp = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(resp.status_code, 302)
        created = Organization.objects.get(name="New Org")
        self.assertEqual(created.representative, "bob")

    def test_representatives_search_excludes_existing_reps_except_current_org(self) -> None:
        from core.models import Organization

        org = Organization.objects.create(
            name="Org",
            representative="bob",
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        bob_user = FreeIPAUser("bob", {"uid": ["bob"], "displayname": ["Bob Example"], "memberof_group": []})
        bobby_user = FreeIPAUser("bobby", {"uid": ["bobby"], "displayname": ["Bobby Example"], "memberof_group": []})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.backends.FreeIPAUser.all", return_value=[bobby_user, bob_user]),
        ):
            url = reverse("organization-representatives-search")
            resp = self.client.get(url, {"q": "bo"})
            self.assertEqual(resp.status_code, 200)
            ids = [r.get("id") for r in resp.json().get("results")]
            self.assertEqual(ids, ["bobby"])

            resp = self.client.get(url, {"q": "bo", "organization_id": str(org.pk)})
            self.assertEqual(resp.status_code, 200)
            ids = [r.get("id") for r in resp.json().get("results")]
            self.assertEqual(ids, ["bob", "bobby"])

    def test_committee_cannot_set_representative_to_user_already_representing_other_org(self) -> None:
        from core.models import Organization

        Organization.objects.create(
            name="Org 1",
            representative="bob",
        )

        org2 = Organization.objects.create(
            name="Org 2",
            business_contact_name="Business",
            business_contact_email="business@example.com",
            pr_marketing_contact_name="Marketing",
            pr_marketing_contact_email="marketing@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website_logo="https://example.com/logo",
            website="https://example.com/",
            representative="carol",
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": []})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        carol = FreeIPAUser("carol", {"uid": ["carol"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        def fake_get(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            if username == "carol":
                return carol
            return None

        payload = self._valid_org_payload(name="Org 2")
        payload["representative"] = "bob"

        with patch("core.backends.FreeIPAUser.get", side_effect=fake_get):
            resp = self.client.post(reverse("organization-edit", args=[org2.pk]), data=payload, follow=False)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already the representative of another organization")
        org2.refresh_from_db()
        self.assertEqual(org2.representative, "carol")

    def test_db_unique_constraint_prevents_duplicate_representatives(self) -> None:
        from core.models import Organization

        Organization.objects.create(
            name="Org 1",
            representative="bob",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Organization.objects.create(
                    name="Org 2",
                    representative="bob",
                )

    def test_representative_change_creates_membershiplog_for_pending_org_request(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType, Note, Organization

        org = Organization.objects.create(
            name="Org",
            representative="carol",
        )

        membership_type, _ = MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        mr = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type=membership_type,
            status=MembershipRequest.Status.pending,
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": []})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        carol = FreeIPAUser("carol", {"uid": ["carol"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        def fake_get(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            if username == "carol":
                return carol
            return None

        payload = self._valid_org_payload(name="Org")
        payload["representative"] = "bob"

        with patch("core.backends.FreeIPAUser.get", side_effect=fake_get):
            resp = self.client.post(reverse("organization-edit", args=[org.pk]), data=payload, follow=False)

        self.assertEqual(resp.status_code, 302)
        org.refresh_from_db()
        self.assertEqual(org.representative, "bob")

        log = MembershipLog.objects.filter(
            membership_request=mr,
            target_organization=org,
            action=MembershipLog.Action.representative_changed,
        ).first()
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.actor_username, "reviewer")
        self.assertEqual(log.target_username, "")
        self.assertEqual(log.membership_type_id, membership_type.pk)

        notes = list(Note.objects.filter(membership_request=mr).order_by("timestamp", "pk"))
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note.username, "reviewer")
        self.assertIsInstance(note.action, dict)
        assert isinstance(note.action, dict)
        self.assertEqual(note.action.get("type"), "representative_changed")
        self.assertEqual(note.action.get("old"), "carol")
        self.assertEqual(note.action.get("new"), "bob")

        with patch("core.backends.FreeIPAUser.get", side_effect=fake_get):
            resp = self.client.get(reverse("membership-request-detail", args=[mr.pk]), follow=False)
        self.assertEqual(resp.status_code, 200)
        from html import unescape

        self.assertIn("Representative changed from carol to bob", unescape(resp.content.decode()))

    def test_representative_change_does_not_create_membershiplog_when_no_pending_requests(self) -> None:
        from core.models import MembershipLog, MembershipType, Note, Organization

        org = Organization.objects.create(
            name="Org",
            representative="carol",
        )

        membership_type, _ = MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": []})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        carol = FreeIPAUser("carol", {"uid": ["carol"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        def fake_get(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            if username == "carol":
                return carol
            return None

        payload = self._valid_org_payload(name="Org")
        payload["representative"] = "bob"

        with patch("core.backends.FreeIPAUser.get", side_effect=fake_get):
            resp = self.client.post(reverse("organization-edit", args=[org.pk]), data=payload, follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            MembershipLog.objects.filter(
                target_organization=org,
                membership_type=membership_type,
                action=MembershipLog.Action.representative_changed,
            ).exists()
        )

        self.assertEqual(Note.objects.count(), 0)

    def test_representative_can_view_org_pages_notes_hidden(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            membership_level_id="silver",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organizations"))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Create an organization profile only")

            resp = self.client.get(reverse("organization-detail", args=[org.pk]))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "AlmaLinux")
            self.assertContains(resp, "Annual dues: $2,500 USD")

            # Navbar should include Organizations link for authenticated users.
            self.assertContains(resp, reverse("organizations"))

    @override_settings(TIME_ZONE="Europe/Berlin")
    def test_org_detail_sponsorship_expiry_displays_utc_consistently(self) -> None:
        from core.models import MembershipType, Organization, OrganizationSponsorship

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )
        membership_type = MembershipType.objects.get(code="silver")

        org = Organization.objects.create(
            name="AlmaLinux",
            membership_level=membership_type,
            representative="bob",
        )

        frozen_now = datetime.datetime(2026, 1, 5, 12, 0, 0, tzinfo=datetime.UTC)
        expires_at = datetime.datetime(2027, 1, 21, 23, 59, 59, tzinfo=datetime.UTC)
        OrganizationSponsorship.objects.create(
            organization=org,
            membership_type=membership_type,
            expires_at=expires_at,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            with patch("django.utils.timezone.now", autospec=True, return_value=frozen_now):
                resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Expires Jan 21, 2027")
        self.assertNotContains(resp, "(UTC)")
        self.assertNotContains(resp, "Expires Jan 22, 2027")

    def test_org_detail_sponsorship_card_links_to_request_for_representative_and_committee(self) -> None:
        from core.models import MembershipRequest, MembershipType, Organization
        from core.permissions import ASTRA_VIEW_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )
        membership_type = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(name="Acme", membership_level=membership_type, representative="bob")
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
            responses=[{"Additional Information": "Org answers"}],
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")
        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp_rep = self.client.get(reverse("organization-detail", args=[org.pk]))
        self.assertEqual(resp_rep.status_code, 200)
        self.assertContains(resp_rep, reverse("membership-request-self", args=[req.pk]))

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []},
        )

        self._login_as_freeipa_user("reviewer")
        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp_committee = self.client.get(reverse("organization-detail", args=[org.pk]))
        self.assertEqual(resp_committee.status_code, 200)
        self.assertContains(resp_committee, reverse("membership-request-detail", args=[req.pk]))

    def test_membership_viewer_can_view_org_but_cannot_see_edit_button(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            membership_level_id="silver",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        # Viewer can see org pages but should not see the Edit button.
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))
            self.assertEqual(resp.status_code, 200)
            self.assertNotContains(resp, reverse("organization-edit", args=[org.pk]))

            resp = self.client.get(reverse("organization-edit", args=[org.pk]))
            self.assertEqual(resp.status_code, 404)

    def test_org_detail_shows_representative_card(self) -> None:
        from core.models import Organization

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            representative="bob",
        )

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": []})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "cn": ["Bob Example"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        def fake_get(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=fake_get):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Representative")
            self.assertContains(resp, "Bob Example")
            self.assertContains(resp, reverse("user-profile", args=["bob"]))

    def test_representative_can_edit_org_data_notes_hidden(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            membership_level_id="silver",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-edit", args=[org.pk]))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "AlmaLinux")
            self.assertContains(resp, 'id="id_website_logo"')
            self.assertNotContains(resp, 'textarea name="website_logo"')

            # Sponsorship requests are handled on the separate manage page.
            self.assertNotContains(resp, 'id="id_membership_level"')
            self.assertNotContains(resp, 'id="id_additional_information"')

            resp = self.client.post(
                reverse("organization-edit", args=[org.pk]),
                data={
                    "business_contact_name": "Business Person Updated",
                    "business_contact_email": "hello@almalinux.org",
                    "business_contact_phone": "",
                    "pr_marketing_contact_name": "PR Person Updated",
                    "pr_marketing_contact_email": "pr-updated@almalinux.org",
                    "pr_marketing_contact_phone": "",
                    "technical_contact_name": "Tech Person Updated",
                    "technical_contact_email": "tech-updated@almalinux.org",
                    "technical_contact_phone": "",
                    "name": "AlmaLinux Updated",
                    "website_logo": "https://example.com/logo-options-updated",
                    "website": "https://example.com/",
                },
                follow=False,
            )
        self.assertEqual(resp.status_code, 302)

        org.refresh_from_db()
        self.assertEqual(org.name, "AlmaLinux Updated")
        self.assertEqual(org.business_contact_email, "hello@almalinux.org")
        self.assertEqual(org.website, "https://example.com/")


    def test_org_detail_header_shows_separate_actions_and_level_badge(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            membership_level_id="gold",
            website="https://almalinux.org/",
            representative="bob",
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")
        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Edit details")
        self.assertContains(resp, reverse("organization-edit", args=[org.pk]))
        self.assertContains(resp, "Change sponsorship tier")
        self.assertContains(resp, reverse("organization-sponsorship-manage", args=[org.pk]))

        self.assertNotContains(resp, "Active sponsor")
        self.assertContains(resp, 'alx-status-badge--active">Gold')

        body = resp.content.decode("utf-8")
        self.assertLess(body.find('class="col-md-7"'), body.find('id="org-contacts-tabs"'))
        self.assertLess(body.find('class="col-md-5"'), body.find("Branding"))

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
        MEDIA_ROOT=_test_media_root,
    )
    def test_representative_logo_upload_is_png_named_by_id(self) -> None:
        from PIL import Image

        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            membership_level_id="silver",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        jpeg = BytesIO()
        Image.new("RGB", (2, 2), color=(200, 10, 10)).save(jpeg, format="JPEG")
        logo_upload = SimpleUploadedFile(
            "logo.jpg",
            jpeg.getvalue(),
            content_type="image/jpeg",
        )

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(
                reverse("organization-edit", args=[org.pk]),
                data={
                    "business_contact_name": "Business Person",
                    "business_contact_email": "contact@almalinux.org",
                    "business_contact_phone": "",
                    "pr_marketing_contact_name": "PR Person",
                    "pr_marketing_contact_email": "pr@almalinux.org",
                    "pr_marketing_contact_phone": "",
                    "technical_contact_name": "Tech Person",
                    "technical_contact_email": "tech@almalinux.org",
                    "technical_contact_phone": "",
                    "membership_level": "silver",
                    "name": "AlmaLinux",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://almalinux.org/",
                    "additional_information": "",
                    "logo": logo_upload,
                },
                follow=False,
            )
        self.assertEqual(resp.status_code, 302)

        org.refresh_from_db()
        expected_logo_path = f"organizations/logos/{org.pk}.png"

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-edit", args=[org.pk]))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, expected_logo_path)

            resp = self.client.get(reverse("organization-detail", args=[org.pk]))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, expected_logo_path)

            resp = self.client.get(reverse("organizations"))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, expected_logo_path)
        self.assertTrue(org.logo.name.endswith(expected_logo_path))

        org.logo.open("rb")
        try:
            self.assertEqual(org.logo.read(8), b"\x89PNG\r\n\x1a\n")
        finally:
            org.logo.close()

    def test_membership_level_change_creates_request_until_approved(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType, Note, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            membership_level_id="silver",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(
                reverse("organization-sponsorship-manage", args=[org.pk]),
                data={
                    "membership_level": "gold",
                    "additional_information": "Please consider our updated sponsorship level.",
                },
                follow=False,
            )
        self.assertEqual(resp.status_code, 302)

        org.refresh_from_db()
        self.assertEqual(org.membership_level_id, "silver")

        req = MembershipRequest.objects.get(status=MembershipRequest.Status.pending)
        self.assertEqual(req.membership_type_id, "gold")
        self.assertEqual(req.requested_organization_id, org.pk)
        self.assertEqual(req.responses, [{"Additional Information": "Please consider our updated sponsorship level."}])

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="bob",
                action={"type": "request_created"},
            ).exists()
        )

        req_log = MembershipLog.objects.get(action=MembershipLog.Action.requested, target_organization=org)
        self.assertEqual(req_log.membership_type_id, "gold")
        self.assertEqual(req_log.membership_request_id, req.pk)

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Under review")
            self.assertContains(resp, "Annual dues: $20,000 USD")

    def test_sponsorship_manage_requires_signed_coc(self) -> None:
        from core.backends import FreeIPAFASAgreement
        from core.models import MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="Blocked Sponsorship",
            business_contact_name="Business Person",
            business_contact_email="contact@example.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@example.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@example.org",
            membership_level_id="silver",
            website_logo="https://example.com/logo-options",
            website="https://example.com/",
            representative="bob",
        )

        self._login_as_freeipa_user("bob")

        coc = FreeIPAFASAgreement(
            settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN,
            {
                "cn": [settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN],
                "ipaenabledflag": ["TRUE"],
                "memberuser_user": [],
            },
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", autospec=True, return_value=bob):
            with patch("core.views_utils.FreeIPAFASAgreement.get", autospec=True, return_value=coc):
                resp = self.client.post(
                    reverse("organization-sponsorship-manage", args=[org.pk]),
                    data={
                        "membership_level": "silver",
                        "additional_information": "Please renew.",
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        expected = (
            f"{reverse('settings')}?agreement={quote(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN)}#agreements"
        )
        self.assertEqual(resp["Location"], expected)
        self.assertEqual(MembershipRequest.objects.count(), 0)


    def test_membership_admin_can_set_org_sponsorship_expiry_when_missing(self) -> None:
        import datetime

        from core.models import MembershipType, Organization, OrganizationSponsorship
        from core.permissions import ASTRA_CHANGE_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "description": "Gold",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", membership_level_id="gold", representative="bob")
        sponsorship = OrganizationSponsorship.objects.create(
            organization=org,
            membership_type_id="gold",
            expires_at=None,
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        new_expires_on = datetime.date(2030, 1, 31)

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={
                    "expires_on": new_expires_on.isoformat(),
                    "next": reverse("organization-detail", args=[org.pk]),
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        sponsorship.refresh_from_db()
        self.assertEqual(
            sponsorship.expires_at,
            datetime.datetime(2030, 1, 31, 23, 59, 59, tzinfo=datetime.UTC),
        )

    def test_membership_admin_can_set_org_sponsorship_expiry_creates_row_when_absent(self) -> None:
        import datetime

        from core.models import FreeIPAPermissionGrant, MembershipType, Organization, OrganizationSponsorship
        from core.permissions import ASTRA_CHANGE_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "description": "Gold",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", membership_level_id="gold", representative="bob")
        OrganizationSponsorship.objects.filter(organization=org).delete()

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        new_expires_on = datetime.date(2030, 1, 31)

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={
                    "expires_on": new_expires_on.isoformat(),
                    "next": reverse("organization-detail", args=[org.pk]),
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        sponsorship = OrganizationSponsorship.objects.get(organization=org)
        self.assertEqual(
            sponsorship.expires_at,
            datetime.datetime(2030, 1, 31, 23, 59, 59, tzinfo=datetime.UTC),
        )

    def test_membership_admin_can_set_org_sponsorship_expiry_when_membership_type_mismatch(self) -> None:
        import datetime

        from core.models import MembershipType, Organization, OrganizationSponsorship
        from core.permissions import ASTRA_CHANGE_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "description": "Gold",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver",
                "description": "Silver",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", membership_level_id="gold", representative="bob")

        # Simulate drift: the current-state sponsorship row exists but points at a different
        # membership_type than the org's current membership_level.
        OrganizationSponsorship.objects.create(
            organization=org,
            membership_type_id="silver",
            expires_at=None,
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        new_expires_on = datetime.date(2030, 1, 31)

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={
                    "expires_on": new_expires_on.isoformat(),
                    "next": reverse("organization-detail", args=[org.pk]),
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        sponsorship = OrganizationSponsorship.objects.get(organization=org)
        self.assertEqual(sponsorship.membership_type_id, "gold")
        self.assertEqual(
            sponsorship.expires_at,
            datetime.datetime(2030, 1, 31, 23, 59, 59, tzinfo=datetime.UTC),
        )

    def test_organization_detail_shows_committee_notes_with_request_link(self) -> None:
        from core.models import MembershipRequest, MembershipType, Note, Organization
        from core.permissions import ASTRA_VIEW_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="gold",
            status=MembershipRequest.Status.pending,
        )
        Note.objects.create(membership_request=req, username="reviewer", content="Org note")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Membership Committee Notes")
        self.assertContains(resp, "Org note")
        self.assertContains(resp, f"(req. #{req.pk})")
        self.assertContains(resp, f'href="{reverse("membership-request-detail", args=[req.pk])}"')

    def test_organization_aggregate_notes_allows_posting_but_hides_vote_buttons(self) -> None:
        from core.models import MembershipRequest, MembershipType, Note, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 3,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        req1 = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="gold",
            status=MembershipRequest.Status.approved,
            decided_at=timezone.now(),
        )
        req2 = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
            status=MembershipRequest.Status.pending,
        )
        Note.objects.create(membership_request=req1, username="reviewer", content="Older org note")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []},
        )
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Membership Committee Notes")
        self.assertContains(resp, 'placeholder="Type a note..."')
        self.assertNotContains(resp, 'data-note-action="vote_approve"')
        self.assertNotContains(resp, 'data-note-action="vote_disapprove"')

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("membership-notes-aggregate-note-add"),
                data={
                    "aggregate_target_type": "org",
                    "aggregate_target": str(org.pk),
                    "note_action": "message",
                    "message": "Hello org aggregate",
                    "compact": "1",
                    "next": reverse("organization-detail", args=[org.pk]),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("Hello org aggregate", payload.get("html") or "")

        self.assertTrue(
            Note.objects.filter(
                membership_request=req2,
                username="reviewer",
                content="Hello org aggregate",
            ).exists()
        )


    def test_sponsorship_expiration_display_and_extend_request(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType, Organization, OrganizationSponsorship

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            membership_level_id="gold",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            additional_information="Renewal note",
            representative="bob",
        )

        expires_at = timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1)
        OrganizationSponsorship.objects.create(
            organization=org,
            membership_type_id="gold",
            expires_at=expires_at,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Expires")
        self.assertContains(resp, "Request renewal")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(reverse("organization-sponsorship-extend", args=[org.pk]), follow=False)
        self.assertEqual(resp.status_code, 302)

        req = MembershipRequest.objects.get(status=MembershipRequest.Status.pending)
        self.assertEqual(req.requested_organization_id, org.pk)
        self.assertEqual(req.membership_type_id, "gold")
        self.assertEqual(req.responses, [{"Additional Information": "Renewal note"}])

        self.assertTrue(
            MembershipLog.objects.filter(
                action=MembershipLog.Action.requested,
                target_organization=org,
                membership_request=req,
            ).exists()
        )

    def test_representative_cannot_submit_second_org_membership_request_while_one_open(self) -> None:
        from core.models import MembershipRequest, MembershipType, Organization, OrganizationSponsorship

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            membership_level_id="gold",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            additional_information="Renewal note",
            representative="bob",
        )

        expires_at = timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1)
        OrganizationSponsorship.objects.create(
            organization=org,
            membership_type_id="gold",
            expires_at=expires_at,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(reverse("organization-sponsorship-extend", args=[org.pk]), follow=False)
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(
            MembershipRequest.objects.filter(
                requested_organization=org,
                status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
            ).count(),
            1,
        )

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(
                reverse("organization-sponsorship-manage", args=[org.pk]),
                data={
                    "membership_level": "silver",
                    "additional_information": "Please review our sponsorship request.",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "A sponsorship request is already pending.")
        self.assertEqual(
            MembershipRequest.objects.filter(
                requested_organization=org,
                status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
            ).count(),
            1,
        )

    def test_committee_can_edit_org_sponsorship_expiry_and_terminate(self) -> None:
        from core.models import (
            FreeIPAPermissionGrant,
            MembershipLog,
            MembershipType,
            Organization,
            OrganizationSponsorship,
        )
        from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            membership_level_id="gold",
            website="https://almalinux.org/",
            representative="bob",
        )

        expires_at = timezone.now() + datetime.timedelta(days=30)
        OrganizationSponsorship.objects.create(
            organization=org,
            membership_type_id="gold",
            expires_at=expires_at,
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_DELETE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []},
        )
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Edit expiry")
        self.assertContains(resp, "Terminate")
        self.assertContains(resp, 'data-target="#sponsorship-expiry-modal"')
        self.assertContains(resp, 'id="sponsorship-expiry-modal"')
        self.assertContains(
            resp,
            f'action="{reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"])}"',
        )
        self.assertContains(resp, 'data-target="#sponsorship-terminate-modal"')
        self.assertContains(resp, 'id="sponsorship-terminate-modal"')
        self.assertContains(
            resp,
            f'action="{reverse("organization-sponsorship-terminate", args=[org.pk, "gold"])}"',
        )

        self.assertContains(
            resp,
            f"Terminate sponsorship for <strong>{org.name}</strong> early?",
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]))
        self.assertEqual(resp.status_code, 404)

        new_expires_on = (timezone.now() + datetime.timedelta(days=90)).date().isoformat()
        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={"expires_on": new_expires_on},
                follow=False,
            )
        self.assertEqual(resp.status_code, 302)

        org.refresh_from_db()
        sponsorship = OrganizationSponsorship.objects.get(organization=org)
        self.assertEqual(sponsorship.membership_type_id, "gold")
        self.assertIsNotNone(sponsorship.expires_at)

        self.assertTrue(
            MembershipLog.objects.filter(
                action=MembershipLog.Action.expiry_changed,
                target_organization=org,
                membership_type_id="gold",
            ).exists()
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(reverse("organization-sponsorship-terminate", args=[org.pk, "gold"]), follow=False)
        self.assertEqual(resp.status_code, 302)

        org.refresh_from_db()
        self.assertIsNone(org.membership_level_id)
        self.assertTrue(
            MembershipLog.objects.filter(
                action=MembershipLog.Action.terminated,
                target_organization=org,
                membership_type_id="gold",
            ).exists()
        )

    def test_sponsorship_uninterrupted_extension_preserves_created_at(self) -> None:
        import datetime

        from core.models import MembershipLog, MembershipType, Organization, OrganizationSponsorship

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )
        membership_type = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(
            name="AlmaLinux",
            membership_level_id="gold",
            representative="bob",
        )

        start_at = datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        extend_at = datetime.datetime(2025, 2, 1, 12, 0, 0, tzinfo=datetime.UTC)

        with patch("django.utils.timezone.now", autospec=True, return_value=start_at):
            first_log = MembershipLog.create_for_org_approval(
                actor_username="reviewer",
                target_organization=org,
                membership_type=membership_type,
                previous_expires_at=None,
                membership_request=None,
            )

        sponsorship = OrganizationSponsorship.objects.get(organization=org)
        self.assertEqual(sponsorship.created_at, start_at)

        previous_expires_at = first_log.expires_at
        assert previous_expires_at is not None

        # Simulate drift: current-state row missing, but the term is uninterrupted.
        OrganizationSponsorship.objects.filter(organization=org).delete()

        with patch("django.utils.timezone.now", autospec=True, return_value=extend_at):
            MembershipLog.create_for_org_approval(
                actor_username="reviewer",
                target_organization=org,
                membership_type=membership_type,
                previous_expires_at=previous_expires_at,
                membership_request=None,
            )

        recreated = OrganizationSponsorship.objects.get(organization=org)
        self.assertEqual(recreated.created_at, start_at)
        self.assertGreater(recreated.expires_at, previous_expires_at)

    def test_expired_sponsorship_starts_new_term_and_resets_created_at(self) -> None:
        import datetime

        from core.models import MembershipLog, MembershipType, Organization, OrganizationSponsorship

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )
        membership_type = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(
            name="AlmaLinux",
            membership_level_id="gold",
            representative="bob",
        )

        start_at = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        after_expiry_at = datetime.datetime(2025, 7, 1, 12, 0, 0, tzinfo=datetime.UTC)

        with patch("django.utils.timezone.now", autospec=True, return_value=start_at):
            MembershipLog.create_for_org_approval(
                actor_username="reviewer",
                target_organization=org,
                membership_type=membership_type,
                previous_expires_at=None,
                membership_request=None,
            )

        # Force an expired current-state row.
        OrganizationSponsorship.objects.filter(organization=org).update(expires_at=start_at)

        with patch("django.utils.timezone.now", autospec=True, return_value=after_expiry_at):
            MembershipLog.create_for_org_approval(
                actor_username="reviewer",
                target_organization=org,
                membership_type=membership_type,
                previous_expires_at=start_at,
                membership_request=None,
            )

        current = OrganizationSponsorship.objects.get(organization=org)
        self.assertEqual(current.created_at, after_expiry_at)

    def test_representative_cannot_extend_expired_sponsorship(self) -> None:
        import datetime

        from core.models import MembershipRequest, MembershipType, Organization, OrganizationSponsorship

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            membership_level_id="gold",
            representative="bob",
        )

        expired_at = timezone.now() - datetime.timedelta(days=1)
        OrganizationSponsorship.objects.create(
            organization=org,
            membership_type_id="gold",
            expires_at=expired_at,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(reverse("organization-sponsorship-extend", args=[org.pk]), follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            MembershipRequest.objects.filter(
                requested_organization=org,
                status=MembershipRequest.Status.pending,
            ).exists()
        )

    def test_non_representative_cannot_view_org_detail(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            membership_level_id="silver",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})
        self._login_as_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 404)

    def test_non_representative_cannot_edit_org(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            membership_level_id="silver",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

    def test_committee_with_change_membership_can_edit_org_and_manage_representatives(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            membership_level_id="silver",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("organization-edit", args=[org.pk]))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, 'name="representative"')
            self.assertContains(resp, "select2.full")
            self.assertContains(resp, "select2.css")

            resp = self.client.post(
                reverse("organization-edit", args=[org.pk]),
                data={
                    "business_contact_name": "Business Person",
                    "business_contact_email": "contact@almalinux.org",
                    "business_contact_phone": "",
                    "pr_marketing_contact_name": "PR Person",
                    "pr_marketing_contact_email": "pr@almalinux.org",
                    "pr_marketing_contact_phone": "",
                    "technical_contact_name": "Tech Person",
                    "technical_contact_email": "tech@almalinux.org",
                    "technical_contact_phone": "",
                    "membership_level": "silver",
                    "name": "AlmaLinux",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://almalinux.org/",
                    "additional_information": "",
                    "representative": "carol",
                },
                follow=False,
            )
        self.assertEqual(resp.status_code, 302)

        org.refresh_from_db()
        self.assertEqual(org.representative, "carol")

    def test_deleting_organization_does_not_delete_membership_requests_or_audit_logs(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType, Organization

        membership_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            representative="bob",
        )

        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="gold",
            status=MembershipRequest.Status.pending,
            responses=[{"Additional Information": "Please consider our updated sponsorship level."}],
        )
        MembershipLog.create_for_org_request(
            actor_username="bob",
            target_organization=org,
            membership_type=membership_type,
            membership_request=req,
        )

        org.delete()

        self.assertTrue(MembershipRequest.objects.filter(pk=req.pk).exists())
        self.assertTrue(MembershipLog.objects.filter(membership_request_id=req.pk).exists())

    def test_user_can_create_organization_and_becomes_representative(self) -> None:
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-create"))
        self.assertEqual(resp.status_code, 200)

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(
                reverse("organization-create"),
                data={
                    "name": "AlmaLinux",
                    "business_contact_name": "Business Person",
                    "business_contact_email": "contact@almalinux.org",
                    "business_contact_phone": "",
                    "pr_marketing_contact_name": "PR Person",
                    "pr_marketing_contact_email": "pr@almalinux.org",
                    "pr_marketing_contact_phone": "",
                    "technical_contact_name": "Tech Person",
                    "technical_contact_email": "tech@almalinux.org",
                    "technical_contact_phone": "",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://almalinux.org/",
                    "additional_information": "We would like to join.",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)

        from core.models import Organization

        created = Organization.objects.get(name="AlmaLinux")
        self.assertEqual(created.representative, "bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organizations"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("organization-detail", args=[created.pk]))

    def test_organization_create_redirects_creator_to_sponsorship_manage(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(
                reverse("organization-create"),
                data={
                    "name": "AlmaLinux",
                    "business_contact_name": "Business Person",
                    "business_contact_email": "contact@almalinux.org",
                    "business_contact_phone": "",
                    "pr_marketing_contact_name": "PR Person",
                    "pr_marketing_contact_email": "pr@almalinux.org",
                    "pr_marketing_contact_phone": "",
                    "technical_contact_name": "Tech Person",
                    "technical_contact_email": "tech@almalinux.org",
                    "technical_contact_phone": "",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://almalinux.org/",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        created = Organization.objects.get(name="AlmaLinux")
        self.assertEqual(created.representative, "bob")
        self.assertEqual(
            resp["Location"],
            reverse("organization-sponsorship-manage", args=[created.pk]),
        )

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            manage_resp = self.client.get(reverse("organization-sponsorship-manage", args=[created.pk]))
        self.assertEqual(manage_resp.status_code, 200)
        self.assertContains(manage_resp, "Manage sponsorship")

    def test_org_edit_highlights_contact_tabs_with_validation_errors(self) -> None:
        from core.models import Organization

        org = Organization.objects.create(
            name="Acme",
            website_logo="https://example.com/logo",
            website="https://example.com/",
            representative="bob",
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(
                reverse("organization-edit", args=[org.pk]),
                data={
                    # Intentionally omit required contact fields in non-active tabs.
                    "name": "Acme",
                    "website_logo": "https://example.com/logo",
                    "website": "https://example.com/",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")

        for tab_id in (
            'id="contacts-business-tab"',
            'id="contacts-marketing-tab"',
            'id="contacts-technical-tab"',
        ):
            idx = body.find(tab_id)
            self.assertNotEqual(idx, -1)
            window = body[max(0, idx - 200) : idx + 200]
            self.assertIn("alx-tab-error", window)

    def test_membership_admin_creating_org_for_other_rep_redirects_to_detail_not_manage(self) -> None:
        from core.models import FreeIPAPermissionGrant, Organization

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("reviewer")

        def fake_get(username: str):
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=fake_get):
            resp = self.client.post(
                reverse("organization-create"),
                data={
                    "representative": "bob",
                    "name": "Acme",
                    "business_contact_name": "Business Person",
                    "business_contact_email": "contact@example.com",
                    "business_contact_phone": "",
                    "pr_marketing_contact_name": "PR Person",
                    "pr_marketing_contact_email": "pr@example.com",
                    "pr_marketing_contact_phone": "",
                    "technical_contact_name": "Tech Person",
                    "technical_contact_email": "tech@example.com",
                    "technical_contact_phone": "",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://example.com/",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        created = Organization.objects.get(name="Acme")
        self.assertEqual(created.representative, "bob")
        self.assertContains(resp, reverse("organization-detail", args=[created.pk]))
        self.assertContains(resp, "Sponsorship Level")

    def test_org_create_highlights_contact_tabs_with_validation_errors(self) -> None:
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": []})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(
                reverse("organization-create"),
                data={
                    # Intentionally omit required contact fields in non-active tabs.
                    "name": "AlmaLinux",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://almalinux.org/",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")

        for tab_id in (
            'id="contacts-business-tab"',
            'id="contacts-marketing-tab"',
            'id="contacts-technical-tab"',
        ):
            idx = body.find(tab_id)
            self.assertNotEqual(idx, -1)
            window = body[max(0, idx - 200) : idx + 200]
            self.assertIn("alx-tab-error", window)

    def test_contacts_tab_error_style_defined(self) -> None:
        css_path = finders.find("core/css/base.css")
        self.assertIsNotNone(css_path)

        css = Path(css_path).read_text(encoding="utf-8")
        self.assertIn(".nav-tabs .nav-link.alx-tab-error", css)

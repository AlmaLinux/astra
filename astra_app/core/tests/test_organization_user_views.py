
import datetime
from io import BytesIO
from pathlib import Path
from tempfile import mkdtemp
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, quote_plus, urlparse

from django.conf import settings
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import views_membership
from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Membership, MembershipTypeCategory
from core.organization_claim import make_organization_claim_token
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_ADD_SEND_MAIL,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
)
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates


class OrganizationUserViewsTests(TestCase):
    _test_media_root = Path(mkdtemp(prefix="alx_test_media_"))

    def setUp(self) -> None:
        self._country_code_patcher = patch(
            "core.views_membership.block_action_without_country_code",
            return_value=None,
        )
        self._country_code_patcher.start()
        self.addCleanup(self._country_code_patcher.stop)

        ensure_core_categories()
        ensure_email_templates()

        MembershipTypeCategory.objects.update_or_create(
            pk="community",
            defaults={
                "is_organization": True,
                "sort_order": 3,
            },
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _valid_org_payload(self, *, name: str) -> dict[str, str]:
        return {
            "name": name,
            "country_code": "US",
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
        }

    def test_organization_create_requires_country_code(self) -> None:
        from core.models import Organization

        self._login_as_freeipa_user("alice")

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        payload = self._valid_org_payload(name="Country Required Org")
        payload.pop("country_code")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            response = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertFalse(Organization.objects.filter(name="Country Required Org").exists())

    def test_organization_create_normalizes_country_code_to_uppercase(self) -> None:
        from core.models import Organization

        self._login_as_freeipa_user("alice")

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        payload = self._valid_org_payload(name="Country Normalize Org")
        payload["country_code"] = "ca"

        with (
            patch("core.backends.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            response = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(response.status_code, 302)
        organization = Organization.objects.get(name="Country Normalize Org")
        self.assertEqual(organization.country_code, "CA")

    def test_organization_create_rejects_invalid_country_code(self) -> None:
        from core.models import Organization

        self._login_as_freeipa_user("alice")

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        payload = self._valid_org_payload(name="Invalid Country Org")
        payload["country_code"] = "ZZ"

        with (
            patch("core.backends.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            response = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid 2-letter ISO country code.")
        self.assertFalse(Organization.objects.filter(name="Invalid Country Org").exists())

    def test_organization_create_rejects_numeric_country_code(self) -> None:
        from core.models import Organization

        self._login_as_freeipa_user("alice")

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        payload = self._valid_org_payload(name="Numeric Country Org")
        payload["country_code"] = "1A"

        with (
            patch("core.backends.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            response = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid 2-letter ISO country code.")
        self.assertFalse(Organization.objects.filter(name="Numeric Country Org").exists())

    def test_organization_create_form_renders_address_fields_and_no_embargo_ui(self) -> None:
        self._login_as_freeipa_user("alice")

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            resp = self.client.get(reverse("organization-create"), follow=False)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="id_street"')
        self.assertContains(resp, 'id="id_city"')
        self.assertContains(resp, 'id="id_state"')
        self.assertContains(resp, 'id="id_postal_code"')
        self.assertContains(resp, 'id="id_country_code"')
        self.assertNotContains(resp, "Compliance warning")
        self.assertNotContains(resp, "embargo")

    def test_organization_edit_updates_country_code(self) -> None:
        from core.models import Organization

        org = Organization.objects.create(
            name="Edit Org",
            representative="alice",
        )

        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            resp_get = self.client.get(reverse("organization-edit", args=[org.pk]), follow=False)

        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, 'id="id_country_code"')
        self.assertContains(resp_get, 'id="id_street"')
        self.assertNotContains(resp_get, "Compliance warning")
        self.assertNotContains(resp_get, "embargo")

        payload = self._valid_org_payload(name="Edit Org Updated")
        payload["country_code"] = "de"

        with (
            patch("core.backends.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            resp_post = self.client.post(reverse("organization-edit", args=[org.pk]), data=payload, follow=False)

        self.assertEqual(resp_post.status_code, 302)
        org.refresh_from_db()
        self.assertEqual(org.country_code, "DE")

    def test_non_committee_representative_cannot_create_second_org(self) -> None:
        from core.models import Organization

        Organization.objects.create(
            name="Existing Org",
            representative="bob",
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            resp = self.client.get(reverse("organization-create"), follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("organizations"))

        payload = self._valid_org_payload(name="Second Org")
        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
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
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        with patch("core.backends.FreeIPAUser.get", autospec=True, return_value=alice):
            with patch("core.views_utils.FreeIPAFASAgreement.get", autospec=True, return_value=coc):
                resp = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(resp.status_code, 302)
        expected = (
            f"{reverse('settings')}?tab=agreements&agreement={quote_plus(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN)}"
        )
        self.assertEqual(resp["Location"], expected)
        self.assertFalse(Organization.objects.filter(name="Blocked Org").exists())

    def test_organization_create_form_renders_without_required_text(self) -> None:
        self._login_as_freeipa_user("alice")

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            resp = self.client.get(reverse("organization-create"), follow=False)

        self.assertEqual(resp.status_code, 200)

        # Spot-check that the include-based rendering preserves labels and help text.
        self.assertContains(resp, "Organization name")
        self.assertContains(resp, "Website URL")
        self.assertContains(resp, "Share a direct link to your logo file, or a link to your brand assets.")
        self.assertContains(
            resp,
            "Enter the URL you want your logo to link to (homepage or a dedicated landing page).",
        )

        # The standardization intentionally removes any hard-coded required indicator.
        self.assertNotContains(resp, "(required)")
        self.assertNotContains(resp, "alx-required-indicator")

        payload = self._valid_org_payload(name="")
        payload["website"] = ""
        payload["website_logo"] = ""

        with (
            patch("core.backends.FreeIPAUser.get", return_value=alice),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            resp = self.client.post(reverse("organization-create"), data=payload, follow=False)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "This field is required.")
        self.assertContains(resp, "invalid-feedback")
        self.assertNotContains(resp, "(required)")

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

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})

        def fake_get(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")

        payload = self._valid_org_payload(name="New Org")
        payload["representative"] = "bob"

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=fake_get),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
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

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")

        bob_user = FreeIPAUser("bob", {"uid": ["bob"], "displayname": ["Bob Example"], "memberof_group": [], "c": ["US"]})
        bobby_user = FreeIPAUser("bobby", {"uid": ["bobby"], "displayname": ["Bobby Example"], "memberof_group": [], "c": ["US"]})

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

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        carol = FreeIPAUser("carol", {"uid": ["carol"], "memberof_group": [], "c": ["US"]})
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
                "category_id": "sponsorship",
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

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        carol = FreeIPAUser("carol", {"uid": ["carol"], "memberof_group": [], "c": ["US"]})
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
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        carol = FreeIPAUser("carol", {"uid": ["carol"], "memberof_group": [], "c": ["US"]})
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
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-silver",
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
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        Membership.objects.create(target_organization=org, membership_type_id="silver")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
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
        from core.models import Membership, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        membership_type = MembershipType.objects.get(code="silver")

        org = Organization.objects.create(
            name="AlmaLinux",
            representative="bob",
        )

        frozen_now = datetime.datetime(2026, 1, 5, 12, 0, 0, tzinfo=datetime.UTC)
        expires_at = datetime.datetime(2027, 1, 21, 23, 59, 59, tzinfo=datetime.UTC)
        Membership.objects.create(
            target_organization=org,
            membership_type=membership_type,
            expires_at=expires_at,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
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
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
                "group_cn": "almalinux-gold",
            },
        )
        membership_type = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type=membership_type,
        )
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type=membership_type,
            status=MembershipRequest.Status.approved,
            responses=[{"Additional Information": "Org answers"}],
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
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
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [], "c": ["US"]},
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
                "category_id": "sponsorship",
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

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
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

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "cn": ["Bob Example"], "memberof_group": [], "c": ["US"]})
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

    def test_unclaimed_organization_shows_badge_in_grid_and_detail(self) -> None:
        from core.models import Organization

        organization = Organization.objects.create(
            name="Unclaimed Org",
            business_contact_email="contact@example.com",
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            organizations_response = self.client.get(reverse("organizations"))
            self.assertEqual(organizations_response.status_code, 200)
            self.assertContains(organizations_response, "Unclaimed")

            detail_response = self.client.get(reverse("organization-detail", args=[organization.pk]))
            self.assertEqual(detail_response.status_code, 200)
            self.assertContains(detail_response, "Unclaimed")

    def test_unclaimed_org_detail_shows_send_claim_invitation_button_for_send_mail_permission(self) -> None:
        from core.models import Organization

        organization = Organization.objects.create(
            name="Unclaimed Org",
            business_contact_email="contact@example.com",
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_ADD_SEND_MAIL,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(reverse("organization-detail", args=[organization.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Send claim invitation")
        send_claim_invitation_url = response.context["send_claim_invitation_url"]
        parsed_url = urlparse(send_claim_invitation_url)
        self.assertEqual(parsed_url.path, reverse("send-mail"))

        query = parse_qs(parsed_url.query)
        self.assertEqual(query.get("type"), ["manual"])
        self.assertEqual(query.get("to"), ["contact@example.com"])
        self.assertEqual(
            query.get("template"),
            [settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME],
        )
        self.assertEqual(query.get("reply_to"), [settings.MEMBERSHIP_COMMITTEE_EMAIL])
        self.assertEqual(query.get("invitation_action"), ["org_claim"])
        self.assertEqual(query.get("invitation_org_id"), [str(organization.pk)])
        self.assertEqual(query.get("organization_name"), ["Unclaimed Org"])
        self.assertEqual(len(query.get("claim_url", [])), 1)
        self.assertIn("/organizations/claim/", query["claim_url"][0])

        self.assertContains(
            response,
            f'href="{reverse("send-mail")}?',
        )
        self.assertNotContains(response, "send-claim-invitation")

    def test_organization_claim_marks_linked_invitation_accepted(self) -> None:
        from core.models import AccountInvitation, Organization

        organization = Organization.objects.create(
            name="Claim Accept Org",
            business_contact_email="contact@example.com",
        )
        invitation = AccountInvitation.objects.create(
            email="contact@example.com",
            invited_by_username="reviewer",
            organization=organization,
            email_template_name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
        )
        token = make_organization_claim_token(organization)

        claimant = FreeIPAUser("claimant", {"uid": ["claimant"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("claimant")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=claimant),
            patch("core.views_organizations.block_action_without_coc", return_value=None),
            patch("core.views_organizations.block_action_without_country_code", return_value=None),
        ):
            response = self.client.post(reverse("organization-claim", args=[token]), follow=False)

        self.assertEqual(response.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)

    def test_active_org_detail_hides_send_claim_invitation_link(self) -> None:
        from core.models import Organization

        organization = Organization.objects.create(
            name="Active Org",
            representative="bob",
            business_contact_email="contact@example.com",
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_ADD_SEND_MAIL,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            response = self.client.get(reverse("organization-detail", args=[organization.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Send claim invitation")
        self.assertNotContains(response, f"template={settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME}")

    def test_representative_can_edit_org_data_notes_hidden(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "category_id": "sponsorship",
                "sort_order": 2,
                "group_cn": "almalinux-gold",
                "enabled": True,
            },
        )
        gold = MembershipType.objects.get(code="gold")
        if gold.group_cn != "almalinux-gold":
            gold.group_cn = "almalinux-gold"
            gold.save(update_fields=["group_cn"])
        self.assertEqual(gold.group_cn, "almalinux-gold")

        MembershipType.objects.get(code="gold")

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-edit", args=[org.pk]))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "AlmaLinux")
            self.assertContains(resp, 'id="id_website_logo"')
            self.assertNotContains(resp, 'textarea name="website_logo"')

            # Membership requests are handled on the separate request page.
            self.assertNotContains(resp, 'id="id_membership_type"')
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
                    "country_code": "US",
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

        # Keep deterministic with --keepdb: only one requestable org type.
        MembershipType.objects.update(enabled=False)

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "category_id": "sponsorship",
                "sort_order": 2,
                "group_cn": "almalinux-gold",
                "enabled": True,
            },
        )

        gold = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(
            name="AlmaLinux",
            website="https://almalinux.org/",
            representative="bob",
        )

        Membership.objects.create(target_organization=org, membership_type=gold)

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")
        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Edit details")
        self.assertContains(resp, reverse("organization-edit", args=[org.pk]))
        self.assertNotContains(resp, "Request membership")
        self.assertNotContains(resp, reverse("organization-membership-request", args=[org.pk]))

        self.assertNotContains(resp, "Active sponsor")
        self.assertContains(resp, 'alx-status-badge--active">Gold')
        self.assertContains(resp, "membership-gold")
        self.assertNotContains(resp, "badge-warning")

        body = resp.content.decode("utf-8")
        self.assertLess(body.find('id="org-contacts-tabs"'), body.find('class="col-md-7"'))
        self.assertLess(body.find('class="col-md-5"'), body.find("Brand assets"))

    def test_org_detail_shows_dual_category_memberships(self) -> None:
        """An org with both sponsorship-category and mirror-category memberships
        displays both badges and both sponsorship entries on the detail page."""
        from core.models import MembershipType, Organization

        gold = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )[0]

        mirror = MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "description": "Mirror member",
                "category_id": "mirror",
                "sort_order": 10,
                "enabled": True,
            },
        )[0]

        org = Organization.objects.create(
            name="DualCatOrg",
            website="https://example.com/",
            representative="bob",
        )

        # Create two memberships in different categories
        Membership.objects.create(target_organization=org, membership_type=gold)
        Membership.objects.create(target_organization=org, membership_type=mirror)

        # Verify both rows exist simultaneously (DB-level enforcement)
        self.assertEqual(
            Membership.objects.filter(target_organization=org).count(),
            2,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)

        # Both membership badges render
        self.assertContains(resp, 'alx-status-badge--active">Gold')
        self.assertContains(resp, 'alx-status-badge--active">Mirror')

        # Both membership names appear in the sponsorship level card
        self.assertContains(resp, "Gold Sponsor Member")
        self.assertContains(resp, "Mirror Member")

        # A same-category duplicate is rejected by the DB constraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Membership.objects.create(
                    target_organization=org,
                    membership_type=MembershipType.objects.update_or_create(
                        code="platinum",
                        defaults={
                            "name": "Platinum Sponsor Member",
                            "category_id": "sponsorship",
                            "sort_order": 3,
                            "enabled": True,
                        },
                    )[0],
                )

    def test_org_detail_shows_change_tier_button_for_multi_type_category(self) -> None:
        from core.models import MembershipType, Organization

        # Keep deterministic with --keepdb: only same-category sponsorship tiers.
        MembershipType.objects.update(enabled=False)

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-silver",
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
                "group_cn": "almalinux-gold",
            },
        )

        org = Organization.objects.create(name="Tiered Org", representative="bob")
        Membership.objects.create(target_organization=org, membership_type_id="silver")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Change tier")
        self.assertContains(
            resp,
            reverse("organization-membership-request", args=[org.pk]) + "?membership_type=silver",
        )
        self.assertNotContains(resp, "Request membership")

    def test_org_detail_hides_request_membership_button_when_no_more_categories_available(self) -> None:
        from core.models import MembershipType, Organization

        # Keep deterministic with --keepdb: only these requestable org types.
        MembershipType.objects.update(enabled=False)

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-silver",
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "description": "Mirror",
                "category_id": "mirror",
                "sort_order": 2,
                "enabled": True,
                "group_cn": "almalinux-mirror",
            },
        )

        org = Organization.objects.create(name="Complete Org", representative="bob")
        Membership.objects.create(target_organization=org, membership_type_id="silver")
        Membership.objects.create(target_organization=org, membership_type_id="mirror")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Request membership")
        self.assertNotContains(resp, reverse("organization-membership-request", args=[org.pk]))

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
                "category_id": "sponsorship",
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
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
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
                    "name": "AlmaLinux",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://almalinux.org/",
                    "country_code": "US",
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

    def test_membership_type_change_creates_request_until_approved(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType, Note, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "category_id": "sponsorship",
                "sort_order": 2,
                "group_cn": "almalinux-gold",
                "enabled": True,
            },
        )
        gold = MembershipType.objects.get(code="gold")
        if gold.group_cn != "almalinux-gold":
            gold.group_cn = "almalinux-gold"
            gold.save(update_fields=["group_cn"])

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        # Current sponsorship stored in Membership table
        Membership.objects.create(target_organization=org, membership_type_id="silver")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})

        factory = RequestFactory()
        request = factory.post(
            reverse("organization-membership-request", args=[org.pk]),
            data={
                "membership_type": "gold",
                "q_sponsorship_details": "Please consider our updated membership level.",
            },
        )
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.session["_freeipa_username"] = "bob"
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        request.user = SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: "bob",
            has_perm=lambda _perm: False,
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            resp = views_membership.membership_request(request, organization_id=org.pk)

        self.assertEqual(resp.status_code, 302)

        # The upgrade request is pending — the current-state Membership row is not changed yet
        self.assertEqual(
            Membership.objects.filter(target_organization=org).first().membership_type_id,
            "silver",
        )

        req = MembershipRequest.objects.get(status=MembershipRequest.Status.pending)
        self.assertEqual(req.membership_type_id, "gold")
        self.assertEqual(req.requested_organization_id, org.pk)
        self.assertEqual(
            req.responses,
            [{"Sponsorship details": "Please consider our updated membership level."}],
        )

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

        self._login_as_freeipa_user("bob")
        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Under review")
            self.assertContains(resp, "Annual dues: $20,000 USD")

    def test_org_membership_request_prefills_current_membership_type(self) -> None:
        """When a representative visits the org request page, the current type should be pre-selected."""
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
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="Prefill Org",
            website_logo="https://example.com/logo",
            website="https://example.com/",
            representative="bob",
        )

        # Prefill is based on Membership row, not org field
        Membership.objects.create(target_organization=org, membership_type_id="gold")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            resp = self.client.get(reverse("organization-membership-request", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'value="gold" selected')
        self.assertNotContains(resp, 'value="gold"')
        self.assertContains(resp, 'value="silver"')

    def test_org_membership_request_requires_representative(self) -> None:
        from core.models import FreeIPAPermissionGrant, MembershipType, Organization
        from core.permissions import ASTRA_VIEW_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Access Org", representative="bob")

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("alice")
        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("organization-membership-request", args=[org.pk]))
        self.assertEqual(resp.status_code, 404)

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")
        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            resp = self.client.get(reverse("organization-membership-request", args=[org.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_org_detail_renewal_cta_uses_canonical_membership_request_link(self) -> None:
        from core.models import Membership, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")

        expires_at = timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1)
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=expires_at,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, reverse("organization-sponsorship-extend", args=[org.pk]))
        self.assertContains(
            resp,
            reverse("organization-membership-request", args=[org.pk]) + "?membership_type=gold",
        )

    def test_sponsorship_extend_endpoint_redirects_to_canonical_request_form(self) -> None:
        from core.models import Membership, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        expires_at = timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1)
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=expires_at,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(
                reverse("organization-sponsorship-extend", args=[org.pk]),
                data={"membership_type": "gold"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp["Location"],
            reverse("organization-membership-request", args=[org.pk]) + "?membership_type=gold",
        )
        self.assertFalse(MembershipRequest.objects.filter(requested_organization=org).exists())

    def test_org_membership_request_dropdown_labels_and_order(self) -> None:
        from core.models import MembershipType, MembershipTypeCategory, Organization

        MembershipTypeCategory.objects.filter(pk="sponsorship").update(is_organization=True, sort_order=2)
        MembershipTypeCategory.objects.filter(pk="mirror").update(is_organization=True, sort_order=1)

        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "category_id": "mirror",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Label Org", representative="bob")
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            resp = self.client.get(reverse("organization-membership-request", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")

        mirror_label = 'value="mirror">Mirror Member<'
        silver_label = 'value="silver">Silver Sponsor Member<'
        gold_label = 'value="gold">Gold Sponsor Member<'

        self.assertIn(mirror_label, body)
        self.assertIn(silver_label, body)
        self.assertIn(gold_label, body)
        self.assertNotIn("Sponsorship ->", body)
        self.assertNotIn("Mirror ->", body)

        mirror_idx = body.find(mirror_label)
        silver_idx = body.find(silver_label)
        gold_idx = body.find(gold_label)
        self.assertGreater(mirror_idx, -1)
        self.assertLess(mirror_idx, silver_idx)
        self.assertLess(silver_idx, gold_idx)

        self.assertNotIn('<optgroup label="Mirror">', body)
        self.assertIn('<optgroup label="Sponsorship">', body)

        self.assertContains(resp, 'id="id_q_sponsorship_details"')
        self.assertContains(resp, 'id="id_q_domain"')

    def test_org_membership_request_blocks_category_with_pending_request(self) -> None:
        from core.models import MembershipRequest, MembershipType, MembershipTypeCategory, Organization

        MembershipTypeCategory.objects.filter(pk="sponsorship").update(is_organization=True, sort_order=1)
        MembershipTypeCategory.objects.filter(pk="mirror").update(is_organization=True, sort_order=2)

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "category_id": "mirror",
                "sort_order": 3,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Pending Org", representative="bob")
        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="gold",
            status=MembershipRequest.Status.pending,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            resp = self.client.get(reverse("organization-membership-request", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertNotIn('value="silver"', body)
        self.assertNotIn('value="gold"', body)
        self.assertIn('value="mirror"', body)

    def test_org_membership_request_blocks_category_with_on_hold_request(self) -> None:
        from core.models import MembershipRequest, MembershipType, MembershipTypeCategory, Organization

        MembershipTypeCategory.objects.filter(pk="sponsorship").update(is_organization=True, sort_order=1)
        MembershipTypeCategory.objects.filter(pk="mirror").update(is_organization=True, sort_order=2)

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "category_id": "mirror",
                "sort_order": 3,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="OnHold Org", representative="bob")
        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="gold",
            status=MembershipRequest.Status.on_hold,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            resp = self.client.get(reverse("organization-membership-request", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertNotIn('value="silver"', body)
        self.assertNotIn('value="gold"', body)
        self.assertIn('value="mirror"', body)

    def test_org_membership_request_allows_other_type_in_category_when_active(self) -> None:
        from core.models import Membership, MembershipType, MembershipTypeCategory, Organization

        MembershipTypeCategory.objects.filter(pk="sponsorship").update(is_organization=True, sort_order=1)

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Active Org", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type_id="silver",
            expires_at=timezone.now() + datetime.timedelta(days=200),
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            resp = self.client.get(reverse("organization-membership-request", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertNotIn('value="silver"', body)
        self.assertIn('value="gold"', body)

    def test_org_membership_request_allows_renewal_when_expiring_soon(self) -> None:
        from core.models import Membership, MembershipType, MembershipTypeCategory, Organization

        MembershipTypeCategory.objects.filter(pk="sponsorship").update(is_organization=True, sort_order=1)

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Expiring Org", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type_id="silver",
            expires_at=timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1),
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            resp = self.client.get(reverse("organization-membership-request", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="silver"')

    def test_org_membership_request_renewal_prefills_last_request_answers(self) -> None:
        from core.models import Membership, MembershipRequest, MembershipType, MembershipTypeCategory, Organization

        MembershipTypeCategory.objects.filter(pk="sponsorship").update(is_organization=True, sort_order=1)

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Expiring Org", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type_id="silver",
            expires_at=timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1),
        )

        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
            status=MembershipRequest.Status.approved,
            responses=[{"Sponsorship details": "Previously submitted sponsor details"}],
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            resp = self.client.get(reverse("organization-membership-request", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        form = resp.context["form"]
        self.assertEqual(
            form.initial.get("q_sponsorship_details"),
            "Previously submitted sponsor details",
        )
        self.assertFalse(form.fields["q_sponsorship_details"].disabled)

    def test_org_membership_request_does_not_call_level_change_for_other_category(self) -> None:
        from core.models import MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "description": "Mirror Member",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "group_cn": "almalinux-gold",
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="Category Check",
            website_logo="https://example.com/logo",
            website="https://example.com/",
            representative="bob",
        )

        Membership.objects.create(target_organization=org, membership_type_id="mirror")

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            resp = self.client.post(
                reverse("organization-membership-request", args=[org.pk]),
                data={
                    "membership_type": "gold",
                    "q_sponsorship_details": "Looking to sponsor.",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertIn("Membership request submitted for review.", messages)
        self.assertEqual(MembershipRequest.objects.filter(requested_organization=org).count(), 1)

    def test_org_membership_request_requires_signed_coc(self) -> None:
        from core.backends import FreeIPAFASAgreement
        from core.models import MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "description": "Mirror Member",
                "category_id": "mirror",
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

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        with patch("core.backends.FreeIPAUser.get", autospec=True, return_value=bob):
            with patch("core.views_utils.FreeIPAFASAgreement.get", autospec=True, return_value=coc):
                resp = self.client.post(
                    reverse("organization-membership-request", args=[org.pk]),
                    data={
                        "membership_type": "silver",
                        "q_sponsorship_details": "Please renew.",
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        expected = (
            f"{reverse('settings')}?tab=agreements&agreement={quote_plus(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN)}"
        )
        self.assertEqual(resp["Location"], expected)
        self.assertEqual(MembershipRequest.objects.count(), 0)


    def test_membership_admin_can_set_org_sponsorship_expiry_when_missing(self) -> None:
        import datetime

        from core.models import Membership, MembershipType, Organization
        from core.permissions import ASTRA_CHANGE_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "description": "Gold",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        sponsorship = Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=None,
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [], "c": ["US"]},
        )
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
        # replace_within_category() deletes the old row and creates a new one,
        # so re-query rather than refresh_from_db on the stale reference.
        sponsorship = Membership.objects.get(target_organization=org)
        self.assertEqual(
            sponsorship.expires_at,
            datetime.datetime(2030, 1, 31, 23, 59, 59, tzinfo=datetime.UTC),
        )

    def test_membership_admin_setting_expiry_in_past_logs_expiry_changed_and_marks_membership_inactive(self) -> None:
        import datetime

        from core.models import FreeIPAPermissionGrant, Membership, MembershipLog, MembershipType, Organization
        from core.permissions import ASTRA_CHANGE_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "description": "Gold",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
                "group_cn": "sponsor-group",
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [], "c": ["US"]},
        )
        rep = FreeIPAUser(
            "bob",
            {"uid": ["bob"], "mail": ["bob@example.com"], "memberof_group": ["sponsor-group"], "c": ["US"]},
        )
        past_date = datetime.date(2000, 1, 1)

        def fake_get(username: str) -> FreeIPAUser | None:
            if username == "bob":
                return rep
            return reviewer

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=fake_get),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_from_group,
        ):
            resp = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={
                    "expires_on": past_date.isoformat(),
                    "next": reverse("organization-detail", args=[org.pk]),
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        membership = Membership.objects.filter(target_organization=org, membership_type_id="gold").first()
        self.assertIsNotNone(membership)
        self.assertIsNotNone(membership.expires_at)
        self.assertFalse(
            Membership.objects.active().filter(target_organization=org, membership_type_id="gold").exists()
        )
        remove_from_group.assert_called_once_with(rep, group_name="sponsor-group")
        self.assertTrue(
            MembershipLog.objects.filter(
                action=MembershipLog.Action.expiry_changed,
                target_organization=org,
                membership_type_id="gold",
            ).exists()
        )
        self.assertFalse(
            MembershipLog.objects.filter(
                action=MembershipLog.Action.terminated,
                target_organization=org,
                membership_type_id="gold",
            ).exists()
        )

    def test_membership_admin_can_set_org_sponsorship_expiry_creates_row_when_absent(self) -> None:
        """Setting expiry when no membership exists returns an error."""
        import datetime

        from core.models import FreeIPAPermissionGrant, Membership, MembershipType, Organization
        from core.permissions import ASTRA_CHANGE_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "description": "Gold",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.filter(target_organization=org).delete()

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")

        new_expires_on = datetime.date(2030, 1, 31)

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={
                    "expires_on": new_expires_on.isoformat(),
                    "next": reverse("organization-detail", args=[org.pk]),
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertIn("That organization does not currently have an active sponsorship of that type.", messages)
        self.assertFalse(Membership.objects.filter(target_organization=org).exists())

    def test_membership_admin_cannot_set_expiry_on_expired_org_sponsorship(self) -> None:
        import datetime

        from core.models import Membership, MembershipType, Organization
        from core.permissions import ASTRA_CHANGE_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "description": "Gold",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        expired_at = timezone.now() - datetime.timedelta(days=1)
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=expired_at,
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")

        new_expires_on = datetime.date(2030, 1, 31)

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={
                    "expires_on": new_expires_on.isoformat(),
                    "next": reverse("organization-detail", args=[org.pk]),
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertIn("That organization does not currently have an active sponsorship of that type.", messages)
        sponsorship = Membership.objects.get(target_organization=org)
        self.assertEqual(sponsorship.expires_at, expired_at)

    def test_membership_admin_can_set_org_sponsorship_expiry_when_membership_type_mismatch(self) -> None:
        """Setting expiry for a type the org does not hold returns an error."""
        import datetime

        from core.models import Membership, MembershipType, Organization
        from core.permissions import ASTRA_CHANGE_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "description": "Gold",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver",
                "description": "Silver",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")

        # Org has silver, but we will try to set expiry for gold.
        Membership.objects.create(
            target_organization=org,
            membership_type_id="silver",
            expires_at=None,
        )

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")

        new_expires_on = datetime.date(2030, 1, 31)

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={
                    "expires_on": new_expires_on.isoformat(),
                    "next": reverse("organization-detail", args=[org.pk]),
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertIn("That organization does not currently have an active sponsorship of that type.", messages)
        # Silver membership should be untouched.
        sponsorship = Membership.objects.get(target_organization=org)
        self.assertEqual(sponsorship.membership_type_id, "silver")
        self.assertIsNone(sponsorship.expires_at)

    def test_organization_detail_shows_committee_notes_with_request_link(self) -> None:
        from core.models import MembershipRequest, MembershipType, Note, Organization
        from core.permissions import ASTRA_VIEW_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "category_id": "sponsorship",
                "sort_order": 2,
                "group_cn": "sponsor-group",
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

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Membership Committee Notes")
        self.assertContains(resp, "Org note")
        self.assertContains(resp, f"(req. #{req.pk})")
        self.assertContains(resp, f'href="{reverse("membership-request-detail", args=[req.pk])}"')

    def test_organization_detail_scopes_request_links_per_membership_type(self) -> None:
        from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "description": "Mirror Member",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")

        Membership.objects.create(target_organization=org, membership_type_id="gold")
        Membership.objects.create(target_organization=org, membership_type_id="mirror")

        req_gold = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="gold",
            status=MembershipRequest.Status.approved,
            decided_at=timezone.now(),
        )
        req_mirror = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="mirror",
            status=MembershipRequest.Status.approved,
            decided_at=timezone.now(),
        )

        MembershipLog.objects.bulk_create(
            [
                MembershipLog(
                    actor_username="reviewer",
                    target_username="",
                    target_organization=org,
                    membership_type_id="gold",
                    membership_request=req_gold,
                    action=MembershipLog.Action.approved,
                ),
                MembershipLog(
                    actor_username="reviewer",
                    target_username="",
                    target_organization=org,
                    membership_type_id="mirror",
                    membership_request=req_mirror,
                    action=MembershipLog.Action.approved,
                ),
            ]
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("membership-request-self", args=[req_gold.pk]))
        self.assertContains(resp, reverse("membership-request-self", args=[req_mirror.pk]))

    def test_organization_detail_allows_renewal_when_other_type_pending(self) -> None:
        from core.models import Membership, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "description": "Mirror Member",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")

        expires_at = timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1)
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=expires_at,
        )
        Membership.objects.create(
            target_organization=org,
            membership_type_id="mirror",
            expires_at=expires_at,
        )

        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="mirror",
            status=MembershipRequest.Status.pending,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp,
            reverse("organization-membership-request", args=[org.pk]) + "?membership_type=gold",
        )
        self.assertContains(resp, "Request renewal")

    def test_organization_detail_pending_request_context_uses_standardized_entry_shape(self) -> None:
        from core.models import Membership, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        expires_at = timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1)
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=expires_at,
        )
        pending = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="gold",
            status=MembershipRequest.Status.pending,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        sponsorship_entries = resp.context["sponsorship_entries"]
        self.assertEqual(len(sponsorship_entries), 1)
        pending_context = sponsorship_entries[0]["pending_request"]
        self.assertIsInstance(pending_context, dict)
        self.assertEqual(pending_context["request_id"], pending.pk)
        self.assertEqual(pending_context["status"], MembershipRequest.Status.pending)

    def test_organization_detail_hides_expired_memberships(self) -> None:
        from core.models import Membership, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "description": "Mirror Member",
                "category_id": "mirror",
                "sort_order": 3,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")

        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )
        Membership.objects.create(
            target_organization=org,
            membership_type_id="mirror",
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Gold Sponsor Member")
        self.assertNotContains(resp, "Mirror Member")

    def test_organization_aggregate_notes_allows_posting_but_hides_vote_buttons(self) -> None:
        from core.models import MembershipRequest, MembershipType, Note, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "category_id": "sponsorship",
                "sort_order": 2,
                "group_cn": "almalinux-gold",
                "enabled": True,
            },
        )

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member",
                "category_id": "sponsorship",
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
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [], "c": ["US"]},
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
        from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
                "group_cn": "sponsor-group",
            },
        )

        gold = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        expires_at = timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1)
        Membership.objects.create(
            target_organization=org,
            membership_type=gold,
            expires_at=expires_at,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Expires")
        self.assertContains(resp, "Request renewal")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            shim_resp = self.client.post(
                reverse("organization-sponsorship-extend", args=[org.pk]),
                data={"membership_type": "gold"},
                follow=False,
            )
            self.assertEqual(shim_resp.status_code, 302)
            self.assertEqual(
                shim_resp["Location"],
                reverse("organization-membership-request", args=[org.pk]) + "?membership_type=gold",
            )
            resp = self.client.post(
                reverse("organization-membership-request", args=[org.pk]),
                data={"membership_type": "gold", "q_sponsorship_details": "Please review our renewal."},
                follow=True,
            )
        self.assertEqual(resp.status_code, 200)
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertIn("Membership request submitted for review.", messages)

        req = MembershipRequest.objects.get(status=MembershipRequest.Status.pending)
        self.assertEqual(req.requested_organization_id, org.pk)
        self.assertEqual(req.membership_type_id, "gold")
        self.assertEqual(req.responses, [{"Sponsorship details": "Please review our renewal."}])

        self.assertTrue(
            MembershipLog.objects.filter(
                action=MembershipLog.Action.requested,
                target_organization=org,
                membership_request=req,
            ).exists()
        )

    def test_sponsorship_extend_requires_membership_type(self) -> None:
        from core.models import Membership, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        gold = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(
            name="Missing Membership Type",
            business_contact_name="Business Person",
            business_contact_email="contact@example.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@example.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@example.org",
            representative="bob",
        )

        expires_at = timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1)
        Membership.objects.create(
            target_organization=org,
            membership_type=gold,
            expires_at=expires_at,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(reverse("organization-sponsorship-extend", args=[org.pk]), follow=True)

        self.assertEqual(resp.status_code, 200)
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertIn("Select a membership to extend.", messages)
        self.assertEqual(MembershipRequest.objects.count(), 0)

    def test_sponsorship_extend_allows_pending_other_type(self) -> None:
        from core.models import Membership, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "description": "Mirror Member",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="Pending Type",
            business_contact_name="Business Person",
            business_contact_email="contact@example.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@example.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@example.org",
            representative="bob",
        )

        expires_at = timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1)
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=expires_at,
        )
        Membership.objects.create(
            target_organization=org,
            membership_type_id="mirror",
            expires_at=expires_at,
        )

        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="mirror",
            status=MembershipRequest.Status.pending,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(
                reverse("organization-sponsorship-extend", args=[org.pk]),
                data={"membership_type": "gold"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp["Location"],
            reverse("organization-membership-request", args=[org.pk]) + "?membership_type=gold",
        )

        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="gold",
            status=MembershipRequest.Status.pending,
        )
        pending_types = set(
            MembershipRequest.objects.filter(
                requested_organization=org,
                status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
            ).values_list("membership_type_id", flat=True)
        )
        self.assertEqual(pending_types, {"gold", "mirror"})

    def test_sponsorship_extend_allows_expiry_equal_now(self) -> None:
        from core.models import Membership, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="Boundary Org",
            representative="bob",
        )

        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        with patch("django.utils.timezone.now", return_value=frozen_now):
            Membership.objects.create(
                target_organization=org,
                membership_type_id="gold",
                expires_at=frozen_now,
            )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("django.utils.timezone.now", return_value=frozen_now),
        ):
            resp = self.client.post(
                reverse("organization-sponsorship-extend", args=[org.pk]),
                data={"membership_type": "gold"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp["Location"],
            reverse("organization-membership-request", args=[org.pk]) + "?membership_type=gold",
        )
        self.assertFalse(MembershipRequest.objects.filter(requested_organization=org).exists())

    def test_sponsorship_extend_rejects_expiry_before_now(self) -> None:
        from core.models import Membership, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="Expired Org",
            representative="bob",
        )

        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        with patch("django.utils.timezone.now", return_value=frozen_now):
            Membership.objects.create(
                target_organization=org,
                membership_type_id="gold",
                expires_at=frozen_now - datetime.timedelta(seconds=1),
            )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("django.utils.timezone.now", return_value=frozen_now),
        ):
            resp = self.client.post(
                reverse("organization-sponsorship-extend", args=[org.pk]),
                data={"membership_type": "gold"},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp["Location"],
            reverse("organization-membership-request", args=[org.pk]) + "?membership_type=gold",
        )
        self.assertFalse(MembershipRequest.objects.filter(requested_organization=org).exists())

    @override_settings(MEMBERSHIP_EXPIRING_SOON_DAYS=90)
    def test_representative_can_submit_second_org_membership_request_for_other_type(self) -> None:
        from core.models import Membership, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member (Annual dues: $20,000 USD)",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        gold = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        expires_at = timezone.now() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS - 1)
        Membership.objects.create(
            target_organization=org,
            membership_type=gold,
            expires_at=expires_at,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            shim_resp = self.client.post(
                reverse("organization-sponsorship-extend", args=[org.pk]),
                data={"membership_type": "gold"},
                follow=False,
            )
        self.assertEqual(shim_resp.status_code, 302)
        self.assertEqual(
            shim_resp["Location"],
            reverse("organization-membership-request", args=[org.pk]) + "?membership_type=gold",
        )

        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="gold",
            status=MembershipRequest.Status.pending,
        )

        self.assertEqual(
            MembershipRequest.objects.filter(
                requested_organization=org,
                status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
            ).count(),
            1,
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_membership.block_action_without_coc", return_value=None),
        ):
            resp = self.client.post(
                reverse("organization-membership-request", args=[org.pk]),
                data={
                    "membership_type": "silver",
                    "q_sponsorship_details": "Please review our sponsorship request.",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Select a valid choice")
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
            Membership,
            MembershipLog,
            MembershipType,
            Organization,
        )
        from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        gold = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            website="https://almalinux.org/",
            representative="bob",
        )

        expires_at = timezone.now() + datetime.timedelta(days=30)
        Membership.objects.create(
            target_organization=org,
            membership_type=gold,
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
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [], "c": ["US"]},
        )
        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Edit expiration")
        self.assertContains(resp, 'data-target="#sponsorship-expiry-modal-gold"')
        self.assertContains(resp, 'id="sponsorship-expiry-modal-gold"')
        self.assertContains(
            resp,
            f'action="{reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"])}"',
        )
        self.assertNotContains(resp, 'data-target="#sponsorship-terminate-modal-gold"')
        self.assertNotContains(resp, 'id="sponsorship-terminate-modal-gold"')
        self.assertContains(
            resp,
            f'action="{reverse("organization-sponsorship-terminate", args=[org.pk, "gold"])}"',
        )

        self.assertContains(resp, f"Manage membership: Gold Sponsor Member for {org.name}")
        self.assertNotContains(resp, "Target:")
        self.assertContains(resp, "Expiration date")
        self.assertContains(resp, "Expiration is an end-of-day date in UTC.")
        self.assertContains(resp, "Save expiration")
        self.assertContains(resp, "Danger zone")
        self.assertContains(resp, "Ends this membership early.")
        self.assertContains(resp, "Terminate membership&hellip;", html=True)
        self.assertContains(resp, 'data-target="#sponsorship-expiry-modal-gold-terminate-collapse"')
        self.assertContains(resp, 'id="sponsorship-expiry-modal-gold-terminate-collapse"')
        self.assertContains(resp, "This will end the membership early and cannot be undone.")
        self.assertContains(resp, "Type the name to confirm")
        self.assertContains(resp, f'placeholder="{org.name}"')
        self.assertContains(resp, f'data-terminate-target="{org.name}"')
        self.assertContains(resp, "Does not match. Type the name to enable termination (case-insensitive).")
        self.assertContains(resp, 'data-terminate-action="cancel"')
        self.assertContains(resp, "Cancel termination")
        self.assertContains(resp, 'id="sponsorship-expiry-modal-gold-terminate-submit"')
        self.assertContains(resp, "disabled")
        self.assertContains(resp, "aria-disabled=\"true\"")
        self.assertContains(resp, "attr('data-terminate-target')")
        self.assertContains(resp, "var modalId = 'sponsorship\\u002Dexpiry\\u002Dmodal\\u002Dgold';")
        self.assertContains(resp, "var inputId = modalId + '-terminate-confirm-input';")
        self.assertContains(resp, "var submitId = modalId + '-terminate-submit';")
        self.assertContains(resp, "jq(document).on('input', '#' + inputId, function() {")
        self.assertContains(resp, "var $input = jq(this);")
        self.assertContains(resp, "jq(document).on('click', '[data-terminate-action=\"cancel\"]', function() {")
        self.assertContains(resp, "jq(collapseSel).on('shown.bs.collapse', function() {")
        self.assertContains(resp, "jq(collapseSel).on('hidden.bs.collapse', function() {")
        self.assertContains(resp, "jq(modalSel).on('hidden.bs.modal', function() {")
        self.assertContains(resp, "$submit.prop('disabled', !matches).attr('aria-disabled', !matches ? 'true' : 'false');")
        self.assertNotContains(resp, "$input.off('input.terminate');")
        self.assertNotContains(resp, "$input.on('input.terminate', updateConfirmState);")
        self.assertNotContains(resp, "jq(collapseSel).on('click', '[data-terminate-action=\"cancel\"]', function () {")
        self.assertNotContains(resp, "jq(modalSel).on('shown.bs.modal hidden.bs.modal', function () {")
        self.assertNotContains(resp, "function setDisabled(btn, disabled) {")
        self.assertNotContains(resp, "data-expiry-modal-state")
        self.assertNotContains(resp, 'data-expiry-action="go-confirm-terminate"')
        self.assertNotContains(resp, 'data-expiry-action="back-to-edit"')

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
        sponsorship = Membership.objects.get(target_organization=org)
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
        self.assertFalse(Membership.objects.filter(target_organization=org).exists())
        self.assertTrue(
            MembershipLog.objects.filter(
                action=MembershipLog.Action.terminated,
                target_organization=org,
                membership_type_id="gold",
            ).exists()
        )

    def test_representative_can_delete_org_and_terminates_sponsorship(self) -> None:
        from core.models import Membership, MembershipLog, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
                "group_cn": "sponsor-group",
            },
        )

        gold = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(
            name="AlmaLinux",
            business_contact_name="Business Person",
            business_contact_email="contact@almalinux.org",
            pr_marketing_contact_name="PR Person",
            pr_marketing_contact_email="pr@almalinux.org",
            technical_contact_name="Tech Person",
            technical_contact_email="tech@almalinux.org",
            website="https://almalinux.org/",
            representative="bob",
        )

        Membership.objects.create(
            target_organization=org,
            membership_type=gold,
            expires_at=timezone.now() + datetime.timedelta(days=90),
        )

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": ["sponsor-group"], "c": ["US"],
            },
        )
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_from_group,
        ):
            resp = self.client.get(reverse("organization-detail", args=[org.pk]))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, 'data-target="#organization-delete-modal"')
            self.assertContains(resp, 'id="organization-delete-modal"')

            resp = self.client.post(reverse("organization-delete", args=[org.pk]), follow=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("organizations"))
        self.assertFalse(Organization.objects.filter(pk=org.pk).exists())
        self.assertTrue(
            MembershipLog.objects.filter(
                action=MembershipLog.Action.terminated,
                target_organization_code=str(org.pk),
                membership_type_id="gold",
            ).exists()
        )
        remove_from_group.assert_called_once_with(bob, group_name="sponsor-group")

    def test_non_representative_cannot_delete_org(self) -> None:
        from core.models import Organization

        org = Organization.objects.create(
            name="AlmaLinux",
            representative="bob",
        )

        alice = FreeIPAUser("alice", {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.post(reverse("organization-delete", args=[org.pk]), follow=False)

        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Organization.objects.filter(pk=org.pk).exists())

    def test_sponsorship_uninterrupted_extension_preserves_created_at(self) -> None:
        import datetime

        from core.models import Membership, MembershipLog, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        membership_type = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(
            name="AlmaLinux",
            representative="bob",
        )

        start_at = datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        extend_at = datetime.datetime(2025, 2, 1, 12, 0, 0, tzinfo=datetime.UTC)

        with patch("django.utils.timezone.now", autospec=True, return_value=start_at):
            first_log = MembershipLog.create_for_approval(
                actor_username="reviewer",
                target_organization=org,
                membership_type=membership_type,
                previous_expires_at=None,
                membership_request=None,
            )

        sponsorship = Membership.objects.get(target_organization=org)
        self.assertEqual(sponsorship.created_at, start_at)

        previous_expires_at = first_log.expires_at
        assert previous_expires_at is not None

        # Simulate drift: current-state row missing, but the term is uninterrupted.
        Membership.objects.filter(target_organization=org).delete()

        with patch("django.utils.timezone.now", autospec=True, return_value=extend_at):
            MembershipLog.create_for_approval(
                actor_username="reviewer",
                target_organization=org,
                membership_type=membership_type,
                previous_expires_at=previous_expires_at,
                membership_request=None,
            )

        recreated = Membership.objects.get(target_organization=org)
        self.assertEqual(recreated.created_at, start_at)
        self.assertGreater(recreated.expires_at, previous_expires_at)

    def test_expired_sponsorship_starts_new_term_and_resets_created_at(self) -> None:
        import datetime

        from core.models import Membership, MembershipLog, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        membership_type = MembershipType.objects.get(code="gold")

        org = Organization.objects.create(
            name="AlmaLinux",
            representative="bob",
        )

        start_at = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        after_expiry_at = datetime.datetime(2025, 7, 1, 12, 0, 0, tzinfo=datetime.UTC)

        with patch("django.utils.timezone.now", autospec=True, return_value=start_at):
            MembershipLog.create_for_approval(
                actor_username="reviewer",
                target_organization=org,
                membership_type=membership_type,
                previous_expires_at=None,
                membership_request=None,
            )

        # Force an expired current-state row.
        Membership.objects.filter(target_organization=org).update(expires_at=start_at)

        with patch("django.utils.timezone.now", autospec=True, return_value=after_expiry_at):
            MembershipLog.create_for_approval(
                actor_username="reviewer",
                target_organization=org,
                membership_type=membership_type,
                previous_expires_at=start_at,
                membership_request=None,
            )

        current = Membership.objects.get(target_organization=org)
        self.assertEqual(current.created_at, after_expiry_at)

    def test_representative_cannot_extend_expired_sponsorship(self) -> None:
        import datetime

        from core.models import Membership, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="AlmaLinux",
            representative="bob",
        )

        expired_at = timezone.now() - datetime.timedelta(days=1)
        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=expired_at,
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
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
                "category_id": "sponsorship",
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
            website_logo="https://example.com/logo-options",
            website="https://almalinux.org/",
            representative="bob",
        )

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
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
                "category_id": "sponsorship",
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
                "category_id": "sponsorship",
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

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
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
                    "name": "AlmaLinux",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://almalinux.org/",
                    "country_code": "US",
                    "representative": "carol",
                },
                follow=False,
            )
        self.assertEqual(resp.status_code, 302)

        org.refresh_from_db()
        self.assertEqual(org.representative, "carol")

    def test_committee_can_edit_unclaimed_org_without_representative(self) -> None:
        from core.models import Organization

        organization = Organization.objects.create(
            name="Unclaimed Editable",
            business_contact_name="Business",
            business_contact_email="business@example.com",
            website_logo="https://example.com/logo-options",
            website="https://example.com/",
            representative="",
        )

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")

        payload = self._valid_org_payload(name="Unclaimed Editable Updated")
        payload["representative"] = ""

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            response = self.client.post(reverse("organization-edit", args=[organization.pk]), data=payload, follow=False)

        self.assertEqual(response.status_code, 302)
        organization.refresh_from_db()
        self.assertEqual(organization.name, "Unclaimed Editable Updated")
        self.assertEqual(organization.representative, "")
        self.assertEqual(organization.status, Organization.Status.unclaimed)

    def test_deleting_organization_does_not_delete_membership_requests_or_audit_logs(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType, Organization

        membership_type, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "category_id": "sponsorship",
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
        MembershipLog.create_for_request(
            actor_username="bob",
            target_organization=org,
            membership_type=membership_type,
            membership_request=req,
        )

        org.delete()

        self.assertTrue(MembershipRequest.objects.filter(pk=req.pk).exists())
        self.assertTrue(MembershipLog.objects.filter(membership_request_id=req.pk).exists())

    def test_user_can_create_organization_and_becomes_representative(self) -> None:
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            resp = self.client.get(reverse("organization-create"))
        self.assertEqual(resp.status_code, 200)

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            resp = self.client.post(
                reverse("organization-create"),
                data={
                    "name": "AlmaLinux",
                    "country_code": "US",
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

        from core.models import Organization

        created = Organization.objects.get(name="AlmaLinux")
        self.assertEqual(created.representative, "bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.get(reverse("organizations"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("organization-detail", args=[created.pk]))

    def test_organization_create_redirects_creator_to_membership_request(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member (Annual dues: $2,500 USD)",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            resp = self.client.post(
                reverse("organization-create"),
                data={
                    "name": "AlmaLinux",
                    "country_code": "US",
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
        self.assertEqual(
            resp["Location"],
            reverse("organization-membership-request", args=[created.pk]),
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            request_resp = self.client.get(reverse("organization-membership-request", args=[created.pk]))
        self.assertEqual(request_resp.status_code, 200)
        self.assertContains(request_resp, "Request Membership")

    def test_org_edit_highlights_contact_tabs_with_validation_errors(self) -> None:
        from core.models import Organization

        org = Organization.objects.create(
            name="Acme",
            website_logo="https://example.com/logo",
            website="https://example.com/",
            representative="bob",
        )

        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with patch("core.backends.FreeIPAUser.get", return_value=bob):
            resp = self.client.post(
                reverse("organization-edit", args=[org.pk]),
                data={
                    # Trigger errors across all contact tabs: required business
                    # fields + invalid optional emails for marketing/technical.
                    "name": "Acme",
                    "website_logo": "https://example.com/logo",
                    "website": "https://example.com/",
                    "country_code": "US",
                    "pr_marketing_contact_email": "not-an-email",
                    "technical_contact_email": "not-an-email",
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

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": [], "c": ["US"]})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("reviewer")

        def fake_get(username: str):
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=fake_get),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            resp = self.client.post(
                reverse("organization-create"),
                data={
                    "representative": "bob",
                    "name": "Acme",
                    "country_code": "US",
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
        self.assertContains(resp, "Membership")

    def test_org_create_highlights_contact_tabs_with_validation_errors(self) -> None:
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        self._login_as_freeipa_user("bob")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=bob),
            patch("core.views_utils.has_signed_coc", return_value=True),
        ):
            resp = self.client.post(
                reverse("organization-create"),
                data={
                    # Trigger errors across all contact tabs: required business
                    # fields + invalid optional emails for marketing/technical.
                    "name": "AlmaLinux",
                    "website_logo": "https://example.com/logo-options",
                    "website": "https://almalinux.org/",
                    "country_code": "US",
                    "pr_marketing_contact_email": "not-an-email",
                    "technical_contact_email": "not-an-email",
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

import datetime
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant, Membership, MembershipType
from core.permissions import ASTRA_CHANGE_MEMBERSHIP
from core.sponsorship_invoices import InvoiceTerm
from core.tests.test_sponsorship_invoices import SponsorshipInvoiceTestMixin


class SponsorshipInvoiceViewTests(SponsorshipInvoiceTestMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="committee",
        )
        self.users = {
            username: FreeIPAUser(username, {"uid": [username], "mail": [f"{username}@example.com"], "memberof_group": []})
            for username in ("rep1", "committee", "stranger")
        }
        user_patch = patch("core.freeipa.user.FreeIPAUser.get", side_effect=self.users.get)
        user_patch.start()
        self.addCleanup(user_patch.stop)

    def _login(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _membership_expiring_in(self, days: int, *, membership_type: MembershipType | None = None) -> Membership:
        return self._membership(
            expires_at=timezone.now() + datetime.timedelta(days=days),
            membership_type=membership_type,
        )

    def _api_invoice(self) -> dict[str, object] | None:
        response = self.client.get(reverse("api-organization-detail-page", args=[self.organization.pk]))
        self.assertEqual(response.status_code, 200)
        [membership] = response.json()["organization"]["memberships"]
        return membership["invoice"]

    def _regenerate(self, membership: Membership):
        return self.client.post(
            reverse(
                "organization-sponsorship-invoice-regenerate",
                args=[self.organization.pk, membership.membership_type_id],
            )
        )

    def test_representative_sees_invoice_while_renewal_is_pending(self) -> None:
        from core.sponsorship_invoices import invoice_number

        membership = self._membership_expiring_in(30)
        self._login("rep1")

        self.assertEqual(
            self._api_invoice(),
            {"number": invoice_number(membership, InvoiceTerm.renewal), "url": None},
        )

        key = self._invoice_key(membership, InvoiceTerm.renewal)
        default_storage.save(key, ContentFile(b"%PDF-stored"))
        self.assertEqual(self._api_invoice()["url"], default_storage.url(key))

    def test_representative_gets_no_invoice_outside_the_renewal_window(self) -> None:
        self._membership_expiring_in(200)
        self._login("rep1")

        self.assertIsNone(self._api_invoice())

    def test_committee_works_with_the_current_term_invoice_outside_the_renewal_window(self) -> None:
        from core.sponsorship_invoices import invoice_number

        # e.g. a newly approved sponsor whose welcome invoice needs correcting.
        membership = self._membership_expiring_in(200)
        self._login("committee")

        self.assertEqual(
            self._api_invoice(),
            {"number": invoice_number(membership, InvoiceTerm.current), "url": None},
        )

        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-current") as render_mock:
            self._regenerate(membership)

        render_mock.assert_called_once_with(membership, InvoiceTerm.current)
        self.assertTrue(default_storage.exists(self._invoice_key(membership, InvoiceTerm.current)))

    def test_free_tiers_have_no_invoice(self) -> None:
        free_tier = MembershipType.objects.create(
            code="community",
            name="Community Sponsor",
            category_id="sponsorship",
            enabled=True,
            annual_dues_usd=0,
        )
        self._membership_expiring_in(30, membership_type=free_tier)
        self._login("committee")

        self.assertIsNone(self._api_invoice())

    def test_representative_regenerates_invoice_while_renewal_is_pending(self) -> None:

        membership = self._membership_expiring_in(30)
        self._login("rep1")

        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-new") as render_mock:
            response = self._regenerate(membership)

        self.assertRedirects(
            response,
            reverse("organization-detail", args=[self.organization.pk]),
            fetch_redirect_response=False,
        )
        render_mock.assert_called_once()
        with default_storage.open(self._invoice_key(membership, InvoiceTerm.renewal), "rb") as stored:
            self.assertEqual(stored.read(), b"%PDF-new")
        [log] = self._invoice_logs()
        self.assertEqual((log["action"], log["actor_username"]), ("invoice_regenerated", "rep1"))

    def test_regenerate_is_refused_for_representative_outside_window_and_strangers(self) -> None:
        membership = self._membership_expiring_in(200)

        for username in ("rep1", "stranger"):
            with self.subTest(username=username):
                self._login(username)
                with patch("core.sponsorship_invoices.render_invoice_pdf") as render_mock:
                    response = self._regenerate(membership)
                self.assertEqual(response.status_code, 404)
                render_mock.assert_not_called()

    @override_settings(SPONSORSHIP_INVOICE_REGENERATE_LIMIT=1)
    def test_regenerate_is_rate_limited(self) -> None:
        membership = self._membership_expiring_in(30)
        self._login("rep1")

        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-new") as render_mock:
            self._regenerate(membership)
            response = self._regenerate(membership)

        render_mock.assert_called_once()
        self.assertRedirects(
            response,
            reverse("organization-detail", args=[self.organization.pk]),
            fetch_redirect_response=False,
        )

    def test_regenerate_failure_is_reported_without_crashing(self) -> None:
        membership = self._membership_expiring_in(30)
        self._login("rep1")

        with (
            patch("core.sponsorship_invoices.render_invoice_pdf", side_effect=RuntimeError("pango exploded")),
            self.assertLogs("core.views_organizations", level="ERROR"),
        ):
            response = self._regenerate(membership)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._invoice_logs(), [])

    def test_detail_payload_includes_billing_notes(self) -> None:
        self._login("rep1")

        response = self.client.get(reverse("api-organization-detail-page", args=[self.organization.pk]))

        self.assertEqual(response.json()["organization"]["billing_notes"], "PO: 70001165892")

    def test_detail_page_bootstraps_regenerate_url_template(self) -> None:
        self._login("rep1")

        response = self.client.get(reverse("organization-detail", args=[self.organization.pk]))

        template = reverse(
            "organization-sponsorship-invoice-regenerate",
            args=[self.organization.pk, "__membership_type_code__"],
        )
        self.assertContains(
            response,
            f'data-organization-detail-sponsorship-invoice-regenerate-url-template="{template}"',
        )

    def test_representative_edits_billing_notes(self) -> None:
        self._login("rep1")
        org = self.organization
        payload = {
            "name": org.name,
            "country_code": org.country_code,
            "business_contact_name": "Billing Person",
            "business_contact_email": org.business_contact_email,
            "website": "https://example.com",
            "street": org.street,
            "city": org.city,
            "state": org.state,
            "postal_code": org.postal_code,
            "billing_notes": "PO: 123\nAttn: Accounts payable",
        }

        response = self.client.post(reverse("organization-edit", args=[org.pk]), data=payload)

        self.assertEqual(response.status_code, 302)
        org.refresh_from_db()
        self.assertEqual(org.billing_notes, "PO: 123\nAttn: Accounts payable")

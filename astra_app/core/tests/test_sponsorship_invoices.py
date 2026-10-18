import datetime
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import TestCase, override_settings
from post_office.models import Email, EmailTemplate

from core.freeipa.user import FreeIPAUser
from core.membership_notes import CUSTOS
from core.models import Membership, MembershipLog, MembershipType, Organization
from core.sponsorship_invoices import BILLING_NOTES_MAX_LINES, InvoiceTerm
from core.tests.utils_test_data import ensure_core_categories

IN_MEMORY_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

PAYMENT_DETAILS = "Pay by ACH, Wire\nBank Info: Example Bank\nAccount Number 000111222"
# Realistic payment details (the ones the dev and E2E stacks print): they make a full-height
# footer, which is what layout tests need to catch overflow.
EXAMPLE_PAYMENT_DETAILS_FILE = Path(settings.BASE_DIR).parent / "docker" / "invoice-payment-details.example.txt"
INCH = 96  # WeasyPrint lays out in CSS pixels.


class SponsorshipInvoiceTestMixin:
    """Shared fixtures: a paid sponsorship tier, an org, and a payment-details file."""

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        self.gold, _ = MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
                "annual_dues_usd": 20000,
            },
        )
        self.organization = Organization.objects.create(
            name="Acme Corp",
            representative="rep1",
            business_contact_email="billing@example.com",
            street="1 Example Way",
            city="Menlo Park",
            state="CA",
            postal_code="94025",
            country_code="US",
            billing_notes="PO: 70001165892",
        )

        payment_file = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        payment_file.write(PAYMENT_DETAILS)
        payment_file.close()
        self.addCleanup(Path(payment_file.name).unlink)
        payment_settings = override_settings(
            SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE=payment_file.name,
            STORAGES=IN_MEMORY_STORAGES,
        )
        payment_settings.enable()
        self.addCleanup(payment_settings.disable)

    def _membership(self, *, expires_at: datetime.datetime, membership_type: MembershipType | None = None) -> Membership:
        return Membership.objects.create(
            target_organization=self.organization,
            membership_type=membership_type or self.gold,
            expires_at=expires_at,
        )

    def _invoice_key(self, membership: Membership, term: InvoiceTerm) -> str:
        from core.sponsorship_invoices import invoice_number, invoice_storage_key

        return invoice_storage_key(membership.target_organization, invoice_number(membership, term))

    def _invoice_filename(self, membership: Membership, term: InvoiceTerm) -> str:
        from core.sponsorship_invoices import invoice_filename, invoice_number

        return invoice_filename(invoice_number(membership, term))

    def _invoice_logs(self) -> list[dict[str, object]]:
        return list(
            MembershipLog.objects.filter(
                target_organization=self.organization,
                action__in=[MembershipLog.Action.invoice_generated, MembershipLog.Action.invoice_regenerated],
            )
            .order_by("created_at", "pk")
            .values("action", "actor_username", "invoice_number")
        )


def _invoice_line_tops(membership: Membership, term: InvoiceTerm) -> list[dict[str, float]]:
    """Lay the invoice out and return, per page, each text line's vertical position."""
    from weasyprint import HTML
    from weasyprint.formatting_structure.boxes import LineBox, TextBox

    from core.sponsorship_invoices import INVOICE_ASSETS_DIR, render_invoice_html

    document = HTML(string=render_invoice_html(membership, term), base_url=f"{INVOICE_ASSETS_DIR.as_uri()}/").render()
    return [
        {
            "".join(box.text for box in line.descendants() if isinstance(box, TextBox)).strip(): line.position_y
            for line in page._page_box.descendants()
            if isinstance(line, LineBox)
        }
        for page in document.pages
    ]


class SponsorshipInvoiceModuleTests(SponsorshipInvoiceTestMixin, TestCase):
    def test_invoice_number_is_org_billed_term_year_and_six_stable_digits(self) -> None:
        from core.sponsorship_invoices import invoice_number

        # 23:59:59 UTC on Dec 31 is already Jan 1 in some time zones; the year must follow UTC.
        membership = self._membership(expires_at=datetime.datetime(2026, 12, 31, 23, 59, 59, tzinfo=datetime.UTC))

        # The welcome invoice bills the term that started a year earlier; the renewal one bills the next term.
        for term, year in ((InvoiceTerm.current, 2025), (InvoiceTerm.renewal, 2026)):
            with self.subTest(term=term):
                number = invoice_number(membership, term)
                self.assertRegex(number, rf"^{self.organization.pk:04d}-{year}-\d{{6}}$")
                # Every reminder must arrive at the same number to find the stored PDF.
                self.assertEqual(invoice_number(Membership.objects.get(pk=membership.pk), term), number)

    def test_storage_key_is_unguessable_per_org(self) -> None:
        from core.sponsorship_invoices import invoice_number

        expires_at = datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.UTC)
        membership = self._membership(expires_at=expires_at)
        other_org = Organization.objects.create(name="Other Org", representative="rep2")
        other_membership = Membership.objects.create(
            target_organization=other_org,
            membership_type=self.gold,
            expires_at=expires_at,
        )

        key = self._invoice_key(membership, InvoiceTerm.renewal)
        self.assertEqual(
            key,
            f"invoices/{self.organization.invoice_token}/AlmaLinux_OS_Foundation_Invoice_{invoice_number(membership, InvoiceTerm.renewal)}.pdf",
        )
        self.assertGreaterEqual(len(self.organization.invoice_token), 20)
        self.assertNotEqual(self.organization.invoice_token, other_org.invoice_token)
        self.assertTrue(self._invoice_key(other_membership, InvoiceTerm.renewal).startswith(f"invoices/{other_org.invoice_token}/"))

    def _apply_log(self, *, action: str, expires_at: datetime.datetime, dues_usd: int | None = None) -> Membership:
        from core.membership_log_side_effects import apply_membership_log_side_effects
        from core.models import MembershipLog

        log = MembershipLog.objects.create(
            actor_username="committee",
            target_organization=self.organization,
            membership_type=self.gold,
            action=action,
            expires_at=expires_at,
            dues_usd=dues_usd,
        )
        apply_membership_log_side_effects(log=log)
        return Membership.objects.get(target_organization=self.organization)

    def test_a_renewed_term_keeps_the_invoice_sent_with_the_reminders(self) -> None:
        from core.models import MembershipLog
        from core.sponsorship_invoices import get_or_create_invoice_pdf, invoice_number

        membership = self._membership(expires_at=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30))
        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-renewal"):
            get_or_create_invoice_pdf(membership, InvoiceTerm.renewal, actor_username="system")

        MembershipLog.create_for_approval(
            actor_username="committee",
            membership_type=self.gold,
            target_organization=self.organization,
            previous_expires_at=membership.expires_at,
        )
        renewed = Membership.objects.get(target_organization=self.organization)

        self.assertEqual(invoice_number(renewed, InvoiceTerm.current), invoice_number(membership, InvoiceTerm.renewal))
        with patch("core.sponsorship_invoices.render_invoice_pdf") as render_mock:
            self.assertEqual(get_or_create_invoice_pdf(renewed, InvoiceTerm.current, actor_username="committee"), b"%PDF-renewal")
        render_mock.assert_not_called()

    def test_a_dues_change_bills_the_term_under_a_new_number(self) -> None:
        from core.models import MembershipLog
        from core.sponsorship_invoices import invoice_number, render_invoice_html

        membership = self._apply_log(
            action=MembershipLog.Action.approved,
            expires_at=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30),
        )
        number = invoice_number(membership, InvoiceTerm.renewal)

        discounted = self._apply_log(action=MembershipLog.Action.dues_changed, expires_at=membership.expires_at, dues_usd=5000)

        self.assertNotEqual(invoice_number(discounted, InvoiceTerm.renewal), number)
        self.assertIn("$5,000", render_invoice_html(discounted, InvoiceTerm.renewal))

    def test_renewal_terms_starting_in_the_same_year_get_separate_invoices(self) -> None:
        from core.models import MembershipLog

        # 2028 is a leap year, so a 365-day term starting Jan 1 ends Dec 31: both renewals start in 2028.
        membership = self._apply_log(
            action=MembershipLog.Action.approved,
            expires_at=datetime.datetime(2028, 1, 1, 23, 59, 59, tzinfo=datetime.UTC),
        )
        renewed = self._apply_log(
            action=MembershipLog.Action.approved,
            expires_at=datetime.datetime(2028, 12, 31, 23, 59, 59, tzinfo=datetime.UTC),
        )

        self.assertNotEqual(self._invoice_key(renewed, InvoiceTerm.renewal), self._invoice_key(membership, InvoiceTerm.renewal))

    def test_welcome_and_renewal_invoices_bill_different_terms(self) -> None:
        from core.sponsorship_invoices import invoice_number, render_invoice_html

        membership = self._membership(expires_at=datetime.datetime(2027, 1, 12, 23, 59, 59, tzinfo=datetime.UTC))

        # A welcome invoice must never be re-sent as the first renewal invoice.
        self.assertNotEqual(invoice_number(membership, InvoiceTerm.current), invoice_number(membership, InvoiceTerm.renewal))
        self.assertNotEqual(
            self._invoice_key(membership, InvoiceTerm.current),
            self._invoice_key(membership, InvoiceTerm.renewal),
        )
        self.assertIn("from January 12th 2026 to January 12th 2027", render_invoice_html(membership, InvoiceTerm.current))
        self.assertIn("from January 12th 2027 to January 12th 2028", render_invoice_html(membership, InvoiceTerm.renewal))

    def test_reminders_reuse_the_stored_invoice_and_regenerating_overwrites_it(self) -> None:
        from core.sponsorship_invoices import get_or_create_invoice_pdf, invoice_number

        membership = self._membership(expires_at=datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.UTC))
        membership.dues_usd = 15000
        membership.save(update_fields=["dues_usd"])

        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-first") as render_mock:
            self.assertEqual(get_or_create_invoice_pdf(membership, InvoiceTerm.renewal, actor_username="system"), b"%PDF-first")
            self.assertEqual(get_or_create_invoice_pdf(membership, InvoiceTerm.renewal, actor_username="system"), b"%PDF-first")
        render_mock.assert_called_once()

        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-second") as render_mock:
            self.assertEqual(
                get_or_create_invoice_pdf(membership, InvoiceTerm.renewal, actor_username="rep1", regenerate=True),
                b"%PDF-second",
            )
        render_mock.assert_called_once()

        # Only renders are logged: serving the stored PDF again bills nothing new.
        number = invoice_number(membership, InvoiceTerm.renewal)
        self.assertEqual(
            self._invoice_logs(),
            [
                {"action": "invoice_generated", "actor_username": "system", "invoice_number": number},
                {"action": "invoice_regenerated", "actor_username": "rep1", "invoice_number": number},
            ],
        )

        key = self._invoice_key(membership, InvoiceTerm.renewal)
        with default_storage.open(key, "rb") as stored:
            self.assertEqual(stored.read(), b"%PDF-second")
        # Overwriting must keep the URL stable instead of saving under a suffixed name.
        self.assertEqual(default_storage.listdir(key.rsplit("/", 1)[0])[1], [key.rsplit("/", 1)[1]])

    def test_every_render_is_noted_and_logged_for_the_committee(self) -> None:
        from core.membership_request_workflow import create_approved_membership_request
        from core.models import MembershipLog, Note
        from core.sponsorship_invoices import get_or_create_invoice_pdf, invoice_number, invoice_storage_key
        from core.templatetags.core_membership_notes import _render_note_content

        term_request = create_approved_membership_request(
            actor_username="committee",
            membership_type=self.gold,
            decided_at=datetime.datetime.now(tz=datetime.UTC),
            target_organization=self.organization,
        )
        membership = self._membership(expires_at=datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.UTC))
        number = invoice_number(membership, InvoiceTerm.renewal)
        url = default_storage.url(invoice_storage_key(self.organization, number))

        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF"):
            get_or_create_invoice_pdf(membership, InvoiceTerm.renewal, actor_username=CUSTOS)
            # Serving the stored PDF again renders nothing, so it is not noted.
            get_or_create_invoice_pdf(membership, InvoiceTerm.renewal, actor_username=CUSTOS)
            get_or_create_invoice_pdf(membership, InvoiceTerm.renewal, actor_username="rep1", regenerate=True)

        notes = list(Note.objects.filter(membership_request=term_request))
        self.assertEqual([note.username for note in notes], [CUSTOS, "rep1"])
        self.assertEqual(
            [note.content for note in notes],
            [
                f"Invoice [{number}]({url}) for $20,000 (term 2026-11-18 to 2027-11-18) generated.",
                f"Invoice [{number}]({url}) for $20,000 (term 2026-11-18 to 2027-11-18) regenerated.",
            ],
        )
        # The committee must be able to click through to the PDF after the sponsorship expires.
        self.assertIn(f'<a href="{url}">{number}</a>', _render_note_content(notes[0]))

        self.assertEqual(
            list(
                MembershipLog.objects.filter(target_organization=self.organization)
                .order_by("created_at", "pk")
                .values_list("action", "actor_username", "membership_type_id", "membership_request_id", "invoice_number")
            ),
            [
                (MembershipLog.Action.invoice_generated, CUSTOS, "gold", term_request.pk, number),
                (MembershipLog.Action.invoice_regenerated, "rep1", "gold", term_request.pk, number),
            ],
        )

    def test_first_renewal_invoice_after_a_welcome_invoice_is_a_separate_pdf(self) -> None:
        from core.sponsorship_invoices import get_or_create_invoice_pdf

        membership = self._membership(expires_at=datetime.datetime(2028, 12, 31, 23, 59, 59, tzinfo=datetime.UTC))

        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-welcome"):
            get_or_create_invoice_pdf(membership, InvoiceTerm.current, actor_username="committee")
        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-renewal") as render_mock:
            self.assertEqual(
                get_or_create_invoice_pdf(membership, InvoiceTerm.renewal, actor_username="system"),
                b"%PDF-renewal",
            )

        render_mock.assert_called_once_with(membership, InvoiceTerm.renewal)
        with default_storage.open(self._invoice_key(membership, InvoiceTerm.current), "rb") as stored:
            self.assertEqual(stored.read(), b"%PDF-welcome")

    def test_invoice_html_contains_billing_data_and_escapes_org_supplied_text(self) -> None:
        from core.sponsorship_invoices import invoice_number, render_invoice_html

        self.organization.name = "Acme <script>alert(1)</script>"
        self.organization.save(update_fields=["name"])
        membership = self._membership(expires_at=datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.UTC))

        # Invoices go out up to 90 days before renewal; the date must be when it was issued.
        with patch("django.utils.timezone.now", return_value=datetime.datetime(2026, 8, 20, 12, tzinfo=datetime.UTC)):
            html = render_invoice_html(membership, InvoiceTerm.renewal)

        self.assertIn(invoice_number(membership, InvoiceTerm.renewal), html)
        self.assertIn("Acme &lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("PO: 70001165892", html)
        self.assertIn("1 Example Way", html)
        self.assertIn("Menlo Park, CA 94025", html)
        self.assertIn("United States", html)
        self.assertIn("Gold Sponsor Member", html)
        self.assertIn("$20,000", html)
        self.assertIn("Invoice date", html)
        self.assertIn("August 20, 2026", html)
        self.assertNotIn("November 18, 2026", html)
        self.assertIn("from November 18th 2026 to November 18th 2027", html)
        self.assertIn("Account Number 000111222", html)

    def test_paired_labels_are_laid_out_on_the_same_line(self) -> None:
        # A long membership name wraps, which is what pushed a bottom-aligned amount down.
        self.gold.name = "Gold Sponsor Membership with a deliberately long tier name"
        self.gold.save(update_fields=["name"])
        membership = self._membership(expires_at=datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.UTC))
        line_tops = _invoice_line_tops(membership, InvoiceTerm.renewal)[0]

        self.assertEqual(line_tops["Bill to:"], line_tops["Invoice #"])
        self.assertEqual(line_tops["Membership type"], line_tops["Amount due"])

        # With an in-kind discount the amounts start with the list price instead.
        membership.dues_usd = 5000
        line_tops = _invoice_line_tops(membership, InvoiceTerm.renewal)[0]
        self.assertEqual(line_tops["Membership type"], line_tops["List price"])

    def test_the_longest_billing_notes_still_fit_one_page_with_the_amount_above_the_footer(self) -> None:
        # Worst case: notes at their 500-character limit, a full address and the three-line discount block.
        self.organization.billing_notes = (
            "OSVC 111131. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Curabitur at augue sit amet nunc "
            "gravida sollicitudin nec id quam. Donec et condimentum purus, sed consectetur eros. Vestibulum ante ipsum "
            "primis in faucibus orci luctus et ultrices posuere cubilia curae; Aliquam egestas eget est nec imperdiet. "
            "Nullam ultrices posuere urna a consectetur. Curabitur fringilla ligula lacus, vel hendrerit dui facilisis "
            "pellentesque. Mauris dapibus molestie dolor suscipit sollicitudin. Ut non viverra."
        )[:500]
        self.organization.save(update_fields=["billing_notes"])
        membership = self._membership(expires_at=datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.UTC))
        membership.dues_usd = 2000

        with self.settings(SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE=str(EXAMPLE_PAYMENT_DETAILS_FILE)):
            pages = _invoice_line_tops(membership, InvoiceTerm.renewal)

        self.assertEqual(len(pages), 1)
        [lines] = pages
        footer_top = lines["Please complete payment upon receipt."]
        # The footer ends at the bottom of the letter-size page (not past it, not above it)...
        self.assertLess(11 * INCH - lines["for specific guidance."], 0.6 * INCH)
        # ...and the amount due sits just above it, the white space being what gives way.
        self.assertLess(footer_top - lines["$2,000"], 1 * INCH)

    def test_billing_notes_beyond_the_line_limit_are_cut_off_without_moving_anything(self) -> None:
        self.organization.billing_notes = "\n".join(f"Note line {number}" for number in range(1, 31))
        self.organization.save(update_fields=["billing_notes"])
        membership = self._membership(expires_at=datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.UTC))

        with self.settings(SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE=str(EXAMPLE_PAYMENT_DETAILS_FILE)):
            pages = _invoice_line_tops(membership, InvoiceTerm.renewal)

        self.assertEqual(len(pages), 1)
        [lines] = pages
        self.assertIn(f"Note line {BILLING_NOTES_MAX_LINES}", lines)
        self.assertNotIn(f"Note line {BILLING_NOTES_MAX_LINES + 1}", lines)
        # The address after the notes is still printed, right below them.
        self.assertIn("1 Example Way", lines)
        self.assertLess(lines["Please complete payment upon receipt."] - lines["$20,000"], 1 * INCH)

    def test_render_invoice_pdf_produces_a_pdf(self) -> None:
        from core.sponsorship_invoices import render_invoice_pdf

        membership = self._membership(expires_at=datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.UTC))

        self.assertTrue(render_invoice_pdf(membership, InvoiceTerm.renewal).startswith(b"%PDF"))

    def test_missing_payment_details_file_raises(self) -> None:
        from core.sponsorship_invoices import render_invoice_html

        membership = self._membership(expires_at=datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.UTC))

        for configured_path in ("", "/nonexistent/payment-details.txt"):
            with self.subTest(configured_path=configured_path):
                with override_settings(SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE=configured_path):
                    with self.assertRaises(ImproperlyConfigured):
                        render_invoice_html(membership, InvoiceTerm.renewal)

    def test_url_fetcher_only_serves_bundled_invoice_assets(self) -> None:
        from core.sponsorship_invoices import INVOICE_ASSETS_DIR, InvoiceAssetFetcher

        asset = next(INVOICE_ASSETS_DIR.glob("*.svg"))
        response = InvoiceAssetFetcher()(asset.as_uri())
        self.assertEqual(response.read(), asset.read_bytes())
        response.close()

        for url in (
            "https://example.com/logo.svg",
            "http://minio:9000/astra-media/mail-images/logo.svg",
            "file:///etc/passwd",
            (INVOICE_ASSETS_DIR / ".." / ".." / ".." / "models.py").as_uri(),
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    InvoiceAssetFetcher()(url)

    def test_payment_details_check_warns_when_file_is_unusable(self) -> None:
        from core.checks_membership import check_sponsorship_invoice_payment_details_file

        self.assertEqual(check_sponsorship_invoice_payment_details_file(), [])
        with override_settings(SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE="/nonexistent/payment-details.txt"):
            issues = check_sponsorship_invoice_payment_details_file()
        self.assertEqual([issue.id for issue in issues], ["core.W002"])


class SponsorshipInvoiceNotificationTests(SponsorshipInvoiceTestMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        for name, subject in (
            (settings.ORGANIZATION_SPONSORSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME, "Sponsorship expiring soon"),
            (settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME, "Sponsorship expired"),
        ):
            EmailTemplate.objects.update_or_create(
                name=name,
                defaults={
                    "subject": subject,
                    "content": "{% if invoice_url %}Invoice {{ invoice_number }}: {{ invoice_url }}{% endif %}",
                    "html_content": "<p>{% if invoice_url %}<a href=\"{{ invoice_url }}\">{{ invoice_number }}</a>{% endif %}</p>",
                },
            )
        self.rep = FreeIPAUser(
            "rep1",
            {
                "uid": ["rep1"],
                "mail": ["rep1@example.com"],
                "fasTimezone": ["UTC"],
                "memberof_group": ["almalinux-gold"],
            },
        )
        self.frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)

    def _expiring_membership(self, *, membership_type: MembershipType | None = None) -> Membership:
        expires_on = self.frozen_now.date() + datetime.timedelta(days=settings.MEMBERSHIP_EXPIRING_SOON_DAYS // 2)
        return self._membership(
            expires_at=datetime.datetime.combine(expires_on, datetime.time(23, 59, 59), tzinfo=datetime.UTC),
            membership_type=membership_type,
        )

    def _run_expiring_soon(self, *, now: datetime.datetime | None = None, dry_run: bool = False) -> None:
        with (
            patch("django.utils.timezone.now", return_value=now or self.frozen_now),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self.rep),
        ):
            call_command("membership_expiration_notifications", dry_run=dry_run)

    def _latest_email(self, template_name: str) -> Email:
        return Email.objects.filter(to="rep1@example.com", template__name=template_name).latest("created")

    def test_expiring_soon_email_attaches_invoice_links_it_and_ccs_business_contact(self) -> None:
        from core.sponsorship_invoices import invoice_number

        membership = self._expiring_membership()

        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-invoice"):
            self._run_expiring_soon()

        email = self._latest_email(settings.ORGANIZATION_SPONSORSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME)
        attachments = list(email.attachments.all())
        self.assertEqual([attachment.name for attachment in attachments], [self._invoice_filename(membership, InvoiceTerm.renewal)])
        self.assertEqual(attachments[0].mimetype, "application/pdf")
        with attachments[0].file.open("rb") as attached:
            self.assertEqual(attached.read(), b"%PDF-invoice")

        expected_url = default_storage.url(self._invoice_key(membership, InvoiceTerm.renewal))
        self.assertEqual(email.context["invoice_url"], expected_url)
        self.assertEqual(email.context["invoice_number"], invoice_number(membership, InvoiceTerm.renewal))
        self.assertIn(expected_url, email.message)
        self.assertEqual(email.cc, [settings.MEMBERSHIP_COMMITTEE_EMAIL, "billing@example.com"])
        self.assertEqual(
            self._invoice_logs(),
            [{"action": "invoice_generated", "actor_username": CUSTOS, "invoice_number": invoice_number(membership, InvoiceTerm.renewal)}],
        )

    def test_later_reminders_reuse_the_stored_invoice(self) -> None:
        self._expiring_membership()
        quarter_window_day = settings.MEMBERSHIP_EXPIRING_SOON_DAYS // 4
        later = self.frozen_now + datetime.timedelta(days=(settings.MEMBERSHIP_EXPIRING_SOON_DAYS // 2) - quarter_window_day)

        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-invoice") as render_mock:
            self._run_expiring_soon()
            self._run_expiring_soon(now=later)

        render_mock.assert_called_once()
        emails = Email.objects.filter(
            to="rep1@example.com",
            template__name=settings.ORGANIZATION_SPONSORSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME,
        )
        self.assertEqual(emails.count(), 2)
        for email in emails:
            self.assertEqual(email.attachments.count(), 1)

    def test_free_tiers_get_no_invoice_and_no_business_contact_cc(self) -> None:
        free_tier = MembershipType.objects.create(
            code="community",
            name="Community Sponsor",
            group_cn="almalinux-community",
            category_id="sponsorship",
            sort_order=1,
            enabled=True,
            annual_dues_usd=0,
        )
        self._expiring_membership(membership_type=free_tier)

        with patch("core.sponsorship_invoices.render_invoice_pdf") as render_mock:
            self._run_expiring_soon()

        render_mock.assert_not_called()
        email = self._latest_email(settings.ORGANIZATION_SPONSORSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME)
        self.assertEqual(email.attachments.count(), 0)
        self.assertNotIn("invoice_url", email.context)
        self.assertEqual(email.cc, [settings.MEMBERSHIP_COMMITTEE_EMAIL])

    def test_dry_run_does_not_generate_invoices(self) -> None:
        self._expiring_membership()

        with patch("core.sponsorship_invoices.render_invoice_pdf") as render_mock:
            self._run_expiring_soon(dry_run=True)

        render_mock.assert_not_called()

    def test_invoice_failure_still_sends_the_reminder(self) -> None:
        self._expiring_membership()

        with (
            patch("core.sponsorship_invoices.render_invoice_pdf", side_effect=RuntimeError("pango exploded")),
            self.assertLogs("core.sponsorship_invoices", level="ERROR"),
        ):
            self._run_expiring_soon()

        email = self._latest_email(settings.ORGANIZATION_SPONSORSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME)
        self.assertEqual(email.attachments.count(), 0)
        self.assertNotIn("invoice_url", email.context)
        self.assertEqual(email.cc, [settings.MEMBERSHIP_COMMITTEE_EMAIL, "billing@example.com"])

    def test_expired_email_attaches_invoice_for_the_lapsed_membership(self) -> None:

        membership = self._membership(expires_at=self.frozen_now - datetime.timedelta(days=1))
        expected_filename = self._invoice_filename(membership, InvoiceTerm.renewal)

        with (
            patch("django.utils.timezone.now", return_value=self.frozen_now),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self.rep),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True),
            patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-invoice"),
        ):
            call_command("membership_expired_cleanup")

        self.assertFalse(Membership.objects.filter(pk=membership.pk).exists())
        email = self._latest_email(settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME)
        self.assertEqual([attachment.name for attachment in email.attachments.all()], [expected_filename])
        self.assertIn("invoice_url", email.context)
        self.assertEqual(email.cc, [settings.MEMBERSHIP_COMMITTEE_EMAIL, "billing@example.com"])


class SponsorshipWelcomeInvoiceTests(SponsorshipInvoiceTestMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.welcome_template, _ = EmailTemplate.objects.update_or_create(
            name="membership-request-approved-gold",
            defaults={
                "subject": "Welcome",
                "content": "Welcome{% if invoice_url %} {{ invoice_number }} {{ invoice_url }}{% endif %}",
                "html_content": "<p>Welcome</p>",
            },
        )
        self.gold.acceptance_template = self.welcome_template
        self.gold.save(update_fields=["acceptance_template"])
        self.rep = FreeIPAUser("rep1", {"uid": ["rep1"], "mail": ["rep1@example.com"], "memberof_group": []})

    def _approve(self) -> Membership:
        from core.membership_request_workflow import approve_membership_request
        from core.models import MembershipRequest

        membership_request = MembershipRequest.objects.create(
            requested_organization=self.organization,
            membership_type=self.gold,
        )
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self.rep),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
            self.captureOnCommitCallbacks(execute=True),
        ):
            approve_membership_request(
                membership_request=membership_request,
                actor_username="committee",
                send_approved_email=True,
            )
        return Membership.objects.get(target_organization=self.organization, membership_type=self.gold)

    def _welcome_email(self) -> Email:
        return Email.objects.get(template__name=self.welcome_template.name)

    def test_new_sponsor_welcome_email_carries_the_current_term_invoice(self) -> None:
        from core.sponsorship_invoices import invoice_number

        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-welcome") as render_mock:
            membership = self._approve()

        render_mock.assert_called_once_with(membership, InvoiceTerm.current)
        email = self._welcome_email()
        self.assertEqual(email.to, ["rep1@example.com"])
        self.assertEqual([attachment.name for attachment in email.attachments.all()], [self._invoice_filename(membership, InvoiceTerm.current)])
        self.assertEqual(email.context["invoice_number"], invoice_number(membership, InvoiceTerm.current))
        self.assertEqual(email.context["invoice_url"], default_storage.url(self._invoice_key(membership, InvoiceTerm.current)))
        self.assertEqual(email.cc, ["billing@example.com"])
        self.assertEqual(
            self._invoice_logs(),
            [{"action": "invoice_generated", "actor_username": "committee", "invoice_number": invoice_number(membership, InvoiceTerm.current)}],
        )

    def test_renewal_approval_email_carries_the_renewed_term_invoice(self) -> None:
        from core.sponsorship_invoices import invoice_number

        # Renewals usually got this invoice with their reminders already; it is the same
        # invoice, and it covers renewals no reminder reached.
        self._membership(expires_at=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30))
        EmailTemplate.objects.get_or_create(
            name=settings.MEMBERSHIP_REQUEST_RENEWAL_APPROVED_EMAIL_TEMPLATE_NAME,
            defaults={"subject": "Renewed", "content": "Renewed", "html_content": "<p>Renewed</p>"},
        )

        with patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-renewed"):
            membership = self._approve()

        email = Email.objects.get(template__name=settings.MEMBERSHIP_REQUEST_RENEWAL_APPROVED_EMAIL_TEMPLATE_NAME)
        self.assertEqual([attachment.name for attachment in email.attachments.all()], [self._invoice_filename(membership, InvoiceTerm.current)])
        self.assertEqual(email.context["invoice_number"], invoice_number(membership, InvoiceTerm.current))
        self.assertEqual(email.cc, ["billing@example.com"])

    def test_invoice_failure_still_approves_and_welcomes(self) -> None:
        with (
            patch("core.sponsorship_invoices.render_invoice_pdf", side_effect=RuntimeError("pango exploded")),
            self.assertLogs("core.sponsorship_invoices", level="ERROR"),
        ):
            self._approve()

        email = self._welcome_email()
        self.assertEqual(email.attachments.count(), 0)
        self.assertNotIn("invoice_url", email.context)
        self.assertEqual(self._invoice_logs(), [])

    def _approve_with_custom_email(self) -> dict[str, list[str]]:
        from urllib.parse import parse_qs, urlsplit

        from django.urls import reverse

        from core.models import FreeIPAPermissionGrant, MembershipRequest
        from core.permissions import ASTRA_ADD_MEMBERSHIP

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="committee",
        )
        users = {
            "rep1": self.rep,
            "committee": FreeIPAUser("committee", {"uid": ["committee"], "mail": ["committee@example.com"], "memberof_group": []}),
        }
        session = self.client.session
        session["_freeipa_username"] = "committee"
        session.save()
        membership_request = MembershipRequest.objects.create(
            requested_organization=self.organization,
            membership_type=self.gold,
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=lambda username, **_kwargs: users.get(username)),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
            patch("core.sponsorship_invoices.render_invoice_pdf", return_value=b"%PDF-welcome"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("membership-request-approve", args=[membership_request.pk]),
                data={"custom_email": "1"},
            )

        self.assertEqual(response.status_code, 302)
        parts = urlsplit(response["Location"])
        self.assertEqual(parts.path, reverse("send-mail"))
        return parse_qs(parts.query)

    def test_custom_email_approval_opens_send_mail_with_the_welcome_invoice_attached(self) -> None:
        from core.mail_attachments import mail_attachments_from_tokens
        from core.sponsorship_invoices import invoice_number

        query = self._approve_with_custom_email()

        membership = Membership.objects.get(target_organization=self.organization, membership_type=self.gold)
        self.assertFalse(Email.objects.filter(template=self.welcome_template).exists())
        [attachment] = mail_attachments_from_tokens(query["attachment"])
        self.assertEqual(attachment.key, self._invoice_key(membership, InvoiceTerm.current))
        self.assertEqual(attachment.name, self._invoice_filename(membership, InvoiceTerm.current))
        self.assertEqual(query["cc"], ["billing@example.com"])
        self.assertEqual(query["invoice_number"], [invoice_number(membership, InvoiceTerm.current)])
        self.assertEqual(query["invoice_url"], [default_storage.url(attachment.key)])
        [log] = self._invoice_logs()
        self.assertEqual(log["actor_username"], "committee")

    def test_custom_email_renewal_approval_attaches_the_renewed_term_invoice(self) -> None:
        from core.mail_attachments import mail_attachments_from_tokens
        from core.sponsorship_invoices import invoice_number

        self._membership(expires_at=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30))

        query = self._approve_with_custom_email()

        membership = Membership.objects.get(target_organization=self.organization, membership_type=self.gold)
        [attachment] = mail_attachments_from_tokens(query["attachment"])
        self.assertEqual(attachment.key, self._invoice_key(membership, InvoiceTerm.current))
        self.assertEqual(query["invoice_number"], [invoice_number(membership, InvoiceTerm.current)])

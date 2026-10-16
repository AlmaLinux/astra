"""Sponsorship invoices: rendered once per billed term with WeasyPrint and cached in storage.

The layout lives in a repo template and the bank details in a root-owned file on the
server, so nobody can change where sponsors send money through Astra itself.
"""

import datetime
import hashlib
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import override
from urllib.parse import urlsplit
from urllib.request import url2pathname

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML
from weasyprint.urls import URLFetcher, URLFetcherResponse

from core.country_codes import country_name_from_code
from core.logging_extras import current_exception_log_fields
from core.mail_attachments import MailAttachment
from core.membership_dues import discount_percent, effective_dues_usd
from core.models import AuditLogEntry, Membership

logger = logging.getLogger(__name__)

# The invoice is a single page, so billing notes longer than this are cut off rather
# than pushing the rest of it onto a second page.
BILLING_NOTES_MAX_LINES = 8

INVOICE_ASSETS_DIR = Path(__file__).resolve().parent / "static" / "core" / "invoices"


class InvoiceTerm(StrEnum):
    """Which membership term an invoice bills."""

    # The term just granted: the welcome invoice for a newly approved sponsor.
    current = "current"
    # The term starting when the membership expires: sent with renewal reminders.
    renewal = "renewal"


def invoice_eligible(membership: Membership) -> bool:
    return (
        membership.target_organization_id is not None
        # In-kind sponsors whose dues are fully waived are never invoiced.
        and effective_dues_usd(membership) > 0
    )


def _term_start(membership: Membership, term: InvoiceTerm) -> datetime.date:
    renewal_start = membership.expires_at.astimezone(datetime.UTC).date()
    if term == InvoiceTerm.renewal:
        return renewal_start
    return renewal_start - datetime.timedelta(days=settings.MEMBERSHIP_VALIDITY_DAYS)


def _number_digits(membership_pk: int, term: InvoiceTerm) -> int:
    # Random-looking but derived from the row and invoice kind, so every reminder
    # arrives at the same number (and stored PDF) without storing anything.
    digest = hashlib.sha256(f"{membership_pk}:{term}".encode()).digest()
    return int.from_bytes(digest[:8]) % 1_000_000


def invoice_number(membership: Membership, term: InvoiceTerm) -> str:
    year = _term_start(membership, term).year
    digits = _number_digits(membership.pk, term)
    # A row's welcome and renewal invoices can share a year, so keep their digits apart.
    if term == InvoiceTerm.renewal and digits == _number_digits(membership.pk, InvoiceTerm.current):
        digits = (digits + 1) % 1_000_000
    return f"{membership.target_organization_id:04d}-{year}-{digits:06d}"


def invoice_filename(membership: Membership, term: InvoiceTerm) -> str:
    return f"AlmaLinux_OS_Foundation_Invoice_{invoice_number(membership, term)}.pdf"


def invoice_storage_key(membership: Membership, term: InvoiceTerm) -> str:
    # The number is derived from the membership pk, which changes whenever the committee
    # edits the membership (rows are recreated), so edited terms never reuse a stale PDF.
    return f"invoices/{membership.target_organization.invoice_token}/{invoice_filename(membership, term)}"


def read_payment_details() -> str:
    path = settings.SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE
    if not path:
        raise ImproperlyConfigured("SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE is not set.")
    try:
        details = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ImproperlyConfigured(f"Cannot read SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE ({path}): {exc}") from exc
    if not details:
        raise ImproperlyConfigured(f"SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE ({path}) is empty.")
    return details


# A payment-details file without these is almost certainly the wrong file or truncated.
PAYMENT_DETAILS_REQUIRED_PHRASES = ("Bank", "Account Number")


def payment_details_problems(details: str) -> list[str]:
    return [
        f'The file does not mention "{phrase}".'
        for phrase in PAYMENT_DETAILS_REQUIRED_PHRASES
        if phrase.casefold() not in details.casefold()
    ]


def render_invoice_html(membership: Membership, term: InvoiceTerm) -> str:
    organization = membership.target_organization
    region = " ".join(part for part in (organization.state, organization.postal_code) if part)
    address_lines = [
        line
        for line in (
            organization.street,
            ", ".join(part for part in (organization.city, region) if part),
            country_name_from_code(organization.country_code) if organization.country_code else "",
        )
        if line
    ]
    term_start = _term_start(membership, term)
    list_price = membership.membership_type.annual_dues_usd
    amount_due = effective_dues_usd(membership)
    discount = list_price - amount_due

    return render_to_string(
        "core/sponsorship_invoice.html",
        {
            "invoice_number": invoice_number(membership, term),
            # Reminders start months before renewal; dating the invoice on issue keeps it payable now.
            "invoice_date": timezone.now().astimezone(datetime.UTC).date(),
            "organization": organization,
            "address_lines": address_lines,
            "membership_type_name": membership.membership_type.name,
            "list_price_usd": f"${list_price:,}",
            "discount_usd": f"-${discount:,}" if discount else "",
            "discount_percent": discount_percent(list_price, amount_due),
            "amount_due_usd": f"${amount_due:,}",
            "term_start_date": term_start,
            "term_end_date": term_start + datetime.timedelta(days=settings.MEMBERSHIP_VALIDITY_DAYS),
            "payment_details": read_payment_details(),
            "billing_notes_max_lines": BILLING_NOTES_MAX_LINES,
        },
    )


class InvoiceAssetFetcher(URLFetcher):
    """Serve only the images bundled with the invoice template.

    Blocks network, storage and arbitrary local file access, so nothing that ends
    up in the rendered HTML can make the renderer fetch other resources.
    """

    @override
    def fetch(self, url: str, headers: dict[str, str] | None = None) -> URLFetcherResponse:
        parts = urlsplit(url)
        if parts.scheme != "file" or not Path(url2pathname(parts.path)).resolve().is_relative_to(INVOICE_ASSETS_DIR):
            raise ValueError(f"Invoices may only load bundled assets, refused: {url}")
        return super().fetch(url, headers)


def render_invoice_pdf(membership: Membership, term: InvoiceTerm) -> bytes:
    return HTML(
        string=render_invoice_html(membership, term),
        base_url=f"{INVOICE_ASSETS_DIR.as_uri()}/",
        url_fetcher=InvoiceAssetFetcher(),
    ).write_pdf()


def get_or_create_invoice_pdf(
    membership: Membership,
    term: InvoiceTerm,
    *,
    actor_username: str,
    regenerate: bool = False,
) -> bytes:
    """Return the stored invoice PDF, rendering and storing it first if needed.

    Regenerating overwrites the PDF in place: same number, same link. Every render is
    audited, since each one may bill different amounts or details under that number.
    """

    key = invoice_storage_key(membership, term)
    if not regenerate:
        try:
            with default_storage.open(key, "rb") as stored:
                return stored.read()
        except FileNotFoundError:
            pass

    pdf = render_invoice_pdf(membership, term)
    # Delete first: storages without overwrite would otherwise save under a
    # suffixed name, breaking the link already sent to the sponsor.
    default_storage.delete(key)
    default_storage.save(key, ContentFile(pdf))
    AuditLogEntry.objects.create(
        organization_id=membership.target_organization_id,
        event_type="sponsorship_invoice_generated",
        payload={
            "invoice_number": invoice_number(membership, term),
            "term": term,
            "membership_type": membership.membership_type_id,
            "amount_due_usd": effective_dues_usd(membership),
            "regenerated": regenerate,
            "actor_username": actor_username,
        },
        is_public=False,
    )
    return pdf


@dataclass(frozen=True)
class InvoiceEmailParts:
    cc: list[str] | None
    context: dict[str, str]
    # Both unset when the invoice could not be produced.
    pdf: bytes | None = None
    # Where the PDF is stored, for handing it to Send Mail as a pre-attached file.
    attachment: MailAttachment | None = None

    @property
    def attachments(self) -> dict[str, object] | None:
        """The invoice in post_office's attachments format, for queued emails."""
        if self.pdf is None or self.attachment is None:
            return None
        return {self.attachment.name: {"file": ContentFile(self.pdf), "mimetype": self.attachment.mimetype}}


def invoice_email_parts(
    membership: Membership,
    term: InvoiceTerm,
    *,
    recipient: str,
    cc: list[str] | None,
    actor_username: str,
) -> InvoiceEmailParts:
    """Attach the invoice to an outgoing email and copy the business contact, who usually pays.

    Invoice failures are logged and leave the email without an attachment: the
    email itself matters more than the invoice.
    """

    business_email = membership.target_organization.business_contact_email.strip()
    if business_email and business_email not in {recipient, *(cc or [])}:
        cc = [*(cc or []), business_email]

    try:
        pdf = get_or_create_invoice_pdf(membership, term, actor_username=actor_username)
    except Exception:
        logger.exception(
            "Failed to generate sponsorship invoice org_id=%s membership_id=%s term=%s",
            membership.target_organization_id,
            membership.pk,
            term,
            extra=current_exception_log_fields(),
        )
        return InvoiceEmailParts(cc=cc, context={})

    key = invoice_storage_key(membership, term)
    return InvoiceEmailParts(
        cc=cc,
        context={
            "invoice_url": default_storage.url(key),
            "invoice_number": invoice_number(membership, term),
        },
        pdf=pdf,
        attachment=MailAttachment(
            key=key,
            name=invoice_filename(membership, term),
            mimetype="application/pdf",
            size=len(pdf),
        ),
    )

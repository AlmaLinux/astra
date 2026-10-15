import hashlib
import logging
from typing import override

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand

from core.membership_notifications import admin_group_emails
from core.sponsorship_invoices import payment_details_problems, read_payment_details
from core.templated_email import queue_templated_email

logger = logging.getLogger(__name__)

# Kept with no expiry; if it is ever evicted, the next run just records a new baseline.
_DIGEST_CACHE_KEY = "sponsorship_invoice_payment_details:sha256"


class Command(BaseCommand):
    help = (
        "Alert the FreeIPA admins if the sponsorship invoice payment details file is unusable, "
        "doesn't look like bank details, or changed since the last run."
    )

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report problems without emailing or recording the file's hash.",
        )

    @override
    def handle(self, *args, **options) -> None:
        dry_run: bool = bool(options.get("dry_run"))

        try:
            details = read_payment_details()
        except ImproperlyConfigured as exc:
            problems = [str(exc)]
        else:
            problems = payment_details_problems(details)
            # Sponsors pay whatever account this file names, so any change is worth a human look.
            digest = hashlib.sha256(details.encode()).hexdigest()
            previous_digest = cache.get(_DIGEST_CACHE_KEY)
            if previous_digest is not None and previous_digest != digest:
                problems.append("The file has changed since the last check.")
            if not dry_run:
                cache.set(_DIGEST_CACHE_KEY, digest, timeout=None)

        if not problems:
            logger.info("sponsorship_invoice_payment_details_check: ok")
            return

        logger.warning("sponsorship_invoice_payment_details_check: problems=%s", problems)
        if dry_run:
            logger.info("[dry-run] Would alert the FreeIPA admins.")
            return

        recipients = admin_group_emails()
        if recipients:
            queue_templated_email(
                recipients=recipients,
                sender=settings.DEFAULT_FROM_EMAIL,
                template_name=settings.SPONSORSHIP_INVOICE_PAYMENT_DETAILS_ALERT_EMAIL_TEMPLATE_NAME,
                context={"path": settings.SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE, "problems": problems},
            )

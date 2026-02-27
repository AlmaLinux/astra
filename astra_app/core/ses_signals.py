import hashlib
import hmac
import logging
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.dispatch import receiver
from django_ses.signals import bounce_received, complaint_received

logger = logging.getLogger(__name__)


def _message_id_hash(mail_obj: dict[str, Any] | None) -> str | None:
    if not isinstance(mail_obj, dict):
        return None

    raw_message_id = str(mail_obj.get("messageId") or mail_obj.get("message_id") or "").strip()
    if not raw_message_id:
        return None

    return hmac.new(
        key=str(settings.SECRET_KEY).encode("utf-8"),
        msg=raw_message_id.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def _first_recipient_domain(
    recipients: Iterable[dict[str, Any]],
) -> str | None:
    domains: set[str] = set()
    for recipient in recipients:
        email = str(recipient.get("emailAddress") or "").strip()
        if "@" not in email:
            continue
        domain = email.rsplit("@", 1)[1].strip().lower()
        if domain:
            domains.add(domain)

    if not domains:
        return None
    return sorted(domains)[0]


def _log_ses_event(
    *,
    ses_event_type: str,
    mail_obj: dict[str, Any] | None,
    recipient_domain: str | None,
    event_source: str,
) -> None:
    log_payload: dict[str, str | bool] = {
        "event": "astra.email.ses.event_received",
        "component": "email",
        "outcome": "received",
        "ses_event_type": ses_event_type,
        "event_source": event_source,
    }

    message_id_hash = _message_id_hash(mail_obj)
    if message_id_hash:
        log_payload["ses_message_id_hash"] = message_id_hash
    else:
        log_payload["ses_message_id_present"] = False

    if recipient_domain:
        log_payload["recipient_domain"] = recipient_domain

    logger.warning(
        "SES event received ses_event_type=%s",
        ses_event_type,
        extra=log_payload,
    )


@receiver(bounce_received)
def handle_ses_bounce_received(
    sender: object,
    mail_obj: dict[str, Any] | None,
    bounce_obj: dict[str, Any] | None,
    raw_message: bytes,
    *args: object,
    **kwargs: object,
) -> None:
    bounced_recipients = (
        bounce_obj.get("bouncedRecipients", [])
        if isinstance(bounce_obj, dict)
        else []
    )
    recipient_domain = _first_recipient_domain(
        recipient
        for recipient in bounced_recipients
        if isinstance(recipient, dict)
    )
    _log_ses_event(
        ses_event_type="bounce",
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source="django_ses.bounce_received",
    )


@receiver(complaint_received)
def handle_ses_complaint_received(
    sender: object,
    mail_obj: dict[str, Any] | None,
    complaint_obj: dict[str, Any] | None,
    raw_message: bytes,
    *args: object,
    **kwargs: object,
) -> None:
    complained_recipients = (
        complaint_obj.get("complainedRecipients", [])
        if isinstance(complaint_obj, dict)
        else []
    )
    recipient_domain = _first_recipient_domain(
        recipient
        for recipient in complained_recipients
        if isinstance(recipient, dict)
    )
    _log_ses_event(
        ses_event_type="complaint",
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source="django_ses.complaint_received",
    )
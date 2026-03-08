import hashlib
import hmac
import logging
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.db import transaction
from django.dispatch import receiver
from django_ses.signals import bounce_received, complaint_received, delivery_received, send_received
from post_office.models import STATUS as POST_OFFICE_STATUS
from post_office.models import Email as PostOfficeEmail
from post_office.models import Log as PostOfficeLog
from post_office.models import RecipientDeliveryStatus

logger = logging.getLogger(__name__)

_RECIPIENT_STATUS_PRECEDENCE: dict[int, int] = {
    RecipientDeliveryStatus.ACCEPTED: 10,
    RecipientDeliveryStatus.DELIVERED: 20,
    RecipientDeliveryStatus.SOFT_BOUNCED: 30,
    RecipientDeliveryStatus.UNDETERMINED_BOUNCED: 40,
    RecipientDeliveryStatus.HARD_BOUNCED: 50,
    RecipientDeliveryStatus.SPAM_COMPLAINT: 60,
}


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
    recipients: Iterable[object],
) -> str | None:
    domains: set[str] = set()
    for recipient in recipients:
        if isinstance(recipient, dict):
            email = str(recipient.get("emailAddress") or recipient.get("email") or "").strip()
        else:
            email = str(recipient or "").strip()
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

    log_method = logger.warning if ses_event_type in {"bounce", "complaint"} else logger.info
    log_method(
        "SES event received ses_event_type=%s",
        ses_event_type,
        extra=log_payload,
    )


def _log_ses_event_outcome(
    *,
    ses_event_type: str,
    mail_obj: dict[str, Any] | None,
    recipient_domain: str | None,
    event_source: str,
    outcome: str,
    match_count: int | None = None,
    normalized_status: int | None = None,
) -> None:
    log_payload: dict[str, str | bool | int] = {
        "event": "astra.email.ses.event_processed",
        "component": "email",
        "outcome": outcome,
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
    if match_count is not None:
        log_payload["match_count"] = match_count
    if normalized_status is not None:
        log_payload["normalized_status"] = RecipientDeliveryStatus(normalized_status).name.lower()

    logger.info(
        "SES event processed ses_event_type=%s outcome=%s",
        ses_event_type,
        outcome,
        extra=log_payload,
    )


def _smtp_message_id(mail_obj: dict[str, Any] | None) -> str | None:
    if not isinstance(mail_obj, dict):
        return None

    common_headers = mail_obj.get("commonHeaders")
    if not isinstance(common_headers, dict):
        return None

    smtp_message_id = str(common_headers.get("messageId") or "").strip()
    return smtp_message_id or None


def _recipient_status_precedence(status: int | None) -> int:
    if status is None:
        return 0
    return _RECIPIENT_STATUS_PRECEDENCE.get(status, 0)


def _bounce_delivery_status(bounce_obj: dict[str, Any] | None) -> int:
    if not isinstance(bounce_obj, dict):
        return RecipientDeliveryStatus.UNDETERMINED_BOUNCED

    bounce_type = str(bounce_obj.get("bounceType") or "").strip().lower()
    if bounce_type == "transient":
        return RecipientDeliveryStatus.SOFT_BOUNCED
    if bounce_type == "permanent":
        return RecipientDeliveryStatus.HARD_BOUNCED
    return RecipientDeliveryStatus.UNDETERMINED_BOUNCED


def _matched_post_office_email(
    *,
    ses_event_type: str,
    mail_obj: dict[str, Any] | None,
    recipient_domain: str | None,
    event_source: str,
) -> PostOfficeEmail | None:
    smtp_message_id = _smtp_message_id(mail_obj)
    if smtp_message_id is None:
        _log_ses_event_outcome(
            ses_event_type=ses_event_type,
            mail_obj=mail_obj,
            recipient_domain=recipient_domain,
            event_source=event_source,
            outcome="missing_message_id",
        )
        return None

    matched_emails = list(PostOfficeEmail.objects.filter(message_id=smtp_message_id).order_by("pk")[:2])
    if not matched_emails:
        _log_ses_event_outcome(
            ses_event_type=ses_event_type,
            mail_obj=mail_obj,
            recipient_domain=recipient_domain,
            event_source=event_source,
            outcome="missing_match",
        )
        return None

    if len(matched_emails) > 1:
        _log_ses_event_outcome(
            ses_event_type=ses_event_type,
            mail_obj=mail_obj,
            recipient_domain=recipient_domain,
            event_source=event_source,
            outcome="ambiguous_match",
            match_count=len(matched_emails),
        )
        return None

    return matched_emails[0]


def _record_post_office_milestone(
    *,
    ses_event_type: str,
    mail_obj: dict[str, Any] | None,
    recipient_domain: str | None,
    event_source: str,
    recipient_delivery_status: int,
    exception_type: str,
    message: str,
    mark_unsent_as_failed: bool,
) -> None:
    matched_post_office_email = _matched_post_office_email(
        ses_event_type=ses_event_type,
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source=event_source,
    )
    if matched_post_office_email is None:
        return
    with transaction.atomic():
        post_office_email = PostOfficeEmail.objects.select_for_update().get(pk=matched_post_office_email.pk)

        current_status = post_office_email.recipient_delivery_status
        if _recipient_status_precedence(recipient_delivery_status) <= _recipient_status_precedence(
            current_status
        ):
            _log_ses_event_outcome(
                ses_event_type=ses_event_type,
                mail_obj=mail_obj,
                recipient_domain=recipient_domain,
                event_source=event_source,
                outcome="stale_or_duplicate",
                normalized_status=recipient_delivery_status,
            )
            return

        update_fields = ["recipient_delivery_status"]
        post_office_email.recipient_delivery_status = recipient_delivery_status
        if mark_unsent_as_failed and post_office_email.status in (
            POST_OFFICE_STATUS.queued,
            POST_OFFICE_STATUS.requeued,
        ):
            post_office_email.status = POST_OFFICE_STATUS.failed
            update_fields.append("status")

        post_office_email.save(update_fields=update_fields)
        PostOfficeLog.objects.create(
            email=post_office_email,
            status=recipient_delivery_status,
            exception_type=exception_type,
            message=message,
        )

    _log_ses_event_outcome(
        ses_event_type=ses_event_type,
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source=event_source,
        outcome="recorded",
        normalized_status=recipient_delivery_status,
    )


def _handle_ses_post_office_event(
    *,
    ses_event_type: str,
    mail_obj: dict[str, Any] | None,
    recipient_domain: str | None,
    event_source: str,
    recipient_delivery_status: int,
    exception_type: str,
    message: str,
    mark_unsent_as_failed: bool = False,
) -> None:
    try:
        _record_post_office_milestone(
            ses_event_type=ses_event_type,
            mail_obj=mail_obj,
            recipient_domain=recipient_domain,
            event_source=event_source,
            recipient_delivery_status=recipient_delivery_status,
            exception_type=exception_type,
            message=message,
            mark_unsent_as_failed=mark_unsent_as_failed,
        )
    except Exception:
        logger.exception("ses_signals: failed to record %s in post_office log", ses_event_type)


@receiver(send_received)
def handle_ses_send_received(
    sender: object,
    mail_obj: dict[str, Any] | None,
    send_obj: dict[str, Any] | None,
    raw_message: bytes,
    *args: object,
    **kwargs: object,
) -> None:
    recipient_domain = _first_recipient_domain(
        (send_obj or {}).get("destination", []) if isinstance(send_obj, dict) else []
    )
    _log_ses_event(
        ses_event_type="send",
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source="django_ses.send_received",
    )
    _handle_ses_post_office_event(
        ses_event_type="send",
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source="django_ses.send_received",
        recipient_delivery_status=RecipientDeliveryStatus.ACCEPTED,
        exception_type="SESSend",
        message="SES accepted by provider",
    )


@receiver(delivery_received)
def handle_ses_delivery_received(
    sender: object,
    mail_obj: dict[str, Any] | None,
    delivery_obj: dict[str, Any] | None,
    raw_message: bytes,
    *args: object,
    **kwargs: object,
) -> None:
    recipient_domain = _first_recipient_domain(
        (delivery_obj or {}).get("recipients", []) if isinstance(delivery_obj, dict) else []
    )
    _log_ses_event(
        ses_event_type="delivery",
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source="django_ses.delivery_received",
    )
    _handle_ses_post_office_event(
        ses_event_type="delivery",
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source="django_ses.delivery_received",
        recipient_delivery_status=RecipientDeliveryStatus.DELIVERED,
        exception_type="SESDelivery",
        message="SES delivered to destination mail server",
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
    recipient_domain = _first_recipient_domain(bounced_recipients)
    _log_ses_event(
        ses_event_type="bounce",
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source="django_ses.bounce_received",
    )
    bounce_type = str(bounce_obj.get("bounceType") or "").strip() if isinstance(bounce_obj, dict) else ""
    bounced_addrs = ", ".join(
        str(recipient.get("emailAddress") or "")
        for recipient in bounced_recipients
        if isinstance(recipient, dict) and recipient.get("emailAddress")
    )
    message = f"SES bounce ({bounce_type or 'unknown'})"
    if bounced_addrs:
        message = f"{message}: {bounced_addrs}"

    _handle_ses_post_office_event(
        ses_event_type="bounce",
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source="django_ses.bounce_received",
        recipient_delivery_status=_bounce_delivery_status(bounce_obj),
        exception_type="SESBounce",
        message=message,
        mark_unsent_as_failed=True,
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
    recipient_domain = _first_recipient_domain(complained_recipients)
    _log_ses_event(
        ses_event_type="complaint",
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source="django_ses.complaint_received",
    )

    _handle_ses_post_office_event(
        ses_event_type="complaint",
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source="django_ses.complaint_received",
        recipient_delivery_status=RecipientDeliveryStatus.SPAM_COMPLAINT,
        exception_type="SESComplaint",
        message="SES spam complaint received",
    )
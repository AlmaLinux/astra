import hashlib
import hmac
import logging
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage as DjangoEmailMessage
from django.db import IntegrityError, transaction
from django.dispatch import receiver
from django_ses.signals import bounce_received, complaint_received, delivery_received, message_sent, send_received
from post_office.models import STATUS as POST_OFFICE_STATUS
from post_office.models import Email as PostOfficeEmail
from post_office.models import Log as PostOfficeLog
from post_office.models import RecipientDeliveryStatus

from core.logging_extras import current_exception_log_fields
from core.models import SESEmailCorrelationAttempt

logger = logging.getLogger(__name__)

_RECIPIENT_STATUS_PRECEDENCE: dict[int, int] = {
    RecipientDeliveryStatus.ACCEPTED: 10,
    RecipientDeliveryStatus.DELIVERED: 20,
    RecipientDeliveryStatus.SOFT_BOUNCED: 30,
    RecipientDeliveryStatus.UNDETERMINED_BOUNCED: 40,
    RecipientDeliveryStatus.HARD_BOUNCED: 50,
    RecipientDeliveryStatus.SPAM_COMPLAINT: 60,
}


def _message_id_hash(raw_message_id: str | None) -> str | None:
    raw_message_id = str(raw_message_id or "").strip()
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


def _provider_message_id(mail_obj: dict[str, Any] | None) -> str | None:
    if not isinstance(mail_obj, dict):
        return None

    provider_message_id = str(mail_obj.get("messageId") or mail_obj.get("message_id") or "").strip()
    return provider_message_id or None


def _log_identifier_hashes(
    log_payload: dict[str, str | bool | int],
    *,
    provider_message_id: str | None,
    smtp_message_id: str | None,
    include_generic: bool,
) -> None:
    provider_message_id_hash = _message_id_hash(provider_message_id)
    smtp_message_id_hash = _message_id_hash(smtp_message_id)

    if provider_message_id_hash:
        log_payload["provider_message_id_hash"] = provider_message_id_hash
        if include_generic:
            log_payload["ses_message_id_hash"] = provider_message_id_hash
    else:
        log_payload["provider_message_id_present"] = False
        if include_generic:
            log_payload["ses_message_id_present"] = False

    if smtp_message_id_hash:
        log_payload["smtp_message_id_hash"] = smtp_message_id_hash
    else:
        log_payload["smtp_message_id_present"] = False


def _log_ses_event(
    *,
    ses_event_type: str,
    mail_obj: dict[str, Any] | None,
    recipient_domain: str | None,
    event_source: str,
) -> None:
    provider_message_id = _provider_message_id(mail_obj)
    smtp_message_id = _smtp_message_id(mail_obj)
    log_payload: dict[str, str | bool] = {
        "event": "astra.email.ses.event_received",
        "component": "email",
        "outcome": "received",
        "ses_event_type": ses_event_type,
        "event_source": event_source,
    }

    _log_identifier_hashes(
        log_payload,
        provider_message_id=provider_message_id,
        smtp_message_id=smtp_message_id,
        include_generic=True,
    )

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
    correlation_source: str,
    match_count: int | None = None,
    normalized_status: int | None = None,
    post_office_email_id: int | None = None,
    provider_post_office_email_id: int | None = None,
    smtp_post_office_email_id: int | None = None,
) -> None:
    provider_message_id = _provider_message_id(mail_obj)
    smtp_message_id = _smtp_message_id(mail_obj)
    log_payload: dict[str, str | bool | int] = {
        "event": "astra.email.ses.event_processed",
        "component": "email",
        "outcome": outcome,
        "correlation_source": correlation_source,
        "ses_event_type": ses_event_type,
        "event_source": event_source,
    }

    _log_identifier_hashes(
        log_payload,
        provider_message_id=provider_message_id,
        smtp_message_id=smtp_message_id,
        include_generic=False,
    )

    if recipient_domain:
        log_payload["recipient_domain"] = recipient_domain
    if match_count is not None:
        log_payload["match_count"] = match_count
    if normalized_status is not None:
        log_payload["normalized_status"] = RecipientDeliveryStatus(normalized_status).name.lower()
    if post_office_email_id is not None:
        log_payload["post_office_email_id"] = post_office_email_id
    if provider_post_office_email_id is not None:
        log_payload["provider_post_office_email_id"] = provider_post_office_email_id
    if smtp_post_office_email_id is not None:
        log_payload["smtp_post_office_email_id"] = smtp_post_office_email_id

    logger.info(
        "SES event processed ses_event_type=%s outcome=%s",
        ses_event_type,
        outcome,
        extra=log_payload,
    )


def _log_ses_attempt_persistence(
    *,
    smtp_message_id: str | None,
    provider_message_id: str | None,
    outcome: str,
    persistence_action: str | None = None,
    post_office_email_id: int | None = None,
    ses_email_correlation_attempt_id: int | None = None,
    match_count: int | None = None,
    existing_post_office_email_id: int | None = None,
) -> None:
    log_payload: dict[str, str | bool | int] = {
        "event": "astra.email.ses.attempt_persisted",
        "component": "email",
        "outcome": outcome,
    }
    _log_identifier_hashes(
        log_payload,
        provider_message_id=provider_message_id,
        smtp_message_id=smtp_message_id,
        include_generic=False,
    )
    if persistence_action is not None:
        log_payload["persistence_action"] = persistence_action
    if post_office_email_id is not None:
        log_payload["post_office_email_id"] = post_office_email_id
    if ses_email_correlation_attempt_id is not None:
        log_payload["ses_email_correlation_attempt_id"] = ses_email_correlation_attempt_id
    if match_count is not None:
        log_payload["match_count"] = match_count
    if existing_post_office_email_id is not None:
        log_payload["existing_post_office_email_id"] = existing_post_office_email_id

    logger.info(
        "SES attempt persistence outcome=%s",
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


def _smtp_message_id_candidates(smtp_message_id: str | None) -> tuple[str, ...]:
    raw_smtp_message_id = str(smtp_message_id or "").strip()
    if not raw_smtp_message_id:
        return ()

    normalized_smtp_message_id = raw_smtp_message_id.removeprefix("<").removesuffix(">")
    candidate_values: list[str] = []
    for candidate in (
        raw_smtp_message_id,
        normalized_smtp_message_id,
        f"<{normalized_smtp_message_id}>",
    ):
        if candidate and candidate not in candidate_values:
            candidate_values.append(candidate)
    return tuple(candidate_values)


def _smtp_matched_post_office_emails(smtp_message_id: str | None) -> list[PostOfficeEmail]:
    candidate_values = _smtp_message_id_candidates(smtp_message_id)
    if not candidate_values:
        return []

    return list(PostOfficeEmail.objects.filter(message_id__in=candidate_values).order_by("pk")[:2])


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


def _resolve_post_office_email_match(
    *,
    mail_obj: dict[str, Any] | None,
) -> dict[str, object]:
    provider_message_id = _provider_message_id(mail_obj)
    smtp_message_id = _smtp_message_id(mail_obj)
    matched_smtp_emails = _smtp_matched_post_office_emails(smtp_message_id) if smtp_message_id else []
    provider_attempt = None
    if provider_message_id is not None:
        provider_attempt = (
            SESEmailCorrelationAttempt.objects.select_related("post_office_email")
            .filter(ses_provider_message_id=provider_message_id)
            .first()
        )

    if provider_attempt is not None:
        provider_email = provider_attempt.post_office_email
        if len(matched_smtp_emails) == 1 and matched_smtp_emails[0].pk != provider_email.pk:
            return {
                "outcome": "provider_id_conflict",
                "correlation_source": "provider_id_conflict",
                "provider_post_office_email_id": provider_email.pk,
                "smtp_post_office_email_id": matched_smtp_emails[0].pk,
            }

        return {
            "email": provider_email,
            "post_office_email_id": provider_email.pk,
            "correlation_source": "provider_message_id",
        }

    if provider_message_id is None and smtp_message_id is None:
        return {
            "outcome": "missing_message_id",
            "correlation_source": "missing_message_id",
        }

    if not matched_smtp_emails:
        return {
            "outcome": "missing_match",
            "correlation_source": "missing_match",
        }

    if len(matched_smtp_emails) > 1:
        return {
            "outcome": "ambiguous_match",
            "correlation_source": "ambiguous_match",
            "match_count": len(matched_smtp_emails),
        }

    smtp_email = matched_smtp_emails[0]
    if provider_message_id is None:
        return {
            "email": smtp_email,
            "post_office_email_id": smtp_email.pk,
            "correlation_source": "smtp_message_id_fallback_no_provider_id",
        }

    if smtp_email.ses_correlation_attempts.exists():
        return {
            "outcome": "provider_id_conflict",
            "correlation_source": "provider_id_conflict",
            "smtp_post_office_email_id": smtp_email.pk,
        }

    return {
        "email": smtp_email,
        "post_office_email_id": smtp_email.pk,
        "correlation_source": "smtp_message_id_fallback_no_provider_metadata",
    }


def _matched_post_office_email(
    *,
    ses_event_type: str,
    mail_obj: dict[str, Any] | None,
    recipient_domain: str | None,
    event_source: str,
    resolution: dict[str, object] | None = None,
) -> PostOfficeEmail | None:
    match_resolution = _resolve_post_office_email_match(mail_obj=mail_obj)
    if resolution is not None:
        resolution.update(match_resolution)

    matched_post_office_email = match_resolution.get("email")
    if isinstance(matched_post_office_email, PostOfficeEmail):
        return matched_post_office_email
    return None


def _smtp_message_id_from_sent_message(message: DjangoEmailMessage) -> str | None:
    smtp_message_id = str(message.extra_headers.get("Message-ID") or "").strip()
    return smtp_message_id or None


def _provider_message_id_from_sent_message(message: DjangoEmailMessage) -> str | None:
    provider_message_id = str(message.extra_headers.get("message_id") or "").strip()
    return provider_message_id or None


def _request_id_from_sent_message(message: DjangoEmailMessage) -> str | None:
    request_id = str(message.extra_headers.get("request_id") or "").strip()
    return request_id or None


def _persist_ses_correlation_attempt(message: DjangoEmailMessage) -> None:
    smtp_message_id = _smtp_message_id_from_sent_message(message)
    provider_message_id = _provider_message_id_from_sent_message(message)
    request_id = _request_id_from_sent_message(message)

    if provider_message_id is None:
        _log_ses_attempt_persistence(
            smtp_message_id=smtp_message_id,
            provider_message_id=None,
            outcome="missing_provider_message_id",
        )
        return

    if smtp_message_id is None:
        _log_ses_attempt_persistence(
            smtp_message_id=None,
            provider_message_id=provider_message_id,
            outcome="missing_message_id",
        )
        return

    matched_post_office_emails = list(PostOfficeEmail.objects.filter(message_id=smtp_message_id).order_by("pk")[:2])
    if not matched_post_office_emails:
        _log_ses_attempt_persistence(
            smtp_message_id=smtp_message_id,
            provider_message_id=provider_message_id,
            outcome="missing_match",
        )
        return

    if len(matched_post_office_emails) > 1:
        _log_ses_attempt_persistence(
            smtp_message_id=smtp_message_id,
            provider_message_id=provider_message_id,
            outcome="ambiguous_match",
            match_count=len(matched_post_office_emails),
        )
        return

    post_office_email = matched_post_office_emails[0]
    try:
        ses_attempt, created = SESEmailCorrelationAttempt.objects.get_or_create(
            ses_provider_message_id=provider_message_id,
            defaults={
                "post_office_email": post_office_email,
                "ses_request_id": request_id,
            },
        )
    except IntegrityError:
        ses_attempt = SESEmailCorrelationAttempt.objects.get(ses_provider_message_id=provider_message_id)
        created = False

    if ses_attempt.post_office_email_id != post_office_email.pk:
        _log_ses_attempt_persistence(
            smtp_message_id=smtp_message_id,
            provider_message_id=provider_message_id,
            outcome="provider_id_conflict",
            post_office_email_id=post_office_email.pk,
            existing_post_office_email_id=ses_attempt.post_office_email_id,
            ses_email_correlation_attempt_id=ses_attempt.pk,
        )
        return

    existing_request_id = str(ses_attempt.ses_request_id or "").strip() or None
    if not created:
        if existing_request_id is None and request_id is not None:
            ses_attempt.ses_request_id = request_id
            ses_attempt.save(update_fields=["ses_request_id", "updated_at"])
        elif existing_request_id is not None and request_id is not None and existing_request_id != request_id:
            # Preserve the first durable request identifier for this provider acceptance attempt.
            _log_ses_attempt_persistence(
                smtp_message_id=smtp_message_id,
                provider_message_id=provider_message_id,
                outcome="request_id_conflict",
                post_office_email_id=post_office_email.pk,
                ses_email_correlation_attempt_id=ses_attempt.pk,
            )
            return

    _log_ses_attempt_persistence(
        smtp_message_id=smtp_message_id,
        provider_message_id=provider_message_id,
        outcome="persisted",
        persistence_action="created" if created else "reused",
        post_office_email_id=post_office_email.pk,
        ses_email_correlation_attempt_id=ses_attempt.pk,
    )


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
    match_resolution: dict[str, object] = {}
    matched_post_office_email = _matched_post_office_email(
        ses_event_type=ses_event_type,
        mail_obj=mail_obj,
        recipient_domain=recipient_domain,
        event_source=event_source,
        resolution=match_resolution,
    )
    if matched_post_office_email is None:
        _log_ses_event_outcome(
            ses_event_type=ses_event_type,
            mail_obj=mail_obj,
            recipient_domain=recipient_domain,
            event_source=event_source,
            outcome=str(match_resolution.get("outcome") or "missing_match"),
            correlation_source=str(match_resolution.get("correlation_source") or "missing_match"),
            match_count=match_resolution.get("match_count") if isinstance(match_resolution.get("match_count"), int) else None,
            provider_post_office_email_id=(
                match_resolution.get("provider_post_office_email_id")
                if isinstance(match_resolution.get("provider_post_office_email_id"), int)
                else None
            ),
            smtp_post_office_email_id=(
                match_resolution.get("smtp_post_office_email_id")
                if isinstance(match_resolution.get("smtp_post_office_email_id"), int)
                else None
            ),
        )
        return

    matched_post_office_email_id = matched_post_office_email.pk
    correlation_source = str(match_resolution.get("correlation_source") or "provider_message_id")
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
                correlation_source=correlation_source,
                normalized_status=recipient_delivery_status,
                post_office_email_id=matched_post_office_email_id,
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
        correlation_source=correlation_source,
        normalized_status=recipient_delivery_status,
        post_office_email_id=matched_post_office_email_id,
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
        logger.exception(
            "ses_signals: failed to record %s in post_office log",
            ses_event_type,
            extra=current_exception_log_fields(),
        )


@receiver(message_sent)
def handle_ses_message_sent(
    sender: object,
    message: DjangoEmailMessage,
    *args: object,
    **kwargs: object,
) -> None:
    try:
        _persist_ses_correlation_attempt(message)
    except Exception:
        logger.exception(
            "ses_signals: failed to persist SES correlation attempt",
            extra=current_exception_log_fields(),
        )


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
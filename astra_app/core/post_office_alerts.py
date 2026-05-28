import logging
import re
from collections.abc import Iterable
from email.utils import getaddresses

from django.db.models.signals import post_save
from django.dispatch import receiver
from post_office.models import STATUS as POST_OFFICE_STATUS
from post_office.models import Email as PostOfficeEmail
from post_office.models import Log as PostOfficeLog

logger = logging.getLogger(__name__)

_SES_DAILY_QUOTA_MESSAGE = "daily message quota exceeded"
_SES_SEND_OPERATION_MARKERS = (
    "when calling the sendrawemail operation",
    "when calling the sendemail operation",
)
_SES_ERROR_CODE_PATTERN = re.compile(r"\((?P<code>[A-Za-z0-9_]+)\)")
_SES_SEND_FAILURE_EVENT = "astra.email.ses.send_failed"


def _candidate_text(log_entry: PostOfficeLog) -> str:
    return f"{log_entry.exception_type} {log_entry.message}".casefold()


def _is_ses_daily_quota_exceeded(log_entry: PostOfficeLog) -> bool:
    candidate_text = _candidate_text(log_entry)
    return _SES_DAILY_QUOTA_MESSAGE in candidate_text


def _is_ses_send_failure(log_entry: PostOfficeLog) -> bool:
    candidate_text = _candidate_text(log_entry)
    return any(marker in candidate_text for marker in _SES_SEND_OPERATION_MARKERS)


def _extract_ses_error_code(message: str) -> str | None:
    match = _SES_ERROR_CODE_PATTERN.search(message)
    if match is None:
        return None
    return match.group("code")


def _recipient_addresses_from_value(value: str | list[str]) -> list[str]:
    joined_value = ",".join(value) if isinstance(value, list) else value
    addresses = getaddresses([joined_value])
    normalized_addresses = {
        email_address.strip().casefold()
        for _, email_address in addresses
        if "@" in email_address and email_address.strip()
    }
    return sorted(normalized_addresses)


def _recipient_addresses(email: PostOfficeEmail) -> list[str]:
    addresses: set[str] = set()
    recipient_values: Iterable[str | list[str]] = (email.to, email.cc, email.bcc)
    for value in recipient_values:
        addresses.update(_recipient_addresses_from_value(value))
    return sorted(addresses)


def _ses_failure_kind(log_entry: PostOfficeLog) -> str:
    if _is_ses_daily_quota_exceeded(log_entry):
        return "daily_quota_exceeded"
    return "other"


def emit_immediate_ses_send_failure_alert(log_entry: PostOfficeLog) -> bool:
    if log_entry.status != POST_OFFICE_STATUS.failed or not _is_ses_send_failure(log_entry):
        return False

    email = log_entry.email
    logger.warning(
        _SES_SEND_FAILURE_EVENT,
        extra={
            "event": _SES_SEND_FAILURE_EVENT,
            "component": "email",
            "provider": "aws_ses",
            "outcome": "failed",
            # These failures happen during the send attempt, before webhook-driven
            # SES lifecycle events can exist for the message.
            "failure_stage": "send_attempt",
            "is_post_acceptance_event": False,
            "ses_failure_kind": _ses_failure_kind(log_entry),
            "ses_error_code": _extract_ses_error_code(log_entry.message),
            "exception_type": log_entry.exception_type,
            "post_office_email_id": email.pk,
            "post_office_log_id": log_entry.pk,
            "post_office_status": email.status,
            "retry_count": email.number_of_retries or 0,
            "backend_alias": email.backend_alias or "default",
            "recipient_addresses": _recipient_addresses(email),
        },
    )
    return True


def emit_immediate_ses_send_failure_alerts(log_entries: Iterable[PostOfficeLog]) -> int:
    return sum(1 for log_entry in log_entries if emit_immediate_ses_send_failure_alert(log_entry))


@receiver(post_save, sender=PostOfficeLog)
def alert_on_immediate_ses_send_failure(
    sender: type[PostOfficeLog],
    instance: PostOfficeLog,
    created: bool,
    **_: object,
) -> None:
    if not created or instance.status != POST_OFFICE_STATUS.failed:
        return

    emit_immediate_ses_send_failure_alert(instance)
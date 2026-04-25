from django.conf import settings
from django.core.management import call_command

from core.cache_tools import clear_default_cache, inspect_default_cache
from core.membership_request_repairs import (
    MembershipRequestRepairResult,
    reset_rejected_membership_request_to_pending,
)
from core.models import MembershipRequest
from core.templated_email import queue_composed_email


def get_membership_request(request_id: int) -> MembershipRequest:
    return MembershipRequest.objects.get(pk=request_id)


def preview_reset_rejected_request(request_id: int) -> MembershipRequestRepairResult:
    membership_request = get_membership_request(request_id)
    return reset_rejected_membership_request_to_pending(
        membership_request=membership_request,
        actor_username="",
        note_content="",
        apply_changes=False,
    )


def apply_reset_rejected_request(
    request_id: int,
    *,
    actor_username: str,
    note_content: str,
) -> MembershipRequestRepairResult:
    membership_request = get_membership_request(request_id)
    return reset_rejected_membership_request_to_pending(
        membership_request=membership_request,
        actor_username=actor_username,
        note_content=note_content,
        apply_changes=True,
    )


def inspect_cache(*, prefix: str | None = None, key: str | None = None, max_chars: int = 4000) -> dict[str, object]:
    return inspect_default_cache(prefix=prefix, key=key, max_chars=max_chars)


def clear_cache() -> dict[str, object]:
    return clear_default_cache()


def run_send_queued_mail() -> dict[str, object]:
    call_command("send_queued_mail")
    return {"ran": True}


def send_test_email(
    recipient_email: str,
    *,
    subject: str = "Astra test email",
    content: str = "Astra maintenance shell test email.",
    text_content: str | None = None,
    html_content: str = "",
    deliver_queued: bool = False,
) -> dict[str, object]:
    recipient = str(recipient_email).strip()
    text_body = content if text_content is None else text_content
    queued_email = queue_composed_email(
        recipients=[recipient],
        sender=settings.DEFAULT_FROM_EMAIL,
        subject_source=subject,
        text_source=text_body,
        html_source=html_content,
        context={"email": recipient, "recipient_email": recipient},
        template_name="maintenance-shell-test-email",
    )
    if deliver_queued:
        call_command("send_queued_mail")
    return {
        "email_id": queued_email.id,
        "recipient_email": recipient,
        "delivered": deliver_queued,
    }


def build_maintenance_shell_namespace() -> dict[str, object]:
    return {
        "MembershipRequest": MembershipRequest,
        "get_membership_request": get_membership_request,
        "preview_reset_rejected_request": preview_reset_rejected_request,
        "apply_reset_rejected_request": apply_reset_rejected_request,
        "inspect_cache": inspect_cache,
        "clear_cache": clear_cache,
        "send_test_email": send_test_email,
        "run_send_queued_mail": run_send_queued_mail,
        "reset_rejected_membership_request_to_pending": reset_rejected_membership_request_to_pending,
    }


def build_maintenance_shell_banner() -> str:
    lines = [
        "Astra maintenance_shell",
        "Use the guided command first: python manage.py membership_request_repair --help",
        "Available helpers:",
        "- get_membership_request(request_id)",
        "- preview_reset_rejected_request(request_id)",
        "- apply_reset_rejected_request(request_id, actor_username='alex', note_content='reason')",
        "- inspect_cache(prefix='freeipa_', key=None)",
        "- clear_cache()",
        "- send_test_email('user@example.com', subject='Astra test email', content='Astra maintenance shell test email.', deliver_queued=False)",
        "- run_send_queued_mail()",
    ]
    return "\n".join(lines)
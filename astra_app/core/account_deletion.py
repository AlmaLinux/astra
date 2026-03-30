import logging

from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone
from python_freeipa import exceptions

from core.freeipa.user import FreeIPAUser
from core.membership_request_workflow import ignore_open_membership_requests_for_target
from core.models import AccountDeletionRequest, Election, Organization, VotingCredential
from core.signals import CANONICAL_SIGNALS

logger = logging.getLogger(__name__)

ACCOUNT_DELETION_STATUS_EVENT_KEYS: dict[str, str] = {
    AccountDeletionRequest.Status.pending_privilege_check: "account_deletion_pending_privilege_check",
    AccountDeletionRequest.Status.approved: "account_deletion_approved",
    AccountDeletionRequest.Status.rejected: "account_deletion_rejected",
    AccountDeletionRequest.Status.cancelled: "account_deletion_cancelled",
    AccountDeletionRequest.Status.completed: "account_deletion_completed",
}


def get_account_deletion_blockers(username: str) -> tuple[list[str], list[str]]:
    normalized_username = str(username or "").strip()
    blocker_codes: list[str] = []
    warnings: list[str] = []

    if Organization.objects.filter(representative=normalized_username).exists():
        blocker_codes.append("organization_representative")
        warnings.append("Manual review required because you are an organization representative.")

    if VotingCredential.objects.filter(
        freeipa_username=normalized_username,
        election__status=Election.Status.open,
    ).exists():
        blocker_codes.append("open_election")
        warnings.append("Manual review required because you have credentials for an open election.")

    return blocker_codes, warnings


def invalidate_sessions_for_freeipa_username(username: str) -> int:
    normalized_username = str(username or "").strip().casefold()
    if not normalized_username:
        return 0

    invalidated = 0
    for session in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        try:
            decoded_session = session.get_decoded()
        except Exception:
            logger.warning(
                "Failed to decode session during account deletion invalidation session_key=%s",
                session.session_key,
                exc_info=True,
            )
            continue

        session_username = str(decoded_session.get("_freeipa_username") or "").strip().casefold()
        if session_username != normalized_username:
            continue

        session.delete()
        invalidated += 1

    return invalidated


def execute_account_deletion_request(
    account_deletion_request: AccountDeletionRequest,
    *,
    actor_username: str | None = None,
) -> int:
    username = str(account_deletion_request.username or "").strip()
    blocker_codes, warnings = get_account_deletion_blockers(username)
    if blocker_codes:
        raise RuntimeError(" ".join(warnings) or f"Manual review required: {', '.join(blocker_codes)}")

    # Drop live Django sessions before removing the FreeIPA identity so a later
    # directory error cannot leave a deleted account with an active session.
    invalidated_sessions = invalidate_sessions_for_freeipa_username(username)

    try:
        FreeIPAUser(username, {"uid": [username]}).delete()
    except exceptions.NotFound:
        logger.info(
            "Account deletion target already absent username=%s request_id=%s",
            username,
            account_deletion_request.pk,
        )

    ignore_open_membership_requests_for_target(
        username=username,
        actor_username=actor_username,
    )

    return invalidated_sessions


def schedule_account_deletion_signal(
    *,
    event_key: str,
    account_deletion_request_id: int,
    actor: str,
) -> None:
    normalized_event_key = str(event_key or "").strip()
    normalized_actor = str(actor or "").strip().lower()
    if not normalized_event_key:
        raise ValueError("event_key is required")

    signal = CANONICAL_SIGNALS.get(normalized_event_key)
    if signal is None:
        raise ValueError(f"Unknown account deletion event key: {normalized_event_key}")

    def _send_signal() -> None:
        account_deletion_request = AccountDeletionRequest.objects.filter(pk=account_deletion_request_id).first()
        if account_deletion_request is None:
            return
        signal.send(
            sender=AccountDeletionRequest,
            account_deletion_request=account_deletion_request,
            actor=normalized_actor,
        )

    transaction.on_commit(_send_signal)
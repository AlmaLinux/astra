from datetime import datetime

from django.core import signing
from django.db import transaction

from core import signals as astra_signals
from core.models import AccountInvitation
from core.tokens import (
    _read_signed_token_unbounded_legacy,
    read_account_invitation_token_unbounded,
)


def load_account_invitation_from_token(invitation_token: str) -> AccountInvitation | None:
    normalized_token = str(invitation_token or "").strip()
    if not normalized_token:
        return None

    try:
        payload = read_account_invitation_token_unbounded(normalized_token)
    except signing.BadSignature:
        try:
            payload = _read_signed_token_unbounded_legacy(normalized_token)
        except signing.BadSignature:
            return None

        if not isinstance(payload, dict):
            return None

    if not isinstance(payload, dict):
        return None

    invitation_id_value = payload.get("invitation_id")
    try:
        invitation_id = int(invitation_id_value)
    except (TypeError, ValueError):
        return None

    return AccountInvitation.objects.filter(
        pk=invitation_id,
        invitation_token=normalized_token,
        dismissed_at__isnull=True,
    ).first()


def schedule_account_invitation_accepted_signal(*, invitation_id: int, actor: str) -> None:
    normalized_actor = str(actor or "").strip().lower()

    def _send_signal() -> None:
        # Re-query after commit so receivers observe the durable row state.
        invitation = AccountInvitation.objects.filter(pk=invitation_id).first()
        if invitation is None:
            return
        astra_signals.account_invitation_accepted.send(
            sender=AccountInvitation,
            account_invitation=invitation,
            actor=normalized_actor,
        )

    transaction.on_commit(_send_signal)


def persist_non_org_invitation_acceptance(
    *,
    invitation: AccountInvitation,
    matched_usernames: list[str],
    actor_username: str,
    now: datetime,
    accepted_username: str = "",
    merge_matched_usernames: bool = False,
) -> bool:
    normalized_matches = sorted({str(username or "").strip().lower() for username in matched_usernames if str(username or "").strip()})
    normalized_accepted_username = str(accepted_username or "").strip().lower()
    normalized_actor_username = str(actor_username or "").strip().lower()

    if invitation.pk is None:
        accepted_transition = invitation.organization_id is None and invitation.accepted_at is None
        invitation.freeipa_matched_usernames = normalized_matches
        invitation.freeipa_last_checked_at = now
        update_fields = ["freeipa_matched_usernames", "freeipa_last_checked_at"]
        if invitation.organization_id is None:
            invitation.accepted_at = invitation.accepted_at or now
            update_fields.insert(0, "accepted_at")
            if normalized_accepted_username and not invitation.accepted_username:
                invitation.accepted_username = normalized_accepted_username
                update_fields.insert(1, "accepted_username")
        invitation.save(update_fields=update_fields)
        return accepted_transition

    with transaction.atomic():
        locked_invitation = AccountInvitation.objects.select_for_update().get(pk=invitation.pk)
        accepted_transition = False
        update_fields = ["freeipa_matched_usernames", "freeipa_last_checked_at"]

        if merge_matched_usernames:
            combined_matches = set(locked_invitation.freeipa_matched_usernames)
            combined_matches.update(normalized_matches)
            locked_invitation.freeipa_matched_usernames = sorted(combined_matches)
        else:
            locked_invitation.freeipa_matched_usernames = normalized_matches
        locked_invitation.freeipa_last_checked_at = now

        if locked_invitation.organization_id is None:
            if locked_invitation.accepted_at is None:
                locked_invitation.accepted_at = now
                update_fields.insert(0, "accepted_at")
                accepted_transition = True
            if normalized_accepted_username and not locked_invitation.accepted_username:
                locked_invitation.accepted_username = normalized_accepted_username
                update_fields.insert(1 if accepted_transition else 0, "accepted_username")

        locked_invitation.save(update_fields=update_fields)

        invitation.organization_id = locked_invitation.organization_id
        invitation.accepted_at = locked_invitation.accepted_at
        invitation.accepted_username = locked_invitation.accepted_username
        invitation.freeipa_matched_usernames = list(locked_invitation.freeipa_matched_usernames)
        invitation.freeipa_last_checked_at = locked_invitation.freeipa_last_checked_at

    if accepted_transition:
        schedule_account_invitation_accepted_signal(invitation_id=invitation.pk, actor=normalized_actor_username)
    return accepted_transition


def reconcile_account_invitation_for_username(
    invitation: AccountInvitation,
    username: str,
    now: datetime,
) -> None:
    normalized_username = str(username or "").strip().lower()
    if not normalized_username:
        return

    persist_non_org_invitation_acceptance(
        invitation=invitation,
        matched_usernames=[normalized_username],
        actor_username=normalized_username,
        now=now,
        accepted_username=normalized_username,
        merge_matched_usernames=True,
    )

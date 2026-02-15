from datetime import datetime

from django.core import signing

from core.models import AccountInvitation
from core.tokens import read_signed_token_unbounded


def load_account_invitation_from_token(invitation_token: str) -> AccountInvitation | None:
    normalized_token = str(invitation_token or "").strip()
    if not normalized_token:
        return None

    try:
        payload = read_signed_token_unbounded(normalized_token)
    except signing.BadSignature:
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


def reconcile_account_invitation_for_username(
    invitation: AccountInvitation,
    username: str,
    now: datetime,
) -> None:
    normalized_username = str(username or "").strip()
    if not normalized_username:
        return

    update_fields = ["freeipa_matched_usernames", "freeipa_last_checked_at"]
    if invitation.organization_id is None:
        invitation.accepted_at = invitation.accepted_at or now
        update_fields.insert(0, "accepted_at")

    usernames = set(invitation.freeipa_matched_usernames)
    usernames.add(normalized_username)
    invitation.freeipa_matched_usernames = sorted(usernames)
    invitation.freeipa_last_checked_at = now
    invitation.save(update_fields=update_fields)

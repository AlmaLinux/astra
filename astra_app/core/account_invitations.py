import csv
import io
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.urls import reverse
from post_office.models import EmailTemplate

from core.account_invitation_reconcile import persist_non_org_invitation_acceptance
from core.email_context import membership_committee_email_context, system_email_context
from core.freeipa.user import FreeIPAUser
from core.logging_extras import current_exception_log_fields
from core.models import AccountInvitation, AccountInvitationSend, Organization
from core.organization_claim import build_organization_claim_url
from core.public_urls import PublicBaseUrlConfigurationError, build_public_absolute_url
from core.templated_email import queue_templated_email

logger = logging.getLogger(__name__)


def normalize_invitation_email(value: str) -> str:
    return str(value or "").strip().lower()


def invitation_template_names() -> list[str]:
    return [str(name).strip() for name in settings.ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES if str(name).strip()]


def bulk_invitation_template_names() -> list[str]:
    return [
        name
        for name in invitation_template_names()
        if name != settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME
    ]


def _build_invitation_email_context(*, invitation: AccountInvitation, actor_username: str) -> dict[str, object]:
    invitation_token = str(invitation.invitation_token or "").strip()
    register_path = reverse("register")
    if not register_path:
        raise PublicBaseUrlConfigurationError("Register URL path is unavailable.")
    login_path = reverse("login")
    if not login_path:
        raise PublicBaseUrlConfigurationError("Login URL path is unavailable.")
    if invitation_token:
        invite_query = urlencode({"invite": invitation_token})
        register_path = f"{register_path}?{invite_query}"
        login_path = f"{login_path}?{invite_query}"

    context: dict[str, object] = {
        "full_name": invitation.full_name,
        "email": invitation.email,
        "invited_by_username": actor_username,
        "invitation_token": invitation_token,
        **system_email_context(),
        "register_url": build_public_absolute_url(register_path, on_missing="raise"),
        "login_url": build_public_absolute_url(login_path, on_missing="raise"),
        **membership_committee_email_context(),
    }

    if invitation.organization_id is not None:
        organization = Organization.objects.filter(pk=invitation.organization_id).first()
        if organization is not None:
            context["organization_name"] = organization.name
            context["claim_url"] = build_organization_claim_url(organization=organization)

    return context


def _send_account_invitation_email(
    *,
    invitation: AccountInvitation,
    actor_username: str,
    template_name: str,
    now: datetime,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: list[str] | None = None,
) -> str:
    effective_reply_to = reply_to if reply_to else [settings.MEMBERSHIP_COMMITTEE_EMAIL]
    try:
        email = queue_templated_email(
            recipients=[invitation.email],
            sender=settings.DEFAULT_FROM_EMAIL,
            template_name=template_name,
            context=_build_invitation_email_context(
                invitation=invitation,
                actor_username=actor_username,
            ),
            cc=cc or None,
            bcc=bcc or None,
            reply_to=effective_reply_to,
        )
    except PublicBaseUrlConfigurationError as exc:
        logger.warning("Account invitation email configuration error: %s", exc)
        AccountInvitationSend.objects.create(
            invitation=invitation,
            sent_by_username=actor_username,
            sent_at=now,
            template_name=template_name,
            result=AccountInvitationSend.Result.failed,
            error_category="configuration_error",
        )
        return "config_error"
    except Exception:
        logger.exception("Failed to queue account invitation email", extra=current_exception_log_fields())
        AccountInvitationSend.objects.create(
            invitation=invitation,
            sent_by_username=actor_username,
            sent_at=now,
            template_name=template_name,
            result=AccountInvitationSend.Result.failed,
            error_category="send_error",
        )
        return "failed"

    invitation.dismissed_at = None
    invitation.dismissed_by_username = ""
    invitation.last_sent_at = now
    invitation.send_count += 1
    invitation.email_template_name = template_name
    invitation.save(
        update_fields=[
            "dismissed_at",
            "dismissed_by_username",
            "last_sent_at",
            "send_count",
            "email_template_name",
        ]
    )

    AccountInvitationSend.objects.create(
        invitation=invitation,
        sent_by_username=actor_username,
        sent_at=now,
        template_name=template_name,
        post_office_email_id=email.id if email else None,
        result=AccountInvitationSend.Result.queued,
    )

    return "queued"


def resolve_invitation_template_selection(
    *,
    template_names: list[str],
    selected_name: str | None,
    allow_default: bool,
) -> tuple[str | None, str | None]:
    if not template_names:
        return None, "no_templates"

    selected = str(selected_name or "").strip()
    if selected:
        template_name = selected if selected in template_names else None
    elif allow_default:
        template_name = template_names[0]
    else:
        template_name = None
    if not template_name:
        return None, "template_invalid"
    if not EmailTemplate.objects.filter(name=template_name).exists():
        return None, "template_unavailable"
    return template_name, None


def build_freeipa_email_lookup() -> dict[str, set[str]]:
    users = FreeIPAUser.all(respect_privacy=False)
    if not users:
        logger.warning(
            "Account invitation FreeIPA lookup: FreeIPAUser.all() returned 0 users; falling back to per-email lookup"
        )
        return {}

    mapping: dict[str, set[str]] = {}
    for user in users:
        email = normalize_invitation_email(user.email)
        username = str(user.username or "").strip().lower()
        if not email or not username:
            continue
        mapping.setdefault(email, set()).add(username)

    return mapping


def parse_invitation_csv(content: str, *, max_rows: int) -> list[dict[str, str]]:
    raw = str(content or "")
    if not raw.strip():
        raise ValueError("CSV is empty.")

    sample = raw[:64 * 1024]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except Exception:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(raw, newline=""), dialect)
    rows = [row for row in reader if any(str(cell or "").strip() for cell in row)]
    if not rows:
        raise ValueError("CSV is empty.")

    header = ["".join(ch for ch in str(cell or "").strip().lower() if ch.isalnum()) for cell in rows[0]]

    header_map: dict[int, str] = {}
    if "email" in header:
        for idx, name in enumerate(header):
            if name == "email":
                header_map[idx] = "email"
            elif name == "fullname":
                header_map[idx] = "full_name"
            elif name in {"note", "notes"}:
                header_map[idx] = "note"
    else:
        header_map = {0: "email", 1: "full_name", 2: "note"}

    data_rows = rows[1:] if "email" in header else rows
    if max_rows > 0 and len(data_rows) > max_rows:
        raise ValueError(f"CSV exceeds the maximum of {max_rows} rows.")

    parsed: list[dict[str, str]] = []
    for row in data_rows:
        entry: dict[str, str] = {"email": "", "full_name": "", "note": ""}
        for idx, key in header_map.items():
            if idx >= len(row):
                continue
            entry[key] = str(row[idx] or "").strip()
        parsed.append(entry)

    return parsed


@dataclass(frozen=True)
class ClassifiedInvitationRow:
    email: str
    full_name: str
    note: str
    status: str
    reason: str
    freeipa_usernames: list[str]
    has_multiple_matches: bool
    existing_invitation: AccountInvitation | None


def _get_cached_freeipa_matches(
    *,
    normalized_email: str,
    freeipa_lookup: Callable[[str], list[str]],
    freeipa_cache: dict[str, list[str]],
) -> list[str]:
    cached = freeipa_cache.get(normalized_email)
    if cached is None:
        cached = sorted(
            {str(username or "").strip().lower() for username in freeipa_lookup(normalized_email) if str(username or "").strip()}
        )
        freeipa_cache[normalized_email] = cached
    return cached


def classify_invitation_upload_rows(
    rows: list[dict[str, str]],
    *,
    existing_invitations: dict[str, AccountInvitation],
    freeipa_lookup: Callable[[str], list[str]],
) -> tuple[list[ClassifiedInvitationRow], dict[str, int]]:
    classified_rows: list[ClassifiedInvitationRow] = []
    counts: dict[str, int] = {"new": 0, "resend": 0, "existing": 0, "invalid": 0, "duplicate": 0}
    seen: set[str] = set()
    freeipa_cache: dict[str, list[str]] = {}

    for row in rows:
        email_raw = str(row.get("email") or "")
        full_name = str(row.get("full_name") or "")
        note = str(row.get("note") or "")
        normalized = normalize_invitation_email(email_raw)

        status = ""
        reason = ""
        matches: list[str] = []
        existing_invitation: AccountInvitation | None = None

        if not normalized:
            status = "invalid"
            reason = "Missing email"
        elif normalized in seen:
            status = "duplicate"
            reason = "Duplicate email in upload"
        else:
            seen.add(normalized)
            try:
                validate_email(normalized)
            except ValidationError:
                status = "invalid"
                reason = "Invalid email"
            else:
                matches = _get_cached_freeipa_matches(
                    normalized_email=normalized,
                    freeipa_lookup=freeipa_lookup,
                    freeipa_cache=freeipa_cache,
                )
                if matches:
                    status = "existing"
                    reason = "Already has an account"
                else:
                    existing_invitation = existing_invitations.get(normalized)
                    if existing_invitation is not None and existing_invitation.accepted_at:
                        status = "existing"
                        reason = "Accepted invitation"
                        matches = [
                            str(username or "").strip().lower()
                            for username in existing_invitation.freeipa_matched_usernames
                            if str(username or "").strip()
                        ]
                    else:
                        status = "resend" if existing_invitation is not None else "new"

        counts[status] = counts.get(status, 0) + 1
        classified_rows.append(
            ClassifiedInvitationRow(
                email=normalized or email_raw,
                full_name=full_name,
                note=note,
                status=status,
                reason=reason,
                freeipa_usernames=matches,
                has_multiple_matches=len(matches) > 1,
                existing_invitation=existing_invitation,
            )
        )

    return classified_rows, counts
def dismiss_account_invitations(
    *,
    invitations: Iterable[AccountInvitation],
    actor_username: str,
    now: datetime,
) -> int:
    updated = 0
    normalized_actor_username = str(actor_username or "").strip()
    for invitation in invitations:
        invitation.dismissed_at = now
        invitation.dismissed_by_username = normalized_actor_username
        invitation.save(update_fields=["dismissed_at", "dismissed_by_username"])
        updated += 1
    return updated


@dataclass(frozen=True)
class AccountInvitationBulkSendSummary:
    queued: int = 0
    existing: int = 0
    invalid: int = 0
    duplicate: int = 0
    skipped_org_linked: int = 0
    config_error: int = 0
    failed: int = 0


def send_account_invitation_rows(
    rows: list[dict[str, str]],
    *,
    actor_username: str,
    template_name: str,
    now: datetime,
    existing_invitations: dict[str, AccountInvitation],
    freeipa_lookup: Callable[[str], list[str]],
    send_email: Callable[[AccountInvitation], str],
) -> AccountInvitationBulkSendSummary:
    classified_rows, _counts = classify_invitation_upload_rows(
        rows,
        existing_invitations=existing_invitations,
        freeipa_lookup=freeipa_lookup,
    )

    queued = 0
    existing = 0
    invalid = 0
    duplicate = 0
    skipped_org_linked = 0
    config_error = 0
    failed = 0

    for row in classified_rows:
        if row.status == "invalid":
            invalid += 1
            continue
        if row.status == "duplicate":
            duplicate += 1
            continue
        if row.status == "existing":
            existing += 1
            continue

        invitation = row.existing_invitation
        if invitation is None:
            invitation = AccountInvitation.objects.create(
                email=row.email,
                full_name=row.full_name,
                note=row.note,
                invited_by_username=actor_username,
                email_template_name=template_name,
            )
            existing_invitations[row.email] = invitation
        elif invitation.organization_id is not None:
            skipped_org_linked += 1
            continue

        if row.full_name:
            invitation.full_name = row.full_name
        if row.note:
            invitation.note = row.note
        invitation.invited_by_username = actor_username
        invitation.email_template_name = template_name
        invitation.save(
            update_fields=[
                "full_name",
                "note",
                "invited_by_username",
                "email_template_name",
            ]
        )

        result = send_email(invitation)
        if result == "queued":
            queued += 1
        elif result == "config_error":
            config_error += 1
        else:
            failed += 1

    return AccountInvitationBulkSendSummary(
        queued=queued,
        existing=existing,
        invalid=invalid,
        duplicate=duplicate,
        skipped_org_linked=skipped_org_linked,
        config_error=config_error,
        failed=failed,
    )


@dataclass(frozen=True)
class AccountInvitationResendSummary:
    queued: int = 0
    accepted: int = 0
    failed: int = 0
    config_error: int = 0
    template_error: int = 0


def summarize_resend_results(results: Iterable[str]) -> AccountInvitationResendSummary:
    queued = 0
    accepted = 0
    failed = 0
    config_error = 0
    template_error = 0

    for result in results:
        if result == "queued":
            queued += 1
        elif result == "accepted":
            accepted += 1
        elif result == "failed":
            failed += 1
        elif result == "config_error":
            config_error += 1
        else:
            template_error += 1

    return AccountInvitationResendSummary(
        queued=queued,
        accepted=accepted,
        failed=failed,
        config_error=config_error,
        template_error=template_error,
    )


def confirm_existing_usernames(usernames: list[str]) -> tuple[list[str], bool]:
    confirmed: list[str] = []
    seen: set[str] = set()

    for username in usernames:
        normalized = str(username or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        try:
            user = FreeIPAUser.get(normalized)
        except Exception:
            logger.exception("Account invitation FreeIPA user lookup failed", extra=current_exception_log_fields())
            return [], False
        if user is None:
            continue
        confirmed.append(normalized)
        seen.add(normalized)

    return sorted(confirmed), True


def find_account_invitation_matches(email: str) -> list[str]:
    normalized = normalize_invitation_email(email)
    if not normalized:
        return []

    try:
        matches = FreeIPAUser.find_usernames_by_email(normalized)
    except Exception:
        logger.exception("Account invitation FreeIPA email lookup failed", extra=current_exception_log_fields())
        return []

    confirmed, ok = confirm_existing_usernames(matches)
    if not ok:
        return []
    return confirmed


def _mark_invitation_accepted_from_email_match(
    *,
    invitation: AccountInvitation,
    matched_usernames: list[str],
    actor_username: str,
    now: datetime,
) -> bool:
    return persist_non_org_invitation_acceptance(
        invitation=invitation,
        matched_usernames=matched_usernames,
        actor_username=actor_username,
        now=now,
    )


@dataclass(frozen=True)
class AccountInvitationRefreshSummary:
    pending_checked: int
    pending_updated: int
    accepted_checked: int
    accepted_updated: int


def refresh_pending_invitations(
    *,
    pending: Iterable[AccountInvitation],
    actor_username: str,
    now: datetime,
) -> tuple[int, int]:
    updated = 0
    checked = 0
    for invitation in pending:
        checked += 1
        matches = find_account_invitation_matches(invitation.email)
        if matches:
            if _mark_invitation_accepted_from_email_match(
                invitation=invitation,
                matched_usernames=matches,
                actor_username=actor_username,
                now=now,
            ):
                updated += 1
        else:
            invitation.freeipa_matched_usernames = []
            invitation.freeipa_last_checked_at = now
            invitation.save(update_fields=["freeipa_matched_usernames", "freeipa_last_checked_at"])
    return updated, checked


def refresh_accepted_invitations(
    *,
    accepted: Iterable[AccountInvitation],
    now: datetime,
) -> tuple[int, int]:
    updated = 0
    checked = 0
    for invitation in accepted:
        checked += 1
        if not invitation.freeipa_matched_usernames:
            continue
        confirmed, ok = confirm_existing_usernames(invitation.freeipa_matched_usernames)
        if not ok:
            continue
        if not confirmed:
            invitation.accepted_at = None
            invitation.freeipa_matched_usernames = []
            invitation.freeipa_last_checked_at = now
            invitation.save(update_fields=["accepted_at", "freeipa_matched_usernames", "freeipa_last_checked_at"])
            updated += 1
            continue
        if confirmed != invitation.freeipa_matched_usernames:
            invitation.freeipa_matched_usernames = confirmed
            invitation.freeipa_last_checked_at = now
            invitation.save(update_fields=["freeipa_matched_usernames", "freeipa_last_checked_at"])
            updated += 1
    return updated, checked


def refresh_account_invitations(
    *,
    actor_username: str,
    now: datetime,
    pending: Iterable[AccountInvitation] | None = None,
    accepted: Iterable[AccountInvitation] | None = None,
) -> AccountInvitationRefreshSummary:
    pending_source = pending
    if pending_source is None:
        pending_source = AccountInvitation.objects.filter(
            dismissed_at__isnull=True,
            accepted_at__isnull=True,
        ).order_by("pk")

    accepted_source = accepted
    if accepted_source is None:
        accepted_source = AccountInvitation.objects.filter(
            dismissed_at__isnull=True,
            accepted_at__isnull=False,
        ).order_by("pk")

    pending_updated, pending_checked = refresh_pending_invitations(
        pending=pending_source,
        actor_username=actor_username,
        now=now,
    )
    accepted_updated, accepted_checked = refresh_accepted_invitations(
        accepted=accepted_source,
        now=now,
    )
    return AccountInvitationRefreshSummary(
        pending_checked=pending_checked,
        pending_updated=pending_updated,
        accepted_checked=accepted_checked,
        accepted_updated=accepted_updated,
    )

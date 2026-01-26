from __future__ import annotations

import logging
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import validate_email
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from post_office.models import EmailTemplate

from core.account_invitations import (
    build_freeipa_email_lookup,
    classify_invitation_rows,
    confirm_existing_usernames,
    find_account_invitation_matches,
    normalize_invitation_email,
    parse_invitation_csv,
)
from core.email_context import system_email_context
from core.models import AccountInvitation, AccountInvitationSend
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.rate_limit import allow_request
from core.templated_email import queue_templated_email

logger = logging.getLogger(__name__)

_PREVIEW_SESSION_KEY = "account_invitation_preview_v1"


def _invitation_template_names() -> list[str]:
    return [str(name).strip() for name in settings.ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES if str(name).strip()]


def _select_invitation_template_name(
    *,
    template_names: list[str],
    selected_name: str | None,
    allow_default: bool,
) -> str | None:
    selected = str(selected_name or "").strip()
    if selected:
        return selected if selected in template_names else None
    if allow_default and template_names:
        return template_names[0]
    return None


def _invitation_register_url(*, token: str) -> str:
    base = str(settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    path = reverse("register")
    if not base or not path:
        return ""
    url = f"{base}{path}"
    normalized_token = str(token or "").strip()
    if normalized_token:
        url = f"{url}?{urlencode({'invite': normalized_token})}"
    return url


def _invitation_login_url(*, token: str) -> str:
    base = str(settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    path = reverse("login")
    if not base or not path:
        return ""
    url = f"{base}{path}"
    normalized_token = str(token or "").strip()
    if normalized_token:
        url = f"{url}?{urlencode({'invite': normalized_token})}"
    return url


def _refresh_pending_invitations(*, pending: list[AccountInvitation], now: timezone.datetime) -> tuple[int, int]:
    updated = 0
    checked = 0
    for invitation in pending:
        checked += 1
        matches = find_account_invitation_matches(invitation.email)
        if matches:
            invitation.accepted_at = invitation.accepted_at or now
            invitation.freeipa_matched_usernames = matches
            invitation.freeipa_last_checked_at = now
            invitation.save(update_fields=["accepted_at", "freeipa_matched_usernames", "freeipa_last_checked_at"])
            updated += 1
        else:
            invitation.freeipa_matched_usernames = []
            invitation.freeipa_last_checked_at = now
            invitation.save(update_fields=["freeipa_matched_usernames", "freeipa_last_checked_at"])
    return updated, checked


def _refresh_accepted_invitations(*, accepted: list[AccountInvitation], now: timezone.datetime) -> int:
    updated = 0
    for invitation in accepted:
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
    return updated


def _resend_invitation(
    *,
    invitation: AccountInvitation,
    actor_username: str,
    template_names: list[str],
    now: timezone.datetime,
) -> str:
    matches = find_account_invitation_matches(invitation.email)
    if matches:
        invitation.accepted_at = invitation.accepted_at or now
        invitation.freeipa_matched_usernames = matches
        invitation.freeipa_last_checked_at = now
        invitation.save(update_fields=["accepted_at", "freeipa_matched_usernames", "freeipa_last_checked_at"])
        return "accepted"

    if not template_names:
        return "template_missing"

    template_name = _select_invitation_template_name(
        template_names=template_names,
        selected_name=invitation.email_template_name,
        allow_default=True,
    )
    if not template_name:
        return "template_invalid"

    if not EmailTemplate.objects.filter(name=template_name).exists():
        return "template_missing"

    invitation_token = str(invitation.invitation_token or "").strip()
    try:
        email = queue_templated_email(
            recipients=[invitation.email],
            sender=settings.DEFAULT_FROM_EMAIL,
            template_name=template_name,
            context={
                "full_name": invitation.full_name,
                "email": invitation.email,
                "invited_by_username": actor_username,
                "invitation_token": invitation_token,
                **system_email_context(),
                "register_url": _invitation_register_url(token=invitation_token),
                "login_url": _invitation_login_url(token=invitation_token),
            },
        )
    except Exception:
        logger.exception("Failed to resend account invitation")
        AccountInvitationSend.objects.create(
            invitation=invitation,
            sent_by_username=actor_username,
            sent_at=now,
            template_name=template_name,
            result=AccountInvitationSend.Result.failed,
            error_category="send_error",
        )
        return "failed"

    invitation.last_sent_at = now
    invitation.send_count += 1
    invitation.email_template_name = template_name
    invitation.save(update_fields=["last_sent_at", "send_count", "email_template_name"])

    AccountInvitationSend.objects.create(
        invitation=invitation,
        sent_by_username=actor_username,
        sent_at=now,
        template_name=template_name,
        post_office_email_id=email.id if email else None,
        result=AccountInvitationSend.Result.queued,
    )

    return "queued"


class AccountInvitationUploadForm(forms.Form):
    csv_file = forms.FileField(required=True)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def account_invitations(request: HttpRequest) -> HttpResponse:
    pending_invitations = list(
        AccountInvitation.objects.filter(dismissed_at__isnull=True, accepted_at__isnull=True)
        .order_by("-invited_at")
        .all()
    )
    if pending_invitations:
        _refresh_pending_invitations(pending=pending_invitations, now=timezone.now())
        pending_invitations = list(
            AccountInvitation.objects.filter(dismissed_at__isnull=True, accepted_at__isnull=True)
            .order_by("-invited_at")
            .all()
        )
    accepted_invitations = (
        AccountInvitation.objects.filter(dismissed_at__isnull=True, accepted_at__isnull=False)
        .order_by("-accepted_at", "-invited_at")
        .all()
    )
    accepted_list = list(accepted_invitations)
    if accepted_list:
        _refresh_accepted_invitations(accepted=accepted_list, now=timezone.now())
        accepted_invitations = (
            AccountInvitation.objects.filter(dismissed_at__isnull=True, accepted_at__isnull=False)
            .order_by("-accepted_at", "-invited_at")
            .all()
        )

    return render(
        request,
        "core/account_invitations.html",
        {
            "pending_invitations": pending_invitations,
            "accepted_invitations": accepted_invitations,
        },
    )


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def account_invitations_upload(request: HttpRequest) -> HttpResponse:
    template_names = _invitation_template_names()
    email_templates = list(EmailTemplate.objects.filter(name__in=template_names).order_by("name"))

    if request.method == "POST":
        form = AccountInvitationUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded: UploadedFile = form.cleaned_data["csv_file"]
            if not template_names:
                form.add_error("csv_file", "No invitation templates are configured.")
            elif not email_templates:
                form.add_error("csv_file", "No invitation templates are configured.")
            elif uploaded.size and uploaded.size > settings.ACCOUNT_INVITATION_MAX_UPLOAD_BYTES:
                form.add_error("csv_file", "CSV file is too large.")
            else:
                raw = uploaded.read()
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = raw.decode("utf-8", errors="replace")

                try:
                    rows = parse_invitation_csv(text, max_rows=settings.ACCOUNT_INVITATION_MAX_CSV_ROWS)
                except ValueError as exc:
                    form.add_error("csv_file", str(exc))
                else:
                    emails = {normalize_invitation_email(row.get("email") or "") for row in rows}
                    existing = {
                        inv.email: inv
                        for inv in AccountInvitation.objects.filter(email__in=[e for e in emails if e]).all()
                    }
                    email_map = build_freeipa_email_lookup()

                    def _bulk_lookup(email: str) -> list[str]:
                        normalized = normalize_invitation_email(email)
                        if not normalized:
                            return []
                        if email_map:
                            return sorted(email_map.get(normalized, set()))
                        return find_account_invitation_matches(normalized)

                    preview_rows, counts = classify_invitation_rows(
                        rows,
                        existing_invitations=existing,
                        freeipa_lookup=_bulk_lookup,
                    )

                    request.session[_PREVIEW_SESSION_KEY] = {
                        "rows": rows,
                    }

                    return render(
                        request,
                        "core/account_invitations_preview.html",
                        {
                            "preview_rows": preview_rows,
                            "counts": counts,
                            "email_templates": email_templates,
                            "default_template_name": email_templates[0].name if email_templates else "",
                        },
                    )
    else:
        form = AccountInvitationUploadForm()

    return render(
        request,
        "core/account_invitations_upload.html",
        {
            "form": form,
            "email_templates": email_templates,
            "default_template_name": email_templates[0].name if email_templates else "",
        },
    )


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def account_invitations_send(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    if not allow_request(
        scope="account_invitation_bulk_send",
        key_parts=[request.user.get_username()],
        limit=settings.ACCOUNT_INVITATION_BULK_SEND_LIMIT,
        window_seconds=settings.ACCOUNT_INVITATION_BULK_SEND_WINDOW_SECONDS,
    ):
        messages.error(request, "Too many send attempts. Try again shortly.")
        return redirect("account-invitations")

    confirm = str(request.POST.get("confirm") or "").strip()
    if confirm != "1":
        messages.error(request, "Confirmation is required before sending invitations.")
        return redirect("account-invitations")

    payload = request.session.get(_PREVIEW_SESSION_KEY)
    if not isinstance(payload, dict):
        messages.error(request, "Invitation preview data is missing. Please upload the CSV again.")
        return redirect("account-invitations")

    rows = payload.get("rows")
    if not isinstance(rows, list):
        messages.error(request, "Invitation preview data is missing. Please upload the CSV again.")
        return redirect("account-invitations")

    template_names = _invitation_template_names()
    template_name = _select_invitation_template_name(
        template_names=template_names,
        selected_name=str(request.POST.get("email_template") or payload.get("template_name") or "").strip(),
        allow_default=False,
    )
    if not template_names:
        messages.error(request, "No invitation templates are configured.")
        return redirect("account-invitations")
    if not template_name:
        messages.error(request, "Select a valid email template.")
        return redirect("account-invitations")

    if not EmailTemplate.objects.filter(name=template_name).exists():
        messages.error(request, "The selected email template is not available.")
        return redirect("account-invitations")

    actor_username = request.user.get_username()
    now = timezone.now()

    sent = 0
    existing = 0
    invalid = 0
    skipped_duplicate = 0
    failed = 0
    seen: set[str] = set()
    lookup_cache: dict[str, list[str]] = {}
    email_map = build_freeipa_email_lookup()

    for row in rows:
        if not isinstance(row, dict):
            invalid += 1
            continue

        email_raw = str(row.get("email") or "")
        full_name = str(row.get("full_name") or "")
        note = str(row.get("note") or "")

        normalized = normalize_invitation_email(email_raw)
        if not normalized:
            invalid += 1
            continue
        try:
            validate_email(normalized)
        except ValidationError:
            invalid += 1
            continue
        if normalized in seen:
            skipped_duplicate += 1
            continue
        seen.add(normalized)

        matches = lookup_cache.get(normalized)
        if matches is None:
            if email_map:
                matches = sorted(email_map.get(normalized, set()))
            else:
                matches = find_account_invitation_matches(normalized)
            lookup_cache[normalized] = matches

        if matches:
            existing += 1
            continue

        invitation, _created = AccountInvitation.objects.get_or_create(
            email=normalized,
            defaults={
                "full_name": full_name,
                "note": note,
                "invited_by_username": actor_username,
                "email_template_name": template_name,
            },
        )

        if full_name:
            invitation.full_name = full_name
        if note:
            invitation.note = note
        invitation.invited_by_username = actor_username
        invitation.email_template_name = template_name

        invitation_token = str(invitation.invitation_token or "").strip()
        try:
            email = queue_templated_email(
                recipients=[normalized],
                sender=settings.DEFAULT_FROM_EMAIL,
                template_name=template_name,
                context={
                    "full_name": full_name,
                    "email": normalized,
                    "invited_by_username": actor_username,
                    "invitation_token": invitation_token,
                    **system_email_context(),
                    "register_url": _invitation_register_url(token=invitation_token),
                    "login_url": _invitation_login_url(token=invitation_token),
                },
            )
        except Exception:
            logger.exception("Failed to queue account invitation email")
            AccountInvitationSend.objects.create(
                invitation=invitation,
                sent_by_username=actor_username,
                sent_at=now,
                template_name=template_name,
                result=AccountInvitationSend.Result.failed,
                error_category="send_error",
            )
            failed += 1
            continue

        invitation.dismissed_at = None
        invitation.dismissed_by_username = ""
        invitation.last_sent_at = now
        invitation.send_count += 1
        invitation.save(
            update_fields=[
                "full_name",
                "note",
                "invited_by_username",
                "email_template_name",
                "dismissed_at",
                "dismissed_by_username",
                "last_sent_at",
                "send_count",
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
        sent += 1

    request.session.pop(_PREVIEW_SESSION_KEY, None)

    logger.info(
        "Account invitation bulk send queued=%s existing=%s invalid=%s duplicate=%s failed=%s",
        sent,
        existing,
        invalid,
        skipped_duplicate,
        failed,
    )

    if sent:
        messages.success(request, f"Queued {sent} invitation(s).")
    if existing:
        messages.info(request, f"Skipped {existing} existing account(s).")
    if skipped_duplicate:
        messages.info(request, f"Skipped {skipped_duplicate} duplicate row(s).")
    if invalid:
        messages.error(request, f"Skipped {invalid} invalid row(s).")
    if failed:
        messages.error(request, f"Failed to queue {failed} invitation(s).")

    return redirect("account-invitations")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def account_invitations_bulk(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    action = str(request.POST.get("bulk_action") or "").strip().lower()
    scope = str(request.POST.get("bulk_scope") or "pending").strip().lower()
    selected = [str(value).strip() for value in request.POST.getlist("selected") if str(value).strip()]

    if not selected:
        messages.error(request, "Select at least one invitation.")
        return redirect("account-invitations")

    if scope not in {"pending", "accepted"}:
        messages.error(request, "Select a valid bulk scope.")
        return redirect("account-invitations")

    if action not in {"resend", "dismiss"}:
        messages.error(request, "Select a valid bulk action.")
        return redirect("account-invitations")

    if scope == "accepted" and action != "dismiss":
        messages.error(request, "Accepted invitations can only be dismissed.")
        return redirect("account-invitations")

    base_qs = AccountInvitation.objects.filter(pk__in=selected, dismissed_at__isnull=True)
    if scope == "pending":
        invitations = list(base_qs.filter(accepted_at__isnull=True))
    else:
        invitations = list(base_qs.filter(accepted_at__isnull=False))

    if not invitations:
        messages.error(request, "No invitations matched your selection.")
        return redirect("account-invitations")

    actor_username = request.user.get_username()
    now = timezone.now()

    if action == "dismiss":
        updated = 0
        for invitation in invitations:
            invitation.dismissed_at = now
            invitation.dismissed_by_username = actor_username
            invitation.save(update_fields=["dismissed_at", "dismissed_by_username"])
            updated += 1

        messages.success(request, f"Dismissed {updated} invitation(s).")
        return redirect("account-invitations")

    if not allow_request(
        scope="account_invitation_bulk_resend",
        key_parts=[actor_username],
        limit=settings.ACCOUNT_INVITATION_RESEND_LIMIT,
        window_seconds=settings.ACCOUNT_INVITATION_RESEND_WINDOW_SECONDS,
    ):
        messages.error(request, "Too many resend attempts. Try again shortly.")
        return redirect("account-invitations")

    template_names = _invitation_template_names()
    if not template_names:
        messages.error(request, "No invitation templates are configured.")
        return redirect("account-invitations")

    queued = 0
    accepted = 0
    failed = 0
    template_error = 0

    for invitation in invitations:
        result = _resend_invitation(
            invitation=invitation,
            actor_username=actor_username,
            template_names=template_names,
            now=now,
        )
        if result == "queued":
            queued += 1
        elif result == "accepted":
            accepted += 1
        elif result == "failed":
            failed += 1
        else:
            template_error += 1

    if queued:
        messages.success(request, f"Resent {queued} invitation(s).")
    if accepted:
        messages.info(request, f"Skipped {accepted} already accepted invitation(s).")
    if failed:
        messages.error(request, f"Failed to resend {failed} invitation(s).")
    if template_error:
        messages.error(request, "One or more invitations could not be resent due to template configuration.")

    return redirect("account-invitations")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def account_invitation_resend(request: HttpRequest, invitation_id: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    if not allow_request(
        scope="account_invitation_resend",
        key_parts=[request.user.get_username(), invitation_id],
        limit=settings.ACCOUNT_INVITATION_RESEND_LIMIT,
        window_seconds=settings.ACCOUNT_INVITATION_RESEND_WINDOW_SECONDS,
    ):
        messages.error(request, "Too many resend attempts. Try again shortly.")
        return redirect("account-invitations")

    invitation = get_object_or_404(AccountInvitation, pk=invitation_id)
    if invitation.dismissed_at is not None:
        messages.error(request, "That invitation has been dismissed.")
        return redirect("account-invitations")

    template_names = _invitation_template_names()
    if not template_names:
        messages.error(request, "No invitation templates are configured.")
        return redirect("account-invitations")

    actor_username = request.user.get_username()
    now = timezone.now()
    result = _resend_invitation(
        invitation=invitation,
        actor_username=actor_username,
        template_names=template_names,
        now=now,
    )
    if result == "accepted":
        messages.info(request, "That invitation is already accepted.")
    elif result == "queued":
        messages.success(request, "Invitation resent.")
    elif result == "failed":
        messages.error(request, "Failed to resend the invitation.")
    else:
        messages.error(request, "No invitation templates are configured.")
    return redirect("account-invitations")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def account_invitation_dismiss(request: HttpRequest, invitation_id: int) -> HttpResponse:
    if request.method != "POST":
        raise Http404("Not found")

    invitation = get_object_or_404(AccountInvitation, pk=invitation_id)
    now = timezone.now()
    invitation.dismissed_at = now
    invitation.dismissed_by_username = request.user.get_username()
    invitation.save(update_fields=["dismissed_at", "dismissed_by_username"])

    messages.success(request, "Invitation dismissed.")
    return redirect("account-invitations")

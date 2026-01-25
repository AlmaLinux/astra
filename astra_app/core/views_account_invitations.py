from __future__ import annotations

import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import validate_email
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from post_office.models import EmailTemplate

from core.account_invitations import (
    classify_invitation_rows,
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


class AccountInvitationUploadForm(forms.Form):
    csv_file = forms.FileField(required=True)
    email_template = forms.ChoiceField(required=True)

    def __init__(self, *args, **kwargs) -> None:
        template_choices = kwargs.pop("template_choices", [])
        super().__init__(*args, **kwargs)
        self.fields["email_template"].choices = template_choices


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
    template_names = [
        str(name).strip()
        for name in settings.ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES
        if str(name).strip()
    ]
    email_templates = list(EmailTemplate.objects.filter(name__in=template_names).order_by("name"))
    template_choices = [(tpl.name, tpl.name) for tpl in email_templates]

    if request.method == "POST":
        form = AccountInvitationUploadForm(request.POST, request.FILES, template_choices=template_choices)
        if form.is_valid():
            uploaded: UploadedFile = form.cleaned_data["csv_file"]
            selected_template = str(form.cleaned_data["email_template"] or "").strip()

            if not template_names:
                form.add_error("email_template", "No invitation templates are configured.")
            elif selected_template not in template_names:
                form.add_error("email_template", "Select a valid email template.")
            elif not email_templates:
                form.add_error("email_template", "No invitation templates are configured.")
            elif not EmailTemplate.objects.filter(name=selected_template).exists():
                form.add_error("email_template", "The selected email template is not available.")
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
                    preview_rows, counts = classify_invitation_rows(
                        rows,
                        existing_invitations=existing,
                        freeipa_lookup=find_account_invitation_matches,
                    )

                    request.session[_PREVIEW_SESSION_KEY] = {
                        "rows": rows,
                        "template_name": selected_template,
                    }

                    return render(
                        request,
                        "core/account_invitations_preview.html",
                        {
                            "preview_rows": preview_rows,
                            "counts": counts,
                            "email_templates": email_templates,
                            "default_template_name": selected_template,
                        },
                    )
    else:
        form = AccountInvitationUploadForm(template_choices=template_choices)

    return render(
        request,
        "core/account_invitations_upload.html",
        {
            "form": form,
            "email_templates": email_templates,
            "default_template_name": template_choices[0][0] if template_choices else "",
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

    template_names = [
        str(name).strip()
        for name in settings.ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES
        if str(name).strip()
    ]
    template_name = str(request.POST.get("email_template") or payload.get("template_name") or "").strip()
    if not template_names:
        messages.error(request, "No invitation templates are configured.")
        return redirect("account-invitations")
    if template_name not in template_names:
        messages.error(request, "Select a valid email template.")
        return redirect("account-invitations")

    if not EmailTemplate.objects.filter(name=template_name).exists():
        messages.error(request, "The selected email template is not available.")
        return redirect("account-invitations")

    actor_username = request.user.get_username()
    now = timezone.now()

    sent = 0
    accepted = 0
    invalid = 0
    skipped_duplicate = 0
    failed = 0
    seen: set[str] = set()
    lookup_cache: dict[str, list[str]] = {}

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
            matches = find_account_invitation_matches(normalized)
            lookup_cache[normalized] = matches

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

        if matches:
            invitation.accepted_at = invitation.accepted_at or now
            invitation.freeipa_matched_usernames = matches
            invitation.freeipa_last_checked_at = now
            invitation.save(
                update_fields=[
                    "full_name",
                    "note",
                    "invited_by_username",
                    "email_template_name",
                    "accepted_at",
                    "freeipa_matched_usernames",
                    "freeipa_last_checked_at",
                ]
            )
            accepted += 1
            continue

        try:
            email = queue_templated_email(
                recipients=[normalized],
                sender=settings.DEFAULT_FROM_EMAIL,
                template_name=template_name,
                context={
                    "full_name": full_name,
                    "email": normalized,
                    "invited_by_username": actor_username,
                    **system_email_context(),
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
        "Account invitation bulk send queued=%s accepted=%s invalid=%s duplicate=%s failed=%s",
        sent,
        accepted,
        invalid,
        skipped_duplicate,
        failed,
    )

    if sent:
        messages.success(request, f"Queued {sent} invitation(s).")
    if accepted:
        messages.info(request, f"Skipped {accepted} already accepted invitation(s).")
    if skipped_duplicate:
        messages.info(request, f"Skipped {skipped_duplicate} duplicate row(s).")
    if invalid:
        messages.error(request, f"Skipped {invalid} invalid row(s).")
    if failed:
        messages.error(request, f"Failed to queue {failed} invitation(s).")

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

    matches = find_account_invitation_matches(invitation.email)
    if matches:
        now = timezone.now()
        invitation.accepted_at = invitation.accepted_at or now
        invitation.freeipa_matched_usernames = matches
        invitation.freeipa_last_checked_at = now
        invitation.save(update_fields=["accepted_at", "freeipa_matched_usernames", "freeipa_last_checked_at"])
        messages.info(request, "That invitation is already accepted.")
        return redirect("account-invitations")

    template_names = [
        str(name).strip()
        for name in settings.ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES
        if str(name).strip()
    ]
    if not template_names:
        messages.error(request, "No invitation templates are configured.")
        return redirect("account-invitations")

    template_name = str(invitation.email_template_name or "").strip() or template_names[0]
    if template_name not in template_names:
        messages.error(request, "Select a valid email template.")
        return redirect("account-invitations")

    if not EmailTemplate.objects.filter(name=template_name).exists():
        messages.error(request, "No invitation templates are configured.")
        return redirect("account-invitations")

    actor_username = request.user.get_username()
    now = timezone.now()

    try:
        email = queue_templated_email(
            recipients=[invitation.email],
            sender=settings.DEFAULT_FROM_EMAIL,
            template_name=template_name,
            context={
                "full_name": invitation.full_name,
                "email": invitation.email,
                "invited_by_username": actor_username,
                **system_email_context(),
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
        messages.error(request, "Failed to resend the invitation.")
        return redirect("account-invitations")

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

    messages.success(request, "Invitation resent.")
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

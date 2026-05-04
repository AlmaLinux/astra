import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import validate_email
from django.db.models.functions import Lower
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from post_office.models import EmailTemplate

from core.account_invitations import (
    _mark_invitation_accepted_from_email_match,
    _send_account_invitation_email,
    build_freeipa_email_lookup,
    bulk_invitation_template_names,
    classify_invitation_upload_rows,
    dismiss_account_invitations,
    find_account_invitation_matches,
    invitation_template_names,
    normalize_invitation_email,
    parse_invitation_csv,
    refresh_account_invitations,
    resolve_invitation_template_selection,
    send_account_invitation_rows,
    summarize_resend_results,
)
from core.forms_base import StyledForm
from core.models import AccountInvitation, Organization
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.rate_limit import allow_request
from core.views_utils import get_username, post_only_404

logger = logging.getLogger(__name__)

_PREVIEW_SESSION_KEY = "account_invitation_preview_v1"
_INVITATION_PUBLIC_BASE_URL_ERROR_MESSAGE = (
    "Invitation email configuration error: PUBLIC_BASE_URL must be configured to build invitation links."
)


def _existing_invitations_by_normalized_email(emails: set[str]) -> dict[str, AccountInvitation]:
    normalized_emails = {normalize_invitation_email(email) for email in emails if normalize_invitation_email(email)}
    if not normalized_emails:
        return {}

    invitations = AccountInvitation.objects.annotate(normalized_email=Lower("email")).filter(
        normalized_email__in=normalized_emails
    )
    existing: dict[str, AccountInvitation] = {}
    for invitation in invitations:
        normalized = normalize_invitation_email(invitation.email)
        if normalized:
            # Legacy rows may carry mixed-case emails; keep matching keyed by
            # normalized lowercase email so CSV imports remain case-insensitive.
            existing[normalized] = invitation
    return existing


def send_organization_claim_invitation(
    *,
    organization: Organization,
    actor_username: str,
    recipient_email: str,
    now: timezone.datetime,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: list[str] | None = None,
) -> tuple[str, AccountInvitation | None]:
    normalized_email = normalize_invitation_email(recipient_email)
    if not normalized_email:
        return "invalid_email", None

    try:
        validate_email(normalized_email)
    except ValidationError:
        return "invalid_email", None

    invitation, _created = AccountInvitation.objects.get_or_create(
        email=normalized_email,
        defaults={
            "invited_by_username": actor_username,
            "email_template_name": settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
            "organization": organization,
        },
    )

    if invitation.organization_id is not None and invitation.organization_id != organization.pk:
        return "conflict", invitation

    invitation.organization = organization
    invitation.invited_by_username = actor_username
    invitation.email_template_name = settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME
    invitation.save(update_fields=["organization", "invited_by_username", "email_template_name"])

    result = _send_account_invitation_email(
        invitation=invitation,
        actor_username=actor_username,
        template_name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
        cc=cc,
        bcc=bcc,
        reply_to=reply_to,
        now=now,
    )
    return result, invitation


def _resend_invitation(
    *,
    invitation: AccountInvitation,
    actor_username: str,
    template_names: list[str],
    now: timezone.datetime,
) -> str:
    if invitation.organization_id is None:
        matches = find_account_invitation_matches(invitation.email)
        if matches:
            _mark_invitation_accepted_from_email_match(
                invitation=invitation,
                matched_usernames=matches,
                actor_username=actor_username,
                now=now,
            )
            return "accepted"

    if not template_names:
        return "template_missing"

    template_name, template_error = resolve_invitation_template_selection(
        template_names=template_names,
        selected_name=invitation.email_template_name,
        allow_default=True,
    )
    if template_error == "template_invalid":
        return "template_invalid"
    if template_error in {"template_unavailable", "no_templates"}:
        return "template_missing"

    return _send_account_invitation_email(
        invitation=invitation,
        actor_username=actor_username,
        template_name=template_name,
        now=now,
    )


class AccountInvitationUploadForm(StyledForm):
    csv_file = forms.FileField(required=True)


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def account_invitations_refresh(request: HttpRequest) -> HttpResponse:
    actor_username = get_username(request)
    summary = refresh_account_invitations(actor_username=actor_username, now=timezone.now())
    total_checked = summary.pending_checked + summary.accepted_checked
    total_updated = summary.pending_updated + summary.accepted_updated
    if total_checked <= 0:
        messages.info(request, "No invitations to refresh.")
    else:
        messages.success(
            request,
            (
                "Refreshed "
                f"{summary.pending_checked} pending and {summary.accepted_checked} accepted invitations; "
                f"updated {total_updated}."
            ),
        )
    return redirect("account-invitations")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def account_invitations_upload(request: HttpRequest) -> HttpResponse:
    template_names = bulk_invitation_template_names()
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
                    form.add_error("csv_file", "CSV file must be UTF-8 encoded.")
                else:
                    try:
                        rows = parse_invitation_csv(text, max_rows=settings.ACCOUNT_INVITATION_MAX_CSV_ROWS)
                    except ValueError as exc:
                        form.add_error("csv_file", str(exc))
                    else:
                        emails = {normalize_invitation_email(row.get("email") or "") for row in rows}
                        existing = _existing_invitations_by_normalized_email(emails)
                        email_map = build_freeipa_email_lookup()

                        def _bulk_lookup(email: str) -> list[str]:
                            normalized = normalize_invitation_email(email)
                            if not normalized:
                                return []
                            if email_map:
                                return sorted(email_map.get(normalized, set()))
                            return find_account_invitation_matches(normalized)

                        classified_rows, counts = classify_invitation_upload_rows(
                            rows,
                            existing_invitations=existing,
                            freeipa_lookup=_bulk_lookup,
                        )
                        preview_rows = [
                            {
                                "email": row.email,
                                "full_name": row.full_name,
                                "note": row.note,
                                "status": row.status,
                                "reason": row.reason,
                                "freeipa_usernames": row.freeipa_usernames,
                                "has_multiple_matches": row.has_multiple_matches,
                            }
                            for row in classified_rows
                        ]

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
@post_only_404
def account_invitations_send(request: HttpRequest) -> HttpResponse:
    if not allow_request(
        scope="account_invitation_bulk_send",
        key_parts=[get_username(request)],
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

    requested_template_name = str(request.POST.get("email_template") or payload.get("template_name") or "").strip()
    if requested_template_name == settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME:
        messages.error(request, "The organization claim template cannot be used for CSV bulk invitations.")
        return redirect("account-invitations")

    template_names = bulk_invitation_template_names()
    template_name, template_error = resolve_invitation_template_selection(
        template_names=template_names,
        selected_name=requested_template_name,
        allow_default=False,
    )
    if template_error == "no_templates":
        messages.error(request, "No invitation templates are configured.")
        return redirect("account-invitations")
    if template_error == "template_invalid":
        messages.error(request, "Select a valid email template.")
        return redirect("account-invitations")
    if template_error == "template_unavailable":
        messages.error(request, "The selected email template is not available.")
        return redirect("account-invitations")

    actor_username = get_username(request)
    now = timezone.now()

    email_map = build_freeipa_email_lookup()
    row_emails = {
        normalize_invitation_email(str(row.get("email") or ""))
        for row in rows
        if isinstance(row, dict)
    }
    existing_invitation_by_email = _existing_invitations_by_normalized_email(row_emails)

    def _bulk_lookup(email: str) -> list[str]:
        normalized = normalize_invitation_email(email)
        if not normalized:
            return []
        if email_map:
            return sorted(email_map.get(normalized, set()))
        return find_account_invitation_matches(normalized)

    def _send_email(invitation: AccountInvitation) -> str:
        return _send_account_invitation_email(
            invitation=invitation,
            actor_username=actor_username,
            template_name=template_name,
            now=now,
        )

    summary = send_account_invitation_rows(
        rows=[row for row in rows if isinstance(row, dict)],
        actor_username=actor_username,
        template_name=template_name,
        now=now,
        existing_invitations=existing_invitation_by_email,
        freeipa_lookup=_bulk_lookup,
        send_email=_send_email,
    )

    invalid = len([row for row in rows if not isinstance(row, dict)]) + summary.invalid

    request.session.pop(_PREVIEW_SESSION_KEY, None)

    logger.info(
        "Account invitation bulk send queued=%s existing=%s invalid=%s duplicate=%s skipped_org_linked=%s config_error=%s failed=%s",
        summary.queued,
        summary.existing,
        invalid,
        summary.duplicate,
        summary.skipped_org_linked,
        summary.config_error,
        summary.failed,
    )

    if summary.queued:
        messages.success(request, f"Queued {summary.queued} invitation(s).")
    if summary.existing:
        messages.info(request, f"Skipped {summary.existing} existing account(s).")
    if summary.skipped_org_linked:
        messages.info(request, f"Skipped {summary.skipped_org_linked} organization-linked invitation row(s).")
    if summary.duplicate:
        messages.info(request, f"Skipped {summary.duplicate} duplicate row(s).")
    if invalid:
        messages.error(request, f"Skipped {invalid} invalid row(s).")
    if summary.config_error:
        messages.error(request, _INVITATION_PUBLIC_BASE_URL_ERROR_MESSAGE)
    if summary.failed:
        messages.error(request, f"Failed to queue {summary.failed} invitation(s).")

    return redirect("account-invitations")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def account_invitations_bulk(request: HttpRequest) -> HttpResponse:
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

    actor_username = get_username(request)
    now = timezone.now()

    if action == "dismiss":
        updated = dismiss_account_invitations(
            invitations=invitations,
            actor_username=actor_username,
            now=now,
        )

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

    template_names = invitation_template_names()
    if not template_names:
        messages.error(request, "No invitation templates are configured.")
        return redirect("account-invitations")

    summary = summarize_resend_results(
        _resend_invitation(
            invitation=invitation,
            actor_username=actor_username,
            template_names=template_names,
            now=now,
        )
        for invitation in invitations
    )

    if summary.queued:
        messages.success(request, f"Resent {summary.queued} invitation(s).")
    if summary.accepted:
        messages.info(request, f"Skipped {summary.accepted} already accepted invitation(s).")
    if summary.failed:
        messages.error(request, f"Failed to resend {summary.failed} invitation(s).")
    if summary.config_error:
        messages.error(request, _INVITATION_PUBLIC_BASE_URL_ERROR_MESSAGE)
    if summary.template_error:
        messages.error(request, "One or more invitations could not be resent due to template configuration.")

    return redirect("account-invitations")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def account_invitation_resend(request: HttpRequest, invitation_id: int) -> HttpResponse:
    if not allow_request(
        scope="account_invitation_resend",
        key_parts=[get_username(request), invitation_id],
        limit=settings.ACCOUNT_INVITATION_RESEND_LIMIT,
        window_seconds=settings.ACCOUNT_INVITATION_RESEND_WINDOW_SECONDS,
    ):
        messages.error(request, "Too many resend attempts. Try again shortly.")
        return redirect("account-invitations")

    invitation = get_object_or_404(AccountInvitation, pk=invitation_id)
    if invitation.dismissed_at is not None:
        messages.error(request, "That invitation has been dismissed.")
        return redirect("account-invitations")

    template_names = invitation_template_names()
    if not template_names:
        messages.error(request, "No invitation templates are configured.")
        return redirect("account-invitations")

    actor_username = get_username(request)
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
    elif result == "config_error":
        messages.error(request, _INVITATION_PUBLIC_BASE_URL_ERROR_MESSAGE)
    elif result == "failed":
        messages.error(request, "Failed to resend the invitation.")
    else:
        messages.error(request, "No invitation templates are configured.")
    return redirect("account-invitations")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
@post_only_404
def account_invitation_dismiss(request: HttpRequest, invitation_id: int) -> HttpResponse:
    invitation = get_object_or_404(AccountInvitation, pk=invitation_id)
    now = timezone.now()
    dismiss_account_invitations(
        invitations=[invitation],
        actor_username=get_username(request),
        now=now,
    )

    messages.success(request, "Invitation dismissed.")
    return redirect("account-invitations")


@permission_required(ASTRA_ADD_MEMBERSHIP, login_url=reverse_lazy("users"))
def account_invitations_vue(request: HttpRequest) -> HttpResponse:
    """
    Vue 3 version of the invitations list page.
    Serves bootstrap data via data-* attributes for Vue hydration.
    """
    return render(
        request,
        "core/account_invitations_vue.html",
        {
            "can_manage_invitations": True,  # Already checked by permission_required
            "can_create_invitations": True,
            "invitation_id_sentinel": "123456789",  # Placeholder for API URL templates
        },
    )

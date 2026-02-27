import csv
import io
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpRequest, HttpResponse, JsonResponse, QueryDict
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from post_office.models import EmailTemplate

from core.email_context import system_email_context, user_email_context_from_user
from core.forms_base import StyledForm
from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
from core.membership_notes import add_note
from core.models import MembershipRequest, Organization
from core.permissions import ASTRA_ADD_SEND_MAIL, json_permission_required
from core.rate_limit import allow_request
from core.templated_email import (
    create_email_template_unique,
    preview_drop_inline_image_tags,
    preview_rewrite_inline_image_tags_to_urls,
    queue_composed_email,
    render_templated_email_preview,
    render_templated_email_preview_response,
    update_email_template,
)
from core.views_account_invitations import send_organization_claim_invitation
from core.views_utils import get_username

logger = logging.getLogger(__name__)


_CSV_SESSION_KEY = "send_mail_csv_payload_v1"
_PREVIEW_CONTEXT_SESSION_KEY = "send_mail_preview_first_context_v1"


@dataclass(frozen=True)
class RecipientPreview:
    variables: list[tuple[str, str]]
    recipient_count: int
    first_context: dict[str, str]


def _best_example_context(*, recipients: list[dict[str, str]], var_names: list[str]) -> dict[str, str]:
    if not recipients:
        return {var: f"-{var}-" for var in var_names}

    best: dict[str, str] = recipients[0]
    best_score = -1

    for ctx in recipients:
        score = 0
        for var in var_names:
            if str(ctx.get(var, "") or "").strip():
                score += 1
        if score > best_score:
            best = ctx
            best_score = score
            if best_score >= len(var_names):
                break

    filled = dict(best)
    for var in var_names:
        value = str(filled.get(var, "") or "").strip()
        if not value:
            filled[var] = f"-{var}-"
    return filled


def _preview_from_recipients(*, recipients: list[dict[str, str]], var_names: list[str]) -> RecipientPreview:
    example_context = _best_example_context(recipients=recipients, var_names=var_names)
    variables = [(v, str(example_context.get(v, ""))) for v in var_names]
    return RecipientPreview(
        variables=variables,
        recipient_count=len(recipients),
        first_context=example_context,
    )


def _normalize_identifier(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z]+", "_", str(value or "").strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        return "field"
    if normalized[0].isdigit():
        return f"field_{normalized}"
    return normalized


def _unique_identifiers(names: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: dict[str, int] = {}
    for raw in names:
        base = _normalize_identifier(raw)
        n = seen.get(base, 0)
        if n == 0:
            out.append(base)
        else:
            out.append(f"{base}_{n + 1}")
        seen[base] = n + 1
    return out


def _extra_context_from_query(query: QueryDict) -> dict[str, str]:
    reserved = {
        "template",
        "type",
        "to",
        "cc",
        "reply_to",
        "action_status",
        "invitation_action",
        "invitation_org_id",
    }

    raw_items: list[tuple[str, str]] = []
    for key, values in query.lists():
        skey = str(key or "").strip()
        if not skey or skey in reserved:
            continue
        cleaned_values = [str(v or "").strip() for v in values]
        joined = ", ".join([v for v in cleaned_values if v])
        if not joined:
            continue
        raw_items.append((skey, joined))

    extra: dict[str, str] = {}
    used: set[str] = set()
    for key, value in raw_items:
        base = _normalize_identifier(key)
        candidate = base
        n = 2
        while candidate in used:
            candidate = f"{base}_{n}"
            n += 1
        used.add(candidate)
        extra[candidate] = value

    return extra


def _apply_extra_context(
    *,
    preview: RecipientPreview | None,
    recipients: list[dict[str, str]],
    extra_context: dict[str, str],
) -> tuple[RecipientPreview | None, list[dict[str, str]]]:
    if not extra_context:
        return preview, recipients

    merged_recipients: list[dict[str, str]] = []
    for recipient in recipients:
        merged = dict(recipient)
        for k, v in extra_context.items():
            # Do not override recipient-provided values.
            if k not in merged:
                merged[k] = v
        merged_recipients.append(merged)

    base_var_names: list[str]
    if preview is not None and preview.variables:
        base_var_names = [v for v, _example in preview.variables]
    elif recipients:
        base_var_names = list(recipients[0].keys())
    else:
        base_var_names = []

    var_names = list(base_var_names)
    for v in extra_context.keys():
        if v not in var_names:
            var_names.append(v)

    if preview is None:
        return preview, merged_recipients

    if not merged_recipients:
        example_context = {v: str(extra_context.get(v) or f"-{v}-") for v in var_names}
        variables = [(v, str(example_context.get(v, ""))) for v in var_names]
        return (
            RecipientPreview(variables=variables, recipient_count=0, first_context=example_context),
            merged_recipients,
        )

    return _preview_from_recipients(recipients=merged_recipients, var_names=var_names), merged_recipients


def _preview_for_group(group_cn: str) -> tuple[RecipientPreview, list[dict[str, str]]]:
    group = FreeIPAGroup.get(group_cn)
    if group is None:
        raise ValueError("Group not found.")

    usernames = sorted(group.member_usernames_recursive(), key=str.lower)
    recipients: list[dict[str, str]] = []
    for username in usernames:
        user = FreeIPAUser.get(username)
        if user is None:
            continue
        ctx = user_email_context_from_user(user=user)
        if not ctx["email"].strip():
            continue
        recipients.append(ctx)

    var_names = ["username", "first_name", "last_name", "email", "full_name"]
    preview = _preview_from_recipients(recipients=recipients, var_names=var_names)
    return preview, recipients


def _detect_csv_email_var(var_names: list[str]) -> str | None:
    for v in var_names:
        if v == "email":
            return v
    return None


def _parse_csv_upload(file_obj) -> tuple[RecipientPreview, list[dict[str, str]], dict[str, str]]:
    raw = file_obj.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    sio = io.StringIO(text)
    dict_reader = csv.DictReader(sio)

    if not dict_reader.fieldnames:
        raise ValueError("CSV is empty.")

    headers = [str(h or "").strip() for h in dict_reader.fieldnames]
    if not any(headers):
        raise ValueError("CSV header row is missing.")

    var_names = _unique_identifiers(headers)
    header_to_var = {h: v for h, v in zip(headers, var_names, strict=False)}

    email_var = _detect_csv_email_var(var_names)
    if email_var is None:
        raise ValueError("CSV must contain an Email column.")

    recipients: list[dict[str, str]] = []
    for row in dict_reader:
        ctx: dict[str, str] = {}
        for header, value in (row or {}).items():
            if header is None:
                continue
            var = header_to_var.get(str(header).strip())
            if not var:
                continue
            ctx[var] = str(value or "").strip()

        if not ctx.get(email_var, "").strip():
            continue
        recipients.append(ctx)

    preview = _preview_from_recipients(recipients=recipients, var_names=var_names)
    return preview, recipients, header_to_var


def _preview_from_csv_session_payload(payload: dict[str, object]) -> tuple[RecipientPreview, list[dict[str, str]]]:
    recipients_raw = payload.get("recipients")
    if not isinstance(recipients_raw, list):
        raise ValueError("Saved CSV recipients are unavailable.")

    recipients: list[dict[str, str]] = []
    for item in recipients_raw:
        if not isinstance(item, dict):
            continue
        recipients.append({str(k): str(v or "").strip() for k, v in item.items()})

    if not recipients:
        preview = RecipientPreview(variables=[], recipient_count=0, first_context={})
        return preview, recipients

    first = recipients[0]

    # Preserve variable order if we have a mapping; otherwise show keys from first row.
    header_to_var_raw = payload.get("header_to_var")
    if isinstance(header_to_var_raw, dict):
        ordered_vars = list(header_to_var_raw.values())
    else:
        ordered_vars = list(first.keys())

    # De-dup while preserving order.
    seen: set[str] = set()
    var_names: list[str] = []
    for v in ordered_vars:
        sv = str(v)
        if not sv or sv in seen:
            continue
        seen.add(sv)
        var_names.append(sv)

    preview = _preview_from_recipients(recipients=recipients, var_names=var_names)
    return preview, recipients


def _group_select_choices() -> list[tuple[str, str]]:
    groups = FreeIPAGroup.all()
    groups_sorted = sorted(groups, key=lambda g: str(g.cn).lower())
    choices: list[tuple[str, str]] = [("", "(Select a group)")]
    for g in groups_sorted:
        cn = str(g.cn or "").strip()
        if not cn:
            continue
        label = cn
        if str(g.description or "").strip():
            label = f"{cn} — {g.description}"
        choices.append((cn, label))
    return choices


def _user_select_choices() -> list[tuple[str, str]]:
    users = FreeIPAUser.all()
    users_sorted = sorted(users, key=lambda u: str(u.username).lower())
    choices: list[tuple[str, str]] = []
    for u in users_sorted:
        username = str(u.username or "").strip()
        if not username:
            continue
        full_name = str(u.full_name or "").strip()
        if full_name and full_name.lower() != username.lower():
            label = f"{full_name} ({username})"
        else:
            label = username
        choices.append((username, label))
    return choices


def _parse_username_list(raw: str) -> list[str]:
    items = [s.strip() for s in str(raw or "").split(",")]
    out: list[str] = []
    for item in items:
        if item:
            out.append(item)
    return out


def _parse_email_list(raw: str) -> list[str]:
    # Be liberal in what we accept here: users often paste addresses
    # separated by commas, whitespace/newlines, or semicolons.
    tokens = re.split(r"[,\s;]+", str(raw or "").strip())
    emails: list[str] = []
    for token in tokens:
        if not token:
            continue
        validate_email(token)
        emails.append(token)
    return emails


class SendMailForm(StyledForm):
    RECIPIENT_MODE_GROUP = "group"
    RECIPIENT_MODE_USERS = "users"
    RECIPIENT_MODE_CSV = "csv"
    RECIPIENT_MODE_MANUAL = "manual"

    recipient_mode = forms.ChoiceField(
        required=False,
        choices=[
            (RECIPIENT_MODE_GROUP, "Group"),
            (RECIPIENT_MODE_USERS, "Users"),
            (RECIPIENT_MODE_CSV, "CSV"),
            (RECIPIENT_MODE_MANUAL, "Manual"),
        ],
    )

    group_cn = forms.ChoiceField(required=False, choices=[], widget=forms.Select(attrs={"class": "form-control"}))

    user_usernames = forms.MultipleChoiceField(
        required=False,
        choices=[],
        widget=forms.SelectMultiple(attrs={"class": "form-control alx-select2", "multiple": "multiple"}),
    )
    csv_file = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".csv,text/csv"}),
    )

    manual_to = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "clara@example.com, alex@example.com",
            }
        ),
    )

    cc = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "cc1@example.com, cc2@example.com"}),
    )
    bcc = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "bcc1@example.com, bcc2@example.com"}),
    )
    reply_to = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "replies@example.com, support@example.com"}
        ),
    )

    email_template_id = forms.IntegerField(required=False)
    subject = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    html_content = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 12, "class": "form-control", "spellcheck": "true"}),
    )
    text_content = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 12, "class": "form-control", "spellcheck": "true"}),
    )

    action = forms.CharField(required=False)
    save_as_name = forms.CharField(required=False)
    invitation_action = forms.CharField(required=False, widget=forms.HiddenInput())
    invitation_org_id = forms.CharField(required=False, widget=forms.HiddenInput())

    extra_context_json = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, group_choices: list[tuple[str, str]] | None = None, **kwargs):
        user_choices: list[tuple[str, str]] | None = kwargs.pop("user_choices", None)
        super().__init__(*args, **kwargs)
        self.fields["group_cn"].choices = group_choices or [("", "(Select a group)")]
        self.fields["user_usernames"].choices = user_choices or []

    def clean_cc(self) -> list[str]:
        try:
            return _parse_email_list(str(self.cleaned_data.get("cc") or ""))
        except Exception as e:
            raise forms.ValidationError(f"Invalid CC address list: {e}") from e

    def clean_bcc(self) -> list[str]:
        try:
            return _parse_email_list(str(self.cleaned_data.get("bcc") or ""))
        except Exception as e:
            raise forms.ValidationError(f"Invalid BCC address list: {e}") from e

    def clean_reply_to(self) -> list[str]:
        try:
            return _parse_email_list(str(self.cleaned_data.get("reply_to") or ""))
        except Exception as e:
            raise forms.ValidationError(f"Invalid Reply-To address list: {e}") from e

    def clean_manual_to(self) -> list[str]:
        try:
            return _parse_email_list(str(self.cleaned_data.get("manual_to") or ""))
        except Exception as e:
            raise forms.ValidationError(f"Invalid manual recipient list: {e}") from e

    def clean_user_usernames(self) -> list[str]:
        raw = self.cleaned_data.get("user_usernames")
        if raw is None:
            return []
        return [str(v) for v in raw]

    def clean_extra_context_json(self) -> dict[str, str]:
        raw = str(self.cleaned_data.get("extra_context_json") or "").strip()
        if not raw:
            return {}

        try:
            parsed = json.loads(raw)
        except Exception as e:
            raise forms.ValidationError(f"Invalid extra context: {e}") from e

        if not isinstance(parsed, dict):
            raise forms.ValidationError("Invalid extra context: expected JSON object")

        out: dict[str, str] = {}
        for k, v in parsed.items():
            key = _normalize_identifier(str(k))
            value = str(v or "").strip()
            if not value:
                continue
            out[key] = value
        return out


def _handle_send_mail_org_claim_invitation(
    *,
    organization_id: int,
    recipient_email: str,
    actor_username: str,
    now: datetime,
) -> tuple[bool, str | None]:
    if not allow_request(
        scope="send_mail_account_invitation_org_claim",
        key_parts=[actor_username, str(organization_id)],
        limit=settings.ACCOUNT_INVITATION_RESEND_LIMIT,
        window_seconds=settings.ACCOUNT_INVITATION_RESEND_WINDOW_SECONDS,
    ):
        return False, "Too many invitation send attempts. Try again shortly."

    organization = Organization.objects.filter(pk=organization_id).first()
    if organization is None:
        return False, "Selected organization was not found."

    result, _invitation = send_organization_claim_invitation(
        organization=organization,
        actor_username=actor_username,
        recipient_email=recipient_email,
        now=now,
    )
    if result == "queued":
        return True, None
    if result == "invalid_email":
        return False, "Invalid invitation recipient email address."
    if result == "conflict":
        return False, "An invitation already exists for this email and is linked to a different organization."
    if result == "config_error":
        return (
            False,
            "Invitation email configuration error: PUBLIC_BASE_URL must be configured to build invitation links.",
        )
    return False, "Failed to queue the organization claim invitation email."


def _preview_for_manual(emails: list[str]) -> tuple[RecipientPreview, list[dict[str, str]]]:
    recipients: list[dict[str, str]] = []
    for email in emails:
        recipients.append(
            {
                "username": "",
                "first_name": "",
                "last_name": "",
                "full_name": "",
                "email": str(email or "").strip(),
            }
        )

    var_names = ["username", "first_name", "last_name", "email", "full_name"]
    preview = _preview_from_recipients(recipients=recipients, var_names=var_names)
    return preview, recipients


def _preview_for_users(usernames: list[str]) -> tuple[RecipientPreview, list[dict[str, str]]]:
    recipients: list[dict[str, str]] = []
    for username in usernames:
        normalized = str(username or "").strip()
        if not normalized:
            continue
        user = FreeIPAUser.get(normalized)
        if user is None:
            continue
        ctx = user_email_context_from_user(user=user)
        if not ctx["email"].strip():
            continue
        recipients.append(ctx)

    var_names = ["username", "first_name", "last_name", "email", "full_name"]
    preview = _preview_from_recipients(recipients=recipients, var_names=var_names)
    return preview, recipients


def _send_mail_templates(*, recipient_mode: str) -> list[EmailTemplate]:
    templates_qs = EmailTemplate.objects.all().order_by("name")
    if recipient_mode == SendMailForm.RECIPIENT_MODE_CSV:
        templates_qs = templates_qs.exclude(name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME)
    return list(templates_qs)


@permission_required(ASTRA_ADD_SEND_MAIL, login_url=reverse_lazy("users"))
def send_mail(request: HttpRequest) -> HttpResponse:
    group_choices = _group_select_choices()
    user_choices = _user_select_choices()

    created_template_id: int | None = None

    preview: RecipientPreview | None = None
    recipients: list[dict[str, str]] = []
    header_to_var: dict[str, str] | None = None

    initial: dict[str, object] = {}
    selected_recipient_mode = ""
    deep_link_autoload_recipients = False
    system_context = system_email_context()
    extra_context = _extra_context_from_query(request.GET)
    extra_context = {**extra_context, **system_context}
    action_status = str(request.POST.get("action_status") or request.GET.get("action_status") or "").strip().lower()
    action_notice = ""
    if action_status:
        action_label = {
            "approved": "approved",
            "accepted": "approved",
            "rejected": "rejected",
            "rfi": "placed on hold",
            "on_hold": "placed on hold",
        }.get(action_status)
        if action_label:
            action_notice = (
                f"This request has already been {action_label}. "
                "No email has been sent yet. It is important to notify the requester, "
                "so please send the custom email now."
            )

    if request.method != "POST":
        template_key = str(request.GET.get("template") or "").strip()
        if template_key:
            selected_template: EmailTemplate | None = None
            if template_key.isdigit():
                selected_template = EmailTemplate.objects.filter(pk=int(template_key)).first()
            if selected_template is None:
                selected_template = EmailTemplate.objects.filter(name=template_key).first()

            if selected_template is None:
                messages.error(request, f"Email template not found: {template_key!r}.")
            else:
                initial.update(
                    {
                        "email_template_id": selected_template.pk,
                        "subject": str(selected_template.subject or ""),
                        "html_content": str(selected_template.html_content or ""),
                        "text_content": str(selected_template.content or ""),
                    }
                )

        prefill_type = str(request.GET.get("type") or "").strip().lower()
        to_raw = str(request.GET.get("to") or "").strip()
        if prefill_type == "csv":
            initial["recipient_mode"] = SendMailForm.RECIPIENT_MODE_CSV
            deep_link_autoload_recipients = True
        elif to_raw:
            if prefill_type == "group":
                initial["recipient_mode"] = SendMailForm.RECIPIENT_MODE_GROUP
                initial["group_cn"] = to_raw
                deep_link_autoload_recipients = True
            elif prefill_type == "manual":
                initial["recipient_mode"] = SendMailForm.RECIPIENT_MODE_MANUAL
                initial["manual_to"] = to_raw
                deep_link_autoload_recipients = True
            elif prefill_type == "users":
                initial["recipient_mode"] = SendMailForm.RECIPIENT_MODE_USERS
                initial["user_usernames"] = _parse_username_list(to_raw)
                deep_link_autoload_recipients = True

        cc_raw = str(request.GET.get("cc") or "").strip()
        if cc_raw:
            initial["cc"] = cc_raw

        reply_to_raw = str(request.GET.get("reply_to") or "").strip()
        if reply_to_raw:
            initial["reply_to"] = reply_to_raw

        invitation_action = str(request.GET.get("invitation_action") or "").strip().lower()
        if invitation_action:
            initial["invitation_action"] = invitation_action

        invitation_org_id = str(request.GET.get("invitation_org_id") or "").strip()
        if invitation_org_id:
            initial["invitation_org_id"] = invitation_org_id

        if extra_context:
            initial["extra_context_json"] = json.dumps(extra_context)

    if request.method == "POST":
        form = SendMailForm(request.POST, request.FILES, group_choices=group_choices, user_choices=user_choices)
        if form.is_valid():
            group_cn = str(form.cleaned_data.get("group_cn") or "").strip()
            csv_file = form.cleaned_data.get("csv_file")
            recipient_mode = str(form.cleaned_data.get("recipient_mode") or "").strip().lower()
            manual_to = form.cleaned_data.get("manual_to") or []
            user_usernames = form.cleaned_data.get("user_usernames") or []

            cc = form.cleaned_data.get("cc") or []
            bcc = form.cleaned_data.get("bcc") or []
            reply_to = form.cleaned_data.get("reply_to") or []

            posted_extra_context = form.cleaned_data.get("extra_context_json") or {}
            posted_extra_context = {**posted_extra_context, **system_context}

            try:
                if recipient_mode == SendMailForm.RECIPIENT_MODE_GROUP:
                    if not group_cn:
                        raise ValueError("Select a group.")
                    preview, recipients = _preview_for_group(group_cn)
                elif recipient_mode == SendMailForm.RECIPIENT_MODE_CSV:
                    if csv_file is not None:
                        preview, recipients, header_to_var = _parse_csv_upload(csv_file)
                        request.session[_CSV_SESSION_KEY] = json.dumps(
                            {
                                "header_to_var": header_to_var,
                                "recipients": recipients,
                            }
                        )
                    else:
                        raw_payload = request.session.get(_CSV_SESSION_KEY)
                        if not raw_payload:
                            raise ValueError("Upload a CSV.")
                        payload = json.loads(str(raw_payload))
                        if not isinstance(payload, dict):
                            raise ValueError("Upload a CSV.")
                        preview, recipients = _preview_from_csv_session_payload(payload)
                elif recipient_mode == SendMailForm.RECIPIENT_MODE_MANUAL:
                    if not manual_to:
                        raise ValueError("Add one or more recipient email addresses.")
                    preview, recipients = _preview_for_manual(list(manual_to))
                elif recipient_mode == SendMailForm.RECIPIENT_MODE_USERS:
                    if not user_usernames:
                        raise ValueError("Select one or more users.")
                    preview, recipients = _preview_for_users(list(user_usernames))
                else:
                    raise ValueError("Choose Group, Users, CSV, or Manual recipients.")
            except ValueError as e:
                messages.error(request, str(e))
                preview = None
                recipients = []

            preview, recipients = _apply_extra_context(
                preview=preview,
                recipients=recipients,
                extra_context=posted_extra_context,
            )
            if preview and preview.first_context:
                request.session[_PREVIEW_CONTEXT_SESSION_KEY] = json.dumps(preview.first_context)

            action = str(form.cleaned_data.get("action") or "").strip().lower()
            subject = str(form.cleaned_data.get("subject") or "")
            html_content = str(form.cleaned_data.get("html_content") or "")
            text_content = str(form.cleaned_data.get("text_content") or "")
            invitation_action = str(form.cleaned_data.get("invitation_action") or "").strip().lower()
            invitation_org_id = str(form.cleaned_data.get("invitation_org_id") or "").strip()

            selected_template_id = form.cleaned_data.get("email_template_id")
            selected_template = None
            if selected_template_id:
                selected_template = EmailTemplate.objects.filter(pk=selected_template_id).first()

            if (
                recipient_mode == SendMailForm.RECIPIENT_MODE_CSV
                and selected_template is not None
                and selected_template.name == settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME
            ):
                messages.error(request, "The organization claim template cannot be used with CSV recipients.")
                action = ""

            if action == "save" and selected_template is not None:
                update_email_template(
                    template=selected_template,
                    subject=subject,
                    html_content=html_content,
                    text_content=text_content,
                )
                messages.success(request, f"Saved template: {selected_template.name}.")
            elif action == "save" and selected_template is None:
                messages.error(request, "Select a template to save, or use Save as.")

            if action == "save_as":
                raw_name = str(form.cleaned_data.get("save_as_name") or "").strip()
                if not raw_name:
                    messages.error(request, "Provide a template name for Save as.")
                else:
                    selected_template = create_email_template_unique(
                        raw_name=raw_name,
                        subject=subject,
                        html_content=html_content,
                        text_content=text_content,
                    )
                    messages.success(request, f"Created template: {selected_template.name}.")
                    created_template_id = selected_template.pk

            if action == "send":
                if preview is None or not recipients:
                    messages.error(request, "No recipients to send to.")
                elif invitation_action == "org_claim":
                    if recipient_mode != SendMailForm.RECIPIENT_MODE_MANUAL:
                        messages.error(request, "Organization claim invitations require manual recipients.")
                    elif len(manual_to) != 1:
                        messages.error(request, "Organization claim invitations require exactly one recipient email.")
                    elif not invitation_org_id.isdigit():
                        messages.error(request, "Organization claim invitation is missing organization context.")
                    else:
                        recipient_email = str(manual_to[0]).strip()
                        try:
                            validate_email(recipient_email)
                        except ValidationError:
                            messages.error(request, "Invalid invitation recipient email address.")
                        else:
                            actor_username = get_username(request)
                            if not actor_username:
                                messages.error(request, "Unable to determine the acting username.")
                            else:
                                success, error_message = _handle_send_mail_org_claim_invitation(
                                    organization_id=int(invitation_org_id),
                                    recipient_email=recipient_email,
                                    actor_username=actor_username,
                                    now=timezone.now(),
                                )
                                if success:
                                    messages.success(request, "Queued 1 email.")
                                    action_notice = ""
                                    action_status = ""
                                else:
                                    messages.error(request, error_message or "Failed to queue the invitation email.")
                else:
                    raw_request_id = str(posted_extra_context.get("membership_request_id") or "").strip()
                    membership_request = None
                    if raw_request_id.isdigit():
                        membership_request = MembershipRequest.objects.filter(pk=int(raw_request_id)).first()

                    email_kind = ""
                    if action_status in {"approved", "accepted"}:
                        email_kind = "approved"
                    elif action_status == "rejected":
                        email_kind = "rejected"
                    elif action_status in {"rfi", "on_hold"}:
                        email_kind = "rfi"
                    elif membership_request is not None:
                        email_kind = "custom"

                    sent = 0
                    failures = 0
                    first_template_error: Exception | None = None
                    for recipient in recipients:
                        to_email = str(recipient.get("email") or "").strip()
                        if not to_email:
                            continue
                        try:
                            queued_email = queue_composed_email(
                                recipients=[to_email],
                                sender=settings.DEFAULT_FROM_EMAIL,
                                subject_source=subject,
                                text_source=text_content,
                                html_source=html_content,
                                context=recipient,
                                cc=cc,
                                bcc=bcc,
                                reply_to=reply_to,
                            )

                            raw_election_id = recipient.get("election_id")
                            if raw_election_id is not None:
                                try:
                                    queued_email.context = {"election_id": int(raw_election_id)}
                                    queued_email.save(update_fields=["context"])
                                except (TypeError, ValueError):
                                    pass

                            sent += 1

                            if membership_request is not None:
                                try:
                                    add_note(
                                        membership_request=membership_request,
                                        username=get_username(request),
                                        action={
                                            "type": "contacted",
                                            "kind": email_kind,
                                            "email_id": queued_email.id,
                                        },
                                    )
                                except Exception:
                                    logger.exception(
                                        "Send mail email-note failed membership_request_id=%s",
                                        raw_request_id,
                                    )
                        except Exception as exc:
                            if first_template_error is None:
                                first_template_error = exc
                            failures += 1
                            logger.exception("Send mail failed email=%s", to_email)

                    if first_template_error is not None and sent == 0:
                        messages.error(request, f"Template error: {first_template_error}")

                    if sent:
                        request.session.pop(_CSV_SESSION_KEY, None)
                        request.session.pop(_PREVIEW_CONTEXT_SESSION_KEY, None)
                        messages.success(request, f"Queued {sent} email{'s' if sent != 1 else ''}.")
                    if failures:
                        messages.error(request, f"Failed to queue {failures} email{'s' if failures != 1 else ''}.")
                    if sent or failures:
                        # Clear the reminder once we've queued at least one email.
                        action_notice = ""
                        action_status = ""

            # Re-render the page with current field values.
            initial.update(
                {
                    "recipient_mode": recipient_mode,
                    "group_cn": group_cn,
                    "user_usernames": list(user_usernames),
                    "manual_to": ", ".join(manual_to),
                    "cc": ", ".join(cc),
                    "bcc": ", ".join(bcc),
                    "reply_to": ", ".join(reply_to),
                    "email_template_id": selected_template.pk if selected_template else selected_template_id,
                    "subject": subject,
                    "html_content": html_content,
                    "text_content": text_content,
                    "extra_context_json": json.dumps(posted_extra_context) if posted_extra_context else "",
                    "invitation_action": invitation_action,
                    "invitation_org_id": invitation_org_id,
                }
            )

            selected_recipient_mode = recipient_mode

            # The template dropdown uses form.data/form.initial (not a bound field), so
            # keep form.initial in sync with our computed state.
            form.initial.update(initial)
        else:
            messages.error(request, "Fix the form errors and try again.")
            initial.update(request.POST.dict())
            if "user_usernames" in request.POST:
                initial["user_usernames"] = request.POST.getlist("user_usernames")
            form.initial.update(initial)
    else:
        form = SendMailForm(initial=initial, group_choices=group_choices, user_choices=user_choices)
        selected_recipient_mode = str(initial.get("recipient_mode") or "").strip().lower()

        # Deep-links should be able to preconfigure and immediately load recipients.
        # This avoids requiring an extra POST just to see counts/variables.
        if deep_link_autoload_recipients:
            try:
                recipient_mode = str(initial.get("recipient_mode") or "").strip().lower()
                if recipient_mode == SendMailForm.RECIPIENT_MODE_GROUP:
                    group_cn = str(initial.get("group_cn") or "").strip()
                    if not group_cn:
                        raise ValueError("Select a group.")
                    preview, recipients = _preview_for_group(group_cn)
                elif recipient_mode == SendMailForm.RECIPIENT_MODE_MANUAL:
                    manual_to_raw = str(initial.get("manual_to") or "")
                    manual_to = _parse_email_list(manual_to_raw)
                    if not manual_to:
                        raise ValueError("Add one or more recipient email addresses.")
                    preview, recipients = _preview_for_manual(manual_to)
                elif recipient_mode == SendMailForm.RECIPIENT_MODE_USERS:
                    raw_usernames = initial.get("user_usernames")
                    if isinstance(raw_usernames, list):
                        usernames = [str(u) for u in raw_usernames]
                    else:
                        usernames = _parse_username_list(str(raw_usernames or ""))
                    if not usernames:
                        raise ValueError("Select one or more users.")
                    preview, recipients = _preview_for_users(usernames)
                elif recipient_mode == SendMailForm.RECIPIENT_MODE_CSV:
                    raw_payload = request.session.get(_CSV_SESSION_KEY)
                    if not raw_payload:
                        raise ValueError("Upload a CSV.")
                    payload = json.loads(str(raw_payload))
                    if not isinstance(payload, dict):
                        raise ValueError("Saved CSV recipients are unavailable.")
                    preview, recipients = _preview_from_csv_session_payload(payload)
                else:
                    preview = None
                    recipients = []
            except ValueError as e:
                messages.error(request, str(e))
                preview = None
                recipients = []

            preview, recipients = _apply_extra_context(
                preview=preview,
                recipients=recipients,
                extra_context=extra_context,
            )

            if preview and preview.first_context:
                request.session[_PREVIEW_CONTEXT_SESSION_KEY] = json.dumps(preview.first_context)

    # Compute templates at the end so any newly-created template is visible
    # immediately after Save as.
    templates = _send_mail_templates(recipient_mode=selected_recipient_mode)

    first_context = preview.first_context if preview else {}

    rendered_preview = {"subject": "", "html": "", "text": ""}
    if first_context and form.is_bound and form.is_valid():
        try:
            rendered_preview.update(
                render_templated_email_preview(
                    subject=str(form.cleaned_data.get("subject") or ""),
                    html_content=preview_rewrite_inline_image_tags_to_urls(
                        str(form.cleaned_data.get("html_content") or "")
                    ),
                    text_content=preview_drop_inline_image_tags(str(form.cleaned_data.get("text_content") or "")),
                    context=first_context,
                )
            )
        except ValueError as e:
            messages.error(request, f"Template error: {e}")

    return render(
        request,
        "core/send_mail.html",
        {
            "form": form,
            "templates": templates,
            "preview": preview,
            "rendered_preview": rendered_preview,
            "csv_session_key": _CSV_SESSION_KEY,
            "has_saved_csv_recipients": bool(request.session.get(_CSV_SESSION_KEY)),
            "created_template_id": created_template_id,
            "selected_recipient_mode": selected_recipient_mode,
            "action_notice": action_notice,
            "action_status": action_status,
        },
    )


@require_POST
@json_permission_required(ASTRA_ADD_SEND_MAIL)
def send_mail_render_preview(request: HttpRequest) -> JsonResponse:
    raw_context = request.session.get(_PREVIEW_CONTEXT_SESSION_KEY)
    if not raw_context:
        return JsonResponse({"error": "Load recipients first."}, status=400)

    try:
        context = json.loads(str(raw_context))
    except Exception:
        return JsonResponse({"error": "Preview context is unavailable."}, status=400)

    if not isinstance(context, dict):
        return JsonResponse({"error": "Preview context is invalid."}, status=400)

    return render_templated_email_preview_response(
        request=request,
        context={str(k): str(v) for k, v in context.items()},
    )

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.http import HttpRequest, JsonResponse
from django.http.response import Http404
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_GET, require_http_methods, require_POST, require_safe
from post_office.models import EmailTemplate

from core.permissions import ASTRA_ADD_ELECTION, ASTRA_ADD_SEND_MAIL, json_permission_required_any
from core.templated_email import (
    create_email_template_unique,
    email_template_to_dict,
    locked_email_template_names,
    placeholder_context_from_sources,
    render_templated_email_preview,
    render_templated_email_preview_response,
    update_email_template,
    validate_email_subject_no_folding,
)

_MANAGE_TEMPLATE_PERMISSIONS: frozenset[str] = frozenset({ASTRA_ADD_ELECTION, ASTRA_ADD_SEND_MAIL})


class EmailTemplateManageForm(forms.Form):
    name = forms.CharField(required=True, widget=forms.TextInput(attrs={"class": "form-control"}))
    description = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    subject = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    html_content = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 12, "class": "form-control", "spellcheck": "true"}),
    )
    text_content = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 12, "class": "form-control", "spellcheck": "true"}),
    )

    def clean_name(self) -> str:
        return str(self.cleaned_data.get("name") or "").strip()

    def clean_description(self) -> str:
        return str(self.cleaned_data.get("description") or "").strip()

    def clean_subject(self) -> str:
        subject = str(self.cleaned_data.get("subject") or "").strip()
        validate_email_subject_no_folding(subject)
        return subject


@require_safe
@permission_required(ASTRA_ADD_SEND_MAIL, login_url=reverse_lazy("users"))
def email_templates(request: HttpRequest):
    templates = list(EmailTemplate.objects.all().order_by("name"))
    return render(
        request,
        "core/email_templates.html",
        {"templates": templates, "locked_names": locked_email_template_names()},
    )


@require_http_methods(["GET", "POST"])
@permission_required(ASTRA_ADD_SEND_MAIL, login_url=reverse_lazy("users"))
def email_template_create(request: HttpRequest):
    rendered_preview = {"html": "", "text": "", "subject": ""}
    available_variables: list[tuple[str, str]] = []

    if request.method == "POST":
        form = EmailTemplateManageForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data["name"]
            if EmailTemplate.objects.filter(name=name).exists():
                form.add_error("name", "A template with this name already exists.")
            else:
                tpl = EmailTemplate.objects.create(
                    name=name,
                    description=form.cleaned_data["description"],
                    subject=str(form.cleaned_data.get("subject") or ""),
                    content=str(form.cleaned_data.get("text_content") or ""),
                    html_content=str(form.cleaned_data.get("html_content") or ""),
                )
                messages.success(request, f"Created template: {tpl.name}.")
                return redirect("email-template-edit", template_id=tpl.pk)
        ctx = placeholder_context_from_sources(
            str(form.data.get("subject") or ""),
            str(form.data.get("html_content") or ""),
            str(form.data.get("text_content") or ""),
        )
        available_variables = list(ctx.items())
        try:
            rendered_preview.update(
                render_templated_email_preview(
                    subject=str(form.data.get("subject") or ""),
                    html_content=str(form.data.get("html_content") or ""),
                    text_content=str(form.data.get("text_content") or ""),
                    context=ctx,
                )
            )
        except ValueError:
            pass
    else:
        form = EmailTemplateManageForm()

    return render(
        request,
        "core/email_template_edit.html",
        {
            "form": form,
            "template": None,
            "compose_templates": [],
            "force_email_template_id": None,
            "rendered_preview": rendered_preview,
            "available_variables": available_variables,
            "is_create": True,
            "is_locked": False,
        },
    )


@require_http_methods(["GET", "POST"])
@permission_required(ASTRA_ADD_SEND_MAIL, login_url=reverse_lazy("users"))
def email_template_edit(request: HttpRequest, template_id: int):
    tpl = EmailTemplate.objects.filter(pk=template_id).first()
    if tpl is None:
        raise Http404("Template not found")

    locked_names = locked_email_template_names()
    is_locked = tpl.name in locked_names

    rendered_preview = {"html": "", "text": "", "subject": ""}
    available_variables: list[tuple[str, str]] = []

    if request.method == "POST":
        form = EmailTemplateManageForm(request.POST)
        if is_locked:
            # A locked template may still be edited, but its identity (name) is fixed by config.
            form.fields["name"].disabled = True
            form.fields["name"].initial = tpl.name
        if form.is_valid():
            name = form.cleaned_data["name"]
            if tpl.name in locked_names and name != tpl.name:
                msg = (
                    "This template is referenced by the app configuration and cannot be renamed."
                    " Update settings (or switch to a different template) first."
                )
                form.add_error("name", msg)
                messages.error(request, msg)
            elif EmailTemplate.objects.exclude(pk=tpl.pk).filter(name=name).exists():
                form.add_error("name", "A template with this name already exists.")
            else:
                tpl.name = name
                tpl.description = form.cleaned_data["description"]
                tpl.subject = str(form.cleaned_data.get("subject") or "")
                tpl.content = str(form.cleaned_data.get("text_content") or "")
                tpl.html_content = str(form.cleaned_data.get("html_content") or "")
                tpl.save(update_fields=["name", "description", "subject", "content", "html_content"])
                messages.success(request, f"Saved template: {tpl.name}.")
                return redirect("email-template-edit", template_id=tpl.pk)

        ctx = placeholder_context_from_sources(
            str(form.data.get("subject") or ""),
            str(form.data.get("html_content") or ""),
            str(form.data.get("text_content") or ""),
        )
        available_variables = list(ctx.items())
        try:
            rendered_preview.update(
                render_templated_email_preview(
                    subject=str(form.data.get("subject") or ""),
                    html_content=str(form.data.get("html_content") or ""),
                    text_content=str(form.data.get("text_content") or ""),
                    context=ctx,
                )
            )
        except ValueError:
            pass
    else:
        form = EmailTemplateManageForm(
            initial={
                "name": tpl.name,
                "description": tpl.description,
                "subject": tpl.subject,
                "text_content": tpl.content,
                "html_content": tpl.html_content,
            }
        )
        if is_locked:
            form.fields["name"].disabled = True
        ctx = placeholder_context_from_sources(tpl.subject, tpl.html_content, tpl.content)
        available_variables = list(ctx.items())
        try:
            rendered_preview.update(
                render_templated_email_preview(
                    subject=str(tpl.subject or ""),
                    html_content=str(tpl.html_content or ""),
                    text_content=str(tpl.content or ""),
                    context=ctx,
                )
            )
        except ValueError:
            pass

    return render(
        request,
        "core/email_template_edit.html",
        {
            "form": form,
            "template": tpl,
            # Only show the current template in the compose dropdown to avoid
            # accidental switching without the proper JS wiring.
            "compose_templates": [tpl],
            "force_email_template_id": tpl.pk,
            "rendered_preview": rendered_preview,
            "available_variables": available_variables,
            "is_create": False,
            "template_delete_url": reverse("email-template-delete", kwargs={"template_id": tpl.pk}),
            "is_locked": is_locked,
        },
    )


@require_POST
@permission_required(ASTRA_ADD_SEND_MAIL, login_url=reverse_lazy("users"))
def email_template_delete(request: HttpRequest, template_id: int):
    tpl = EmailTemplate.objects.filter(pk=template_id).first()
    if tpl is None:
        raise Http404("Template not found")

    if tpl.name in locked_email_template_names():
        messages.error(
            request,
            "This template is referenced by the app configuration or a membership type and cannot be deleted."
            " Update settings (or switch to a different template) first.",
        )
        return redirect("email-templates")

    try:
        name = str(tpl.name)
        tpl.delete()
        messages.success(request, f"Deleted template: {name}.")
    except ProtectedError:
        messages.error(request, "This template is in use and cannot be deleted.")

    return redirect("email-templates")


@require_GET
@json_permission_required_any(_MANAGE_TEMPLATE_PERMISSIONS)
def email_template_json(request: HttpRequest, template_id: int) -> JsonResponse:
    template = EmailTemplate.objects.filter(pk=template_id).first()
    if template is None:
        raise Http404("Template not found")

    return JsonResponse(email_template_to_dict(template))


@require_POST
@permission_required(ASTRA_ADD_SEND_MAIL, login_url=reverse_lazy("users"))
def email_template_render_preview(request: HttpRequest) -> JsonResponse:
    return render_templated_email_preview_response(request=request, context={})


@require_POST
@json_permission_required_any(_MANAGE_TEMPLATE_PERMISSIONS)
def email_template_save(request: HttpRequest) -> JsonResponse:
    template_id_raw = str(request.POST.get("email_template_id") or "").strip()
    if not template_id_raw:
        return JsonResponse({"ok": False, "error": "email_template_id is required"}, status=400)

    try:
        template_id = int(template_id_raw)
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid email_template_id"}, status=400)

    template = EmailTemplate.objects.filter(pk=template_id).first()
    if template is None:
        return JsonResponse({"ok": False, "error": "Template not found"}, status=404)

    try:
        update_email_template(
            template=template,
            subject=str(request.POST.get("subject") or ""),
            html_content=str(request.POST.get("html_content") or ""),
            text_content=str(request.POST.get("text_content") or ""),
        )
    except ValidationError as exc:
        message = exc.messages[0] if exc.messages else str(exc)
        return JsonResponse({"ok": False, "error": message}, status=400)

    return JsonResponse({"ok": True, "id": template.pk, "name": template.name})


@require_POST
@json_permission_required_any(_MANAGE_TEMPLATE_PERMISSIONS)
def email_template_save_as(request: HttpRequest) -> JsonResponse:
    raw_name = str(request.POST.get("name") or "").strip()
    if not raw_name:
        return JsonResponse({"ok": False, "error": "name is required"}, status=400)

    try:
        template = create_email_template_unique(
            raw_name=raw_name,
            subject=str(request.POST.get("subject") or ""),
            html_content=str(request.POST.get("html_content") or ""),
            text_content=str(request.POST.get("text_content") or ""),
        )
    except ValidationError as exc:
        message = exc.messages[0] if exc.messages else str(exc)
        return JsonResponse({"ok": False, "error": message}, status=400)

    return JsonResponse({"ok": True, "id": template.pk, "name": template.name})

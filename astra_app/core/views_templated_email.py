import json

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

from core.forms_base import StyledForm
from core.permissions import ASTRA_ADD_ELECTION, ASTRA_ADD_SEND_MAIL, json_permission_required_any
from core.templated_email import (
    create_email_template_unique,
    email_template_to_dict,
    locked_email_template_names,
    placeholder_context_from_sources,
    render_templated_email_preview,
    render_templated_email_preview_response,
    update_email_template,
    validate_email_subject,
)


def _preview_and_variables(
    *, subject: str, html_content: str, text_content: str
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Compute rendered preview + available variable list from raw template sources.

    Returns (rendered_preview dict, available_variables list). Used by both the
    create and edit views to avoid repeating the same placeholder→render logic.
    """
    ctx = placeholder_context_from_sources(subject, html_content, text_content)
    available_variables = list(ctx.items())
    rendered_preview = {"html": "", "text": "", "subject": ""}
    try:
        rendered_preview.update(
            render_templated_email_preview(
                subject=subject,
                html_content=html_content,
                text_content=text_content,
                context=ctx,
            )
        )
    except ValueError:
        pass
    return rendered_preview, available_variables

_MANAGE_TEMPLATE_PERMISSIONS: frozenset[str] = frozenset({ASTRA_ADD_ELECTION, ASTRA_ADD_SEND_MAIL})


class EmailTemplateManageForm(StyledForm):
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
        try:
            return validate_email_subject(subject)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc


def _serialize_email_template_manage_field(*, bound_field: forms.BoundField) -> dict[str, object]:
    widget = bound_field.field.widget
    if isinstance(widget, forms.Textarea):
        widget_type = "textarea"
    elif isinstance(widget, forms.Select):
        widget_type = "select"
    else:
        widget_type = "text"

    value = bound_field.value()
    attrs = {key: str(attr_value) for key, attr_value in widget.attrs.items() if attr_value is not None}
    return {
        "name": bound_field.name,
        "id": bound_field.id_for_label,
        "widget": widget_type,
        "value": "" if value is None else str(value),
        "required": bool(bound_field.field.required),
        "disabled": bool(bound_field.field.disabled),
        "errors": [str(error) for error in bound_field.errors],
        "attrs": attrs,
    }


def _serialize_email_template_manage_form(*, form: EmailTemplateManageForm) -> dict[str, object]:
    return {
        "is_bound": form.is_bound,
        "non_field_errors": [str(error) for error in form.non_field_errors()],
        "fields": [_serialize_email_template_manage_field(bound_field=field) for field in form],
    }


def _build_email_templates_list_payload() -> dict[str, object]:
    locked_names = locked_email_template_names()
    templates = list(EmailTemplate.objects.all().order_by("name"))
    return {
        "templates": [
            {
                "id": template.pk,
                "name": template.name,
                "description": template.description or "",
                "is_locked": template.name in locked_names,
            }
            for template in templates
        ]
    }


def _build_email_template_editor_payload(
    *,
    form: EmailTemplateManageForm,
    template: EmailTemplate | None,
    is_create: bool,
    is_locked: bool,
    rendered_preview: dict[str, str],
    available_variables: list[tuple[str, str]],
) -> dict[str, object]:
    return {
        "mode": "create" if is_create else "edit",
        "template": None
        if template is None
        else {
            "id": template.pk,
            "name": template.name,
            "description": template.description or "",
            "is_locked": is_locked,
        },
        "form": _serialize_email_template_manage_form(form=form),
        "compose": {
            "selected_template_id": None if template is None else template.pk,
            "template_options": []
            if template is None
            else [
                {
                    "id": template.pk,
                    "name": template.name,
                }
            ],
            "available_variables": [
                {
                    "name": variable_name,
                    "example": example,
                }
                for variable_name, example in available_variables
            ],
            "preview": {
                "subject": str(rendered_preview.get("subject") or ""),
                "html": str(rendered_preview.get("html") or ""),
                "text": str(rendered_preview.get("text") or ""),
            },
        },
    }


def _email_template_edit_url_template() -> str:
    sentinel = 987654321
    return reverse("email-template-edit", kwargs={"template_id": sentinel}).replace(str(sentinel), "__template_id__")


def _email_template_delete_url_template() -> str:
    sentinel = 987654321
    return reverse("email-template-delete", kwargs={"template_id": sentinel}).replace(str(sentinel), "__template_id__")


@require_safe
@permission_required(ASTRA_ADD_SEND_MAIL, login_url=reverse_lazy("users"))
def email_templates(request: HttpRequest):
    return render(
        request,
        "core/email_templates.html",
        {
            "email_templates_api_url": reverse("api-email-templates-detail"),
            "email_template_create_url": reverse("email-template-create"),
            "email_template_edit_url_template": _email_template_edit_url_template(),
            "email_template_delete_url_template": _email_template_delete_url_template(),
            "email_templates_initial_payload": _build_email_templates_list_payload(),
        },
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
        rendered_preview, available_variables = _preview_and_variables(
            subject=str(form.data.get("subject") or ""),
            html_content=str(form.data.get("html_content") or ""),
            text_content=str(form.data.get("text_content") or ""),
        )
    else:
        form = EmailTemplateManageForm()

    return render(
        request,
        "core/email_template_edit.html",
        {
            "is_create": True,
            "email_template_editor_api_url": reverse("api-email-template-create-detail"),
            "email_template_list_url": reverse("email-templates"),
            "email_template_submit_url": reverse("email-template-create"),
            "email_template_preview_url": reverse("email-template-render-preview"),
            "email_template_editor_initial_payload": _build_email_template_editor_payload(
                form=form,
                template=None,
                is_create=True,
                is_locked=False,
                rendered_preview=rendered_preview,
                available_variables=available_variables,
            ),
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

    if request.method == "POST":
        form = EmailTemplateManageForm(request.POST)
        if is_locked:
            # A locked template may still be edited, but its identity (name) is fixed by config.
            form.fields["name"].disabled = True
            form.fields["name"].initial = tpl.name
        if form.is_valid():
            name = form.cleaned_data["name"]
            requested_name = str(request.POST.get("name") or "").strip()
            if tpl.name in locked_names and requested_name and requested_name != tpl.name:
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

        rendered_preview, available_variables = _preview_and_variables(
            subject=str(form.data.get("subject") or ""),
            html_content=str(form.data.get("html_content") or ""),
            text_content=str(form.data.get("text_content") or ""),
        )
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
        rendered_preview, available_variables = _preview_and_variables(
            subject=str(tpl.subject or ""),
            html_content=str(tpl.html_content or ""),
            text_content=str(tpl.content or ""),
        )

    return render(
        request,
        "core/email_template_edit.html",
        {
            "is_create": False,
            "email_template_editor_api_url": reverse("api-email-template-edit-detail", kwargs={"template_id": tpl.pk}),
            "email_template_list_url": reverse("email-templates"),
            "email_template_submit_url": reverse("email-template-edit", kwargs={"template_id": tpl.pk}),
            "email_template_preview_url": reverse("email-template-render-preview"),
            "email_template_delete_url": reverse("email-template-delete", kwargs={"template_id": tpl.pk}),
            "email_template_editor_initial_payload": _build_email_template_editor_payload(
                form=form,
                template=tpl,
                is_create=False,
                is_locked=is_locked,
                rendered_preview=rendered_preview,
                available_variables=available_variables,
            ),
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


@require_GET
@json_permission_required_any(_MANAGE_TEMPLATE_PERMISSIONS)
def email_templates_detail_api(request: HttpRequest) -> JsonResponse:
    return JsonResponse(_build_email_templates_list_payload())


@require_GET
@json_permission_required_any(_MANAGE_TEMPLATE_PERMISSIONS)
def email_template_create_detail_api(request: HttpRequest) -> JsonResponse:
    form = EmailTemplateManageForm()
    return JsonResponse(
        _build_email_template_editor_payload(
            form=form,
            template=None,
            is_create=True,
            is_locked=False,
            rendered_preview={"html": "", "text": "", "subject": ""},
            available_variables=[],
        )
    )


@require_GET
@json_permission_required_any(_MANAGE_TEMPLATE_PERMISSIONS)
def email_template_edit_detail_api(request: HttpRequest, template_id: int) -> JsonResponse:
    template = EmailTemplate.objects.filter(pk=template_id).first()
    if template is None:
        raise Http404("Template not found")

    is_locked = template.name in locked_email_template_names()
    form = EmailTemplateManageForm(
        initial={
            "name": template.name,
            "description": template.description,
            "subject": template.subject,
            "text_content": template.content,
            "html_content": template.html_content,
        }
    )
    if is_locked:
        form.fields["name"].disabled = True

    rendered_preview, available_variables = _preview_and_variables(
        subject=str(template.subject or ""),
        html_content=str(template.html_content or ""),
        text_content=str(template.content or ""),
    )
    return JsonResponse(
        _build_email_template_editor_payload(
            form=form,
            template=template,
            is_create=False,
            is_locked=is_locked,
            rendered_preview=rendered_preview,
            available_variables=available_variables,
        )
    )


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
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

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
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    return JsonResponse({"ok": True, "id": template.pk, "name": template.name})

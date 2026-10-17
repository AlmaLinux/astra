"""JSON descriptions of bound Django forms, for the Vue pages that render them."""

from collections.abc import Callable, Collection

from django import forms

# Widget attrs safe to hand to the browser for plain text-like inputs.
TEXT_INPUT_ATTRS = frozenset(
    {
        "autocomplete",
        "autocapitalize",
        "autocorrect",
        "inputmode",
        "maxlength",
        "minlength",
        "pattern",
        "placeholder",
        "spellcheck",
    }
)

# Most specific first: SelectMultiple is a Select, and most inputs are Inputs.
_WIDGET_KINDS: tuple[tuple[type[forms.Widget], str], ...] = (
    (forms.HiddenInput, "hidden"),
    (forms.CheckboxInput, "checkbox"),
    (forms.PasswordInput, "password"),
    (forms.EmailInput, "email"),
    (forms.Textarea, "textarea"),
    (forms.SelectMultiple, "select-multiple"),
    (forms.Select, "select"),
    (forms.ClearableFileInput, "file"),
)


def widget_kind(widget: forms.Widget) -> str:
    return next((kind for widget_class, kind in _WIDGET_KINDS if isinstance(widget, widget_class)), "text")


def form_field_payload(bound_field: forms.BoundField, *, allowed_attrs: Collection[str] | None = None) -> dict[str, object]:
    """Describe one field: name, id, widget kind, value, flags, errors and HTML attrs.

    The value is the one the widget would render, so password inputs never echo what was typed.
    With allowed_attrs, attrs are the rendered widget's limited to those names; without, the
    widget's declared attrs. Checkboxes add checked and selects add options.
    """
    field = bound_field.field
    widget = field.widget
    kind = widget_kind(widget)
    rendered = widget.get_context(bound_field.html_name, bound_field.value(), bound_field.build_widget_attrs(widget.attrs))[
        "widget"
    ]
    value = rendered["value"]
    if isinstance(widget, forms.widgets.ChoiceWidget) and not widget.allow_multiple_selected:
        # Choice widgets render a list of selected values; a single choice has at most one.
        value = value[0] if value else ""
    attrs = widget.attrs if allowed_attrs is None else {k: v for k, v in rendered["attrs"].items() if k in allowed_attrs}

    payload: dict[str, object] = {
        "name": bound_field.name,
        "id": bound_field.id_for_label,
        "widget": kind,
        "value": "" if value is None else value,
        "required": field.required,
        "disabled": field.disabled,
        "errors": [str(error) for error in bound_field.errors],
        "attrs": {key: str(attr_value) for key, attr_value in attrs.items() if attr_value is not None},
    }
    if kind == "checkbox":
        payload["checked"] = bool(bound_field.value())
    elif kind in {"select", "select-multiple"}:
        payload["options"] = [{"value": str(option_value), "label": str(label)} for option_value, label in field.choices]
    return payload


def form_payload(
    form: forms.BaseForm,
    field_payload: Callable[[forms.BoundField], dict[str, object]] = form_field_payload,
) -> dict[str, object]:
    return {
        "is_bound": form.is_bound,
        "non_field_errors": [str(error) for error in form.non_field_errors()],
        "fields": [field_payload(bound_field) for bound_field in form],
    }

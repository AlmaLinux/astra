"""Shared form base classes for AdminLTE/Bootstrap CSS integration.

All forms that need Bootstrap form-control styling should inherit from
StyledForm (for plain forms) or StyledModelForm (for model forms).
"""


from django import forms


class _StyledFormMixin:
    """Apply AdminLTE-friendly CSS classes to widgets.

    Mixed into both Form and ModelForm bases.
    """

    def _apply_css_classes(self) -> None:
        for _name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(field.widget, forms.ClearableFileInput):
                field.widget.attrs.setdefault("class", "form-control-file")
            elif isinstance(field.widget, forms.SelectMultiple):
                field.widget.attrs.setdefault("class", "form-control")
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "form-control")
                field.widget.attrs.setdefault("spellcheck", "true")
            else:
                field.widget.attrs.setdefault("class", "form-control")

    def _append_css_class(self, class_name: str) -> None:
        for field in self.fields.values():
            current = str(field.widget.attrs.get("class", "")).strip()
            classes = {css for css in current.split(" ") if css}
            classes.add(class_name)
            field.widget.attrs["class"] = " ".join(sorted(classes))

    def _apply_invalid_classes(self) -> None:
        """Mark invalid widgets with is-invalid for AdminLTE/Bootstrap highlighting."""
        for name in self.errors.keys():
            if name not in self.fields:
                continue
            widget = self.fields[name].widget
            css = widget.attrs.get("class", "")
            if "is-invalid" not in css:
                widget.attrs["class"] = (css + " is-invalid").strip()


class StyledForm(_StyledFormMixin, forms.Form):
    """Form base that auto-applies Bootstrap CSS classes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_css_classes()

    def full_clean(self):
        super().full_clean()
        self._apply_invalid_classes()


class StyledModelForm(_StyledFormMixin, forms.ModelForm):
    """ModelForm base that auto-applies Bootstrap CSS classes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_css_classes()

    def full_clean(self):
        super().full_clean()
        self._apply_invalid_classes()

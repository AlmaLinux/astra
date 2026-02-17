# Bootstrap 4.4 form validation standard

This document defines Astra's shared form validation contract.

## Server-side validation (Django)

- New forms must inherit from `core.forms_base.StyledForm` or `core.forms_base.StyledModelForm`.
- Styled bases apply Bootstrap widget classes and append `is-invalid` to widgets with field errors.
- Shared field templates render errors using `.invalid-feedback`.

## Shared form markup

Use the shared form attribute include on forms that should use Bootstrap validation states:

```django
<form method="post" {% include 'core/_form_validation_attrs.html' with form=form %}>
```

This adds:

- `class="needs-validation ..."`
- `novalidate`
- `was-validated` when the bound form is re-rendered after submit

### Optional hook marker

For page-specific client validation, add a marker:

```django
{% include 'core/_form_validation_attrs.html' with form=form validation_hook='membership-mirror' %}
```

## Shared client-side behavior

`core/js/form_validation_bootstrap44.js` is the single client-side entry point.

For opted-in forms (`form.needs-validation[novalidate]`), it:

- runs browser constraint checks via `form.checkValidity()`
- applies `.was-validated` on submit attempts
- blocks submit when invalid
- dispatches `astra:validate-form` for page-specific `setCustomValidity()` hooks
- validates individual fields in real time (blur first, then input/change once touched or invalid)
- applies/removes `.is-invalid` per field (invalid-only UX; no `.is-valid` styling)
- synchronizes required label indicators when field `required` state changes dynamically

## Required field indicators

Shared field includes render a required marker next to labels via `core/_form_field_required_indicator.html`.

- Required fields render a visible `*` marker on initial load.
- Optional fields keep the marker in the DOM but hidden (`d-none`) so dynamic `required` toggles can show it without a full rerender.
- Marker text is paired with an `sr-only` companion for accessibility.

## CSS feedback visibility contract

Real-time validation depends on field-level `.is-invalid` classes.

- `.invalid-feedback` should become visible when the related field has `.is-invalid`, even if the form does not yet have `.was-validated`.
- Input-group variants also surface feedback using the shared `.input-group.is-invalid + .invalid-feedback` rule.

## Field include variants

Use these include templates:

- `_form_field.html`: default wrapper + inner field rendering
- `_form_field_inner.html`: render control + errors/help without outer wrapper
- `_form_field_checkbox.html`: checkbox layout (`form-check`)
- `_form_field_input_group.html`: fields with prefix/suffix input groups
- `_form_field_datalist.html`: text input with datalist options
- `_form_field_widget_fallback.html`: JS-enhanced widget + textarea fallback

## Migration checklist for custom templates

1. Move field rendering to `_form_field*.html` includes where possible.
2. Replace ad-hoc error blocks with `.invalid-feedback`.
3. Opt form into shared validation attrs include.
4. Keep custom rules in page JS with `setCustomValidity()`, and let shared JS handle submit state.

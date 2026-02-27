import json
from dataclasses import dataclass
from enum import StrEnum
from typing import override

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from core.forms_base import StyledForm
from core.membership import (
    get_membership_request_eligibility,
)
from core.membership_constants import MembershipCategoryCode
from core.models import MembershipRequest, MembershipType, MembershipTypeCategory, Organization


class _AnswerKind(StrEnum):
    text = "text"
    url = "url"


@dataclass(frozen=True, slots=True)
class _QuestionSpec:
    name: str
    title: str
    required: bool
    answer_kind: _AnswerKind = _AnswerKind.text
    url_assume_scheme: str | None = None

    @property
    def field_name(self) -> str:
        return f"q_{self.name.lower().replace(' ', '_')}"


class _AssumedSchemeURLField(forms.URLField):
    default_validators = [URLValidator(schemes=["http", "https"])]

    def __init__(
        self,
        *,
        assume_scheme: str,
        **kwargs,
    ) -> None:
        self._assume_scheme = assume_scheme
        super().__init__(**kwargs)

    def to_python(self, value) -> str | None:
        normalized = super().to_python(value)
        if normalized and "://" not in normalized:
            return f"{self._assume_scheme}://{normalized}"
        return normalized


class _HttpURLField(forms.URLField):
    default_validators = [URLValidator(schemes=["http", "https"])]


class MembershipRequestForm(StyledForm):
    _INDIVIDUAL_QUESTIONS: tuple[_QuestionSpec, ...] = (
        _QuestionSpec(
            name="Contributions",
            title=(
                "Please provide a summary of your contributions to the AlmaLinux Community, including links if appropriate."
            ),
            required=True,
        ),
    )

    _MIRROR_QUESTIONS: tuple[_QuestionSpec, ...] = (
        _QuestionSpec(
            name="Domain",
            title="Domain name of the mirror",
            required=True,
            answer_kind=_AnswerKind.url,
            url_assume_scheme="https",
        ),
        _QuestionSpec(
            name="Pull request",
            title="Please provide a link to your pull request on https://github.com/AlmaLinux/mirrors/",
            required=True,
            answer_kind=_AnswerKind.url,
            url_assume_scheme="https",
        ),
        _QuestionSpec(
            name="Additional info",
            title="Please provide any additional information the Membership Committee should know",
            required=False,
        ),
    )

    _SPONSORSHIP_QUESTIONS: tuple[_QuestionSpec, ...] = (
        _QuestionSpec(
            name="Sponsorship details",
            title="Please describe your organization's sponsorship goals and planned community participation.",
            required=True,
        ),
    )

    membership_type = forms.ModelChoiceField(
        queryset=MembershipType.objects.enabled().ordered_for_display(),
        empty_label=None,
        to_field_name="code",
    )

    q_contributions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 6, "spellcheck": "true"}))
    q_domain = forms.CharField(required=False)
    q_pull_request = forms.CharField(required=False)
    q_additional_info = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4, "spellcheck": "true"}))
    q_sponsorship_details = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "spellcheck": "true"}),
    )

    @classmethod
    def _question_spec_by_name(cls) -> dict[str, _QuestionSpec]:
        return {spec.name: spec for spec in cls.all_question_specs()}

    @classmethod
    def _field_for_spec(cls, spec: _QuestionSpec) -> forms.Field:
        if spec.answer_kind != _AnswerKind.url:
            raise ValueError(f"Spec {spec.name!r} is not a URL question")
        if spec.url_assume_scheme:
            # Use a plain text input so browsers don't reject bare domains.
            # We still validate on submit via Django, and add client-side JS validation.
            return _AssumedSchemeURLField(
                required=False,
                assume_scheme=spec.url_assume_scheme,
                widget=forms.TextInput(attrs={"inputmode": "url", "autocomplete": "url"}),
            )
        return _HttpURLField(required=False)

    @classmethod
    def question_specs_for_membership_type(cls, membership_type: MembershipType) -> tuple[_QuestionSpec, ...]:
        category_id = membership_type.category_id
        if category_id == MembershipCategoryCode.mirror:
            return cls._MIRROR_QUESTIONS
        if category_id == MembershipCategoryCode.sponsorship:
            return cls._SPONSORSHIP_QUESTIONS
        return cls._INDIVIDUAL_QUESTIONS

    @classmethod
    def all_question_specs(cls) -> tuple[_QuestionSpec, ...]:
        return (*cls._INDIVIDUAL_QUESTIONS, *cls._MIRROR_QUESTIONS, *cls._SPONSORSHIP_QUESTIONS)

    @staticmethod
    def _category_title(category: MembershipTypeCategory) -> str:
        return str(category.name or "").replace("_", " ").title()

    @classmethod
    def _grouped_choices(
        cls, membership_types: list[MembershipType]
    ) -> list[tuple[str, str] | tuple[str, list[tuple[str, str]]]]:
        if not membership_types:
            return []

        grouped: list[tuple[str, str] | tuple[str, list[tuple[str, str]]]] = []
        current_category_id = ""
        current_category_title = ""
        current_options: list[tuple[str, str]] = []

        def _flush_group() -> None:
            if not current_options:
                return
            if len(current_options) == 1:
                grouped.append(current_options[0])
                return
            grouped.append((current_category_title, list(current_options)))

        for membership_type in membership_types:
            if membership_type.category_id != current_category_id:
                _flush_group()

                current_category_id = membership_type.category_id
                current_category_title = cls._category_title(membership_type.category)
                current_options = []

            current_options.append((membership_type.code, membership_type.name))

        _flush_group()

        return grouped

    @override
    def __init__(self, *args, username: str | None = None, organization: Organization | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        if username is None and organization is None:
            raise ValueError("MembershipRequestForm requires a username or organization.")

        for spec in self.all_question_specs():
            if spec.answer_kind == _AnswerKind.url:
                self.fields[spec.field_name] = self._field_for_spec(spec)
            self.fields[spec.field_name].label = spec.title
            self.fields[spec.field_name].required = False

        self._apply_css_classes()
        self._append_css_class("w-100")

        eligibility = get_membership_request_eligibility(username=username, organization=organization)
        self._blocked_membership_type_codes = eligibility.blocked_membership_type_codes
        self._pending_membership_category_ids = eligibility.pending_membership_category_ids

        membership_type_field = self.fields["membership_type"]
        assert isinstance(membership_type_field, forms.ModelChoiceField)
        base = MembershipType.objects.enabled()
        if organization is None:
            base = base.filter(category__is_individual=True)
        else:
            base = base.filter(category__is_organization=True)

        membership_type_field.queryset = (
            base.exclude(code__in=self._blocked_membership_type_codes)
            .exclude(category_id__in=self._pending_membership_category_ids)
            .ordered_for_display()
        )
        membership_type_field.choices = self._grouped_choices(list(membership_type_field.queryset))
        membership_type_field.widget.attrs["data-category-map"] = json.dumps(
            {membership_type.code: membership_type.category_id for membership_type in membership_type_field.queryset}
        )

    def clean_membership_type(self) -> MembershipType:
        membership_type: MembershipType = self.cleaned_data["membership_type"]
        if membership_type.category_id in self._pending_membership_category_ids:
            raise ValidationError("You already have a pending request in that category.")
        if membership_type.code in self._blocked_membership_type_codes:
            raise ValidationError("You already have a valid membership of that type.")
        return membership_type

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        membership_type: MembershipType | None = cleaned.get("membership_type")
        if membership_type is None:
            return cleaned

        specs = self.question_specs_for_membership_type(membership_type)

        for spec in specs:
            raw = cleaned.get(spec.field_name)
            value = str(raw or "").strip()
            cleaned[spec.field_name] = value
            if spec.required and not value:
                self.add_error(spec.field_name, "This field is required.")

        return cleaned

    def responses(self) -> list[dict[str, str]]:
        membership_type: MembershipType = self.cleaned_data["membership_type"]
        specs = self.question_specs_for_membership_type(membership_type)
        out: list[dict[str, str]] = []
        for spec in specs:
            value = str(self.cleaned_data.get(spec.field_name) or "").strip()
            if value or spec.required:
                out.append({spec.name: value})
        return out


class MembershipRejectForm(forms.Form):
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3, "spellcheck": "true"}))


class MembershipRequestUpdateResponsesForm(StyledForm):
    def __init__(self, *args, membership_request: MembershipRequest, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._question_specs: list[_QuestionSpec] = []

        for item in membership_request.responses or []:
            if not isinstance(item, dict):
                continue
            for question, answer in item.items():
                question_name = str(question)
                if question_name.strip().lower() == "additional information":
                    # Canonicalize legacy casing variants onto one persisted key.
                    question_name = "Additional information"
                spec = _QuestionSpec(name=question_name, title=question_name, required=False)
                if spec.field_name in self.fields:
                    continue

                known = MembershipRequestForm._question_spec_by_name().get(spec.name)
                if known is not None and known.answer_kind == _AnswerKind.url:
                    field = MembershipRequestForm._field_for_spec(known)
                else:
                    field = forms.CharField(
                        required=False,
                        widget=forms.Textarea(attrs={"rows": 4}),
                    )
                field.label = spec.title
                field.initial = str(answer or "")
                self.fields[spec.field_name] = field
                self._question_specs.append(spec)

        # Always provide a place for clarifications; reuses the same field name
        # so an existing response isn't duplicated.
        extra_spec = _QuestionSpec(name="Additional information", title="Additional information", required=False)
        if extra_spec.field_name not in self.fields:
            self.fields[extra_spec.field_name] = forms.CharField(
                required=False,
                label=extra_spec.title,
                widget=forms.Textarea(attrs={"rows": 4}),
            )
            self._question_specs.append(extra_spec)

        self._apply_css_classes()
        self._append_css_class("w-100")

    def responses(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for spec in self._question_specs:
            value = str(self.cleaned_data.get(spec.field_name) or "").strip()
            if value:
                out.append({spec.name: value})
        return out


class MembershipUpdateExpiryForm(forms.Form):
    expires_on = forms.DateField(required=True, widget=forms.DateInput(attrs={"type": "date"}))

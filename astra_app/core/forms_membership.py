from dataclasses import dataclass
from enum import StrEnum

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Q

from core.membership import (
    get_extendable_membership_type_codes_for_username,
    get_valid_membership_type_codes_for_username,
)
from core.models import MembershipRequest, MembershipType


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


class MembershipRequestForm(forms.Form):
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

    membership_type = forms.ModelChoiceField(
        queryset=MembershipType.objects.filter(enabled=True).order_by("sort_order", "code"),
        empty_label=None,
        to_field_name="code",
    )

    q_contributions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 6, "spellcheck": "true"}))
    q_domain = forms.CharField(required=False)
    q_pull_request = forms.CharField(required=False)
    q_additional_info = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4, "spellcheck": "true"}))

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
        if membership_type.code == "mirror":
            return cls._MIRROR_QUESTIONS
        return cls._INDIVIDUAL_QUESTIONS

    @classmethod
    def all_question_specs(cls) -> tuple[_QuestionSpec, ...]:
        return (*cls._INDIVIDUAL_QUESTIONS, *cls._MIRROR_QUESTIONS)

    def __init__(self, *args, username: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        for spec in self.all_question_specs():
            if spec.answer_kind == _AnswerKind.url:
                self.fields[spec.field_name] = self._field_for_spec(spec)
            self.fields[spec.field_name].label = spec.title
            self.fields[spec.field_name].required = False

        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control w-100")
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("spellcheck", "true")

        valid_codes = get_valid_membership_type_codes_for_username(username)
        extendable_codes = get_extendable_membership_type_codes_for_username(username)
        self._blocked_membership_type_codes = valid_codes - extendable_codes

        self._pending_membership_type_codes = set(
            MembershipRequest.objects.filter(
                requested_username=username,
                status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
            ).values_list("membership_type_id", flat=True)
        )

        membership_type_field = self.fields["membership_type"]
        assert isinstance(membership_type_field, forms.ModelChoiceField)
        membership_type_field.queryset = (
            MembershipType.objects.filter(enabled=True).filter(Q(isIndividual=True) | Q(code="mirror"))
            .exclude(code__in=self._blocked_membership_type_codes)
            .exclude(code__in=self._pending_membership_type_codes)
            .order_by("sort_order", "code")
        )

    def clean_membership_type(self) -> MembershipType:
        membership_type: MembershipType = self.cleaned_data["membership_type"]
        if membership_type.code in self._blocked_membership_type_codes:
            raise ValidationError("You already have a valid membership of that type.")
        if membership_type.code in self._pending_membership_type_codes:
            raise ValidationError("You already have a pending request of that type.")
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


class MembershipRequestUpdateResponsesForm(forms.Form):
    def __init__(self, *args, membership_request: MembershipRequest, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._question_specs: list[_QuestionSpec] = []

        for item in membership_request.responses or []:
            if not isinstance(item, dict):
                continue
            for question, answer in item.items():
                spec = _QuestionSpec(name=str(question), title=str(question), required=False)
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

        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control w-100")
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("spellcheck", "true")

    def responses(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for spec in self._question_specs:
            value = str(self.cleaned_data.get(spec.field_name) or "").strip()
            if value:
                out.append({spec.name: value})
        return out


class MembershipUpdateExpiryForm(forms.Form):
    expires_on = forms.DateField(required=True, widget=forms.DateInput(attrs={"type": "date"}))

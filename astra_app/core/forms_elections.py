from __future__ import annotations

import datetime
from typing import override

from django import forms
from django.conf import settings
from django.forms import modelformset_factory
from django.utils import timezone

from core.backends import FreeIPAGroup
from core.models import Candidate, Election, ExclusionGroup

_DATETIME_LOCAL_INPUT_FORMATS: list[str] = [
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    *list(settings.DATETIME_INPUT_FORMATS),
]


class ElectionDetailsForm(forms.ModelForm):
    eligible_group_cn = forms.ChoiceField(
        required=False,
        choices=[("", "")],
        widget=forms.Select(
            attrs={
                "class": "form-control alx-select2",
                "data-ajax-url": "/groups/search/",
                "data-placeholder": "(no group restriction)",
            }
        ),
    )

    number_of_seats = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control smallNumber", "min": 1, "step": 1}),
        initial=1,
    )
    quorum = forms.IntegerField(
        min_value=0,
        max_value=100,
        initial=10,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control smallNumber",
                "min": 0,
                "max": 100,
                "step": 1,
            }
        ),
    )
    start_datetime = forms.DateTimeField(
        input_formats=_DATETIME_LOCAL_INPUT_FORMATS,
        widget=forms.DateTimeInput(attrs={"class": "form-control js-datetime-picker", "type": "datetime-local"}),
    )
    end_datetime = forms.DateTimeField(
        input_formats=_DATETIME_LOCAL_INPUT_FORMATS,
        widget=forms.DateTimeInput(attrs={"class": "form-control js-datetime-picker", "type": "datetime-local"}),
    )

    class Meta:
        model = Election
        fields = [
            "name",
            "description",
            "url",
            "start_datetime",
            "end_datetime",
            "number_of_seats",
            "quorum",
            "eligible_group_cn",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3, "spellcheck": "true"}),
            "url": forms.URLInput(attrs={"class": "form-control"}),
        }

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()

        start_dt = cleaned.get("start_datetime")
        end_dt = cleaned.get("end_datetime")

        if isinstance(start_dt, datetime.datetime) and timezone.is_naive(start_dt):
            cleaned["start_datetime"] = timezone.make_aware(start_dt)
        if isinstance(end_dt, datetime.datetime) and timezone.is_naive(end_dt):
            cleaned["end_datetime"] = timezone.make_aware(end_dt)

        start_dt = cleaned.get("start_datetime")
        end_dt = cleaned.get("end_datetime")
        if isinstance(start_dt, datetime.datetime) and isinstance(end_dt, datetime.datetime):
            if end_dt <= start_dt:
                self.add_error("end_datetime", "End must be after start.")

        return cleaned

    @override
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Select2 uses AJAX for options; only include the current selection so the
        # widget renders a value even without preloading all groups.
        raw: str = ""
        if self.is_bound:
            raw = str(self.data.get("eligible_group_cn") or "").strip()
        else:
            raw = str(self.initial.get("eligible_group_cn") or "").strip()
            if not raw and self.instance.pk is not None:
                raw = str(self.instance.eligible_group_cn or "").strip()

        choices: list[tuple[str, str]] = [("", "")]
        if raw:
            choices.append((raw, raw))
        self.fields["eligible_group_cn"].choices = choices

    def clean_eligible_group_cn(self) -> str:
        cn = str(self.cleaned_data.get("eligible_group_cn") or "").strip()
        if not cn:
            return ""

        group = FreeIPAGroup.get(cn)
        if group is None:
            raise forms.ValidationError("Unknown group.", code="unknown_group")

        return cn


class ElectionEndDateForm(forms.ModelForm):
    """Form for extending an election end datetime.

    When an election is open, the edit UI disables other fields; disabled inputs
    are not submitted by browsers, so we validate just end_datetime.
    """

    class Meta:
        model = Election
        fields = ["end_datetime"]
        widgets = {
            "end_datetime": forms.DateTimeInput(
                attrs={"class": "form-control js-datetime-picker", "type": "datetime-local"}
            ),
        }

    end_datetime = forms.DateTimeField(
        input_formats=_DATETIME_LOCAL_INPUT_FORMATS,
        widget=forms.DateTimeInput(attrs={"class": "form-control js-datetime-picker", "type": "datetime-local"}),
    )

    def clean_end_datetime(self) -> datetime.datetime:
        end_dt = self.cleaned_data["end_datetime"]
        if timezone.is_naive(end_dt):
            return timezone.make_aware(end_dt)
        return end_dt


class ElectionVotingEmailForm(forms.Form):
    email_template_id = forms.IntegerField(required=False)

    subject = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control"}))

    html_content = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 10, "spellcheck": "true"}),
    )

    text_content = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 10, "spellcheck": "true"}),
    )


class CandidateWizardForm(forms.ModelForm):
    freeipa_username = forms.ChoiceField(required=True, choices=[], widget=forms.Select(attrs={"class": "form-control alx-select2"}))
    nominated_by = forms.ChoiceField(required=True, choices=[], widget=forms.Select(attrs={"class": "form-control alx-select2"}))

    class Meta:
        model = Candidate
        fields = [
            "freeipa_username",
            "nominated_by",
            "description",
            "url",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2, "spellcheck": "true"}),
            "url": forms.URLInput(attrs={"class": "form-control"}),
        }


CandidateWizardFormSet = modelformset_factory(
    Candidate,
    form=CandidateWizardForm,
    can_delete=True,
    extra=1,
)


class ExclusionGroupWizardForm(forms.ModelForm):
    candidate_usernames = forms.MultipleChoiceField(
        required=False,
        choices=[],
        widget=forms.SelectMultiple(attrs={"class": "form-control", "multiple": "multiple", "size": "6"}),
        help_text="Select candidates included in this group.",
    )

    class Meta:
        model = ExclusionGroup
        fields = [
            "name",
            "max_elected",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Employees of X",
                }
            ),
            "max_elected": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        }


ExclusionGroupWizardFormSet = modelformset_factory(
    ExclusionGroup,
    form=ExclusionGroupWizardForm,
    can_delete=True,
    extra=1,
)

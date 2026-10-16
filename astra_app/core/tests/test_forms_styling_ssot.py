from django import forms
from django.test import TestCase

from core.forms_base import StyledForm, StyledModelForm
from core.forms_elections import (
    CandidateWizardForm,
    ElectionDetailsForm,
    ElectionEndDateForm,
    ElectionVotingEmailForm,
    ExclusionGroupWizardForm,
)
from core.forms_groups import GroupEditForm
from core.forms_membership import MembershipRequestForm, MembershipRequestUpdateResponsesForm
from core.forms_organizations import OrganizationEditForm


class _ValidationProbeForm(StyledForm):
    required_value = forms.CharField(required=True)


class _TextareaProbeForm(StyledForm):
    notes = forms.CharField(max_length=5, widget=forms.Textarea)


class FormsStylingSSOTTests(TestCase):
    def test_textarea_crlf_counts_as_one_character_like_the_browser(self) -> None:
        # Browsers submit textarea line breaks as CRLF but count them as one character
        # for maxlength and the character counter.
        form = _TextareaProbeForm(data={"notes": "ab\r\ncd"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["notes"], "ab\ncd")

    def test_organization_billing_notes_limit_matches_browser_count(self) -> None:
        notes = "\r\n".join(["x" * 99] * 5)  # 499 characters in the browser, 503 submitted
        form = OrganizationEditForm(data={"billing_notes": notes})
        self.assertNotIn("billing_notes", form.errors)
        self.assertEqual(form.fields["billing_notes"].widget.attrs["maxlength"], "500")


    def test_membership_forms_use_shared_styled_form_base(self) -> None:
        self.assertTrue(issubclass(MembershipRequestForm, StyledForm))
        self.assertTrue(issubclass(MembershipRequestUpdateResponsesForm, StyledForm))

    def test_organization_edit_form_uses_shared_styled_model_form_base(self) -> None:
        self.assertTrue(issubclass(OrganizationEditForm, StyledModelForm))

    def test_group_and_election_forms_use_shared_styled_bases(self) -> None:
        self.assertTrue(issubclass(GroupEditForm, StyledForm))
        self.assertTrue(issubclass(ElectionDetailsForm, StyledModelForm))
        self.assertTrue(issubclass(ElectionEndDateForm, StyledModelForm))
        self.assertTrue(issubclass(ElectionVotingEmailForm, StyledForm))
        self.assertTrue(issubclass(CandidateWizardForm, StyledModelForm))
        self.assertTrue(issubclass(ExclusionGroupWizardForm, StyledModelForm))

    def test_styled_form_exposes_bootstrap_validation_classes(self) -> None:
        unbound_form = _ValidationProbeForm()
        self.assertEqual(unbound_form.bootstrap_validation_css_classes, "needs-validation")

        invalid_bound_form = _ValidationProbeForm(data={"required_value": ""})
        self.assertFalse(invalid_bound_form.is_valid())
        self.assertEqual(
            invalid_bound_form.bootstrap_validation_css_classes,
            "needs-validation was-validated",
        )

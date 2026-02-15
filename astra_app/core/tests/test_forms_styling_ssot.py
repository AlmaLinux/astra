from django.test import TestCase

from core.forms_base import StyledForm, StyledModelForm
from core.forms_membership import MembershipRequestForm, MembershipRequestUpdateResponsesForm
from core.forms_organizations import OrganizationEditForm


class FormsStylingSSOTTests(TestCase):
    def test_membership_forms_use_shared_styled_form_base(self) -> None:
        self.assertTrue(issubclass(MembershipRequestForm, StyledForm))
        self.assertTrue(issubclass(MembershipRequestUpdateResponsesForm, StyledForm))

    def test_organization_edit_form_uses_shared_styled_model_form_base(self) -> None:
        self.assertTrue(issubclass(OrganizationEditForm, StyledModelForm))

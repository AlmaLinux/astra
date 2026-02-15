from typing import override

from django import forms

from core.backends import FreeIPAUser
from core.forms_base import StyledModelForm
from core.models import Organization
from core.user_labels import user_choice_from_freeipa


class OrganizationEditForm(StyledModelForm):
    representative = forms.ChoiceField(
        required=False,
        widget=forms.Select(
            attrs={
                "class": "form-control alx-select2",
                "data-placeholder": "Search users…",
            }
        ),
        help_text="Select the user who will be the organization's representative.",
    )

    class Meta:
        model = Organization
        fields = (
            "business_contact_name",
            "business_contact_email",
            "business_contact_phone",
            "pr_marketing_contact_name",
            "pr_marketing_contact_email",
            "pr_marketing_contact_phone",
            "technical_contact_name",
            "technical_contact_email",
            "technical_contact_phone",
            "name",
            "website_logo",
            "website",
            "logo",
        )

        labels = {
            "business_contact_name": "Name",
            "business_contact_email": "Email",
            "business_contact_phone": "Phone",
            "pr_marketing_contact_name": "Name",
            "pr_marketing_contact_email": "Email",
            "pr_marketing_contact_phone": "Phone",
            "technical_contact_name": "Name",
            "technical_contact_email": "Email",
            "technical_contact_phone": "Phone",
            "name": "Organization name",
            "website_logo": "Website logo (URL)",
            "website": "Website URL",
            "logo": "Accounts logo (upload)",
        }

        help_texts = {
            "name": "This is the name we will display publicly for sponsor recognition.",
            "website_logo": "Share a direct link to your logo file, or a link to your brand assets.",
            "website": "Enter the URL you want your logo to link to (homepage or a dedicated landing page).",
        }

    @override
    def __init__(self, *args, **kwargs):
        self.can_select_representatives: bool = bool(kwargs.pop("can_select_representatives", False))
        super().__init__(*args, **kwargs)

        self.fields["business_contact_name"].required = True
        self.fields["business_contact_email"].required = True
        self.fields["pr_marketing_contact_name"].required = True
        self.fields["pr_marketing_contact_email"].required = True
        self.fields["technical_contact_name"].required = True
        self.fields["technical_contact_email"].required = True
        self.fields["name"].required = True
        self.fields["website_logo"].required = True
        self.fields["website"].required = True

        self.fields["website"].widget = forms.URLInput(attrs={"class": "form-control", "placeholder": "https://…"})
        self.fields["website_logo"].widget = forms.URLInput(attrs={"class": "form-control", "placeholder": "https://…"})
        self.fields["business_contact_email"].widget = forms.EmailInput(attrs={"class": "form-control"})
        self.fields["pr_marketing_contact_email"].widget = forms.EmailInput(attrs={"class": "form-control"})
        self.fields["technical_contact_email"].widget = forms.EmailInput(attrs={"class": "form-control"})

        if not self.can_select_representatives:
            # Representative is defaulted to the creator; only membership admins can change.
            del self.fields["representative"]
        else:
            # Select2 uses AJAX, so only include currently-selected value as a choice.
            current = ""
            if self.is_bound:
                current = str(self.data.get("representative") or "").strip()
            else:
                initial = self.initial.get("representative")
                current = str(initial or "").strip()
            self.fields["representative"].choices = [user_choice_from_freeipa(current)] if current else []

    def clean_representative(self) -> str:
        if "representative" not in self.fields:
            return ""

        username = str(self.cleaned_data.get("representative") or "").strip()

        if not username:
            return ""

        if FreeIPAUser.get(username) is None:
            raise forms.ValidationError(
                f"Unknown user: {username}",
                code="unknown_representative",
            )

        # Avoid leaking which org they represent; just state the rule.
        conflict_exists = (
            Organization.objects.filter(representative=username)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if conflict_exists:
            raise forms.ValidationError(
                "That user is already the representative of another organization.",
                code="representative_not_unique",
            )

        return username


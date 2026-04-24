from django.template import Context, Template
from django.test import TestCase

from core.models import Organization


class OrganizationWidgetTemplateTagTests(TestCase):
    def test_organization_widget_requires_precomputed_memberships_mapping(self) -> None:
        org = Organization.objects.create(
            name="ContractOrg",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website_logo="https://example.com/logo",
            website="https://example.com/",
            representative="alice",
        )

        tpl = Template("""{% load core_organization_widget %}{% organization organization %}""")

        with self.assertRaisesMessage(RuntimeError, "organization_memberships_by_id"):
            tpl.render(Context({"organization": org}))

    def test_organization_widget_requires_explicit_membership_entry_for_organization(self) -> None:
        org = Organization.objects.create(
            name="ContractOrg",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website_logo="https://example.com/logo",
            website="https://example.com/",
            representative="alice",
        )

        tpl = Template("""{% load core_organization_widget %}{% organization organization %}""")

        with self.assertRaisesMessage(RuntimeError, "organization_memberships_by_id"):
            tpl.render(
                Context(
                    {
                        "organization": org,
                        "organization_memberships_by_id": {},
                    }
                )
            )
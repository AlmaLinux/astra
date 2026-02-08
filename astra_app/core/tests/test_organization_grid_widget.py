
from django.template import Context, Template
from django.test import RequestFactory, TestCase

from core.models import MembershipType, Organization


class OrganizationGridTemplateTagTests(TestCase):
    def test_organization_grid_renders_building_fallback_and_sponsorship_label(self) -> None:
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member",
                "isOrganization": True,
                "isIndividual": False,
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="ExampleOrg",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            membership_level_id="silver",
            website_logo="https://example.com/logo",
            website="https://example.com/",
            representative="alice",
        )

        request = RequestFactory().get("/organizations/")

        tpl = Template("""{% load core_organization_grid %}{% organization_grid organizations=organizations %}""")
        html = tpl.render(Context({"request": request, "organizations": [org]}))

        self.assertIn("ExampleOrg", html)
        self.assertIn("fa-building", html)
        self.assertIn("badge-pill", html)
        self.assertIn("Silver Sponsor", html)

    def test_organization_grid_paginates(self) -> None:
        orgs: list[Organization] = []
        for i in range(65):
            orgs.append(
                Organization.objects.create(
                    name=f"Org {i:03d}",
                    business_contact_name="Biz",
                    business_contact_email="biz@example.com",
                    pr_marketing_contact_name="PR",
                    pr_marketing_contact_email="pr@example.com",
                    technical_contact_name="Tech",
                    technical_contact_email="tech@example.com",
                    website_logo="https://example.com/logo",
                    website="https://example.com/",
                    representative=f"rep{i:03d}",
                )
            )

        request = RequestFactory().get("/organizations/", {"page": "2"})
        tpl = Template("""{% load core_organization_grid %}{% organization_grid organizations=organizations %}""")
        html = tpl.render(Context({"request": request, "organizations": orgs}))

        self.assertIn("Org 028", html)
        self.assertNotIn("Org 027", html)

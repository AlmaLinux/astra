
import datetime

from django.template import Context, Template
from django.test import RequestFactory, TestCase

from core.models import Membership, MembershipType, Organization


class OrganizationGridTemplateTagTests(TestCase):
    def test_organization_grid_renders_building_fallback_and_sponsorship_label(self) -> None:
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member",
                "category_id": "sponsorship",
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
            website_logo="https://example.com/logo",
            website="https://example.com/",
            representative="alice",
        )

        Membership.objects.create(
            target_organization=org,
            membership_type_id="silver",
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

    def test_organization_grid_hides_expired_memberships(self) -> None:
        from django.utils import timezone

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "description": "Silver Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="ExpiredOrg",
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

        Membership.objects.create(
            target_organization=org,
            membership_type_id="silver",
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )

        request = RequestFactory().get("/organizations/")
        tpl = Template("""{% load core_organization_grid %}{% organization_grid organizations=organizations %}""")
        html = tpl.render(Context({"request": request, "organizations": [org]}))

        self.assertIn("ExpiredOrg", html)
        self.assertIn("No membership", html)
        self.assertNotIn("Silver Sponsor", html)

    def test_organization_grid_orders_membership_pills(self) -> None:
        from core.models import MembershipTypeCategory

        MembershipTypeCategory.objects.update_or_create(
            pk="mirror",
            defaults={
                "is_individual": True,
                "is_organization": True,
                "sort_order": 1,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 2,
            },
        )

        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror Member",
                "description": "Mirror Member",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "description": "Gold Sponsor Member",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="OrderOrg",
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

        Membership.objects.create(target_organization=org, membership_type_id="gold")
        Membership.objects.create(target_organization=org, membership_type_id="mirror")

        request = RequestFactory().get("/organizations/")
        tpl = Template("""{% load core_organization_grid %}{% organization_grid organizations=organizations %}""")
        html = tpl.render(Context({"request": request, "organizations": [org]}))

        mirror_idx = html.find("Mirror Member")
        gold_idx = html.find("Gold Sponsor Member")
        self.assertGreater(mirror_idx, -1)
        self.assertGreater(gold_idx, -1)
        self.assertLess(mirror_idx, gold_idx)

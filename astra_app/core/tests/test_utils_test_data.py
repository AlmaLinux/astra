from django.test import TestCase
from post_office.models import EmailTemplate

from core.models import MembershipTypeCategory
from core.templated_email import configured_email_template_names
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates


class UtilsTestDataTests(TestCase):
    def test_ensure_core_categories_creates_required_rows(self) -> None:
        MembershipTypeCategory.objects.all().delete()

        ensure_core_categories()

        self.assertEqual(
            MembershipTypeCategory.objects.filter(
                name__in=["individual", "mirror", "sponsorship", "contributor", "emeritus"]
            ).count(),
            5,
        )

    def test_ensure_email_templates_creates_required_rows(self) -> None:
        configured_names = sorted(configured_email_template_names())
        EmailTemplate.objects.filter(name__in=configured_names).delete()

        ensure_email_templates()

        self.assertEqual(
            EmailTemplate.objects.filter(name__in=configured_names).count(),
            len(configured_names),
        )

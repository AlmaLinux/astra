
from django.test import TestCase


class OrganizationIntPrimaryKeyTests(TestCase):
    def test_organization_uses_autoincrementing_int_pk(self) -> None:
        from core.models import Organization

        org = Organization.objects.create(name="Example Org", representative="bob")
        self.assertIsInstance(org.pk, int)

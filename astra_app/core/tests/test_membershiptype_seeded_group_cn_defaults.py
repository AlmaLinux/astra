
from django.test import TestCase

from core.models import MembershipType
from core.tests.utils_test_data import ensure_core_categories


class MembershipTypeSeededDefaultsTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        MembershipType.objects.get_or_create(
            pk="individual",
            defaults={
                "name": "Individual Members",
                "group_cn": "individual-members",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.get_or_create(
            pk="mirror",
            defaults={
                "name": "Mirror Members",
                "group_cn": "mirror-members",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

    def test_seeded_individual_and_mirror_types_have_default_group_cn(self) -> None:
        # Without a default mapping, new installs can't submit/approve user membership
        # requests because membership types aren't linked to FreeIPA groups.
        self.assertEqual(
            MembershipType.objects.get(pk="individual").group_cn,
            "individual-members",
        )
        self.assertEqual(
            MembershipType.objects.get(pk="mirror").group_cn,
            "mirror-members",
        )

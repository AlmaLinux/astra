
from django.test import TestCase

from core.models import MembershipType


class MembershipTypeSeededDefaultsTests(TestCase):
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

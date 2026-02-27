
from django.conf import settings
from django.test import TestCase

from core.models import FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_ELECTION


class SeededElectionCommitteePermissionGrantTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
        )

    def test_election_committee_has_add_election_permission_grant(self) -> None:
        self.assertTrue(
            FreeIPAPermissionGrant.objects.filter(
                permission=ASTRA_ADD_ELECTION,
                principal_type="group",
                principal_name=settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            ).exists()
        )

from __future__ import annotations

from django.test import TestCase

from core.models import FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_ELECTION


class SeededElectionCommitteePermissionGrantTests(TestCase):
    def test_election_committee_has_add_election_permission_grant(self) -> None:
        self.assertTrue(
            FreeIPAPermissionGrant.objects.filter(
                permission=ASTRA_ADD_ELECTION,
                principal_type="group",
                principal_name="election-committee",
            ).exists()
        )

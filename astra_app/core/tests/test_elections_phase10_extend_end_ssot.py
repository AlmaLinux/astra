import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import Election, FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_ELECTION


class ElectionsPhase10ExtendEndSSOTTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_both_extend_end_entrypoints_delegate_to_shared_helper(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Extend helper delegation",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get") as get_user,
            patch("core.views_elections.lifecycle._extend_election_end_from_post") as lifecycle_helper,
        ):
            get_user.return_value = FreeIPAUser(
                "admin",
                {
                    "uid": ["admin"],
                    "mail": ["admin@example.com"],
                    "memberof_group": [],
                    "memberofindirect_group": [],
                },
            )
            lifecycle_helper.return_value = SimpleNamespace(success=True, errors=())
            response = self.client.post(
                reverse("election-extend-end", args=[election.id]),
                data={"confirm": election.name, "end_datetime": (now + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")},
            )

        self.assertEqual(response.status_code, 302)
        lifecycle_helper.assert_called_once()

        with (
            patch("core.freeipa.user.FreeIPAUser.get") as get_user,
            patch("core.views_elections.edit._extend_election_end_from_post") as edit_helper,
        ):
            get_user.return_value = FreeIPAUser(
                "admin",
                {
                    "uid": ["admin"],
                    "mail": ["admin@example.com"],
                    "memberof_group": [],
                    "memberofindirect_group": [],
                },
            )
            edit_helper.return_value = SimpleNamespace(success=True, errors=())
            response = self.client.post(
                reverse("election-edit", args=[election.id]),
                data={"action": "extend_end", "end_datetime": (now + datetime.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")},
            )

        self.assertEqual(response.status_code, 302)
        edit_helper.assert_called_once()

import datetime
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from django.conf import settings
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.exceptions import FreeIPAMisconfiguredError
from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
from core.models import (
    AuditLogEntry,
    Candidate,
    Election,
    FreeIPAPermissionGrant,
    Membership,
    MembershipType,
    Organization,
    VotingCredential,
)
from core.permissions import ASTRA_ADD_ELECTION
from core.tests.utils_test_data import ensure_core_categories

ADMIN_USER = FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []})


def _run_inline(target: Callable[[], None], *, name: str) -> None:
    """Stand-in for the credential delivery thread so tests stay synchronous."""
    target()


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionStartLifecycleTests(TestCase):
    """The draft -> open transition, driven through the start API the UI uses."""

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        self.now = timezone.now()
        self._login_as_freeipa_user("admin")
        self._grant_manage_elections("admin")
        self.individual_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            description="",
            category_id="individual",
            sort_order=1,
            enabled=True,
            votes=1,
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_manage_elections(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def _draft_election(self, **overrides: Any) -> Election:
        defaults: dict[str, Any] = {
            "name": "Draft election",
            "description": "",
            "url": "",
            "start_datetime": self.now + datetime.timedelta(days=1),
            "end_datetime": self.now + datetime.timedelta(days=2),
            "number_of_seats": 1,
            "status": Election.Status.draft,
            "voting_email_subject": "Hello {{ username }}",
            "voting_email_html": "<p>Hi {{ username }}</p>",
            "voting_email_text": "Hi {{ username }}",
        }
        return Election.objects.create(**{**defaults, **overrides})

    def _member(self, username: str, *, membership_type: MembershipType | None = None) -> None:
        """Seed a membership old enough to clear the eligibility cutoff."""
        membership = Membership.objects.create(
            target_username=username,
            membership_type=membership_type or self.individual_type,
            expires_at=None,
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=self.now - datetime.timedelta(days=200))

    def _committee_group_patch(self, *, member_usernames: list[str]):
        group = FreeIPAGroup(settings.FREEIPA_ELECTION_COMMITTEE_GROUP, {"member_user": member_usernames})

        def _get_group(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
            if str(cn) != group.cn:
                raise FreeIPAMisconfiguredError("Unknown group")
            return group

        return patch("core.elections_eligibility.get_freeipa_group_for_elections", side_effect=_get_group)

    def _start(self, election: Election) -> HttpResponse:
        return self.client.post(reverse("api-election-start", args=[election.id]))

    def _assert_start_blocked(self, response: HttpResponse, *, expected: str) -> None:
        self.assertEqual(response.status_code, 400)
        errors = response.json()["errors"]
        self.assertTrue(any(expected in error for error in errors), errors)

    def test_start_opens_election_issues_credentials_and_uses_snapshot_templates(self) -> None:
        started_at = self.now + datetime.timedelta(hours=3)
        election = self._draft_election()
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        for username in ("voter1", "alice", "nominator"):
            self._member(username)

        voter_user = FreeIPAUser("voter1", {"uid": ["voter1"], "memberof_group": [], "mail": ["voter1@example.com"]})

        def get_user(username: str, **_: object) -> FreeIPAUser | None:
            return {"admin": ADMIN_USER, "voter1": voter_user}.get(username)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=get_user),
            patch("core.freeipa.user.FreeIPAUser.warm_user_cache"),
            patch("core.mail_progress._spawn", side_effect=_run_inline),
            patch("core.elections_services.timezone.now", return_value=started_at),
            patch(
                "core.elections_services.send_voting_credential_email",
                autospec=True,
                return_value=None,
            ) as send_credential_email_mock,
        ):
            response = self._start(election)

        self.assertEqual(response.status_code, 200)
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.open)
        self.assertEqual(election.start_datetime, started_at)
        self.assertEqual(election.start_datetime.tzinfo, timezone.UTC)
        self.assertTrue(VotingCredential.objects.filter(election=election, freeipa_username="voter1").exists())

        # Snapshot templates should flow through the composed credential email path.
        self.assertEqual(send_credential_email_mock.call_args.kwargs["subject_template"], election.voting_email_subject)
        self.assertEqual(send_credential_email_mock.call_args.kwargs["html_template"], election.voting_email_html)
        self.assertEqual(send_credential_email_mock.call_args.kwargs["text_template"], election.voting_email_text)

    def test_start_is_atomic_on_credential_issuance_failure(self) -> None:
        election = self._draft_election()
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        for username in ("voter1", "alice", "nominator"):
            self._member(username)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER),
            patch(
                "core.elections_services.issue_credentials_at_start_transition",
                side_effect=RuntimeError("credential issue failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self._start(election)

        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.draft)
        self.assertEqual(VotingCredential.objects.filter(election=election).count(), 0)

    def test_start_audit_entry_records_the_operator_as_actor(self) -> None:
        election = self._draft_election()
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        for username in ("voter1", "alice", "nominator"):
            self._member(username)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER),
            patch("core.freeipa.user.FreeIPAUser.warm_user_cache"),
            patch("core.mail_progress._spawn", side_effect=_run_inline),
        ):
            response = self._start(election)

        self.assertEqual(response.status_code, 200)
        audit = AuditLogEntry.objects.get(election=election, event_type="election_started")
        self.assertIsInstance(audit.payload, dict)
        self.assertEqual(audit.payload.get("actor"), "admin")

    def test_start_blocks_election_without_candidates(self) -> None:
        election = self._draft_election()
        self._member("voter1")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER):
            response = self._start(election)

        self._assert_start_blocked(response, expected="Add at least one candidate")
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.draft)

    def test_start_blocks_self_nominating_candidate(self) -> None:
        election = self._draft_election()
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="alice")
        self._member("voter1")
        self._member("alice")

        with (
            self._committee_group_patch(member_usernames=[]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER),
        ):
            response = self._start(election)

        self._assert_start_blocked(response, expected="Candidates cannot nominate themselves")
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.draft)
        self.assertFalse(VotingCredential.objects.filter(election=election).exists())

    def test_start_blocks_committee_member_candidates(self) -> None:
        election = self._draft_election()
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        for username in ("voter1", "alice", "nominator"):
            self._member(username)

        with (
            self._committee_group_patch(member_usernames=["alice"]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER),
        ):
            response = self._start(election)

        self._assert_start_blocked(response, expected="Election committee members cannot be candidates")
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.draft)

    def test_start_blocks_committee_member_nominators(self) -> None:
        election = self._draft_election()
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="bob")
        for username in ("voter1", "alice", "bob"):
            self._member(username)

        with (
            self._committee_group_patch(member_usernames=["bob"]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER),
        ):
            response = self._start(election)

        self._assert_start_blocked(response, expected="Election committee members cannot nominate candidates")
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.draft)

    def test_start_blocks_ineligible_candidates(self) -> None:
        election = self._draft_election()
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")
        self._member("voter1")
        self._member("nominator")

        with (
            self._committee_group_patch(member_usernames=[]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER),
        ):
            response = self._start(election)

        self._assert_start_blocked(response, expected="Candidate is not eligible")
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.draft)

    def test_start_blocks_election_without_eligible_voters(self) -> None:
        election = self._draft_election()
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        with (
            self._committee_group_patch(member_usernames=[]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER),
        ):
            response = self._start(election)

        self._assert_start_blocked(response, expected="No eligible voters were found for this election.")
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.draft)

    def _organization_nominated_election(
        self,
        *,
        organization_expires_at: datetime.datetime | None,
    ) -> tuple[Election, str]:
        election = self._draft_election()
        organization = Organization.objects.create(name="Infra Foundation", representative="")
        organization_nominator_id = f"org:{organization.id}"
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by=organization_nominator_id,
        )
        sponsor_type = MembershipType.objects.create(
            code="sponsor",
            name="Sponsor",
            description="",
            category_id="sponsorship",
            sort_order=1,
            enabled=True,
            votes=1,
        )
        self._member("voter1")
        self._member("alice")
        org_membership = Membership.objects.create(
            target_organization=organization,
            membership_type=sponsor_type,
            expires_at=organization_expires_at,
        )
        Membership.objects.filter(pk=org_membership.pk).update(created_at=self.now - datetime.timedelta(days=200))
        return election, organization_nominator_id

    def test_start_accepts_a_valid_organization_nominator(self) -> None:
        election, _ = self._organization_nominated_election(organization_expires_at=None)

        with (
            self._committee_group_patch(member_usernames=[]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER),
            patch("core.freeipa.user.FreeIPAUser.warm_user_cache"),
            patch("core.mail_progress._spawn", side_effect=_run_inline),
        ):
            response = self._start(election)

        self.assertEqual(response.status_code, 200)
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.open)

    def test_start_rejects_expired_organization_nominator_without_leaking_its_identifier(self) -> None:
        election, organization_nominator_id = self._organization_nominated_election(organization_expires_at=self.now)

        with (
            self._committee_group_patch(member_usernames=[]),
            patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER),
        ):
            response = self._start(election)

        self.assertEqual(response.status_code, 400)
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.draft)

        nominator_errors = [
            error
            for error in response.json()["errors"]
            if error.startswith("One or more nominators are not eligible for this election:")
        ]
        self.assertEqual(len(nominator_errors), 1)
        self.assertNotIn(organization_nominator_id, nominator_errors[0])
        self.assertIn("Infra Foundation", nominator_errors[0])


class StartedElectionEditRedirectTests(TestCase):
    """The edit route is read-only once an election has been started."""

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_manage_elections(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def _open_election(self, *, name: str) -> Election:
        now = timezone.now()
        return Election.objects.create(
            name=name,
            description="",
            url="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=2),
            number_of_seats=1,
            status=Election.Status.open,
        )

    def test_started_election_edit_redirects_end_election_action(self) -> None:
        election = self._open_election(name="Started election")
        VotingCredential.objects.create(
            election=election,
            public_id=VotingCredential.generate_public_id(),
            freeipa_username="voter1",
            weight=1,
        )
        self._login_as_freeipa_user("admin")
        self._grant_manage_elections("admin")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER):
            response = self.client.post(
                reverse("election-edit", args=[election.id]),
                data={"action": "end_election"},
                follow=False,
            )

        self.assertRedirects(
            response,
            reverse("election-detail", args=[election.id]),
            fetch_redirect_response=False,
        )
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.open)
        self.assertEqual(VotingCredential.objects.get(election=election).freeipa_username, "voter1")

    def test_started_election_edit_redirects_to_detail_without_saving(self) -> None:
        election = self._open_election(name="Started election")
        self._login_as_freeipa_user("admin")
        self._grant_manage_elections("admin")
        detail_url = reverse("election-detail", args=[election.id])

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=ADMIN_USER):
            get_response = self.client.get(reverse("election-edit", args=[election.id]))
            post_response = self.client.post(
                reverse("election-edit", args=[election.id]),
                data={"action": "save_draft", "name": "Renamed election"},
                follow=False,
            )

        self.assertRedirects(get_response, detail_url, fetch_redirect_response=False)
        self.assertRedirects(post_response, detail_url, fetch_redirect_response=False)
        election.refresh_from_db()
        self.assertEqual(election.name, "Started election")

import datetime
import time
from collections.abc import Callable
from dataclasses import asdict
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from post_office.models import Email

from core import elections_start_progress
from core.elections_start_progress import ElectionStartProgress, ElectionStartState
from core.freeipa.user import FreeIPAUser
from core.models import (
    Candidate,
    Election,
    FreeIPAPermissionGrant,
    Membership,
    MembershipType,
    VotingCredential,
)
from core.permissions import ASTRA_ADD_ELECTION
from core.tests.utils_test_data import ensure_core_categories

VOTER_USERNAMES = ["voter1", "voter2", "voter3", "alice", "nominator"]


def _run_inline(target: Callable[[], None], *, name: str) -> None:
    """Stand-in for the delivery thread so tests stay on the test connection."""
    target()


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionStartProgressApiTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        cache.clear()

        now = timezone.now()
        self.election = Election.objects.create(
            name="Progress election",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=2),
            number_of_seats=1,
            status=Election.Status.draft,
            voting_email_subject="Hello {{ username }}",
            voting_email_html="<p>Hi {{ username }}</p>",
            voting_email_text="Hi {{ username }}",
        )
        Candidate.objects.create(election=self.election, freeipa_username="alice", nominated_by="nominator")

        membership_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            description="",
            category_id="individual",
            sort_order=1,
            enabled=True,
            votes=1,
        )
        for username in VOTER_USERNAMES:
            membership = Membership.objects.create(
                target_username=username,
                membership_type=membership_type,
                expires_at=None,
            )
            Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=200))

        session = self.client.session
        session["_freeipa_username"] = "admin"
        session.save()
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        self.users = {
            "admin": FreeIPAUser("admin", {"uid": ["admin"], "memberof_group": []}),
            **{
                username: FreeIPAUser(
                    username,
                    {"uid": [username], "memberof_group": [], "mail": [f"{username}@example.com"]},
                )
                for username in VOTER_USERNAMES
            },
        }

    def _get_user(self, username: str, **_: object) -> FreeIPAUser | None:
        return self.users.get(username)

    def _start(self) -> dict[str, object]:
        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user),
            patch("core.freeipa.user.FreeIPAUser.warm_user_cache"),
            patch("core.elections_start_progress._spawn", side_effect=_run_inline),
        ):
            response = self.client.post(reverse("api-election-start", args=[self.election.id]))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_start_opens_election_and_reports_completed_delivery(self) -> None:
        payload = self._start()

        self.assertTrue(payload["ok"])
        self.election.refresh_from_db()
        self.assertEqual(self.election.status, Election.Status.open)
        self.assertEqual(VotingCredential.objects.filter(election=self.election).count(), len(VOTER_USERNAMES))

        progress = payload["start_progress"]
        self.assertEqual(progress["state"], ElectionStartState.done)
        self.assertEqual(progress["total"], len(VOTER_USERNAMES))
        self.assertEqual(progress["processed"], len(VOTER_USERNAMES))
        self.assertEqual(progress["emailed"], len(VOTER_USERNAMES))
        self.assertEqual(progress["skipped"], 0)
        self.assertEqual(progress["failures"], 0)

        for username in VOTER_USERNAMES:
            self.assertTrue(Email.objects.filter(to=[f"{username}@example.com"]).exists())

    def test_starting_an_already_started_election_changes_nothing(self) -> None:
        self._start()
        emails_after_first_start = Email.objects.count()

        second = self._start()

        self.assertTrue(second["ok"])
        self.assertEqual(second["election"]["status"], Election.Status.open)
        self.assertEqual(Email.objects.count(), emails_after_first_start)
        self.assertEqual(VotingCredential.objects.filter(election=self.election).count(), len(VOTER_USERNAMES))

    def test_start_progress_endpoint_reports_a_running_delivery(self) -> None:
        elections_start_progress.write(
            election_id=self.election.id,
            progress=ElectionStartProgress(total=100, processed=40, emailed=38, skipped=2),
        )

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user):
            response = self.client.get(reverse("api-election-start-progress", args=[self.election.id]))

        self.assertEqual(response.status_code, 200)
        progress = response.json()["start_progress"]
        self.assertEqual(progress["state"], ElectionStartState.running)
        self.assertEqual(progress["processed"], 40)
        self.assertEqual(progress["total"], 100)

    def test_start_progress_endpoint_reports_no_delivery_when_none_ran(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user):
            response = self.client.get(reverse("api-election-start-progress", args=[self.election.id]))

        self.assertIsNone(response.json()["start_progress"])

    def test_abandoned_delivery_is_reported_as_stalled(self) -> None:
        stale = ElectionStartProgress(total=100, processed=40)
        stale.updated_at = time.time() - elections_start_progress.PROGRESS_STALE_SECONDS - 1
        cache.set(
            elections_start_progress._cache_key(self.election.id),
            asdict(stale),
            elections_start_progress.PROGRESS_TTL_SECONDS,
        )

        progress = elections_start_progress.read(election_id=self.election.id)

        self.assertIsNotNone(progress)
        self.assertEqual(progress.state, ElectionStartState.stalled)
        self.assertEqual(progress.message, elections_start_progress.STALLED_MESSAGE)

    def test_failed_delivery_is_reported_without_reverting_the_open_election(self) -> None:
        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user),
            patch("core.elections_start_progress._spawn", side_effect=_run_inline),
            patch(
                "core.elections_services.complete_election_start",
                side_effect=RuntimeError("delivery exploded"),
            ),
        ):
            response = self.client.post(reverse("api-election-start", args=[self.election.id]))

        self.assertEqual(response.status_code, 200)
        progress = response.json()["start_progress"]
        self.assertEqual(progress["state"], ElectionStartState.failed)
        self.assertEqual(progress["message"], elections_start_progress.FAILED_MESSAGE)

        self.election.refresh_from_db()
        self.assertEqual(self.election.status, Election.Status.open)


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionStartDeliveryProgressTests(TestCase):
    def test_delivery_reports_progress_per_batch(self) -> None:
        from core import elections_services

        now = timezone.now()
        election = Election.objects.create(
            name="Batched delivery election",
            description="",
            url="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
            voting_email_subject="Hello {{ username }}",
            voting_email_html="<p>Hi {{ username }}</p>",
            voting_email_text="Hi {{ username }}",
        )
        credentials = [
            VotingCredential.objects.create(
                election=election,
                public_id=VotingCredential.generate_public_id(),
                freeipa_username=f"voter{index}",
                weight=1,
            )
            for index in range(elections_services.ELECTION_START_EMAIL_BATCH_SIZE + 2)
        ]
        # Every voter resolves to a user without an email address, so delivery
        # skips them all and the test exercises batching, not email rendering.
        reports: list[elections_services.ElectionStartDelivery] = []

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=None),
            patch("core.freeipa.user.FreeIPAUser.warm_user_cache"),
        ):
            delivery = elections_services.deliver_start_credential_emails(
                election=election,
                credentials=credentials,
                on_progress=reports.append,
            )

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0].processed, elections_services.ELECTION_START_EMAIL_BATCH_SIZE)
        self.assertEqual(reports[-1].processed, len(credentials))
        self.assertEqual(delivery.total, len(credentials))
        self.assertEqual(delivery.skipped, len(credentials))
        self.assertEqual(delivery.emailed, 0)

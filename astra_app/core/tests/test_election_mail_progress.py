import datetime
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from post_office.models import Email

from core import elections_services, mail_delivery, mail_progress
from core.freeipa.user import FreeIPAUser
from core.mail_progress import MailRunKind, MailRunProgress, MailRunState
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
            patch("core.mail_progress._spawn", side_effect=_run_inline),
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

        progress = payload["mail_progress"]
        self.assertEqual(progress["state"], MailRunState.done)
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
        mail_progress.write(
            scope=str(self.election.id),
            kind=MailRunKind.election_start,
            progress=MailRunProgress(total=100, processed=40, emailed=38, skipped=2),
        )

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user):
            response = self.client.get(reverse("api-election-mail-progress", args=[self.election.id]) + "?kind=start")

        self.assertEqual(response.status_code, 200)
        progress = response.json()["mail_progress"]
        self.assertEqual(progress["state"], MailRunState.running)
        self.assertEqual(progress["processed"], 40)
        self.assertEqual(progress["total"], 100)

    def test_start_progress_endpoint_reports_no_delivery_when_none_ran(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user):
            response = self.client.get(reverse("api-election-mail-progress", args=[self.election.id]) + "?kind=start")

        self.assertIsNone(response.json()["mail_progress"])

    def test_abandoned_delivery_is_reported_as_stalled(self) -> None:
        stale = MailRunProgress(total=100, processed=40)
        stale.updated_at = time.time() - mail_progress.PROGRESS_STALE_SECONDS - 1
        cache.set(
            mail_progress._cache_key(str(self.election.id), MailRunKind.election_start),
            asdict(stale),
            mail_progress.PROGRESS_TTL_SECONDS,
        )

        progress = mail_progress.read(scope=str(self.election.id), kind=MailRunKind.election_start)

        self.assertIsNotNone(progress)
        self.assertEqual(progress.state, MailRunState.stalled)
        self.assertEqual(progress.message, mail_progress.STALLED_MESSAGE)

    def test_failed_delivery_is_reported_without_reverting_the_open_election(self) -> None:
        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user),
            patch("core.mail_progress._spawn", side_effect=_run_inline),
            patch(
                "core.elections_services.complete_election_start",
                side_effect=RuntimeError("delivery exploded"),
            ),
        ):
            response = self.client.post(reverse("api-election-start", args=[self.election.id]))

        self.assertEqual(response.status_code, 200)
        progress = response.json()["mail_progress"]
        self.assertEqual(progress["state"], MailRunState.failed)
        self.assertEqual(progress["message"], mail_progress.FAILED_MESSAGE)

        self.election.refresh_from_db()
        self.assertEqual(self.election.status, Election.Status.open)


class ElectionReminderProgressApiTests(TestCase):
    """Reminding a whole electorate reports progress the same way a start does."""

    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        cache.clear()

        now = timezone.now()
        self.election = Election.objects.create(
            name="Reminder progress election",
            description="",
            url="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        for index in range(3):
            VotingCredential.objects.create(
                election=self.election,
                public_id=f"reminder-cred-{index}",
                freeipa_username=f"voter{index}",
                weight=1,
            )

        session = self.client.session
        session["_freeipa_username"] = "admin"
        session.save()
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

    def _get_user(self, username: str, **_: object) -> FreeIPAUser:
        return FreeIPAUser(
            username,
            {"uid": [username], "memberof_group": [], "mail": [f"{username}@example.com"]},
        )

    def _send(self, body: dict[str, object]) -> dict[str, object]:
        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user),
            patch("core.freeipa.user.FreeIPAUser.warm_user_cache"),
            patch("core.mail_progress._spawn", side_effect=_run_inline),
            patch("core.elections_services.send_voting_credential_email", autospec=True, return_value=None),
        ):
            response = self.client.post(
                reverse("api-election-send-mail-credentials", args=[self.election.id]),
                data=json.dumps(body),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_reminding_everyone_reports_delivery_progress(self) -> None:
        payload = self._send({})

        self.assertTrue(payload["ok"])
        progress = payload["mail_progress"]
        self.assertEqual(progress["state"], MailRunState.done)
        self.assertEqual(progress["total"], 3)
        self.assertEqual(progress["emailed"], 3)

    def test_reminder_progress_is_readable_from_the_polling_endpoint(self) -> None:
        self._send({})

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user):
            response = self.client.get(
                reverse("api-election-mail-progress", args=[self.election.id]) + "?kind=reminder"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mail_progress"]["emailed"], 3)

    def test_reminder_and_start_progress_do_not_overwrite_each_other(self) -> None:
        mail_progress.write(
            scope=str(self.election.id),
            kind=MailRunKind.election_start,
            progress=MailRunProgress(state=MailRunState.done, total=999, processed=999, emailed=999),
        )

        self._send({})

        start = mail_progress.read(scope=str(self.election.id), kind=MailRunKind.election_start)
        reminder = mail_progress.read(scope=str(self.election.id), kind=MailRunKind.election_reminder)
        self.assertEqual(start.emailed, 999)
        self.assertEqual(reminder.emailed, 3)

    def test_emailing_one_voter_stays_synchronous(self) -> None:
        payload = self._send({"username": "voter1"})

        self.assertEqual(payload["message"], "Queued voting credential email for 1 recipient.")
        self.assertNotIn("mail_progress", payload)
        self.assertIsNone(mail_progress.read(scope=str(self.election.id), kind=MailRunKind.election_reminder))

    def test_unknown_progress_kind_is_rejected(self) -> None:
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user):
            response = self.client.get(
                reverse("api-election-mail-progress", args=[self.election.id]) + "?kind=nonsense"
            )

        self.assertEqual(response.status_code, 400)


class ElectionMailDeliveryProgressTests(TestCase):
    """The delivery loop reports progress once per batch it has persisted."""

    def setUp(self) -> None:
        super().setUp()
        now = timezone.now()
        self.election = Election.objects.create(
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

    def test_progress_is_reported_once_per_persisted_batch(self) -> None:
        recipient_count = mail_delivery.MAIL_BATCH_SIZE + 2
        recipients = [
            elections_services.ElectionEmailRecipient(
                username=f"voter{index}",
                email=f"voter{index}@example.com",
                credential_public_id=f"cred-{index}",
            )
            for index in range(recipient_count)
        ]
        reports: list[mail_delivery.MailDelivery] = []

        with patch(
            "core.elections_services.send_voting_credential_email",
            autospec=True,
            return_value=None,
        ):
            delivery = elections_services.deliver_election_emails(
                election=self.election,
                recipients=recipients,
                on_progress=reports.append,
            )

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0].processed, mail_delivery.MAIL_BATCH_SIZE)
        self.assertEqual(reports[-1].processed, recipient_count)
        self.assertEqual(delivery.total, recipient_count)
        self.assertEqual(delivery.emailed, recipient_count)
        self.assertEqual(delivery.failures, 0)

    def test_unreachable_voters_are_counted_against_the_same_total(self) -> None:
        credentials = [
            VotingCredential.objects.create(
                election=self.election,
                public_id=VotingCredential.generate_public_id(),
                freeipa_username=f"voter{index}",
                weight=1,
            )
            for index in range(3)
        ]
        reachable = FreeIPAUser(
            "voter0",
            {"uid": ["voter0"], "memberof_group": [], "mail": ["voter0@example.com"]},
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=lambda username, **_: reachable if username == "voter0" else None),
            patch("core.freeipa.user.FreeIPAUser.warm_user_cache"),
            patch("core.elections_services.send_voting_credential_email", autospec=True, return_value=None),
        ):
            delivery = elections_services.deliver_start_credential_emails(
                election=self.election,
                credentials=credentials,
            )

        # The bar still counts every voter the operator set out to email.
        self.assertEqual(delivery.total, 3)
        self.assertEqual(delivery.processed, 3)
        self.assertEqual(delivery.emailed, 1)
        self.assertEqual(delivery.skipped, 2)

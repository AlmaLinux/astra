import datetime
import json
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from post_office.models import Email

from core import elections_services, mail_progress
from core import signals as astra_signals
from core.freeipa.user import FreeIPAUser
from core.mail_delivery import MailDelivery, MailDeliveryError, deliver_in_batches
from core.mail_progress import MailRunKind, MailRunProgress, MailRunState
from core.models import (
    AccountInvitation,
    AuditLogEntry,
    Election,
    FreeIPAPermissionGrant,
    VotingCredential,
)
from core.permissions import ASTRA_ADD_ELECTION, ASTRA_ADD_MEMBERSHIP, ASTRA_ADD_SEND_MAIL


def _run_inline(target, *, name: str) -> None:
    """Stand-in for the delivery thread so these tests stay synchronous."""
    target()


class MailDeliveryTests(TestCase):
    """The batching primitive every bulk send shares."""

    def test_progress_is_reported_once_per_batch(self) -> None:
        from core.mail_delivery import MAIL_BATCH_SIZE

        items = list(range(MAIL_BATCH_SIZE + 3))
        reports: list[MailDelivery] = []

        delivery = deliver_in_batches(items=items, prepare=lambda _: None, on_progress=reports.append)

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0].processed, MAIL_BATCH_SIZE)
        self.assertEqual(delivery.processed, len(items))
        self.assertEqual(delivery.emailed, len(items))

    def test_one_bad_recipient_does_not_stop_the_run(self) -> None:
        def prepare(item: int) -> None:
            if item == 1:
                raise MailDeliveryError("no address")
            return None

        delivery = deliver_in_batches(items=[0, 1, 2], prepare=prepare)

        self.assertEqual(delivery.emailed, 2)
        self.assertEqual(delivery.failures, 1)
        self.assertEqual(delivery.processed, 3)

    def test_pre_filtered_recipients_still_count_towards_the_total(self) -> None:
        delivery = deliver_in_batches(items=[0, 1], prepare=lambda _: None, skipped=3)

        self.assertEqual(delivery.total, 5)
        self.assertEqual(delivery.processed, 5)
        self.assertEqual(delivery.skipped, 3)

    def test_an_empty_run_still_reports_once(self) -> None:
        reports: list[MailDelivery] = []

        delivery = deliver_in_batches(items=[], prepare=lambda _: None, on_progress=reports.append)

        self.assertEqual(len(reports), 1)
        self.assertEqual(delivery.total, 0)


class PersonalMailProgressApiTests(TestCase):
    """Runs that belong to an operator rather than to an election."""

    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        session = self.client.session
        session["_freeipa_username"] = "operator"
        session.save()
        self.operator = FreeIPAUser("operator", {"uid": ["operator"], "memberof_group": []})

    def _grant(self, permission: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="operator",
            permission=permission,
        )

    def _get(self, kind: str):
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self.operator):
            return self.client.get(reverse("api-mail-progress") + f"?kind={kind}")

    def test_progress_is_reported_for_the_callers_own_run(self) -> None:
        self._grant(ASTRA_ADD_SEND_MAIL)
        mail_progress.write(
            scope="operator",
            kind=MailRunKind.send_mail,
            progress=MailRunProgress(total=400, processed=120, emailed=118, skipped=2),
        )

        response = self._get("send_mail")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mail_progress"]["processed"], 120)

    def test_another_operators_run_is_never_reported(self) -> None:
        self._grant(ASTRA_ADD_SEND_MAIL)
        mail_progress.write(
            scope="someone-else",
            kind=MailRunKind.send_mail,
            progress=MailRunProgress(total=400, processed=120),
        )

        self.assertIsNone(self._get("send_mail").json()["mail_progress"])

    def test_each_run_kind_requires_the_permission_that_starts_it(self) -> None:
        self._grant(ASTRA_ADD_SEND_MAIL)

        self.assertEqual(self._get("send_mail").status_code, 200)
        self.assertEqual(self._get("invitation_resend").status_code, 403)

    def test_election_runs_are_not_addressable_here(self) -> None:
        self._grant(ASTRA_ADD_SEND_MAIL)

        self.assertEqual(self._get("start").status_code, 400)

    def test_acknowledging_a_run_drops_its_record(self) -> None:
        self._grant(ASTRA_ADD_MEMBERSHIP)
        mail_progress.write(
            scope="operator",
            kind=MailRunKind.invitation_resend,
            progress=MailRunProgress(state=MailRunState.done, total=5, processed=5, emailed=5),
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self.operator):
            response = self.client.post(reverse("api-mail-progress-ack"), data={"kind": "invitation_resend"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(mail_progress.read(scope="operator", kind=MailRunKind.invitation_resend))


class SendMailProgressTests(TestCase):
    """The Send Mail tool hands delivery to a background run."""

    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_SEND_MAIL,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        session = self.client.session
        session["_freeipa_username"] = "reviewer"
        session.save()
        self.reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )

    def test_sending_to_many_recipients_reports_progress(self) -> None:
        recipients = ", ".join(f"voter{index}@example.com" for index in range(25))

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self.reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.mail_progress._spawn", side_effect=_run_inline),
            patch("core.views_send_mail.queue_composed_email") as queue_mock,
        ):
            response = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": recipients,
                    "subject": "Hello {{ email }}",
                    "text_content": "Hi {{ email }}",
                    "html_content": "<p>Hi {{ email }}</p>",
                    "action": "send",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(queue_mock.call_count, 25)

        progress = mail_progress.read(scope="reviewer", kind=MailRunKind.send_mail)
        self.assertEqual(progress.state, MailRunState.done)
        self.assertEqual(progress.total, 25)
        self.assertEqual(progress.emailed, 25)

    def test_the_page_offers_the_endpoints_that_follow_the_run(self) -> None:
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self.reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
        ):
            response = self.client.get(reverse("send-mail"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-send-mail-progress-api-url")
        self.assertContains(response, "data-send-mail-progress-ack-api-url")


class InvitationResendProgressTests(TestCase):
    """Bulk invitation resends hand delivery to a background run."""

    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        session = self.client.session
        session["_freeipa_username"] = "committee"
        session.save()
        self.committee = FreeIPAUser(
            "committee",
            {"uid": ["committee"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )

    def test_resending_many_invitations_reports_progress(self) -> None:
        invitations = [
            AccountInvitation.objects.create(
                email=f"pending{index}@example.com",
                full_name=f"Pending {index}",
                invited_by_username="committee",
                email_template_name="account-invite",
            )
            for index in range(15)
        ]

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self.committee),
            patch("core.mail_progress._spawn", side_effect=_run_inline),
            patch("core.account_invitations.queue_templated_email", return_value=None),
        ):
            response = self.client.post(
                reverse("api-account-invitations-bulk"),
                data=json.dumps(
                    {
                        "bulk_action": "resend",
                        "bulk_scope": "pending",
                        "selected": [invitation.pk for invitation in invitations],
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        progress = response.json()["mail_progress"]
        self.assertEqual(progress["state"], MailRunState.done)
        self.assertEqual(progress["total"], 15)
        self.assertEqual(progress["emailed"], 15)

    def test_an_invitation_that_cannot_be_queued_is_counted_as_a_failure(self) -> None:
        for index in range(2):
            AccountInvitation.objects.create(
                email=f"pending{index}@example.com",
                invited_by_username="committee",
                email_template_name="account-invite",
            )
        selected = list(AccountInvitation.objects.values_list("pk", flat=True))

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self.committee),
            patch("core.mail_progress._spawn", side_effect=_run_inline),
            patch(
                "core.account_invitations.queue_templated_email",
                side_effect=[None, RuntimeError("queue failed")],
            ),
        ):
            response = self.client.post(
                reverse("api-account-invitations-bulk"),
                data=json.dumps({"bulk_action": "resend", "bulk_scope": "pending", "selected": selected}),
                content_type="application/json",
            )

        progress = response.json()["mail_progress"]
        self.assertEqual(progress["emailed"], 1)
        self.assertEqual(progress["failures"], 1)


class InterruptedStartRecoveryTests(TestCase):
    """An interrupted start can be finished without repeating any of its work."""

    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        now = timezone.now()
        self.election = Election.objects.create(
            name="Half delivered election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
            voting_email_subject="Hello {{ username }}",
            voting_email_html="<p>Hi {{ username }}</p>",
            voting_email_text="Hi {{ username }}",
        )
        self.credentials = [
            VotingCredential.objects.create(
                election=self.election,
                public_id=f"cred-{index}",
                freeipa_username=f"voter{index}",
                weight=1,
            )
            for index in range(5)
        ]

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

    def _record_start(self) -> None:
        """Stand in for the audit entry a completed start would have written."""
        AuditLogEntry.objects.create(
            election=self.election,
            event_type="election_started",
            payload={"eligible_voters": 5, "emailed": 5, "skipped": 0, "failures": 0},
            is_public=True,
        )

    def _queue_credential_email_for(self, username: str, *, with_username_context: bool = True) -> None:
        """Stand in for an email the interrupted run managed to queue."""
        context = {"election_id": self.election.id}
        if with_username_context:
            context["username"] = username
        Email.objects.create(
            from_email="elections@example.com",
            to=[f"{username}@example.com"],
            subject="Your credential",
            message="body",
            context=context,
        )

    def test_it_counts_voters_no_credential_email_went_out_for(self) -> None:
        for username in ("voter0", "voter1"):
            self._queue_credential_email_for(username)

        state = elections_services.interrupted_start(election=self.election)

        self.assertEqual(state.credential_count, 5)
        self.assertEqual(state.emailed_count, 2)
        self.assertEqual(state.missing_email_count, 3)
        self.assertTrue(state.is_interrupted)

    def test_a_repeat_send_to_the_same_voter_never_masks_the_gap(self) -> None:
        self._queue_credential_email_for("voter0")
        self._queue_credential_email_for("voter0")
        self._queue_credential_email_for("voter1")

        self.assertEqual(elections_services.interrupted_start(election=self.election).missing_email_count, 3)

    def test_a_complete_start_is_not_reported_as_interrupted(self) -> None:
        for credential in self.credentials:
            self._queue_credential_email_for(str(credential.freeipa_username))
        self._record_start()

        self.assertFalse(elections_services.interrupted_start(election=self.election).is_interrupted)

    def test_a_start_that_emailed_everyone_but_recorded_nothing_is_interrupted(self) -> None:
        for credential in self.credentials:
            self._queue_credential_email_for(str(credential.freeipa_username))

        state = elections_services.interrupted_start(election=self.election)

        self.assertEqual(state.missing_email_count, 0)
        self.assertFalse(state.start_recorded)
        self.assertTrue(state.is_interrupted)

    def test_emails_queued_before_usernames_were_recorded_match_on_address(self) -> None:
        self._queue_credential_email_for("voter0", with_username_context=False)

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user):
            recipients, _skipped = elections_services.unemailed_credential_recipients(election=self.election)

        self.assertNotIn("voter0", [recipient.username for recipient in recipients])
        self.assertEqual(len(recipients), 4)

    def test_finishing_reaches_only_the_voters_that_were_missed(self) -> None:
        for username in ("voter0", "voter1"):
            self._queue_credential_email_for(username)
        self._record_start()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user),
            patch("core.freeipa.user.FreeIPAUser.warm_user_cache"),
            patch("core.mail_progress._spawn", side_effect=_run_inline),
            patch("core.elections_services.send_voting_credential_email", autospec=True, return_value=None) as send_mock,
        ):
            response = self.client.post(
                reverse("api-election-complete-start", args=[self.election.id])
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["recipient_count"], 3)
        self.assertEqual(payload["mail_progress"]["emailed"], 3)

        emailed = sorted(call.kwargs["username"] for call in send_mock.call_args_list)
        self.assertEqual(emailed, ["voter2", "voter3", "voter4"])

    def test_finishing_is_refused_when_the_start_already_completed(self) -> None:
        for credential in self.credentials:
            self._queue_credential_email_for(str(credential.freeipa_username))
        self._record_start()

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user):
            response = self.client.post(
                reverse("api-election-complete-start", args=[self.election.id])
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("already finished", response.json()["errors"][0])

    def test_the_warning_is_shown_on_the_election_page_only_while_work_is_outstanding(self) -> None:
        self._queue_credential_email_for("voter0")

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user):
            response = self.client.get(reverse("election-detail", args=[self.election.id]))

        self.assertContains(response, "data-election-interrupted-start-root")
        self.assertContains(response, 'data-election-interrupted-start-missing-count="4"')
        self.assertContains(response, 'data-election-interrupted-start-recorded="false"')

        for credential in self.credentials[1:]:
            self._queue_credential_email_for(str(credential.freeipa_username))
        self._record_start()

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user):
            response = self.client.get(reverse("election-detail", args=[self.election.id]))

        self.assertNotContains(response, "data-election-interrupted-start-root")

    def test_finishing_records_and_announces_a_start_that_was_never_recorded(self) -> None:
        for credential in self.credentials:
            self._queue_credential_email_for(str(credential.freeipa_username))

        opened: list[dict[str, object]] = []

        def _record_announcement(sender: object, **kwargs: object) -> None:
            opened.append(kwargs)

        # weak=False: a local receiver would otherwise be collected before it fires.
        astra_signals.election_opened.connect(_record_announcement, weak=False, dispatch_uid="test-interrupted-start")
        self.addCleanup(astra_signals.election_opened.disconnect, dispatch_uid="test-interrupted-start")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user),
            patch("core.freeipa.user.FreeIPAUser.warm_user_cache"),
            patch("core.mail_progress._spawn", side_effect=_run_inline),
            patch("core.elections_services.send_voting_credential_email", autospec=True, return_value=None) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(reverse("api-election-complete-start", args=[self.election.id]))

        self.assertEqual(response.status_code, 200)
        # Everyone already had their credentials, so nobody is emailed again.
        send_mock.assert_not_called()

        audit = AuditLogEntry.objects.get(election=self.election, event_type="election_started")
        self.assertEqual(audit.payload["eligible_voters"], 5)
        self.assertEqual(audit.payload["emailed"], 5)
        self.assertEqual(audit.payload["actor"], "admin")
        # The audit log must not imply this was recorded as the start happened.
        self.assertTrue(audit.payload["recovered"])

        # The announcement that never went out when the election opened.
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["election"].id, self.election.id)

    def test_finishing_does_not_record_a_second_start_entry(self) -> None:
        self._queue_credential_email_for("voter0")
        self._record_start()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=self._get_user),
            patch("core.freeipa.user.FreeIPAUser.warm_user_cache"),
            patch("core.mail_progress._spawn", side_effect=_run_inline),
            patch("core.elections_services.send_voting_credential_email", autospec=True, return_value=None),
        ):
            self.client.post(reverse("api-election-complete-start", args=[self.election.id]))

        self.assertEqual(
            AuditLogEntry.objects.filter(election=self.election, event_type="election_started").count(),
            1,
        )

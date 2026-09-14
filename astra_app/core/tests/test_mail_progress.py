import json
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from core import mail_progress
from core.freeipa.user import FreeIPAUser
from core.mail_delivery import MailDelivery, MailDeliveryError, deliver_in_batches
from core.mail_progress import MailRunKind, MailRunProgress, MailRunState
from core.models import AccountInvitation, FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_ADD_SEND_MAIL


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

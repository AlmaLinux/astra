import queue
import threading
from unittest.mock import patch

from django.contrib import admin
from django.db import close_old_connections, connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django_ses.signals import bounce_received, complaint_received
from post_office.models import STATUS as POST_OFFICE_STATUS
from post_office.models import Email as PostOfficeEmail
from post_office.models import Log as PostOfficeLog
from post_office.models import RecipientDeliveryStatus

from core.ses_signals import (
    _matched_post_office_email,
    handle_ses_bounce_received,
    handle_ses_complaint_received,
    handle_ses_delivery_received,
    handle_ses_send_received,
)


class SESOperationalVisibilityTests(SimpleTestCase):
    def test_send_signal_emits_info_not_warning_for_positive_event(self) -> None:
        with (
            patch("core.ses_signals._handle_ses_post_office_event") as handle_event_mock,
            patch("core.ses_signals.logger.info") as info_mock,
            patch("core.ses_signals.logger.warning") as warning_mock,
        ):
            handle_ses_send_received(
                sender=self.__class__,
                mail_obj={"commonHeaders": {"messageId": "<positive@example.org>"}},
                send_obj={"destination": ["alice@example.org"]},
                raw_message=b"{}",
            )

        handle_event_mock.assert_called_once()
        warning_mock.assert_not_called()
        info_mock.assert_called()
        processed_events = [
            call.kwargs.get("extra", {}).get("event")
            for call in info_mock.call_args_list
            if call.kwargs.get("extra")
        ]
        self.assertIn("astra.email.ses.event_received", processed_events)

    @override_settings(
        AWS_SES_ADD_BOUNCE_TO_BLACKLIST=False,
        AWS_SES_USE_BLACKLIST=False,
    )
    def test_bounce_signal_emits_structured_log_without_raw_email(self) -> None:
        with patch("core.ses_signals.logger.warning") as warning_mock:
            bounce_received.send(
                sender=self.__class__,
                mail_obj={"messageId": "ses-message-123"},
                bounce_obj={
                    "bouncedRecipients": [
                        {"emailAddress": "alice@example.org"},
                    ]
                },
                raw_message=b"{}",
            )

        warning_mock.assert_called_once()
        args, kwargs = warning_mock.call_args
        extra = kwargs.get("extra") or {}

        self.assertEqual(extra.get("event"), "astra.email.ses.event_received")
        self.assertEqual(extra.get("component"), "email")
        self.assertEqual(extra.get("outcome"), "received")
        self.assertEqual(extra.get("ses_event_type"), "bounce")
        self.assertEqual(extra.get("recipient_domain"), "example.org")
        self.assertEqual(extra.get("event_source"), "django_ses.bounce_received")

        message_id_hash = str(extra.get("ses_message_id_hash") or "")
        self.assertTrue(message_id_hash)
        self.assertNotEqual(message_id_hash, "ses-message-123")

        serialized_payload = f"{args!r} {kwargs!r}"
        self.assertNotIn("alice@example.org", serialized_payload)

    @override_settings(
        AWS_SES_ADD_COMPLAINT_TO_BLACKLIST=False,
        AWS_SES_USE_BLACKLIST=False,
    )
    def test_complaint_signal_emits_structured_log_without_raw_email(self) -> None:
        with patch("core.ses_signals.logger.warning") as warning_mock:
            complaint_received.send(
                sender=self.__class__,
                mail_obj={},
                complaint_obj={
                    "complainedRecipients": [
                        {"emailAddress": "complaint@example.net"},
                    ]
                },
                raw_message=b"{}",
            )

        warning_mock.assert_called_once()
        args, kwargs = warning_mock.call_args
        extra = kwargs.get("extra") or {}

        self.assertEqual(extra.get("event"), "astra.email.ses.event_received")
        self.assertEqual(extra.get("component"), "email")
        self.assertEqual(extra.get("outcome"), "received")
        self.assertEqual(extra.get("ses_event_type"), "complaint")
        self.assertEqual(extra.get("recipient_domain"), "example.net")
        self.assertEqual(extra.get("event_source"), "django_ses.complaint_received")
        self.assertEqual(extra.get("ses_message_id_present"), False)

        serialized_payload = f"{args!r} {kwargs!r}"
        self.assertNotIn("complaint@example.net", serialized_payload)

    def test_blacklisted_email_admin_is_registered(self) -> None:
        from django_ses.models import BlacklistedEmail

        self.assertIn(BlacklistedEmail, admin.site._registry)
        model_admin = admin.site._registry[BlacklistedEmail]
        self.assertIn("email", tuple(model_admin.search_fields or ()))


class SESPostOfficeAuditLogTests(TestCase):
    def _create_email(self, *, message_id: str, status: int) -> PostOfficeEmail:
        return PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="to@example.com",
            subject="Test",
            message="Test message",
            message_id=message_id,
            status=status,
        )

    def test_send_sets_accepted_summary_and_milestone_log(self) -> None:
        email = self._create_email(message_id="<accepted@example.com>", status=POST_OFFICE_STATUS.sent)

        handle_ses_send_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<accepted@example.com>"}},
            send_obj={"destination": ["to@example.com"]},
            raw_message=b"{}",
        )

        email.refresh_from_db()
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.ACCEPTED)

        log_entry = PostOfficeLog.objects.get(email=email)
        self.assertEqual(log_entry.status, RecipientDeliveryStatus.ACCEPTED)
        self.assertEqual(log_entry.message, "SES accepted by provider")

    def test_delivery_upgrades_accepted_and_suppresses_duplicate_delivery(self) -> None:
        email = self._create_email(message_id="<delivered@example.com>", status=POST_OFFICE_STATUS.sent)

        handle_ses_send_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<delivered@example.com>"}},
            send_obj={"destination": ["to@example.com"]},
            raw_message=b"{}",
        )
        handle_ses_delivery_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<delivered@example.com>"}},
            delivery_obj={"recipients": ["to@example.com"]},
            raw_message=b"{}",
        )
        handle_ses_delivery_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<delivered@example.com>"}},
            delivery_obj={"recipients": ["to@example.com"]},
            raw_message=b"{}",
        )

        email.refresh_from_db()
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.DELIVERED)

        logs = list(PostOfficeLog.objects.filter(email=email).order_by("id"))
        self.assertEqual([log.status for log in logs], [
            RecipientDeliveryStatus.ACCEPTED,
            RecipientDeliveryStatus.DELIVERED,
        ])

    def test_stale_lower_precedence_event_does_not_downgrade_summary(self) -> None:
        email = self._create_email(message_id="<stale@example.com>", status=POST_OFFICE_STATUS.sent)

        handle_ses_delivery_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<stale@example.com>"}},
            delivery_obj={"recipients": ["to@example.com"]},
            raw_message=b"{}",
        )
        handle_ses_send_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<stale@example.com>"}},
            send_obj={"destination": ["to@example.com"]},
            raw_message=b"{}",
        )

        email.refresh_from_db()
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.DELIVERED)
        self.assertEqual(PostOfficeLog.objects.filter(email=email).count(), 1)

    def test_bounce_creates_post_office_log_for_matching_smtp_message_id(self) -> None:
        email = self._create_email(message_id="<abc123@example.com>", status=POST_OFFICE_STATUS.queued)

        handle_ses_bounce_received(
            sender=self.__class__,
            mail_obj={
                "messageId": "ses-internal-id",
                "commonHeaders": {"messageId": "<abc123@example.com>"},
            },
            bounce_obj={
                "bounceType": "Permanent",
                "bouncedRecipients": [{"emailAddress": "to@example.com"}],
            },
            raw_message=b"{}",
        )

        log_entry = (
            PostOfficeLog.objects.filter(email=email, exception_type="SESBounce")
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(log_entry)
        assert log_entry is not None
        self.assertEqual(log_entry.exception_type, "SESBounce")
        self.assertEqual(log_entry.status, RecipientDeliveryStatus.HARD_BOUNCED)
        self.assertIn("Permanent", log_entry.message)
        self.assertIn("to@example.com", log_entry.message)

        email.refresh_from_db()
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.HARD_BOUNCED)

    def test_bounce_marks_queued_email_as_failed(self) -> None:
        email = self._create_email(message_id="<queued@example.com>", status=POST_OFFICE_STATUS.queued)

        handle_ses_bounce_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<queued@example.com>"}},
            bounce_obj={
                "bounceType": "Transient",
                "bouncedRecipients": [{"emailAddress": "to@example.com"}],
            },
            raw_message=b"{}",
        )

        email.refresh_from_db()
        self.assertEqual(email.status, POST_OFFICE_STATUS.failed)

    def test_bounce_keeps_sent_email_status_but_still_logs_failure(self) -> None:
        email = self._create_email(message_id="<sent@example.com>", status=POST_OFFICE_STATUS.sent)

        handle_ses_bounce_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<sent@example.com>"}},
            bounce_obj={
                "bounceType": "Permanent",
                "bouncedRecipients": [{"emailAddress": "to@example.com"}],
            },
            raw_message=b"{}",
        )

        email.refresh_from_db()
        self.assertEqual(email.status, POST_OFFICE_STATUS.sent)
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.HARD_BOUNCED)
        self.assertTrue(PostOfficeLog.objects.filter(email=email, exception_type="SESBounce").exists())

    def test_bounce_with_missing_or_unmatched_message_id_is_safe(self) -> None:
        self._create_email(message_id="<existing@example.com>", status=POST_OFFICE_STATUS.queued)

        handle_ses_bounce_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<missing@example.com>"}},
            bounce_obj={
                "bounceType": "Permanent",
                "bouncedRecipients": [{"emailAddress": "missing@example.com"}],
            },
            raw_message=b"{}",
        )

        handle_ses_bounce_received(
            sender=self.__class__,
            mail_obj=None,
            bounce_obj=None,
            raw_message=b"{}",
        )

        self.assertEqual(PostOfficeLog.objects.count(), 0)

    def test_missing_match_logs_processed_outcome(self) -> None:
        with patch("core.ses_signals.logger.info") as info_mock:
            handle_ses_delivery_received(
                sender=self.__class__,
                mail_obj={"commonHeaders": {"messageId": "<missing@example.com>"}},
                delivery_obj={"recipients": ["to@example.com"]},
                raw_message=b"{}",
            )

        processed_events = [
            call.kwargs.get("extra", {})
            for call in info_mock.call_args_list
            if call.kwargs.get("extra", {}).get("event") == "astra.email.ses.event_processed"
        ]
        self.assertEqual(len(processed_events), 1)
        self.assertEqual(processed_events[0]["outcome"], "missing_match")
        self.assertEqual(processed_events[0]["ses_event_type"], "delivery")
        self.assertEqual(processed_events[0]["event_source"], "django_ses.delivery_received")
        self.assertEqual(processed_events[0]["recipient_domain"], "example.com")

    def test_ambiguous_message_id_does_not_mutate_any_email(self) -> None:
        first = self._create_email(message_id="<duplicate@example.com>", status=POST_OFFICE_STATUS.sent)
        second = self._create_email(message_id="<duplicate@example.com>", status=POST_OFFICE_STATUS.sent)

        handle_ses_delivery_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<duplicate@example.com>"}},
            delivery_obj={"recipients": ["to@example.com"]},
            raw_message=b"{}",
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(first.recipient_delivery_status)
        self.assertIsNone(second.recipient_delivery_status)
        self.assertEqual(PostOfficeLog.objects.count(), 0)

    def test_ambiguous_match_logs_processed_outcome(self) -> None:
        self._create_email(message_id="<duplicate@example.com>", status=POST_OFFICE_STATUS.sent)
        self._create_email(message_id="<duplicate@example.com>", status=POST_OFFICE_STATUS.sent)

        with patch("core.ses_signals.logger.info") as info_mock:
            handle_ses_delivery_received(
                sender=self.__class__,
                mail_obj={"commonHeaders": {"messageId": "<duplicate@example.com>"}},
                delivery_obj={"recipients": ["to@example.com"]},
                raw_message=b"{}",
            )

        processed_events = [
            call.kwargs.get("extra", {})
            for call in info_mock.call_args_list
            if call.kwargs.get("extra", {}).get("event") == "astra.email.ses.event_processed"
        ]
        self.assertEqual(len(processed_events), 1)
        self.assertEqual(processed_events[0]["outcome"], "ambiguous_match")
        self.assertEqual(processed_events[0]["ses_event_type"], "delivery")
        self.assertEqual(processed_events[0]["event_source"], "django_ses.delivery_received")
        self.assertEqual(processed_events[0]["recipient_domain"], "example.com")
        self.assertEqual(processed_events[0]["match_count"], 2)

    def test_multi_recipient_email_tracks_single_aggregate_summary(self) -> None:
        email = PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="alice@example.com,bob@example.com",
            subject="Test",
            message="Test message",
            message_id="<aggregate@example.com>",
            status=POST_OFFICE_STATUS.sent,
        )

        handle_ses_delivery_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<aggregate@example.com>"}},
            delivery_obj={"recipients": ["alice@example.com", "bob@example.com"]},
            raw_message=b"{}",
        )

        email.refresh_from_db()
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.DELIVERED)

        logs = list(PostOfficeLog.objects.filter(email=email))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].status, RecipientDeliveryStatus.DELIVERED)

    def test_duplicate_delivery_logs_stale_or_duplicate_outcome(self) -> None:
        email = self._create_email(message_id="<stale-duplicate@example.com>", status=POST_OFFICE_STATUS.sent)

        handle_ses_delivery_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<stale-duplicate@example.com>"}},
            delivery_obj={"recipients": ["to@example.com"]},
            raw_message=b"{}",
        )

        with patch("core.ses_signals.logger.info") as info_mock:
            handle_ses_delivery_received(
                sender=self.__class__,
                mail_obj={"commonHeaders": {"messageId": "<stale-duplicate@example.com>"}},
                delivery_obj={"recipients": ["to@example.com"]},
                raw_message=b"{}",
            )

        email.refresh_from_db()
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.DELIVERED)
        self.assertEqual(PostOfficeLog.objects.filter(email=email).count(), 1)

        processed_events = [
            call.kwargs.get("extra", {})
            for call in info_mock.call_args_list
            if call.kwargs.get("extra", {}).get("event") == "astra.email.ses.event_processed"
        ]
        self.assertEqual(len(processed_events), 1)
        self.assertEqual(processed_events[0]["outcome"], "stale_or_duplicate")
        self.assertEqual(processed_events[0]["ses_event_type"], "delivery")
        self.assertEqual(processed_events[0]["event_source"], "django_ses.delivery_received")
        self.assertEqual(processed_events[0]["recipient_domain"], "example.com")
        self.assertEqual(processed_events[0]["normalized_status"], "delivered")

    def test_processing_failure_is_logged_and_swallowed(self) -> None:
        email = self._create_email(message_id="<failure@example.com>", status=POST_OFFICE_STATUS.sent)

        with (
            patch.object(PostOfficeLog.objects, "create", side_effect=RuntimeError("boom")),
            patch("core.ses_signals.logger.exception") as exception_mock,
        ):
            handle_ses_delivery_received(
                sender=self.__class__,
                mail_obj={"commonHeaders": {"messageId": "<failure@example.com>"}},
                delivery_obj={"recipients": ["to@example.com"]},
                raw_message=b"{}",
            )

        email.refresh_from_db()
        self.assertIsNone(email.recipient_delivery_status)
        self.assertFalse(PostOfficeLog.objects.filter(email=email).exists())
        exception_mock.assert_called_once()

    def test_complaint_creates_post_office_log_for_matching_smtp_message_id(self) -> None:
        email = self._create_email(message_id="<complaint@example.com>", status=POST_OFFICE_STATUS.sent)

        handle_ses_complaint_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<complaint@example.com>"}},
            complaint_obj={
                "complainedRecipients": [{"emailAddress": "complaint@example.com"}],
            },
            raw_message=b"{}",
        )

        log_entry = (
            PostOfficeLog.objects.filter(email=email, exception_type="SESComplaint")
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(log_entry)
        assert log_entry is not None
        self.assertEqual(log_entry.exception_type, "SESComplaint")
        self.assertEqual(log_entry.status, RecipientDeliveryStatus.SPAM_COMPLAINT)
        self.assertEqual(log_entry.message, "SES spam complaint received")

        email.refresh_from_db()
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.SPAM_COMPLAINT)

    def test_complaint_overrides_bounce_and_suppresses_duplicate_complaint(self) -> None:
        email = self._create_email(message_id="<complaint-upgrade@example.com>", status=POST_OFFICE_STATUS.sent)

        handle_ses_bounce_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<complaint-upgrade@example.com>"}},
            bounce_obj={
                "bounceType": "Transient",
                "bouncedRecipients": [{"emailAddress": "to@example.com"}],
            },
            raw_message=b"{}",
        )
        handle_ses_complaint_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<complaint-upgrade@example.com>"}},
            complaint_obj={
                "complainedRecipients": [{"emailAddress": "to@example.com"}],
            },
            raw_message=b"{}",
        )
        handle_ses_complaint_received(
            sender=self.__class__,
            mail_obj={"commonHeaders": {"messageId": "<complaint-upgrade@example.com>"}},
            complaint_obj={
                "complainedRecipients": [{"emailAddress": "to@example.com"}],
            },
            raw_message=b"{}",
        )

        email.refresh_from_db()
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.SPAM_COMPLAINT)
        self.assertEqual(
            [log.status for log in PostOfficeLog.objects.filter(email=email).order_by("id")],
            [
                RecipientDeliveryStatus.SOFT_BOUNCED,
                RecipientDeliveryStatus.SPAM_COMPLAINT,
            ],
        )


class SESPostOfficeConcurrencyTests(TransactionTestCase):
    def _create_email(self, *, message_id: str, status: int) -> PostOfficeEmail:
        return PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="to@example.com",
            subject="Test",
            message="Test message",
            message_id=message_id,
            status=status,
        )

    def _start_event_thread(
        self,
        *,
        handler: object,
        mail_obj: dict[str, object],
        payload_name: str,
        payload: dict[str, object],
        errors: queue.Queue[BaseException],
    ) -> threading.Thread:
        def run() -> None:
            close_old_connections()
            try:
                kwargs = {
                    "sender": self.__class__,
                    "mail_obj": mail_obj,
                    payload_name: payload,
                    "raw_message": b"{}",
                }
                handler(**kwargs)
            except BaseException as exc:  # pragma: no cover - assertion surfaced below
                errors.put(exc)
            finally:
                close_old_connections()

        thread = threading.Thread(target=run)
        thread.start()
        return thread

    def _join_threads(
        self,
        *,
        threads: list[threading.Thread],
        errors: queue.Queue[BaseException],
    ) -> None:
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "worker thread did not finish")

        if not errors.empty():
            raise errors.get()

    def test_concurrent_duplicate_delivery_records_only_one_milestone(self) -> None:
        if not connection.features.has_select_for_update:
            self.skipTest("database does not support select_for_update")

        email = self._create_email(message_id="<concurrent-duplicate@example.com>", status=POST_OFFICE_STATUS.sent)
        errors: queue.Queue[BaseException] = queue.Queue()
        real_match = _matched_post_office_email
        barrier = threading.Barrier(2)

        def synchronized_match(*args: object, **kwargs: object) -> PostOfficeEmail | None:
            matched_email = real_match(*args, **kwargs)
            if matched_email is not None:
                barrier.wait(timeout=5)
            return matched_email

        with patch("core.ses_signals._matched_post_office_email", side_effect=synchronized_match):
            first_thread = self._start_event_thread(
                handler=handle_ses_delivery_received,
                mail_obj={"commonHeaders": {"messageId": "<concurrent-duplicate@example.com>"}},
                payload_name="delivery_obj",
                payload={"recipients": ["to@example.com"]},
                errors=errors,
            )

            second_thread = self._start_event_thread(
                handler=handle_ses_delivery_received,
                mail_obj={"commonHeaders": {"messageId": "<concurrent-duplicate@example.com>"}},
                payload_name="delivery_obj",
                payload={"recipients": ["to@example.com"]},
                errors=errors,
            )

            self._join_threads(threads=[first_thread, second_thread], errors=errors)

        email.refresh_from_db()
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.DELIVERED)
        self.assertEqual(
            list(PostOfficeLog.objects.filter(email=email).values_list("status", flat=True)),
            [RecipientDeliveryStatus.DELIVERED],
        )

    def test_concurrent_lower_precedence_event_cannot_downgrade_summary(self) -> None:
        if not connection.features.has_select_for_update:
            self.skipTest("database does not support select_for_update")

        email = self._create_email(message_id="<concurrent-precedence@example.com>", status=POST_OFFICE_STATUS.sent)
        errors: queue.Queue[BaseException] = queue.Queue()
        real_match = _matched_post_office_email
        barrier = threading.Barrier(2)

        def synchronized_match(*args: object, **kwargs: object) -> PostOfficeEmail | None:
            matched_email = real_match(*args, **kwargs)
            if matched_email is not None:
                barrier.wait(timeout=5)
            return matched_email

        with patch("core.ses_signals._matched_post_office_email", side_effect=synchronized_match):
            delivery_thread = self._start_event_thread(
                handler=handle_ses_delivery_received,
                mail_obj={"commonHeaders": {"messageId": "<concurrent-precedence@example.com>"}},
                payload_name="delivery_obj",
                payload={"recipients": ["to@example.com"]},
                errors=errors,
            )

            send_thread = self._start_event_thread(
                handler=handle_ses_send_received,
                mail_obj={"commonHeaders": {"messageId": "<concurrent-precedence@example.com>"}},
                payload_name="send_obj",
                payload={"destination": ["to@example.com"]},
                errors=errors,
            )

            self._join_threads(threads=[delivery_thread, send_thread], errors=errors)

        email.refresh_from_db()
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.DELIVERED)
        milestone_statuses = list(PostOfficeLog.objects.filter(email=email).values_list("status", flat=True))

        # Either worker may win the row lock first, so the milestone history is
        # valid as just DELIVERED or as ACCEPTED promoted to DELIVERED.
        self.assertEqual(milestone_statuses[-1], RecipientDeliveryStatus.DELIVERED)
        self.assertEqual(milestone_statuses.count(RecipientDeliveryStatus.DELIVERED), 1)
        self.assertLessEqual(milestone_statuses.count(RecipientDeliveryStatus.ACCEPTED), 1)
        self.assertEqual(
            set(milestone_statuses),
            {RecipientDeliveryStatus.DELIVERED}
            if RecipientDeliveryStatus.ACCEPTED not in milestone_statuses
            else {RecipientDeliveryStatus.ACCEPTED, RecipientDeliveryStatus.DELIVERED},
        )
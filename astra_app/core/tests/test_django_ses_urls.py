import json

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from post_office.models import STATUS as POST_OFFICE_STATUS
from post_office.models import Email as PostOfficeEmail
from post_office.models import Log as PostOfficeLog
from post_office.models import RecipientDeliveryStatus


class DjangoSesUrlsTests(SimpleTestCase):
    def test_event_webhook_url_is_wired(self):
        # Name used by django-ses upstream docs/tests.
        self.assertEqual(reverse('event_webhook'), '/ses/event-webhook/')

    def test_stats_dashboard_url_is_wired(self):
        # Provided by django_ses.urls include.
        self.assertEqual(reverse('django_ses_stats'), '/admin/django-ses/')


class DjangoSesEventWebhookIntegrationTests(TestCase):
    def _delivery_notification(self, *, message_id: str) -> dict[str, str]:
        return {
            "Type": "Notification",
            "Message": json.dumps(
                {
                    "eventType": "Delivery",
                    "mail": {
                        "commonHeaders": {"messageId": message_id},
                    },
                    "delivery": {
                        "recipients": ["alice@example.com"],
                    },
                }
            ),
        }

    @override_settings(AWS_SES_VERIFY_EVENT_SIGNATURES=False)
    def test_event_webhook_posts_delivery_and_suppresses_duplicate_replay(self) -> None:
        email = PostOfficeEmail.objects.create(
            from_email="from@example.com",
            to="alice@example.com",
            subject="Webhook test",
            message="Webhook body",
            message_id="<webhook-delivery@example.com>",
            status=POST_OFFICE_STATUS.sent,
        )

        first_response = self.client.post(
            reverse("event_webhook"),
            data=json.dumps(self._delivery_notification(message_id="<webhook-delivery@example.com>")),
            content_type="application/json",
        )
        second_response = self.client.post(
            reverse("event_webhook"),
            data=json.dumps(self._delivery_notification(message_id="<webhook-delivery@example.com>")),
            content_type="application/json",
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)

        email.refresh_from_db()
        self.assertEqual(email.recipient_delivery_status, RecipientDeliveryStatus.DELIVERED)
        self.assertEqual(
            list(PostOfficeLog.objects.filter(email=email).values_list("status", flat=True)),
            [RecipientDeliveryStatus.DELIVERED],
        )

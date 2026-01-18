from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings


class TemplatedEmailTemplateDriftTests(TestCase):
    @override_settings(ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME="vote-receipt-drift-test")
    def test_vote_receipt_template_missing_weight_logs_warning(self) -> None:
        """Operators must be warned if the effective receipt template drifts.

        Admins can edit django-post-office templates via UI. If `weight` is
        removed from the template, voters lose a required non-secret hash input.
        """

        from post_office.models import EmailTemplate

        from core.templated_email import queue_templated_email

        EmailTemplate.objects.create(
            name="vote-receipt-drift-test",
            subject="Receipt {{ ballot_hash }}",
            content="Receipt {{ ballot_hash }} Nonce {{ nonce }}",
            html_content="<p>Receipt <code>{{ ballot_hash }}</code></p>",
        )

        with (
            patch("post_office.mail.send", autospec=True),
            self.assertLogs("core.templated_email", level="WARNING") as logs,
        ):
            queue_templated_email(
                recipients=["alice@example.com"],
                sender="noreply@example.com",
                template_name="vote-receipt-drift-test",
                context={
                    "username": "alice",
                    "ballot_hash": "a" * 64,
                    "nonce": "n" * 32,
                    "weight": 1,
                },
            )

        self.assertTrue(
            any("missing required" in line.lower() and "weight" in line.lower() for line in logs.output),
            f"expected warning about missing weight, got: {logs.output}",
        )

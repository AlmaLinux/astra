from unittest.mock import patch

from django.test import SimpleTestCase


class UserEmailContextLoggingTests(SimpleTestCase):
    def test_user_email_context_logs_warning_on_freeipa_degradation(self) -> None:
        from core.email_context import user_email_context

        with (
            patch("core.email_context.FreeIPAUser.get", side_effect=RuntimeError("down")),
            patch("core.email_context.logger.warning") as warning_mock,
        ):
            context = user_email_context(username="alice")

        self.assertEqual(
            context,
            {
                "username": "alice",
                "first_name": "",
                "last_name": "",
                "full_name": "alice",
                "email": "",
            },
        )
        warning_mock.assert_called_once()

        _args, kwargs = warning_mock.call_args
        extra = kwargs.get("extra") or {}

        self.assertEqual(extra.get("event"), "astra.email.context.degraded")
        self.assertEqual(extra.get("component"), "email")
        self.assertEqual(extra.get("outcome"), "warning")
        self.assertEqual(extra.get("template_name"), "unknown")
        self.assertEqual(extra.get("reason_code"), "RuntimeError")
        self.assertEqual(extra.get("freeipa_operation"), "FreeIPAUser.get")
        self.assertEqual(extra.get("correlation_id"), "email_context.user_email_context")

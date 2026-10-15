import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from post_office.models import Email

from core.freeipa.user import FreeIPAUser
from core.tests.utils_test_data import ensure_email_templates

GOOD_DETAILS = "Pay by ACH, Wire\nBank Info: Example Bank\nAccount Number 000111222\n"


class _AdminGroup:
    members = ["alice", "bob"]


class PaymentDetailsCheckCommandTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        ensure_email_templates()
        payment_file = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        payment_file.close()
        self.path = Path(payment_file.name)
        self.addCleanup(self.path.unlink, missing_ok=True)
        self.path.write_text(GOOD_DETAILS)
        file_setting = override_settings(SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE=str(self.path))
        file_setting.enable()
        self.addCleanup(file_setting.disable)

        users = {
            username: FreeIPAUser(username, {"uid": [username], "mail": [f"{username}@example.com"], "memberof_group": []})
            for username in _AdminGroup.members
        }
        for target, kwargs in (
            ("core.freeipa.group.FreeIPAGroup.get", {"return_value": _AdminGroup()}),
            ("core.freeipa.user.FreeIPAUser.get", {"side_effect": lambda username, **_kwargs: users.get(username)}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, **options) -> None:
        call_command("sponsorship_invoice_payment_details_check", **options)

    def _alerts(self) -> list[Email]:
        return list(
            Email.objects.filter(template__name=settings.SPONSORSHIP_INVOICE_PAYMENT_DETAILS_ALERT_EMAIL_TEMPLATE_NAME)
        )

    def test_a_sensible_unchanged_file_raises_no_alert(self) -> None:
        self._run()
        self._run()

        self.assertEqual(self._alerts(), [])

    def test_a_changed_file_alerts_the_admins_once(self) -> None:
        self._run()
        self.path.write_text(GOOD_DETAILS.replace("000111222", "999999999"))

        self._run()
        self._run()

        [alert] = self._alerts()
        self.assertEqual(sorted(alert.to), ["alice@example.com", "bob@example.com"])
        self.assertIn("changed", " ".join(alert.context["problems"]))
        self.assertEqual(alert.context["path"], str(self.path))

    def test_missing_bank_details_alert_the_admins(self) -> None:
        self.path.write_text("Please pay us somehow.\nBank: Example Bank\n")

        self._run()

        [alert] = self._alerts()
        self.assertEqual(alert.context["problems"], ['The file does not mention "Account Number".'])

    def test_an_unusable_file_alerts_the_admins(self) -> None:
        for configured in ("", "/nonexistent/payment-details.txt"):
            with self.subTest(configured=configured):
                Email.objects.all().delete()
                with override_settings(SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE=configured):
                    self._run()
                [alert] = self._alerts()
                self.assertIn("SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE", alert.context["problems"][0])

    def test_dry_run_neither_alerts_nor_records_the_hash(self) -> None:
        self._run(dry_run=True)
        self.path.write_text(GOOD_DETAILS.replace("000111222", "999999999"))
        self._run(dry_run=True)

        self.assertEqual(self._alerts(), [])
        # The first real run only records a baseline.
        self._run()
        self.assertEqual(self._alerts(), [])

    def test_runs_as_part_of_the_daily_operations(self) -> None:
        with patch("core.operations.call_command") as call_mock:
            call_command("operations_daily")

        self.assertIn(
            "sponsorship_invoice_payment_details_check",
            [call.args[0] for call in call_mock.call_args_list],
        )

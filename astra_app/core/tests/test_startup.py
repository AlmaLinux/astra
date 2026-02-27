import importlib
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

import core.startup


class StartupFreeIPARetryTests(SimpleTestCase):
    def test_startup_retries_on_transient_freeipa_failure(self) -> None:
        startup_module = importlib.reload(core.startup)

        with (
            patch(
                "core.startup.membership_type_group_cns",
                return_value={"individual-members"},
            ),
            patch(
                "core.startup.FreeIPAGroup.get",
                side_effect=[
                    requests.exceptions.ConnectionError("temporary-1"),
                    requests.exceptions.ConnectionError("temporary-2"),
                    SimpleNamespace(fas_group=False),
                ],
            ) as get_mock,
            patch("core.startup.time", create=True) as time_mock,
        ):
            startup_module.ensure_membership_type_groups_exist()

        self.assertEqual(get_mock.call_count, 3)
        self.assertEqual(time_mock.sleep.call_count, 2)
        self.assertTrue(startup_module._membership_groups_synced)

    def test_startup_logs_error_after_all_retries_exhausted(self) -> None:
        startup_module = importlib.reload(core.startup)

        with (
            patch(
                "core.startup.membership_type_group_cns",
                return_value={"individual-members"},
            ),
            patch(
                "core.startup.FreeIPAGroup.get",
                side_effect=requests.exceptions.ConnectionError("still-down"),
            ) as get_mock,
            patch("core.startup.time", create=True) as time_mock,
            self.assertLogs("core.startup", level="ERROR") as log_context,
        ):
            startup_module.ensure_membership_type_groups_exist()

        self.assertEqual(get_mock.call_count, 3)
        self.assertEqual(time_mock.sleep.call_count, 2)
        self.assertFalse(startup_module._membership_groups_synced)
        self.assertTrue(
            any(
                "FreeIPA unavailable after" in message
                for message in log_context.output
            ),
        )

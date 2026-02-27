
from unittest.mock import patch

import requests
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase

from core.freeipa.circuit_breaker import (
    _elections_freeipa_circuit_open,
    _freeipa_circuit_open,
)
from core.freeipa.client import _with_freeipa_service_client_retry
from core.freeipa.exceptions import FreeIPAUnavailableError
from core.freeipa.group import get_freeipa_group_for_elections


class FreeIPACircuitBreakerTests(TestCase):
    def setUp(self) -> None:
        cache.clear()

    def tearDown(self) -> None:
        cache.clear()

    def test_circuit_opens_after_three_availability_failures(self) -> None:
        calls: dict[str, int] = {"count": 0}

        def get_client() -> object:
            calls["count"] += 1
            return object()

        def fail(_client: object) -> None:
            raise requests.exceptions.ConnectionError()

        for _ in range(3):
            with self.assertRaises(requests.exceptions.ConnectionError):
                _with_freeipa_service_client_retry(get_client, fail)

        self.assertTrue(_freeipa_circuit_open())

        calls["count"] = 0
        with self.assertRaises(FreeIPAUnavailableError):
            _with_freeipa_service_client_retry(get_client, lambda _client: None)
        self.assertEqual(calls["count"], 0)

    def test_circuit_resets_after_success(self) -> None:
        def get_client() -> object:
            return object()

        def fail(_client: object) -> None:
            raise requests.exceptions.Timeout()

        with self.assertRaises(requests.exceptions.Timeout):
            _with_freeipa_service_client_retry(get_client, fail)

        _with_freeipa_service_client_retry(get_client, lambda _client: {"ok": True})

        for _ in range(2):
            with self.assertRaises(requests.exceptions.Timeout):
                _with_freeipa_service_client_retry(get_client, fail)

        self.assertFalse(_freeipa_circuit_open())

        with self.assertRaises(requests.exceptions.Timeout):
            _with_freeipa_service_client_retry(get_client, fail)

        self.assertTrue(_freeipa_circuit_open())

    def test_elections_circuit_requires_failure_threshold_before_open(self) -> None:
        threshold = settings.FREEIPA_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES

        with (
            self.assertLogs("core.backends", level="WARNING") as captured,
            patch(
                "core.freeipa.group._with_freeipa_service_client_retry",
                side_effect=requests.exceptions.ConnectionError("transient network failure"),
            ) as mocked_lookup,
        ):
            with self.assertRaises(FreeIPAUnavailableError):
                get_freeipa_group_for_elections(cn="elections-voters", require_fresh=True)
            self.assertFalse(_elections_freeipa_circuit_open())

            for _ in range(max(threshold - 2, 0)):
                with self.assertRaises(FreeIPAUnavailableError):
                    get_freeipa_group_for_elections(cn="elections-voters", require_fresh=True)
                self.assertFalse(_elections_freeipa_circuit_open())

            with self.assertRaises(FreeIPAUnavailableError):
                get_freeipa_group_for_elections(cn="elections-voters", require_fresh=True)

        self.assertTrue(_elections_freeipa_circuit_open())
        self.assertTrue(any("astra.freeipa.circuit_breaker.transition" in entry for entry in captured.output))
        self.assertGreaterEqual(mocked_lookup.call_count, threshold)

    def test_circuit_transition_log_contains_required_structured_fields(self) -> None:
        threshold = settings.FREEIPA_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES

        with (
            patch(
                "core.freeipa.group._with_freeipa_service_client_retry",
                side_effect=requests.exceptions.ConnectionError("transient network failure"),
            ),
            patch("core.freeipa.circuit_breaker.logger.warning") as warning_mock,
        ):
            for _ in range(threshold):
                with self.assertRaises(FreeIPAUnavailableError):
                    get_freeipa_group_for_elections(cn="elections-voters", require_fresh=True)

        transition_call = None
        for call in warning_mock.call_args_list:
            message = str(call.args[0]) if call.args else ""
            if "astra.freeipa.circuit_breaker.transition" in message:
                transition_call = call
                break

        self.assertIsNotNone(transition_call)
        assert transition_call is not None
        extra = transition_call.kwargs.get("extra", {})

        self.assertEqual(extra.get("event"), "astra.freeipa.circuit_breaker.transition")
        self.assertEqual(extra.get("component"), "freeipa")
        self.assertEqual(extra.get("from_state"), "closed")
        self.assertEqual(extra.get("to_state"), "open")
        self.assertEqual(extra.get("outcome"), "transition")
        self.assertEqual(extra.get("breaker_name"), "freeipa.elections")
        self.assertIn("correlation_id", extra)

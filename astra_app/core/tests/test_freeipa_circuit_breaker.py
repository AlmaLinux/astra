
import requests
from django.core.cache import cache
from django.test import TestCase

from core import backends


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
                backends._with_freeipa_service_client_retry(get_client, fail)

        self.assertTrue(backends._freeipa_circuit_open())

        calls["count"] = 0
        with self.assertRaises(backends.FreeIPAUnavailableError):
            backends._with_freeipa_service_client_retry(get_client, lambda _client: None)
        self.assertEqual(calls["count"], 0)

    def test_circuit_resets_after_success(self) -> None:
        def get_client() -> object:
            return object()

        def fail(_client: object) -> None:
            raise requests.exceptions.Timeout()

        with self.assertRaises(requests.exceptions.Timeout):
            backends._with_freeipa_service_client_retry(get_client, fail)

        backends._with_freeipa_service_client_retry(get_client, lambda _client: {"ok": True})

        for _ in range(2):
            with self.assertRaises(requests.exceptions.Timeout):
                backends._with_freeipa_service_client_retry(get_client, fail)

        self.assertFalse(backends._freeipa_circuit_open())

        with self.assertRaises(requests.exceptions.Timeout):
            backends._with_freeipa_service_client_retry(get_client, fail)

        self.assertTrue(backends._freeipa_circuit_open())

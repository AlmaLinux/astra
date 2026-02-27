from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings

from core.address_geocoding import decompose_full_address_with_photon


class _FakeResponse:
    def __init__(self, payload: dict[str, object], *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> dict[str, object]:
        return self._payload


class AddressGeocodingTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()

    def test_decompose_full_address_with_photon_returns_parsed_parts(self) -> None:
        payload = {
            "features": [
                {
                    "properties": {
                        "street": "Main St",
                        "housenumber": "123",
                        "city": "Austin",
                        "state": "Texas",
                        "postcode": "78701",
                        "countrycode": "us",
                    }
                }
            ]
        }

        with patch("core.address_geocoding.requests.get", return_value=_FakeResponse(payload)):
            result = decompose_full_address_with_photon("123 Main St, Austin, TX 1")

        self.assertEqual(
            result,
            {
                "street": "Main St 123",
                "city": "Austin",
                "state": "Texas",
                "postal_code": "78701",
                "country_code": "US",
            },
        )

    def test_decompose_full_address_with_photon_returns_empty_on_failure(self) -> None:
        with patch("core.address_geocoding.requests.get", side_effect=RuntimeError("boom")):
            result = decompose_full_address_with_photon("123 Main St, Austin, TX 2")

        self.assertEqual(result, {})

    def test_decompose_full_address_with_photon_retries_transient_failures(self) -> None:
        payload = {
            "features": [
                {
                    "properties": {
                        "street": "Main St",
                        "housenumber": "123",
                        "city": "Austin",
                        "state": "Texas",
                        "postcode": "78701",
                        "countrycode": "us",
                    }
                }
            ]
        }

        with patch(
            "core.address_geocoding.requests.get",
            side_effect=[RuntimeError("temporary"), _FakeResponse(payload)],
        ):
            result = decompose_full_address_with_photon("123 Main St, Austin, TX 3")

        self.assertEqual(result.get("country_code"), "US")

    def test_decompose_full_address_with_photon_does_not_retry_403(self) -> None:
        # 403 is a permanent rejection; retrying wastes time and logs.
        with patch(
            "core.address_geocoding.requests.get",
            return_value=_FakeResponse({}, status_code=403),
        ) as mock_get:
            result = decompose_full_address_with_photon("https://www.example.com")

        self.assertEqual(result, {})
        self.assertEqual(mock_get.call_count, 1)

    def test_decompose_full_address_with_photon_retries_on_other_errors(self) -> None:
        # Non-listed codes (e.g. 429) are still retried.
        with patch(
            "core.address_geocoding.requests.get",
            return_value=_FakeResponse({}, status_code=429),
        ) as mock_get:
            result = decompose_full_address_with_photon("https://www.example.com 429")

        self.assertEqual(result, {})
        self.assertEqual(mock_get.call_count, 3)

    @override_settings(GEOCODING_ENDPOINT="https://geo.example.test/search/")
    def test_decompose_full_address_uses_configured_geocoding_endpoint(self) -> None:
        payload = {
            "features": [
                {
                    "properties": {
                        "street": "Main St",
                        "housenumber": "123",
                        "city": "Austin",
                        "state": "Texas",
                        "postcode": "78701",
                        "countrycode": "us",
                    }
                }
            ]
        }
        response = _FakeResponse(payload)

        with patch("core.address_geocoding.requests.get", return_value=response) as mock_get:
            result = decompose_full_address_with_photon("123 Main St, Austin, TX 4")

        self.assertEqual(result.get("country_code"), "US")
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_get.call_args.args[0], "https://geo.example.test/search/")

    @override_settings(GEOCODING_TIMEOUT=17)
    def test_decompose_full_address_uses_configured_geocoding_timeout(self) -> None:
        payload = {
            "features": [
                {
                    "properties": {
                        "street": "Main St",
                        "housenumber": "123",
                        "city": "Austin",
                        "state": "Texas",
                        "postcode": "78701",
                        "countrycode": "us",
                    }
                }
            ]
        }
        response = _FakeResponse(payload)

        with patch("core.address_geocoding.requests.get", return_value=response) as mock_get:
            result = decompose_full_address_with_photon("123 Main St, Austin, TX 5")

        self.assertEqual(result.get("country_code"), "US")
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 17)

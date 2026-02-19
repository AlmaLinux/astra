from unittest.mock import patch
from urllib.error import HTTPError

from django.test import TestCase

from core.address_geocoding import decompose_full_address_with_photon


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload.encode("utf-8")


class AddressGeocodingTests(TestCase):
    def test_decompose_full_address_with_photon_returns_parsed_parts(self) -> None:
        payload = (
            '{"features": ['
            '{"properties": {'
            '"street": "Main St", '
            '"housenumber": "123", '
            '"city": "Austin", '
            '"state": "Texas", '
            '"postcode": "78701", '
            '"countrycode": "us"'
            '}}]}'
        )

        with patch("core.address_geocoding.urlopen", return_value=_FakeResponse(payload)):
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
        with patch("core.address_geocoding.urlopen", side_effect=RuntimeError("boom")):
            result = decompose_full_address_with_photon("123 Main St, Austin, TX 2")

        self.assertEqual(result, {})

    def test_decompose_full_address_with_photon_retries_transient_failures(self) -> None:
        payload = (
            '{"features": ['
            '{"properties": {'
            '"street": "Main St", '
            '"housenumber": "123", '
            '"city": "Austin", '
            '"state": "Texas", '
            '"postcode": "78701", '
            '"countrycode": "us"'
            '}}]}'
        )

        with patch(
            "core.address_geocoding.urlopen",
            side_effect=[RuntimeError("temporary"), _FakeResponse(payload)],
        ):
            result = decompose_full_address_with_photon("123 Main St, Austin, TX 3")

        self.assertEqual(result.get("country_code"), "US")

    def test_decompose_full_address_with_photon_does_not_retry_403(self) -> None:
        # 403 is a permanent rejection; retrying wastes time and logs.
        error = HTTPError(url=None, code=403, msg="Forbidden", hdrs=None, fp=None)  # type: ignore[arg-type]
        with patch("core.address_geocoding.urlopen", side_effect=error) as mock_urlopen:
            result = decompose_full_address_with_photon("https://www.example.com")

        self.assertEqual(result, {})
        self.assertEqual(mock_urlopen.call_count, 1)

    def test_decompose_full_address_with_photon_retries_on_other_errors(self) -> None:
        # Non-listed codes (e.g. 429) are still retried.
        rate_limit = HTTPError(url=None, code=429, msg="Too Many Requests", hdrs=None, fp=None)  # type: ignore[arg-type]
        with patch("core.address_geocoding.urlopen", side_effect=rate_limit) as mock_urlopen:
            result = decompose_full_address_with_photon("https://www.example.com 429")

        self.assertEqual(result, {})
        self.assertEqual(mock_urlopen.call_count, 3)

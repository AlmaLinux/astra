from unittest.mock import patch

from django.test import TestCase

from core.address_geocoding import decompose_full_address_with_photon


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
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

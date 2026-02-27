import hashlib
import logging
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)

_PHOTON_ADDRESS_CACHE_PREFIX = "photon_address_parts:"
_PHOTON_ADDRESS_CACHE_TTL_SECONDS = 24 * 60 * 60
_PHOTON_MAX_ATTEMPTS = 3


def _photon_address_parts_from_feature(feature: dict[str, Any]) -> dict[str, str] | None:
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        return None

    street_name = _normalize_str(properties.get("street") or properties.get("name"))
    house_number = _normalize_str(properties.get("housenumber"))
    street = f"{street_name} {house_number}".strip()

    city = _normalize_str(
        properties.get("city")
        or properties.get("town")
        or properties.get("village")
        or properties.get("hamlet")
        or properties.get("locality")
    )
    state = _normalize_str(properties.get("state"))
    postal_code = _normalize_str(properties.get("postcode"))
    country_code = _normalize_str(properties.get("countrycode")).upper()

    result = {
        "street": street,
        "city": city,
        "state": state,
        "postal_code": postal_code,
        "country_code": country_code,
    }
    if not any(result.values()):
        return None

    return {key: value for key, value in result.items() if value}


def decompose_full_address_with_photon(
    full_address: str,
    *,
    timeout_seconds: int | None = None,
) -> dict[str, str]:
    query = _normalize_str(full_address)
    if not query:
        return {}

    normalized_query = query.lower()
    query_hash = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()
    cache_key = f"{_PHOTON_ADDRESS_CACHE_PREFIX}{query_hash}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    endpoint = settings.GEOCODING_ENDPOINT
    timeout = timeout_seconds if timeout_seconds is not None else settings.GEOCODING_TIMEOUT
    payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(1, _PHOTON_MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                endpoint,
                params={"q": query, "limit": 1},
                headers={"User-Agent": "astra-address-import/1.0"},
                timeout=timeout,
            )
            response.raise_for_status()
            parsed = response.json()
            if isinstance(parsed, dict):
                payload = parsed
            else:
                payload = {}
            break
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Photon geocoding attempt failed query=%r attempt=%d/%d error=%s",
                query,
                attempt,
                _PHOTON_MAX_ATTEMPTS,
                exc,
            )
            # Specific codes that are permanent rejections; retrying won't help.
            if isinstance(exc, requests.HTTPError):
                response = exc.response
                if response is not None and response.status_code == 403:
                    break

    if payload is None:
        if last_error is not None:
            logger.error("Photon geocoding failed for full_address lookup: %s", last_error)
        cache.set(cache_key, {}, timeout=_PHOTON_ADDRESS_CACHE_TTL_SECONDS)
        return {}

    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list) or not features:
        cache.set(cache_key, {}, timeout=_PHOTON_ADDRESS_CACHE_TTL_SECONDS)
        return {}

    first_feature = features[0]
    if not isinstance(first_feature, dict):
        cache.set(cache_key, {}, timeout=_PHOTON_ADDRESS_CACHE_TTL_SECONDS)
        return {}

    result = _photon_address_parts_from_feature(first_feature) or {}
    cache.set(cache_key, result, timeout=_PHOTON_ADDRESS_CACHE_TTL_SECONDS)
    return result

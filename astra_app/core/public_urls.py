from typing import Literal

from django.conf import settings

MissingPublicBaseUrlBehavior = Literal["raise", "relative", "empty"]


class PublicBaseUrlConfigurationError(ValueError):
    """Raised when PUBLIC_BASE_URL is required but not configured."""


def normalize_public_base_url(base_url: str | None) -> str:
    return str(base_url or "").strip().rstrip("/")


def build_public_absolute_url(
    path: str,
    *,
    on_missing: MissingPublicBaseUrlBehavior = "raise",
    base_url: str | None = None,
) -> str:
    normalized_base = normalize_public_base_url(base_url if base_url is not None else settings.PUBLIC_BASE_URL)
    normalized_path = str(path or "").strip()
    if not normalized_path:
        return ""
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"

    if not normalized_base:
        if on_missing == "relative":
            return normalized_path
        if on_missing == "empty":
            return ""
        raise PublicBaseUrlConfigurationError("PUBLIC_BASE_URL must be configured to build absolute links.")

    return f"{normalized_base}{normalized_path}"

import datetime
import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

import requests
from django.core.cache import cache
from django.utils import timezone

_MIRROR_BADGE_CACHE_PREFIX = "mirror-badge-status:"
_MIRROR_BADGE_CACHE_TTL_SECONDS = 12 * 60 * 60
_MIRROR_TIME_TIMEOUT_SECONDS = 2
_MIRROR_TIME_MAX_BYTES = 128
_MIRROR_TIME_FRESHNESS_WINDOW = datetime.timedelta(hours=24)


class MirrorBadgeUrlValidationError(ValueError):
    """Raised when a mirror URL is not eligible for TIME probing."""


class MirrorBadgeHostBlockedError(ValueError):
    """Raised when a mirror hostname resolves to a blocked network."""


class MirrorBadgeHostResolutionError(ValueError):
    """Raised when a mirror hostname cannot be resolved."""


@dataclass(frozen=True)
class MirrorBadgeStatus:
    key: str
    label: str
    color: str


_STATUS_OK = MirrorBadgeStatus(key="ok", label="ok", color="#2f9e44")
_STATUS_STALE = MirrorBadgeStatus(key="stale", label="stale", color="#d48806")
_STATUS_MISSING = MirrorBadgeStatus(key="missing", label="missing", color="#6c757d")
_STATUS_TIMED_OUT = MirrorBadgeStatus(key="timed_out", label="timed out", color="#b35c00")
_STATUS_UNREACHABLE = MirrorBadgeStatus(key="unreachable", label="unreachable", color="#b02a37")
_STATUS_INVALID = MirrorBadgeStatus(key="invalid", label="invalid", color="#343a40")
_STATUS_BLOCKED = MirrorBadgeStatus(key="blocked", label="blocked", color="#5f3dc4")
_STATUS_ERROR = MirrorBadgeStatus(key="error", label="error", color="#343a40")

_STATUS_BY_KEY: dict[str, MirrorBadgeStatus] = {
    _STATUS_OK.key: _STATUS_OK,
    _STATUS_STALE.key: _STATUS_STALE,
    _STATUS_MISSING.key: _STATUS_MISSING,
    _STATUS_TIMED_OUT.key: _STATUS_TIMED_OUT,
    _STATUS_UNREACHABLE.key: _STATUS_UNREACHABLE,
    _STATUS_INVALID.key: _STATUS_INVALID,
    _STATUS_BLOCKED.key: _STATUS_BLOCKED,
    _STATUS_ERROR.key: _STATUS_ERROR,
}


def mirror_badge_tooltip(status: MirrorBadgeStatus) -> str:
    match status.key:
        case "ok":
            return "Mirror status: ok (TIME updated within the last 24 hours)."
        case "stale":
            return "Mirror status: stale (TIME is older than 24 hours, or from the future)."
        case "missing":
            return "Mirror status: missing (TIME returned HTTP 404)."
        case "timed_out":
            return "Mirror status: timed out (TIME request exceeded the timeout)."
        case "unreachable":
            return "Mirror status: unreachable (DNS lookup or connection failed)."
        case "invalid":
            return "Mirror status: invalid (expected an http(s) URL with a hostname; no query/fragment/userinfo)."
        case "blocked":
            return "Mirror status: blocked (hostname resolves to a private or otherwise blocked IP range)."
        case _:
            return "Mirror status: error (unexpected response or invalid TIME contents)."


def _format_netloc(hostname: str, port: int | None) -> str:
    host_part = hostname
    if ":" in hostname:
        host_part = f"[{hostname}]"
    if port is None:
        return host_part
    return f"{host_part}:{port}"


def _is_blocked_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _validate_resolved_host(hostname: str, port: int) -> None:
    try:
        host_info = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise MirrorBadgeHostResolutionError("Host cannot be resolved.") from exc

    if not host_info:
        raise MirrorBadgeHostResolutionError("Host cannot be resolved.")

    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for _, _, _, _, sockaddr in host_info:
        ip_text = str(sockaddr[0]).strip()
        if not ip_text:
            continue
        addresses.add(ipaddress.ip_address(ip_text))

    if not addresses:
        raise MirrorBadgeHostResolutionError("Host cannot be resolved.")

    if any(_is_blocked_ip(address) for address in addresses):
        raise MirrorBadgeHostBlockedError("Host resolves to a blocked network.")


def normalize_mirror_base_url(raw_url: str) -> tuple[str, str, int]:
    candidate = str(raw_url or "").strip()
    if not candidate:
        raise MirrorBadgeUrlValidationError("url is required.")

    parts = urlsplit(candidate)
    scheme = str(parts.scheme or "").lower()
    if scheme not in {"https", "http"}:
        raise MirrorBadgeUrlValidationError("Only http and https URLs are allowed.")

    if parts.username or parts.password:
        raise MirrorBadgeUrlValidationError("User info in URL is not allowed.")

    if parts.query or parts.fragment:
        raise MirrorBadgeUrlValidationError("Query strings and fragments are not allowed.")

    try:
        port = parts.port
    except ValueError as exc:
        raise MirrorBadgeUrlValidationError("URL port is invalid.") from exc
    if scheme == "http" and port not in {None, 80}:
        raise MirrorBadgeUrlValidationError("http URLs are limited to port 80.")

    hostname = str(parts.hostname or "").strip()
    if not hostname:
        raise MirrorBadgeUrlValidationError("A hostname is required.")

    normalized_path = "/" + str(parts.path or "").lstrip("/")
    normalized_path = normalized_path.rstrip("/") or "/"

    normalized_parts = SplitResult(
        scheme=scheme,
        netloc=_format_netloc(hostname.lower(), port),
        path=normalized_path,
        query="",
        fragment="",
    )
    normalized = urlunsplit(normalized_parts)

    resolved_port = 443 if scheme == "https" else 80
    if port is not None:
        resolved_port = port
    return normalized, hostname, resolved_port


def _time_url_from_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    time_path = f"{parts.path.rstrip('/')}/TIME"
    if not time_path.startswith("/"):
        time_path = f"/{time_path}"
    return urlunsplit((parts.scheme, parts.netloc, time_path, "", ""))


def _read_time_payload(response: requests.Response) -> str:
    chunks: list[bytes] = []
    total_bytes = 0
    for chunk in response.iter_content(chunk_size=32):
        if not chunk:
            continue
        total_bytes += len(chunk)
        if total_bytes > _MIRROR_TIME_MAX_BYTES:
            raise ValueError("TIME payload is too large.")
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace").strip()


def _parse_time_value(raw_value: str) -> datetime.datetime | None:
    value = str(raw_value or "").strip()
    if not value:
        return None

    try:
        seconds = float(value)
    except ValueError:
        seconds = None

    if seconds is not None:
        if seconds > 10_000_000_000:
            seconds = seconds / 1000.0
        try:
            parsed = datetime.datetime.fromtimestamp(seconds, tz=datetime.UTC)
        except (ValueError, OSError, OverflowError):
            return None
        return parsed

    iso_value = value
    if iso_value.endswith("Z"):
        iso_value = f"{iso_value[:-1]}+00:00"
    try:
        parsed_iso = datetime.datetime.fromisoformat(iso_value)
    except ValueError:
        return None
    if parsed_iso.tzinfo is None:
        parsed_iso = parsed_iso.replace(tzinfo=datetime.UTC)
    return parsed_iso.astimezone(datetime.UTC)


def _status_from_response(*, response: requests.Response, now_utc: datetime.datetime) -> MirrorBadgeStatus:
    if response.status_code == 404:
        return _STATUS_MISSING
    if response.status_code != 200:
        return _STATUS_ERROR

    try:
        payload = _read_time_payload(response)
    except ValueError:
        return _STATUS_ERROR

    parsed_time = _parse_time_value(payload)
    if parsed_time is None:
        return _STATUS_ERROR

    if parsed_time > now_utc:
        return _STATUS_STALE

    if now_utc - parsed_time <= _MIRROR_TIME_FRESHNESS_WINDOW:
        return _STATUS_OK

    return _STATUS_STALE


def _cache_key_for_url(base_url: str) -> str:
    digest = hashlib.sha256(base_url.encode("utf-8")).hexdigest()
    return f"{_MIRROR_BADGE_CACHE_PREFIX}{digest}"


def probe_mirror_time_status(raw_url: str) -> MirrorBadgeStatus:
    raw_candidate = str(raw_url or "").strip()
    invalid_cache_key = _cache_key_for_url(f"invalid:{raw_candidate}")
    cached_invalid = cache.get(invalid_cache_key)
    if cached_invalid == _STATUS_INVALID.key:
        return _STATUS_INVALID

    try:
        normalized_url, hostname, resolved_port = normalize_mirror_base_url(raw_candidate)
    except MirrorBadgeUrlValidationError:
        cache.set(invalid_cache_key, _STATUS_INVALID.key, timeout=_MIRROR_BADGE_CACHE_TTL_SECONDS)
        return _STATUS_INVALID

    cache_key = _cache_key_for_url(normalized_url)
    cached = cache.get(cache_key)
    if isinstance(cached, str):
        cached_status = _STATUS_BY_KEY.get(cached)
        if cached_status is not None:
            return cached_status

    try:
        _validate_resolved_host(hostname, resolved_port)
    except MirrorBadgeHostBlockedError:
        status = _STATUS_BLOCKED
        cache.set(cache_key, status.key, timeout=_MIRROR_BADGE_CACHE_TTL_SECONDS)
        return status
    except MirrorBadgeHostResolutionError:
        status = _STATUS_UNREACHABLE
        cache.set(cache_key, status.key, timeout=_MIRROR_BADGE_CACHE_TTL_SECONDS)
        return status

    time_url = _time_url_from_base_url(normalized_url)
    now_utc = timezone.now().astimezone(datetime.UTC)
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(
                time_url,
                timeout=(_MIRROR_TIME_TIMEOUT_SECONDS, _MIRROR_TIME_TIMEOUT_SECONDS),
                allow_redirects=False,
                stream=True,
                headers={"User-Agent": "astra-mirror-badge/1.0"},
            )
    except requests.exceptions.Timeout:
        status = _STATUS_TIMED_OUT
    except requests.exceptions.ConnectionError:
        status = _STATUS_UNREACHABLE
    except requests.exceptions.RequestException:
        status = _STATUS_ERROR
    except Exception:
        status = _STATUS_ERROR
    else:
        try:
            status = _status_from_response(response=response, now_utc=now_utc)
        finally:
            response.close()

    cache.set(cache_key, status.key, timeout=_MIRROR_BADGE_CACHE_TTL_SECONDS)
    return status


def render_mirror_badge_svg(status: MirrorBadgeStatus) -> str:
    # Render at a small native size, without relying on aggressive downscaling.
    # A slightly wider badge keeps the text readable (esp. labels like "unreachable").
    svg_width = 140
    svg_height = 20
    left_width = 60
    right_width = svg_width - left_width

    label_left = "mirror"
    label_right = status.label

    right_len = len(label_right)
    if right_len <= 8:
        right_font_size = 10
    elif right_len <= 10:
        right_font_size = 9
    else:
        right_font_size = 8

    left_center_x = left_width / 2
    right_center_x = left_width + (right_width / 2)

    return (
        f'<svg width="{svg_width}" height="{svg_height}" viewBox="0 0 {svg_width} {svg_height}" '
        'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Mirror status">'
        f"<title>{label_left}: {label_right}</title>"
        '<defs>'
        '<linearGradient id="s" x2="0" y2="1">'
        '<stop offset="0" stop-color="#fff" stop-opacity=".15"/>'
        '<stop offset="1" stop-opacity=".15"/>'
        '</linearGradient>'
        '</defs>'
        f'<g clip-path="inset(0 round 3)">'
        f'<rect width="{left_width}" height="{svg_height}" fill="#4a414e"/>'
        f'<rect x="{left_width}" width="{right_width}" height="{svg_height}" fill="{status.color}"/>'
        f'<rect width="{svg_width}" height="{svg_height}" fill="url(#s)"/>'
        '</g>'
        '<g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" font-weight="700" text-rendering="geometricPrecision">'
        f'<text x="{left_center_x}" y="14" font-size="10" text-anchor="middle" fill="#010101" fill-opacity=".25">{label_left}</text>'
        f'<text x="{left_center_x}" y="13" font-size="10" text-anchor="middle">{label_left}</text>'
        f'<text x="{right_center_x}" y="14" font-size="{right_font_size}" text-anchor="middle" fill="#010101" fill-opacity=".25">{label_right}</text>'
        f'<text x="{right_center_x}" y="13" font-size="{right_font_size}" text-anchor="middle">{label_right}</text>'
        '</g>'
        '</svg>'
    )


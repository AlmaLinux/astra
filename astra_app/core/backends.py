from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from functools import lru_cache

from django.conf import settings
from django.contrib.auth.backends import BaseBackend
from django.core.cache import cache
from django.utils.crypto import salted_hmac
from python_freeipa import ClientMeta, exceptions

logger = logging.getLogger(__name__)

_service_client_local = threading.local()
_viewer_username_local = threading.local()


def _clean_str_list(values: object) -> list[str]:
    """Normalize FreeIPA multi-valued attributes into a clean list[str].

    FreeIPA (and plugins) can return strings, lists, or missing values.
    We sanitize at the ingestion boundary so the rest of the codebase can
    treat these as stable, already-clean lists.
    """

    if values is None:
        return []
    if isinstance(values, str):
        s = values.strip()
        return [s] if s else []
    if isinstance(values, (list, tuple, set)):
        out: list[str] = []
        seen: set[str] = set()
        for item in values:
            if item is None:
                continue
            s = str(item).strip()
            if not s or s in seen:
                continue
            out.append(s)
            seen.add(s)
        return out

    s = str(values).strip()
    return [s] if s else []


def _first_attr_ci(data: dict[str, object], key: str, default: object | None = None) -> object | None:
    """Return the first value for an attribute key, case-insensitively.

    FreeIPA extensions sometimes expose attributes in different casings
    depending on client/server/plugin versions (e.g. `fasisprivate` vs
    `fasIsPrivate`).
    """

    if key in data:
        value = data.get(key, default)
    else:
        key_lower = key.lower()
        value = data.get(key_lower)
        if value is None:
            for k, v in data.items():
                if str(k).lower() == key_lower:
                    value = v
                    break
            else:
                value = default

    if isinstance(value, list):
        return value[0] if value else default
    return value


class FreeIPAOperationFailed(RuntimeError):
    """Raised when FreeIPA returns a structured failure without raising."""


class FreeIPAUnavailableError(RuntimeError):
    """Raised when elections-critical FreeIPA lookups cannot be completed."""


class FreeIPAMisconfiguredError(RuntimeError):
    """Raised when elections-critical FreeIPA configuration is missing."""


def _compact_repr(value: object, *, limit: int = 400) -> str:
    rendered = repr(value)
    if len(rendered) > limit:
        return f"{rendered[:limit]}…"
    return rendered


def _has_truthy_failure(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        # A non-empty dict usually indicates at least one member entry failed.
        return len(value) > 0
    return bool(value)


def _raise_if_freeipa_failed(result: object, *, action: str, subject: str) -> None:
    if not isinstance(result, dict):
        return

    failed = result.get("failed")
    if not failed:
        return

    def failed_has_truthy(value: object) -> bool:
        if isinstance(value, dict):
            return any(failed_has_truthy(v) for v in value.values())
        if isinstance(value, list):
            return any(failed_has_truthy(v) for v in value)
        return _has_truthy_failure(value)

    # FreeIPA's group_{add,remove}_member often returns a `failed` skeleton even
    # on success, e.g. {'member': {'user': [], 'group': [], ...}}. Only treat it
    # as an error when any member bucket is non-empty.
    if action in {"group_add_member", "group_remove_member"} and isinstance(failed, dict):
        member = failed.get("member")
        if isinstance(member, dict):
            user_bucket = member.get("user")

            def _is_benign_group_member_message(value: object, *, is_add: bool) -> bool:
                # FreeIPA can return human strings here such as:
                # - add: "This entry is already a member"
                # - remove: "This entry is not a member"
                # Treat these as idempotent outcomes so membership sync/extension
                # logic doesn't fail when state already matches the request.
                text = str(value or "").strip().lower()
                if not text:
                    return True
                if is_add:
                    return "already" in text and "member" in text
                return "not" in text and "member" in text

            if isinstance(user_bucket, list) and user_bucket:
                if action == "group_add_member" and all(
                    _is_benign_group_member_message(v, is_add=True) for v in user_bucket
                ):
                    user_bucket = []
                if action == "group_remove_member" and all(
                    _is_benign_group_member_message(v, is_add=False) for v in user_bucket
                ):
                    user_bucket = []

            buckets = [
                user_bucket,
                member.get("group"),
                member.get("service"),
                member.get("idoverrideuser"),
            ]
            if not any(_has_truthy_failure(b) for b in buckets):
                return

    if action in {"group_add_member_manager", "group_remove_member_manager"}:
        # These operations may return a `failed` skeleton on success.
        if not failed_has_truthy(failed):
            return

    # FreeIPA's fasagreement membership operations can also return a `failed`
    # skeleton on success, e.g. {'member': {'group': []}} or
    # {'memberuser': {'user': []}} depending on method and server version.
    if action in {
        "fasagreement_add_group",
        "fasagreement_remove_group",
        "fasagreement_add_user",
        "fasagreement_remove_user",
    } and isinstance(failed, dict):
        buckets: list[object] = []
        member = failed.get("member")
        if isinstance(member, dict):
            buckets.extend([member.get("group"), member.get("user")])
        memberuser = failed.get("memberuser")
        if isinstance(memberuser, dict):
            buckets.extend([memberuser.get("user")])

        if buckets and not any(_has_truthy_failure(b) for b in buckets):
            return

    items: list[str] = []

    def walk(prefix: list[str], value: object) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk([*prefix, str(k)], v)
            return
        if isinstance(value, list):
            for v in value:
                walk(prefix, v)
            return
        if value is None:
            return
        key = "/".join(prefix) if prefix else "failed"
        items.append(f"{key}: {value}")

    walk([], failed)
    details = "; ".join(items[:6])
    if len(items) > 6:
        details = f"{details}; …"
    if not details:
        details = f"failed={_compact_repr(failed)}"
    raise FreeIPAOperationFailed(f"FreeIPA {action} failed ({subject}): {details}")


def _get_freeipa_client(username: str, password: str) -> ClientMeta:
    """Create and login a FreeIPA client.

    Centralize client construction + login so we don't duplicate host/SSL/login
    wiring across user/group helpers and the auth backend.
    """

    client = ClientMeta(host=settings.FREEIPA_HOST, verify_ssl=settings.FREEIPA_VERIFY_SSL)
    client.login(username, password)
    return client


def _get_freeipa_service_client_cached() -> ClientMeta:
    """Return a cached service-account client for the current thread.

    FreeIPA operations often happen in bursts during a single request.
    Reusing the service client's logged-in session avoids repeated logins.
    The per-request middleware clears this cache at request boundaries.
    """

    if hasattr(_service_client_local, "client"):
        client = _service_client_local.client
        if client is not None:
            return client

    client = _get_freeipa_client(settings.FREEIPA_SERVICE_USER, settings.FREEIPA_SERVICE_PASSWORD)
    _service_client_local.client = client
    return client


def clear_freeipa_service_client_cache() -> None:
    """Clear any cached service client for the current thread."""

    if hasattr(_service_client_local, "client"):
        delattr(_service_client_local, "client")


def set_current_viewer_username(username: str | None) -> None:
    """Set the per-request viewer username for privacy redaction.

    We redact "private" users (fasIsPrivate) for everyone except the viewer.
    This is stored in a threadlocal so the anonymization decision can be made
    at the FreeIPA ingestion boundary (FreeIPAUser initialization).
    """

    if username is None:
        if hasattr(_viewer_username_local, "username"):
            delattr(_viewer_username_local, "username")
        return

    normalized = str(username).strip()
    if not normalized:
        if hasattr(_viewer_username_local, "username"):
            delattr(_viewer_username_local, "username")
        return

    _viewer_username_local.username = normalized


def clear_current_viewer_username() -> None:
    if hasattr(_viewer_username_local, "username"):
        delattr(_viewer_username_local, "username")


def _get_current_viewer_username() -> str | None:
    if not hasattr(_viewer_username_local, "username"):
        return None

    username = _viewer_username_local.username
    if isinstance(username, str) and username.strip():
        return username
    return None


def _with_freeipa_service_client_retry[T](get_client: Callable[[], ClientMeta], fn: Callable[[ClientMeta], T]) -> T:
    """Run a service-account request, retrying once if the session expired.

    python-freeipa raises Unauthorized on HTTP 401, which is what we see when
    the FreeIPA session cookie expires or a connection is reset.
    """

    try:
        client = get_client()
        return fn(client)
    except exceptions.PasswordExpired as e:
        # Service account password expiration is not recoverable by retrying.
        # Make it loud in logs and let the request fail as a 500.
        logger.exception(f"FreeIPA service account password expired: {e}")
        clear_freeipa_service_client_cache()
        raise
    except exceptions.Unauthorized:
        clear_freeipa_service_client_cache()
        client = get_client()
        return fn(client)
    except Exception as e:
        logger.exception(f"FreeIPA service account operation failed: {e}")
        raise


def _user_cache_key(username: str) -> str:
    # Keep legacy key format to avoid surprises.
    return f'freeipa_user_{username}'


def _group_cache_key(cn: str) -> str:
    return f'freeipa_group_{cn}'


def _users_list_cache_key() -> str:
    return 'freeipa_users_all'


def _groups_list_cache_key() -> str:
    return 'freeipa_groups_all'


def _agreements_list_cache_key() -> str:
    return "freeipa_fasagreements_all"


def _invalidate_users_list_cache() -> None:
    cache.delete(_users_list_cache_key())


def _invalidate_groups_list_cache() -> None:
    cache.delete(_groups_list_cache_key())


def _invalidate_agreements_list_cache() -> None:
    cache.delete(_agreements_list_cache_key())


def _invalidate_user_cache(username: str) -> None:
    cache.delete(_user_cache_key(username))


def _invalidate_group_cache(cn: str) -> None:
    cache.delete(_group_cache_key(cn))


_ELECTIONS_FREEIPA_CIRCUIT_CACHE_KEY = "freeipa_elections_circuit_open"


def _elections_freeipa_circuit_open() -> bool:
    return bool(cache.get(_ELECTIONS_FREEIPA_CIRCUIT_CACHE_KEY))


def _open_elections_freeipa_circuit() -> None:
    cache.set(
        _ELECTIONS_FREEIPA_CIRCUIT_CACHE_KEY,
        True,
        timeout=settings.ELECTION_FREEIPA_CIRCUIT_BREAKER_SECONDS,
    )


def get_freeipa_group_for_elections(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
    """Fetch a FreeIPA group for elections-critical checks.

    Uses a circuit breaker to fail closed when FreeIPA is unavailable and
    distinguishes missing groups from transient failures.
    """

    group_cn = str(cn or "").strip()
    if not group_cn:
        raise FreeIPAMisconfiguredError("FreeIPA group cn is required")

    if _elections_freeipa_circuit_open():
        raise FreeIPAUnavailableError("FreeIPA circuit breaker is open")

    cache_key = _group_cache_key(group_cn)
    if not require_fresh:
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return FreeIPAGroup(group_cn, cached_data)

    try:
        result = _with_freeipa_service_client_retry(
            FreeIPAGroup.get_client,
            lambda client: client.group_find(o_cn=group_cn, o_all=True, o_no_members=False),
        )
    except Exception as exc:
        _open_elections_freeipa_circuit()
        logger.exception("FreeIPA elections group lookup failed cn=%s: %s", group_cn, exc)
        raise FreeIPAUnavailableError("FreeIPA group lookup failed") from exc

    if not isinstance(result, dict) or result.get("count", 0) <= 0:
        raise FreeIPAMisconfiguredError(f"FreeIPA group not found: {group_cn}")

    group_data = (result.get("result") or [None])[0]
    if not isinstance(group_data, dict):
        raise FreeIPAMisconfiguredError(f"FreeIPA group not found: {group_cn}")

    cache.set(cache_key, group_data)
    return FreeIPAGroup(group_cn, group_data)


def _agreement_cache_key(cn: str) -> str:
    # Agreement CNs can contain spaces and other characters that are invalid for
    # some cache backends (notably memcached). Use a stable hash so keys are
    # always safe and short.
    normalized = cn.strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"freeipa_fasagreement_{digest}"


def _invalidate_agreement_cache(cn: str) -> None:
    cache.delete(_agreement_cache_key(cn))


@lru_cache(maxsize=4096)
def _session_user_id_for_username(username: str) -> int:
    """Return a stable integer id for storing in Django's session.

    We keep Django's default auth user model (integer PK). To avoid the session
    loader failing on non-integer values (e.g. 'admin'), we store a deterministic
    integer derived from the username and SECRET_KEY.

    This does not imply any DB persistence; it is only a session identifier.
    """

    digest = salted_hmac('freeipa-session', username, secret=settings.SECRET_KEY).digest()
    # Use a positive 63-bit integer.
    return int.from_bytes(digest[:8], 'big') & 0x7FFFFFFFFFFFFFFF


class _FreeIPAPK:
    attname = 'username'
    name = 'username'

    def value_to_string(self, obj):
        username = getattr(obj, 'username', None)
        if username:
            return str(_session_user_id_for_username(username))
        return str(getattr(obj, 'pk', ''))


class _FreeIPAMeta:
    pk = _FreeIPAPK()

class FreeIPAManager:
    """
    A mock manager to mimic Django's RelatedManager.
    """
    def __init__(self, iterable):
        self._iterable = iterable

    def all(self):
        return self._iterable

    def count(self):
        return len(self._iterable)

    def __iter__(self):
        return iter(self._iterable)

class FreeIPAUser:
    """
    A non-persistent user object backed by FreeIPA.
    """
    def __init__(self, username, user_data=None):
        self.username = str(username).strip() if username else ""
        self.backend = 'core.backends.FreeIPAAuthBackend'
        # Never keep a direct reference to cached data (especially with the
        # locmem cache backend) to avoid any accidental cross-request/viewer
        # contamination.
        self._user_data = dict(user_data) if isinstance(user_data, dict) else {}
        self.is_authenticated = True
        self.is_anonymous = False
        # Django's auth/session machinery expects model-like metadata.
        self._meta = _FreeIPAMeta()

        # Django may set this via the update_last_login signal. We intentionally
        # do not persist it anywhere (no DB, no FreeIPA, no cache).
        self.last_login = None

        def _first(key, default=None):
            value = self._user_data.get(key, default)
            if isinstance(value, list):
                return value[0] if value else default
            return value

        # Map FreeIPA attributes to Django attributes
        self.first_name = _first('givenname') or ""
        self.last_name = _first('sn') or ""
        # Noggin precedence for display name:
        # displayname > gecos > cn (common name)
        self.commonname = _first("cn") or ""
        self.displayname = _first("displayname") or ""
        self.gecos = _first("gecos") or ""
        # Some upstream template tags (e.g. django-avatar gravatar provider)
        # assume the email attribute is always a string and call .encode().
        # FreeIPA users may not have a mail attribute, so normalize to "".
        self.email = _first('mail') or ""

        # Used for password-reset token invalidation (Noggin-style): if the
        # password changes after issuing a token, that token should no longer
        # be usable.
        krb_last_pwd_change = _first_attr_ci(self._user_data, "krbLastPwdChange", None)
        self.last_password_change = str(krb_last_pwd_change).strip() if krb_last_pwd_change else ""

        # FreeIPA field (Fedora/FAS extension) used as a general status note.
        # Stored in FreeIPA as a text field, usually returned as a list.
        fas_status_note = _first_attr_ci(self._user_data, "fasstatusnote", None)
        self.fasstatusnote = str(fas_status_note).strip() if fas_status_note else ""

        # Privacy flag is commonly exposed as `fasisprivate` (Noggin/FAS), but
        # some clients/plugins surface it as `fasIsPrivate`.
        fas_is_private_raw = _first_attr_ci(self._user_data, "fasIsPrivate", None)
        if fas_is_private_raw is None:
            fas_is_private_raw = _first_attr_ci(self._user_data, "fasisprivate", None)
        if fas_is_private_raw is None:
            self.fas_is_private = False
        elif isinstance(fas_is_private_raw, bool):
            self.fas_is_private = bool(fas_is_private_raw)
        else:
            self.fas_is_private = str(fas_is_private_raw).strip().upper() in {"TRUE", "T", "YES", "Y", "1", "ON"}

        # Determine status based on FreeIPA data
        # nsaccountlock: True means locked (inactive)
        nsaccountlock = self._user_data.get('nsaccountlock', False)
        if isinstance(nsaccountlock, list):
            nsaccountlock = nsaccountlock[0] if nsaccountlock else False
        self.is_active = not bool(nsaccountlock)

        # Permissions/Groups logic
        self.direct_groups_list = _clean_str_list(self._user_data.get("memberof_group", []))
        self.indirect_groups_list = _clean_str_list(self._user_data.get("memberofindirect_group", []))
        self.groups_list = _clean_str_list(self.direct_groups_list + self.indirect_groups_list)

        # Simple mapping for staff/superuser based on groups
        # Configure these group names in settings
        admin_group = settings.FREEIPA_ADMIN_GROUP
        self.is_staff = admin_group in self.groups_list
        self.is_superuser = admin_group in self.groups_list

        viewer_username = _get_current_viewer_username()
        if self.fas_is_private and viewer_username and viewer_username.lower() != self.username.lower():
            self.anonymize()

    @property
    def groups(self):
        """
        Returns a manager-like object containing FreeIPAGroup objects.
        Note: If this user object was created lazily (without data), this list might be empty.
        """
        return FreeIPAManager([FreeIPAGroup(cn) for cn in self.groups_list])

    @property
    def user_permissions(self):
        """
        Returns an empty manager as we use groups for permissions.
        """
        return FreeIPAManager([])

    @property
    def pk(self):
        return _session_user_id_for_username(self.username)

    @property
    def id(self):
        return _session_user_id_for_username(self.username)

    def get_username(self):
        return self.username

    @property
    def full_name(self) -> str:
        # Noggin precedence for display name:
        # displayname > gecos > cn (common name) > givenname+sn > username
        displayname = str(self.displayname or "").strip()
        if displayname:
            return displayname

        gecos = str(self.gecos or "").strip()
        if gecos:
            return gecos

        commonname = str(self.commonname or "").strip()
        if commonname:
            return commonname

        derived = f"{self.first_name or ''} {self.last_name or ''}".strip()
        return derived or self.username

    def get_full_name(self) -> str:
        # Compatibility for Django/user-like APIs. Use `.full_name` for new code.
        return self.full_name

    def anonymize(self) -> None:
        """Redact private fields in-place if the user opted into privacy.

        This keeps only:
        - username
        - groups (memberof_group)
        - fasIsPrivate itself

        Email is redacted (CRITICAL-01 privacy fix).
        Agreements are computed separately from FreeIPA and are not stored on
        the user object.
        """

        if not self.fas_is_private:
            return

        self.email = ""
        self.first_name = ""
        self.last_name = ""
        self.displayname = ""
        self.gecos = ""
        self.commonname = ""

        self._user_data = {
            "uid": [self.username],
            "mail": [],
            "memberof_group": list(self.groups_list),
            "fasIsPrivate": ["TRUE"],
        }

    def get_short_name(self):
        return self.first_name or self.username

    def get_session_auth_hash(self):
        # Used by Django to invalidate sessions on credential changes.
        return salted_hmac('freeipa-user', self.username, secret=settings.SECRET_KEY).hexdigest()

    @classmethod
    def get_client(cls) -> ClientMeta:
        """Return a FreeIPA client authenticated as the service account."""

        return _get_freeipa_service_client_cached()

    @classmethod
    def all(cls):
        """
        Returns a list of all users from FreeIPA.
        """
        def _fetch_users() -> list[dict[str, object]]:
            # FreeIPA server/client may default to returning only 100 entries.
            # Request an unlimited result set where supported.
            result = _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: client.user_find(o_all=True, o_no_members=False, o_sizelimit=0, o_timelimit=0),
            )
            return result.get('result', [])

        try:
            users = cache.get_or_set(_users_list_cache_key(), _fetch_users) or []
            # Cache may legitimately contain an empty list; treat that as a hit.
            excluded = {str(u).strip().lower() for u in settings.FREEIPA_FILTERED_USERNAMES}
            out: list[FreeIPAUser] = []
            for user_data in users:
                if not isinstance(user_data, dict):
                    continue

                uid = user_data.get("uid")
                if isinstance(uid, list):
                    username = uid[0] if uid else ""
                else:
                    username = uid or ""
                username = str(username).strip()
                if not username:
                    continue

                if username.lower() in excluded:
                    continue

                out.append(cls(username, user_data))
            return out
        except Exception as e:
            # On failure, avoid poisoning the cache with an empty list.
            logger.exception(f"Failed to list users: {e}")
            return []

    @classmethod
    def _fetch_full_user(cls, client: ClientMeta, username: str):
        """Return a single user's full attribute dict.

        Prefer user_show (returns full attribute set including custom schema
        like Fedora's FAS fields). Fallback to user_find if needed.
        """
        def _try(label: str, fn):
            try:
                return fn()
            except exceptions.Unauthorized:
                # Treat as an auth/session issue so callers can retry with a fresh login.
                raise
            except TypeError as e:
                # Signature mismatch across python-freeipa versions.
                logger.debug("FreeIPA call failed (TypeError) label=%s username=%s error=%s", label, username, e)
                return None
            except exceptions.FreeIPAError as e:
                # FreeIPA returned an API-level error.
                logger.debug("FreeIPA call failed label=%s username=%s error=%s", label, username, e)
                return None
            except Exception:
                logger.exception("FreeIPA call failed (unexpected) label=%s username=%s", label, username)
                return None

        # Try common call styles across python-freeipa versions.
        res = _try("user_show(username)", lambda: client.user_show(username, o_all=True, o_no_members=False))
        if res and 'result' in res:
            return res['result']

        res = _try("user_show(a_uid=...)", lambda: client.user_show(a_uid=username, o_all=True, o_no_members=False))
        if res and 'result' in res:
            return res['result']

        # Fallback to a targeted find.
        res = _try("user_find(o_uid=...)", lambda: client.user_find(o_uid=username, o_all=True, o_no_members=False))
        if res and res.get('count', 0) > 0:
            return res['result'][0]
        return None

    @classmethod
    def get(cls, username):
        """
        Fetch a single user by username.
        """
        # Check cache first
        cache_key = _user_cache_key(username)
        cached_data = cache.get(cache_key)

        if cached_data is not None:
            return cls(username, cached_data)

        try:
            user_data = _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: cls._fetch_full_user(client, username),
            )
            if user_data is not None:
                cache.set(cache_key, user_data)
                return cls(username, user_data)
        except Exception as e:
            logger.exception(f"Failed to get user username={username}: {e}")
            raise
        return None

    @classmethod
    def find_by_email(cls, email: str) -> FreeIPAUser | None:
        email = (email or "").strip().lower()
        if not email:
            return None

        def _do(client: ClientMeta):
            return client.user_find(o_mail=email, o_all=True, o_no_members=False)

        try:
            res = _with_freeipa_service_client_retry(cls.get_client, _do)
            if not isinstance(res, dict) or res.get("count", 0) <= 0:
                return None

            first = (res.get("result") or [None])[0]
            if not isinstance(first, dict):
                return None

            uid = first.get("uid")
            if isinstance(uid, list):
                username = (uid[0] if uid else "") or ""
            else:
                username = uid or ""
            username = str(username).strip()
            if not username:
                return None

            return cls(username, first)
        except Exception as e:
            logger.exception(f"Failed to find user by email email={email}: {e}")
            return None

    @classmethod
    def find_usernames_by_email(cls, email: str) -> list[str]:
        normalized = (email or "").strip().lower()
        if not normalized:
            return []

        def _do(client: ClientMeta):
            return client.user_find(o_mail=normalized, o_all=True, o_no_members=False)

        try:
            res = _with_freeipa_service_client_retry(cls.get_client, _do)
        except Exception:
            logger.exception("Failed to find users by email")
            return []

        if not isinstance(res, dict) or res.get("count", 0) <= 0:
            return []

        results = res.get("result")
        if not isinstance(results, list):
            return []

        usernames: set[str] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            uid = item.get("uid")
            if isinstance(uid, list):
                values = uid
            else:
                values = [uid]
            for value in values:
                name = str(value or "").strip().lower()
                if name:
                    usernames.add(name)

        return sorted(usernames)

    @classmethod
    def create(cls, username, **kwargs):
        """
        Create a new user in FreeIPA.
        kwargs should match FreeIPA user_add arguments (e.g., givenname, sn, mail, password).
        """
        try:
            givenname = kwargs.pop('givenname', None) or kwargs.pop('first_name', None)
            sn = kwargs.pop('sn', None) or kwargs.pop('last_name', None)
            if not givenname or not sn:
                raise ValueError('FreeIPA user creation requires givenname/first_name and sn/last_name')

            # Keep cn/displayname/gecos in sync. Intentionally do not allow
            # per-field overrides (admin UX treats display name as derived).
            cn = f"{givenname or ''} {sn or ''}"

            initials = f"{(str(givenname).strip()[:1] or '').upper()}{(str(sn).strip()[:1] or '').upper()}"

            ipa_kwargs = {}

            ipa_kwargs["o_displayname"] = cn
            ipa_kwargs["o_gecos"] = cn
            if initials:
                ipa_kwargs["o_initials"] = initials

            mail = kwargs.pop('mail', None) or kwargs.pop('email', None)
            if mail:
                ipa_kwargs['o_mail'] = mail

            password = kwargs.pop('password', None) or kwargs.pop('userpassword', None)
            if password:
                ipa_kwargs['o_userpassword'] = password

            for key, value in kwargs.items():
                if key.startswith(('o_', 'a_')):
                    ipa_kwargs[key] = value
                else:
                    ipa_kwargs[f"o_{key}"] = value

            _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: client.user_add(username, givenname, sn, cn, **ipa_kwargs),
            )
            # New user should appear in lists; invalidate list cache and warm this user's cache.
            _invalidate_users_list_cache()
            return cls.get(username)
        except Exception:
            logger.exception("Failed to create user username=%s", username)
            raise

    def save(self, *args, **kwargs):
        """Persist changes.

        - If called by Django's update_last_login signal (update_fields includes
          only 'last_login'), do nothing (we don't persist last_login).
        - Otherwise, update selected fields in FreeIPA.
        """

        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields_set = set(update_fields)
            if update_fields_set == {'last_login'}:
                return

        updates = {}
        if self.first_name:
            updates['o_givenname'] = self.first_name
        if self.last_name:
            updates['o_sn'] = self.last_name
        if self.email:
            updates['o_mail'] = self.email

        # Persist activation state by toggling FreeIPA's nsaccountlock.
        # Locked account => nsaccountlock=True => is_active=False.
        if hasattr(self, 'is_active'):
            updates['o_nsaccountlock'] = (not bool(self.is_active))

        # Always keep cn/displayname/gecos in sync.
        desired_name = f"{self.first_name or ''} {self.last_name or ''}"
        updates["o_cn"] = desired_name
        updates["o_gecos"] = desired_name
        updates["o_displayname"] = desired_name

        initials = f"{(str(self.first_name).strip()[:1] or '').upper()}{(str(self.last_name).strip()[:1] or '').upper()}"
        if initials:
            updates["o_initials"] = initials

        try:
            if updates:
                try:
                    _with_freeipa_service_client_retry(
                        self.get_client,
                        lambda client: client.user_mod(self.username, **updates),
                    )
                except exceptions.BadRequest as e:
                    # FreeIPA returns BadRequest("no modifications to be performed") when
                    # user_mod is called with values identical to the current state.
                    if "no modifications to be performed" not in str(e).lower():
                        raise
                    logger.info("FreeIPA user_mod was a no-op username=%s", self.username)

            # Invalidate and refresh cache entries.
            _invalidate_user_cache(self.username)
            _invalidate_users_list_cache()
            # Warm fresh data for subsequent reads.
            FreeIPAUser.get(self.username)
        except Exception as e:
            logger.exception("Failed to update user username=%s: %s", self.username, e)
            raise

    def delete(self):
        """
        Delete the user from FreeIPA.
        """
        try:
            _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.user_del(self.username),
            )
            _invalidate_user_cache(self.username)
            _invalidate_users_list_cache()
        except Exception:
            logger.exception("Failed to delete user username=%s", self.username)
            raise


    def get_all_permissions(self, obj=None):
        if obj is not None:
            return set()
        return self.get_group_permissions(obj) | self.get_user_permissions(obj)

    def get_user_permissions(self, obj=None):
        if obj is not None:
            return set()

        try:
            from core.models import FreeIPAPermissionGrant
        except Exception:
            # Avoid hard-failing early during app startup / migrations.
            return set()

        username = str(self.username or "").strip().lower()
        if not username:
            return set()

        return set(
            FreeIPAPermissionGrant.objects.filter(
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name=username,
            ).values_list("permission", flat=True)
        )

    def has_perm(self, perm, obj=None):
        # Check if user has permission
        if self.is_active and self.is_superuser:
            return True
        return perm in self.get_all_permissions(obj)

    def has_perms(self, perm_list, obj=None):
        return all(self.has_perm(perm, obj) for perm in perm_list)

    def has_module_perms(self, app_label):
        if self.is_active and self.is_superuser:
            return True
        return any(perm.startswith(f"{app_label}.") for perm in self.get_all_permissions())

    def get_group_permissions(self, obj=None):
        if obj is not None:
            return set()

        perms = set()
        group_permissions_map = settings.FREEIPA_GROUP_PERMISSIONS

        for group in self.groups_list:
            if group in group_permissions_map:
                perms.update(group_permissions_map[group])

        try:
            from core.models import FreeIPAPermissionGrant
        except Exception:
            return perms

        groups = [str(g or "").strip().lower() for g in self.groups_list if str(g or "").strip()]
        if not groups:
            return perms

        perms.update(
            FreeIPAPermissionGrant.objects.filter(
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name__in=groups,
            ).values_list("permission", flat=True)
        )

        return perms

    def __str__(self):
        return self.username

    def __eq__(self, other):
        return isinstance(other, FreeIPAUser) and self.username == other.username

    def __hash__(self):
        return hash(self.username)

    # Additional methods for group management as requested
    def add_to_group(self, group_name):
        try:
            res = _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.group_add_member(group_name, o_user=[self.username]),
            )
            _raise_if_freeipa_failed(res, action="group_add_member", subject=f"user={self.username} group={group_name}")
            # Membership affects both user and group views.
            _invalidate_user_cache(self.username)
            _invalidate_group_cache(group_name)
            _invalidate_groups_list_cache()
            # Warm both caches.
            fresh_user = FreeIPAUser.get(self.username)
            FreeIPAGroup.get(group_name)
            if not fresh_user:
                raise FreeIPAOperationFailed(
                    f"FreeIPA group_add_member reported success but user could not be re-fetched (user={self.username} group={group_name})"
                )
            if group_name not in fresh_user.groups_list:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_add_member reported success but membership not present after refresh "
                    f"(user={self.username} group={group_name} response={_compact_repr(res)})"
                )
        except Exception:
            logger.exception("Failed to add user to group username=%s group=%s", self.username, group_name)
            raise

    def remove_from_group(self, group_name):
        try:
            res = _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.group_remove_member(group_name, o_user=[self.username]),
            )
            _raise_if_freeipa_failed(res, action="group_remove_member", subject=f"user={self.username} group={group_name}")
            # Membership affects both user and group views.
            _invalidate_user_cache(self.username)
            _invalidate_group_cache(group_name)
            _invalidate_groups_list_cache()
            fresh_user = FreeIPAUser.get(self.username)
            FreeIPAGroup.get(group_name)
            if not fresh_user:
                raise FreeIPAOperationFailed(
                    f"FreeIPA group_remove_member reported success but user could not be re-fetched (user={self.username} group={group_name})"
                )
            if group_name in fresh_user.groups_list:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_remove_member reported success but membership still present after refresh "
                    f"(user={self.username} group={group_name} response={_compact_repr(res)})"
                )
        except Exception:
            logger.exception("Failed to remove user from group username=%s group=%s", self.username, group_name)
            raise


class FreeIPAGroup:
    """
    A non-persistent group object backed by FreeIPA.
    """
    def __init__(self, cn, group_data=None):
        self.cn = str(cn).strip() if cn else ""
        self._group_data = group_data or {}

        description = self._group_data.get('description', None)
        if isinstance(description, list):
            description = description[0] if description else None
        self.description = str(description).strip() if description else ""

        self.members = _clean_str_list(self._group_data.get('member_user', []))

        # Nested groups.
        self.member_groups = _clean_str_list(self._group_data.get('member_group', []))

        sponsors = None
        for key in ("membermanager_user", "membermanager", "membermanageruser_user"):
            if key in self._group_data:
                sponsors = self._group_data.get(key)
                break
        self.sponsors = _clean_str_list(sponsors)

        # Nested sponsor groups.
        self.sponsor_groups = _clean_str_list(self._group_data.get("membermanager_group", []))

        # FAS extension attributes
        fas_url = self._group_data.get('fasurl', None)
        if isinstance(fas_url, list):
            fas_url = fas_url[0] if fas_url else None
        self.fas_url = fas_url

        fas_mailing_list = self._group_data.get('fasmailinglist', None)
        if isinstance(fas_mailing_list, list):
            fas_mailing_list = fas_mailing_list[0] if fas_mailing_list else None
        self.fas_mailing_list = fas_mailing_list

        self.fas_irc_channels = _clean_str_list(self._group_data.get('fasircchannel', []))

        fas_discussion_url = self._group_data.get('fasdiscussionurl', None)
        if isinstance(fas_discussion_url, list):
            fas_discussion_url = fas_discussion_url[0] if fas_discussion_url else None
        self.fas_discussion_url = fas_discussion_url

        # Check if group has fasGroup support.
        # Prefer an explicit `fasgroup` attribute if present (some FreeIPA
        # deployments expose this boolean), otherwise fall back to checking
        # objectClass membership (case-insensitive).
        fasgroup_field = self._group_data.get('fasgroup', None)
        if isinstance(fasgroup_field, list):
            fasgroup_field = fasgroup_field[0] if fasgroup_field else None
        if fasgroup_field is not None:
            # Normalize typical boolean-ish values.
            if isinstance(fasgroup_field, bool):
                self.fas_group = bool(fasgroup_field)
            else:
                s = str(fasgroup_field).strip().upper()
                self.fas_group = s in {"TRUE", "T", "YES", "Y", "1", "ON"}
        else:
            object_classes = _clean_str_list(self._group_data.get('objectclass', []))
            self.fas_group = 'fasgroup' in [oc.lower() for oc in object_classes]

    def __str__(self):
        return self.cn

    @classmethod
    def get_client(cls) -> ClientMeta:
        """Return a FreeIPA client authenticated as the service account."""

        return _get_freeipa_service_client_cached()

    @classmethod
    def _rpc(cls, client: ClientMeta, method: str, args: list[object] | None, params: dict[str, object] | None):
        if not hasattr(client, "_request"):
            raise FreeIPAOperationFailed("FreeIPA client does not support raw JSON-RPC requests")
        return client._request(method, args or [], params or {})

    @classmethod
    def all(cls):
        """
        Returns a list of all groups from FreeIPA.
        """
        def _fetch_groups() -> list[dict[str, object]]:
            result = _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: client.group_find(o_all=True, o_no_members=False, o_sizelimit=0, o_timelimit=0),
            )
            return result.get('result', [])

        try:
            groups = cache.get_or_set(_groups_list_cache_key(), _fetch_groups) or []
            # Cache may legitimately contain an empty list; treat that as a hit.
            return [cls(g['cn'][0], g) for g in groups]
        except Exception as e:
            # On failure, avoid poisoning the cache with an empty list.
            logger.exception(f"Failed to list groups: {e}")
            return []

    @classmethod
    def get(cls, cn):
        """
        Fetch a single group by cn.
        """
        cache_key = _group_cache_key(cn)
        cached_data = cache.get(cache_key)

        if cached_data is not None:
            return cls(cn, cached_data)

        try:
            result = _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: client.group_find(o_cn=cn, o_all=True, o_no_members=False),
            )
            if result['count'] > 0:
                group_data = result['result'][0]
                cache.set(cache_key, group_data)
                return cls(cn, group_data)
        except Exception as e:
            logger.exception(f"Failed to get group cn={cn}: {e}")
        return None

    @classmethod
    def create(cls, cn, description=None, fas_group: bool = False):
        """
        Create a new group in FreeIPA. If `fas_group` is True, attempt to
        request the fasGroup objectClass at creation time.
        """
        try:
            kwargs = {}
            if description:
                kwargs['o_description'] = description

            if fas_group:
                kwargs['fasgroup'] = True

            _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: client.group_add(cn, **kwargs),
            )
            _invalidate_groups_list_cache()
            return cls.get(cn)
        except Exception:
            logger.exception("Failed to create group cn=%s", cn)
            raise

    def save(self):
        """
        Updates the group data in FreeIPA.
        """
        def _first_str(value: object) -> str:
            if isinstance(value, list):
                value = value[0] if value else ""
            return str(value or "").strip()

        old_description = _first_str(self._group_data.get("description"))
        old_fas_url = _first_str(self._group_data.get("fasurl"))
        old_fas_mailing_list = _first_str(self._group_data.get("fasmailinglist"))
        old_fas_discussion_url = _first_str(self._group_data.get("fasdiscussionurl"))
        old_fas_irc_channels = set(_clean_str_list(self._group_data.get("fasircchannel", [])))

        new_description = str(self.description or "").strip()
        new_fas_url = str(self.fas_url or "").strip()
        new_fas_mailing_list = str(self.fas_mailing_list or "").strip()
        new_fas_discussion_url = str(self.fas_discussion_url or "").strip()
        new_fas_irc_channels = [str(ch or "").strip() for ch in (self.fas_irc_channels or [])]
        new_fas_irc_channels = [ch for ch in new_fas_irc_channels if ch]
        new_fas_irc_channels_set = set(new_fas_irc_channels)

        # python_freeipa validates keyword args for group_mod against its built-in
        # schema and does not include freeipa-fas extension options in some
        # builds. Use FreeIPA's generic setattr/addattr/delattr support instead.
        setattrs: list[str] = []
        addattrs: list[str] = []
        delattrs: list[str] = []

        def _maybe_update_single(attr: str, *, old: str, new: str) -> None:
            if (old or "") == (new or ""):
                return
            if new:
                setattrs.append(f"{attr}={new}")
            elif old:
                # python_freeipa validates delattr entries as name=value.
                delattrs.append(f"{attr}=")

        _maybe_update_single("description", old=old_description, new=new_description)
        _maybe_update_single("fasurl", old=old_fas_url, new=new_fas_url)
        _maybe_update_single("fasmailinglist", old=old_fas_mailing_list, new=new_fas_mailing_list)
        _maybe_update_single("fasdiscussionurl", old=old_fas_discussion_url, new=new_fas_discussion_url)

        if old_fas_irc_channels != new_fas_irc_channels_set:
            # Update multi-valued attribute using per-value add/remove.
            # Clearing with a bare name triggers python_freeipa ValidationError (expects name=value).
            to_remove = old_fas_irc_channels - new_fas_irc_channels_set
            to_add = new_fas_irc_channels_set - old_fas_irc_channels

            for ch in sorted(to_remove, key=str.lower):
                delattrs.append(f"fasircchannel={ch}")
            for ch in sorted(to_add, key=str.lower):
                addattrs.append(f"fasircchannel={ch}")

        try:
            if setattrs or addattrs or delattrs:
                try:
                    kwargs: dict[str, object] = {}
                    if setattrs:
                        kwargs["o_setattr"] = setattrs
                    if addattrs:
                        kwargs["o_addattr"] = addattrs
                    if delattrs:
                        kwargs["o_delattr"] = delattrs
                    _with_freeipa_service_client_retry(
                        self.get_client,
                        lambda client: client.group_mod(self.cn, **kwargs),
                    )
                except exceptions.BadRequest as e:
                    # FreeIPA can return BadRequest("no modifications to be performed") when
                    # group_mod is called with values identical to the current state.
                    if "no modifications to be performed" not in str(e).lower():
                        raise
                    logger.info("FreeIPA group_mod was a no-op cn=%s", self.cn)
            else:
                # Avoid calling group_mod with no updates (causes BadRequest)
                return

            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
        except Exception as e:
            logger.exception("Failed to update group cn=%s: %s", self.cn, e)
            raise

    def delete(self):
        """
        Delete the group from FreeIPA.
        First remove all members, then delete the group.
        """
        try:
            # Remove all members first (required for group deletion in FreeIPA)
            if self.members:
                res = _with_freeipa_service_client_retry(
                    self.get_client,
                    lambda client: client.group_remove_member(self.cn, o_user=self.members),
                )
                _raise_if_freeipa_failed(res, action="group_remove_member", subject=f"group={self.cn}")
                # Invalidate caches for affected users
                for username in self.members:
                    _invalidate_user_cache(username)

            if self.member_groups:
                res = _with_freeipa_service_client_retry(
                    self.get_client,
                    lambda client: client.group_remove_member(self.cn, o_group=self.member_groups),
                )
                _raise_if_freeipa_failed(res, action="group_remove_member", subject=f"group={self.cn}")

            # Now delete the group
            _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.group_del(self.cn),
            )
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
        except Exception:
            logger.exception("Failed to delete group cn=%s", self.cn)
            raise

    def add_member(self, username):
        try:
            res = _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.group_add_member(self.cn, o_user=[username]),
            )
            _raise_if_freeipa_failed(res, action="group_add_member", subject=f"group={self.cn} user={username}")
            _invalidate_group_cache(self.cn)
            _invalidate_user_cache(username)  # user's group list changes
            _invalidate_groups_list_cache()
            # Warm both caches.
            fresh_group = FreeIPAGroup.get(self.cn)
            fresh_user = FreeIPAUser.get(username)
            if fresh_group and username not in fresh_group.members:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_add_member reported success but membership not present after refresh "
                    f"(group={self.cn} user={username} response={_compact_repr(res)})"
                )
            if fresh_user and self.cn not in fresh_user.groups_list:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_add_member reported success but user does not show membership after refresh "
                    f"(group={self.cn} user={username} response={_compact_repr(res)})"
                )
        except Exception:
            logger.exception("Failed to add member username=%s group=%s", username, self.cn)
            raise

    def add_sponsor(self, username: str) -> None:
        username = username.strip()
        if not username:
            return
        try:
            def _do(client: ClientMeta):
                try:
                    return self._rpc(client, "group_add_member_manager", [self.cn], {"user": [username]})
                except Exception:
                    return self._rpc(client, "group_add_member_manager", [self.cn], {"users": [username]})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="group_add_member_manager", subject=f"group={self.cn} user={username}")
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
        except Exception:
            logger.exception("Failed to add sponsor username=%s group=%s", username, self.cn)
            raise

    def remove_sponsor(self, username: str) -> None:
        username = username.strip()
        if not username:
            return
        try:
            def _do(client: ClientMeta):
                try:
                    return self._rpc(client, "group_remove_member_manager", [self.cn], {"user": [username]})
                except Exception:
                    return self._rpc(client, "group_remove_member_manager", [self.cn], {"users": [username]})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="group_remove_member_manager", subject=f"group={self.cn} user={username}")
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
        except Exception:
            logger.exception("Failed to remove sponsor username=%s group=%s", username, self.cn)
            raise

    def add_sponsor_group(self, group_cn: str) -> None:
        group_cn = str(group_cn).strip()
        if not group_cn:
            return
        try:
            def _do(client: ClientMeta):
                return self._rpc(client, "group_add_member_manager", [self.cn], {"group": [group_cn]})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(
                res,
                action="group_add_member_manager",
                subject=f"group={self.cn} sponsor_group={group_cn}",
            )
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
        except Exception:
            logger.exception("Failed to add sponsor group parent=%s sponsor_group=%s", self.cn, group_cn)
            raise

    def remove_sponsor_group(self, group_cn: str) -> None:
        group_cn = str(group_cn).strip()
        if not group_cn:
            return
        try:
            def _do(client: ClientMeta):
                return self._rpc(client, "group_remove_member_manager", [self.cn], {"group": [group_cn]})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(
                res,
                action="group_remove_member_manager",
                subject=f"group={self.cn} sponsor_group={group_cn}",
            )
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
        except Exception:
            logger.exception("Failed to remove sponsor group parent=%s sponsor_group=%s", self.cn, group_cn)
            raise

    def remove_member(self, username):
        try:
            res = _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.group_remove_member(self.cn, o_user=[username]),
            )
            _raise_if_freeipa_failed(res, action="group_remove_member", subject=f"group={self.cn} user={username}")
            _invalidate_group_cache(self.cn)
            _invalidate_user_cache(username)  # user's group list changes
            _invalidate_groups_list_cache()
            fresh_group = FreeIPAGroup.get(self.cn)
            fresh_user = FreeIPAUser.get(username)
            if fresh_group and username in fresh_group.members:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_remove_member reported success but membership still present after refresh "
                    f"(group={self.cn} user={username} response={_compact_repr(res)})"
                )
            if fresh_user and self.cn in fresh_user.groups_list:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_remove_member reported success but user still shows membership after refresh "
                    f"(group={self.cn} user={username} response={_compact_repr(res)})"
                )
        except Exception:
            logger.exception("Failed to remove member username=%s group=%s", username, self.cn)
            raise

    def add_member_group(self, group_cn: str) -> None:
        group_cn = str(group_cn).strip()
        if not group_cn:
            return
        try:
            def _do(client: ClientMeta):
                try:
                    return client.group_add_member(self.cn, o_group=[group_cn])
                except TypeError:
                    return client.group_add_member(self.cn, group=[group_cn])

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="group_add_member", subject=f"group={self.cn} group_member={group_cn}")
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
            self._recursive_member_usernames_cache = None
        except Exception:
            logger.exception("Failed to add member group parent=%s child=%s", self.cn, group_cn)
            raise

    def remove_member_group(self, group_cn: str) -> None:
        group_cn = str(group_cn).strip()
        if not group_cn:
            return
        try:
            def _do(client: ClientMeta):
                try:
                    return client.group_remove_member(self.cn, o_group=[group_cn])
                except TypeError:
                    return client.group_remove_member(self.cn, group=[group_cn])

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="group_remove_member", subject=f"group={self.cn} group_member={group_cn}")
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
            self._recursive_member_usernames_cache = None
        except Exception:
            logger.exception("Failed to remove member group parent=%s child=%s", self.cn, group_cn)
            raise

    def member_usernames_recursive(self) -> set[str]:
        cached = getattr(self, "_recursive_member_usernames_cache", None)
        if isinstance(cached, set):
            return set(cached)
        users = self._member_usernames_recursive(visited=set())
        self._recursive_member_usernames_cache = set(users)
        return users

    def _member_usernames_recursive(self, *, visited: set[str]) -> set[str]:
        cn = str(self.cn or "").strip()
        key = cn.lower()
        if key and key in visited:
            return set()
        if key:
            visited.add(key)

        users: set[str] = set(self.members)
        for child_cn in sorted(set(self.member_groups), key=str.lower):
            child = FreeIPAGroup.get(child_cn)
            if child is None:
                continue
            try:
                users |= child._member_usernames_recursive(visited=visited)
            except Exception:
                # Best-effort: ignore broken nested groups.
                logger.exception("Failed to expand nested group members parent=%s child=%s", self.cn, child_cn)
                continue
        return users

    def member_count_recursive(self) -> int:
        cached = getattr(self, "_recursive_member_usernames_cache", None)
        if isinstance(cached, set):
            return len(cached)
        return len(self.member_usernames_recursive())


class FreeIPAFASAgreement:
    """A non-persistent User Agreement object backed by FreeIPA.

    This relies on the freeipa-fas plugin, which exposes the fasagreement
    family of commands (fasagreement-find/show/add/mod/del/enable/disable,
    and membership operations).
    """

    def __init__(self, cn: str, agreement_data: dict[str, object] | None = None):
        self.cn = cn.strip()
        self._agreement_data: dict[str, object] = agreement_data or {}

        description = self._agreement_data.get("description", "")
        if isinstance(description, list):
            description = description[0] if description else ""
        self.description: str = str(description).strip() if description else ""

        enabled_raw = self._agreement_data.get("ipaenabledflag", None)
        if isinstance(enabled_raw, list):
            enabled_raw = enabled_raw[0] if enabled_raw else None
        if enabled_raw is None:
            self.enabled = True
        elif isinstance(enabled_raw, bool):
            self.enabled = bool(enabled_raw)
        else:
            self.enabled = str(enabled_raw).strip().upper() in {"TRUE", "T", "YES", "Y", "1", "ON"}

        self.groups = self._multi_value_first_present(
            self._agreement_data,
            keys=("member_group", "member", "membergroup"),
        )
        self.users = self._multi_value_first_present(
            self._agreement_data,
            keys=("memberuser_user", "memberuser", "member_user"),
        )

    @staticmethod
    def _multi_value_first_present(source: dict[str, object], *, keys: tuple[str, ...]) -> list[str]:
        for key in keys:
            value = source.get(key, None)
            if value is None:
                continue
            cleaned = _clean_str_list(value)
            if cleaned:
                return cleaned
        return []

    def __str__(self) -> str:
        return self.cn

    @classmethod
    def get_client(cls) -> ClientMeta:
        return _get_freeipa_service_client_cached()

    @classmethod
    def _rpc(cls, client: ClientMeta, method: str, args: list[object] | None, params: dict[str, object] | None):
        """Call a FreeIPA JSON-RPC method.

        python-freeipa's ClientMeta doesn't necessarily generate methods for
        custom plugin commands like fasagreement_*. However, all clients expose
        a raw `_request()` method which can call any command supported by the
        server.
        """

        if not hasattr(client, "_request"):
            raise FreeIPAOperationFailed("FreeIPA client does not support raw JSON-RPC requests")

        return client._request(method, args or [], params or {})

    @classmethod
    def all(cls) -> list[FreeIPAFASAgreement]:
        cache_key = _agreements_list_cache_key()
        cached = cache.get(cache_key)
        if cached is not None:
            agreements = cached or []
        else:
            try:
                result = _with_freeipa_service_client_retry(
                    cls.get_client,
                    lambda client: cls._rpc(
                        client,
                        "fasagreement_find",
                        [],
                        {"all": True, "sizelimit": 0, "timelimit": 0},
                    ),
                )
                agreements = (result or {}).get("result", []) if isinstance(result, dict) else []
                cache.set(cache_key, agreements)
            except Exception as e:
                logger.exception(f"Failed to list FAS agreements: {e}")
                return []

        items: list[FreeIPAFASAgreement] = []
        for a in agreements:
            if not isinstance(a, dict):
                continue
            cn = a.get("cn")
            if isinstance(cn, list):
                cn = cn[0] if cn else None
            if not cn:
                continue
            items.append(cls(str(cn), a))
        return items

    @classmethod
    def get(cls, cn: str) -> FreeIPAFASAgreement | None:
        cache_key = _agreement_cache_key(cn)
        cached = cache.get(cache_key)
        if cached is not None:
            return cls(cn, cached)

        try:
            result = _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: cls._rpc(
                    client,
                    "fasagreement_show",
                    [cn],
                    {"all": True},
                ),
            )
            if isinstance(result, dict) and isinstance(result.get("result"), dict):
                data = result["result"]
                cache.set(cache_key, data)
                return cls(cn, data)
        except Exception as e:
            logger.exception(f"Failed to get FAS agreement cn={cn}: {e}")
        return None

    @classmethod
    def create(cls, cn: str, *, description: str | None = None) -> FreeIPAFASAgreement:
        desc = description.strip() if description else ""
        try:
            params: dict[str, object] = {}
            if desc:
                params["description"] = desc
            _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: cls._rpc(
                    client,
                    "fasagreement_add",
                    [cn],
                    params,
                ),
            )
            _invalidate_agreements_list_cache()
            return cls.get(cn) or cls(cn, {"cn": [cn], "description": [desc], "ipaenabledflag": ["TRUE"]})
        except Exception:
            logger.exception("Failed to create FAS agreement cn=%s", cn)
            raise

    def set_description(self, description: str | None) -> None:
        desc = description.strip() if description else ""
        try:
            _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: self._rpc(
                    client,
                    "fasagreement_mod",
                    [self.cn],
                    {"description": desc},
                ),
            )
            _invalidate_agreement_cache(self.cn)
            _invalidate_agreements_list_cache()
            self.description = desc
        except Exception:
            logger.exception("Failed to modify FAS agreement description cn=%s", self.cn)
            raise

    def set_enabled(self, enabled: bool) -> None:
        try:
            if enabled:
                _with_freeipa_service_client_retry(
                    self.get_client,
                    lambda client: self._rpc(client, "fasagreement_enable", [self.cn], {}),
                )
            else:
                _with_freeipa_service_client_retry(
                    self.get_client,
                    lambda client: self._rpc(client, "fasagreement_disable", [self.cn], {}),
                )
            _invalidate_agreement_cache(self.cn)
            _invalidate_agreements_list_cache()
            self.enabled = bool(enabled)
        except Exception:
            logger.exception("Failed to set FAS agreement enabled cn=%s enabled=%s", self.cn, enabled)
            raise

    def add_group(self, group_cn: str) -> None:
        try:
            def _do(client: ClientMeta):
                try:
                    return self._rpc(client, "fasagreement_add_group", [self.cn], {"group": group_cn})
                except Exception:
                    # Some clients/servers may accept 'groups' as parameter name.
                    return self._rpc(client, "fasagreement_add_group", [self.cn], {"groups": group_cn})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="fasagreement_add_group", subject=f"agreement={self.cn} group={group_cn}")
            _invalidate_agreement_cache(self.cn)
            _invalidate_agreements_list_cache()

            fresh = type(self).get(self.cn)
            if not fresh or group_cn not in set(fresh.groups):
                raise FreeIPAOperationFailed(
                    f"FreeIPA fasagreement_add_group did not persist (agreement={self.cn} group={group_cn})"
                )

            self.groups = list(fresh.groups)
        except Exception:
            logger.exception("Failed to add group to FAS agreement cn=%s group=%s", self.cn, group_cn)
            raise

    def remove_group(self, group_cn: str) -> None:
        try:
            def _do(client: ClientMeta):
                try:
                    return self._rpc(client, "fasagreement_remove_group", [self.cn], {"group": group_cn})
                except Exception:
                    return self._rpc(client, "fasagreement_remove_group", [self.cn], {"groups": group_cn})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="fasagreement_remove_group", subject=f"agreement={self.cn} group={group_cn}")
            _invalidate_agreement_cache(self.cn)
            _invalidate_agreements_list_cache()

            fresh = type(self).get(self.cn)
            if fresh and group_cn in set(fresh.groups):
                raise FreeIPAOperationFailed(
                    f"FreeIPA fasagreement_remove_group did not persist (agreement={self.cn} group={group_cn})"
                )

            if fresh:
                self.groups = list(fresh.groups)
        except Exception:
            logger.exception("Failed to remove group from FAS agreement cn=%s group=%s", self.cn, group_cn)
            raise

    def add_user(self, username: str) -> None:
        try:
            def _do(client: ClientMeta):
                try:
                    return self._rpc(client, "fasagreement_add_user", [self.cn], {"user": username})
                except Exception:
                    return self._rpc(client, "fasagreement_add_user", [self.cn], {"users": username})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="fasagreement_add_user", subject=f"agreement={self.cn} user={username}")
            _invalidate_agreement_cache(self.cn)
            _invalidate_agreements_list_cache()

            # Don't claim success if the server ignored the change (we've seen
            # this happen when parameter shapes don't match the plugin).
            fresh = type(self).get(self.cn)
            if not fresh or username not in set(fresh.users):
                raise FreeIPAOperationFailed(
                    f"FreeIPA fasagreement_add_user did not persist (agreement={self.cn} user={username})"
                )

            self.users = list(fresh.users)
        except Exception:
            logger.exception("Failed to add user to FAS agreement cn=%s user=%s", self.cn, username)
            raise

    def remove_user(self, username: str) -> None:
        try:
            def _do(client: ClientMeta):
                try:
                    return self._rpc(client, "fasagreement_remove_user", [self.cn], {"user": username})
                except Exception:
                    return self._rpc(client, "fasagreement_remove_user", [self.cn], {"users": username})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="fasagreement_remove_user", subject=f"agreement={self.cn} user={username}")
            _invalidate_agreement_cache(self.cn)
            _invalidate_agreements_list_cache()

            fresh = type(self).get(self.cn)
            if fresh and username in set(fresh.users):
                raise FreeIPAOperationFailed(
                    f"FreeIPA fasagreement_remove_user did not persist (agreement={self.cn} user={username})"
                )

            if fresh:
                self.users = list(fresh.users)
        except Exception:
            logger.exception("Failed to remove user from FAS agreement cn=%s user=%s", self.cn, username)
            raise

    def delete(self) -> None:
        try:
            _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: self._rpc(client, "fasagreement_del", [self.cn], {}),
            )
            _invalidate_agreement_cache(self.cn)
            _invalidate_agreements_list_cache()
        except exceptions.Denied as e:
            # freeipa-fas blocks deletion when groups/users are still linked.
            # Deleting the agreement implies these links should be removed, so
            # do that automatically and retry once.
            msg = str(e)
            if "Not allowed to delete User Agreement with linked groups" not in msg:
                logger.exception("Failed to delete FAS agreement cn=%s", self.cn)
                raise

            logger.info(
                "FreeIPA denied deletion of agreement cn=%s due to linked members; unlinking and retrying",
                self.cn,
            )

            _invalidate_agreement_cache(self.cn)
            fresh = self.get(self.cn) or self
            for group_cn in list(fresh.groups):
                fresh.remove_group(group_cn)
            for username in list(fresh.users):
                fresh.remove_user(username)

            _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: self._rpc(client, "fasagreement_del", [self.cn], {}),
            )
            _invalidate_agreement_cache(self.cn)
            _invalidate_agreements_list_cache()
        except Exception:
            logger.exception("Failed to delete FAS agreement cn=%s", self.cn)
            raise


class FreeIPAAuthBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        logger.debug("authenticate: username=%s", username)

        try:
            # Try to login with provided credentials
            client = _get_freeipa_client(username, password)

            # Fetch full user data (includes custom/FAS attributes)
            user_data = FreeIPAUser._fetch_full_user(client, username)
            if user_data:
                logger.debug("authenticate: success username=%s", username)
                user = FreeIPAUser(username, user_data)
                # Persist username inside the session so reloads don't depend on LocMemCache.
                if request is not None and hasattr(request, 'session'):
                    request.session['_freeipa_username'] = username
                return user
            return None
        except exceptions.PasswordExpired:
            logger.debug("authenticate: password expired username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_password_expired", True)
                try:
                    request.session["_freeipa_pwexp_username"] = username
                except Exception:
                    pass
            return None
        except exceptions.UserLocked:
            logger.debug("authenticate: user locked username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Your account is locked. Please contact support.")
            return None
        except exceptions.KrbPrincipalExpired:
            logger.debug("authenticate: principal expired username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Your account credentials have expired. Please contact support.")
            return None
        except exceptions.InvalidSessionPassword:
            logger.debug("authenticate: invalid session password username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Invalid username or password.")
            return None
        except exceptions.Denied:
            logger.debug("authenticate: denied username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Login denied.")
            return None
        except exceptions.Unauthorized:
            logger.debug("authenticate: unauthorized username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Invalid username or password.")
            return None
        except exceptions.BadRequest as e:
            # Catch-all for FreeIPA-side errors that are still a 4xx-style response.
            logger.warning("authenticate: bad request username=%s error=%s", username, e)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Login failed due to a FreeIPA error.")
            return None
        except Exception:
            logger.exception("FreeIPA authentication error username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Login failed due to an internal error.")
            return None

    def get_user(self, user_id):
        # Intentionally return None.
        #
        # This project stores the FreeIPA username in the session at login
        # (request.session['_freeipa_username']). Our middleware restores the
        # user object from that value on every request, which works across
        # restarts and multi-process deployments without depending on a shared
        # cache backend.
        return None

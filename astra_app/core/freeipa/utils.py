import hashlib
from functools import lru_cache

from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import salted_hmac

from core.freeipa.exceptions import FreeIPAOperationFailed


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
        return len(value) > 0
    return bool(value)


def _is_benign_membership_message(value: object, *, is_add: bool) -> bool:
    """Treat idempotent add/remove outcomes as non-errors.

    FreeIPA returns human-readable strings like "This entry is already a member"
    or "This entry is not a member" when the state already matches the request.
    Both group and agreement membership operations share this pattern.
    """
    text = str(value or "").strip().lower()
    if not text:
        return True
    if is_add:
        return "already" in text and "member" in text
    return "not" in text and "member" in text


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

    if action in {"group_add_member", "group_remove_member"} and isinstance(failed, dict):
        member = failed.get("member")
        if isinstance(member, dict):
            user_bucket = member.get("user")

            if isinstance(user_bucket, list) and user_bucket:
                if action == "group_add_member" and all(
                    _is_benign_membership_message(v, is_add=True) for v in user_bucket
                ):
                    user_bucket = []
                if action == "group_remove_member" and all(
                    _is_benign_membership_message(v, is_add=False) for v in user_bucket
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
        if not failed_has_truthy(failed):
            return

    if action in {
        "fasagreement_add_group",
        "fasagreement_remove_group",
        "fasagreement_add_user",
        "fasagreement_remove_user",
    } and isinstance(failed, dict):
        def _clean_member_bucket(value: object, *, is_add: bool) -> object:
            if isinstance(value, list) and value:
                if all(_is_benign_membership_message(v, is_add=is_add) for v in value):
                    return []
            return value

        buckets: list[object] = []
        member = failed.get("member")
        if isinstance(member, dict):
            member_user = member.get("user")
            if action in {"fasagreement_add_user", "fasagreement_remove_user"}:
                member_user = _clean_member_bucket(
                    member_user,
                    is_add=(action == "fasagreement_add_user"),
                )
            buckets.extend([member.get("group"), member_user])
        memberuser = failed.get("memberuser")
        if isinstance(memberuser, dict):
            memberuser_user = memberuser.get("user")
            if action in {"fasagreement_add_user", "fasagreement_remove_user"}:
                memberuser_user = _clean_member_bucket(
                    memberuser_user,
                    is_add=(action == "fasagreement_add_user"),
                )
            buckets.extend([memberuser_user])

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


def _user_cache_key(username: str) -> str:
    return f'freeipa_user_{username}'


def _group_cache_key(cn: str) -> str:
    return f'freeipa_group_{cn}'


def _users_list_cache_key() -> str:
    return 'freeipa_users_all'


def _groups_list_cache_key() -> str:
    return 'freeipa_groups_all'


def _agreements_list_cache_key() -> str:
    return "freeipa_fasagreements_all"


def _agreement_cache_key(cn: str) -> str:
    normalized = cn.strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"freeipa_fasagreement_{digest}"


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
    return int.from_bytes(digest[:8], 'big') & 0x7FFFFFFFFFFFFFFF


__all__ = [
    "_clean_str_list",
    "_first_attr_ci",
    "_compact_repr",
    "_has_truthy_failure",
    "_is_benign_membership_message",
    "_raise_if_freeipa_failed",
    "_user_cache_key",
    "_group_cache_key",
    "_users_list_cache_key",
    "_groups_list_cache_key",
    "_agreements_list_cache_key",
    "_agreement_cache_key",
    "_invalidate_users_list_cache",
    "_invalidate_groups_list_cache",
    "_invalidate_agreements_list_cache",
    "_invalidate_user_cache",
    "_invalidate_group_cache",
    "_invalidate_agreement_cache",
    "_session_user_id_for_username",
]

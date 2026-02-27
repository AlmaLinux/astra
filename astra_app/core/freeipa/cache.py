import hashlib
from functools import lru_cache

from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import salted_hmac


def _user_cache_key(username: str) -> str:
    return f"freeipa_user_{username}"


def _group_cache_key(cn: str) -> str:
    return f"freeipa_group_{cn}"


def _users_list_cache_key() -> str:
    return "freeipa_users_all"


def _groups_list_cache_key() -> str:
    return "freeipa_groups_all"


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


def _agreement_cache_key(cn: str) -> str:
    normalized = cn.strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"freeipa_fasagreement_{digest}"


def _invalidate_agreement_cache(cn: str) -> None:
    cache.delete(_agreement_cache_key(cn))


@lru_cache(maxsize=4096)
def _session_user_id_for_username(username: str) -> int:
    digest = salted_hmac("freeipa-session", username, secret=settings.SECRET_KEY).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


__all__ = [
    "_user_cache_key",
    "_group_cache_key",
    "_users_list_cache_key",
    "_groups_list_cache_key",
    "_agreements_list_cache_key",
    "_invalidate_users_list_cache",
    "_invalidate_groups_list_cache",
    "_invalidate_agreements_list_cache",
    "_invalidate_user_cache",
    "_invalidate_group_cache",
    "_agreement_cache_key",
    "_invalidate_agreement_cache",
    "_session_user_id_for_username",
]

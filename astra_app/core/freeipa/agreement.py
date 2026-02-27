from __future__ import annotations

import logging

from django.core.cache import cache
from python_freeipa import ClientMeta, exceptions

from core.freeipa.client import _with_freeipa_service_client_retry
from core.freeipa.exceptions import FreeIPAOperationFailed
from core.freeipa.user import _FreeIPAClientMixin
from core.freeipa.utils import (
    _agreement_cache_key,
    _agreements_list_cache_key,
    _clean_str_list,
    _invalidate_agreement_cache,
    _invalidate_agreements_list_cache,
    _raise_if_freeipa_failed,
)
from core.ipa_utils import bool_from_ipa

logger = logging.getLogger("core.backends")


class FreeIPAFASAgreement(_FreeIPAClientMixin):
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
        self.enabled = bool_from_ipa(enabled_raw, default=True)

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


__all__ = ["FreeIPAFASAgreement"]

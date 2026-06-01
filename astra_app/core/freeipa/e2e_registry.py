from copy import deepcopy
from typing import cast

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from python_freeipa import ClientMeta, exceptions

from core.freeipa.utils import (
    _invalidate_agreement_cache,
    _invalidate_agreements_list_cache,
    _invalidate_group_cache,
    _invalidate_groups_list_cache,
    _invalidate_user_cache,
    _invalidate_users_list_cache,
)

E2E_FREEIPA_REGULAR_USERNAMES = tuple(f"regular{index:02d}" for index in range(1, 61))
E2E_FREEIPA_USERNAMES = (*E2E_FREEIPA_REGULAR_USERNAMES, "regular", "admin")
_E2E_USER_REGISTRY: dict[str, dict[str, object]] | None = None
_E2E_STAGEUSER_REGISTRY: dict[str, dict[str, object]] | None = None
_E2E_GROUP_REGISTRY: dict[str, dict[str, object]] | None = None
_E2E_AGREEMENT_REGISTRY: dict[str, dict[str, object]] | None = None
_E2E_USER_REGISTRY_CACHE_KEY = "astra:e2e_freeipa:users"
_E2E_STAGEUSER_REGISTRY_CACHE_KEY = "astra:e2e_freeipa:stageusers"
_E2E_GROUP_REGISTRY_CACHE_KEY = "astra:e2e_freeipa:groups"
_E2E_AGREEMENT_REGISTRY_CACHE_KEY = "astra:e2e_freeipa:agreements"


def is_e2e_fake_freeipa_enabled() -> bool:
    return settings.ASTRA_E2E_MODE and settings.ASTRA_E2E_FAKE_FREEIPA_ENABLED


def _require_e2e_fake_freeipa_enabled() -> None:
    if is_e2e_fake_freeipa_enabled():
        return

    raise ImproperlyConfigured(
        "Fake FreeIPA registry requires ASTRA_E2E_MODE=True and ASTRA_E2E_FAKE_FREEIPA_ENABLED=True."
    )


def _normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def _normalize_agreement_cn(cn: str) -> str:
    return str(cn or "").strip()


def _build_e2e_user_registry() -> dict[str, dict[str, object]]:
    admin_group = settings.FREEIPA_ADMIN_GROUP
    registry: dict[str, dict[str, object]] = {
        "regular": {
            "password": "regular-password",
            "user": {
                "uid": ["regular"],
                "givenname": ["Regular"],
                "sn": ["User"],
                "displayname": ["Regular User"],
                "cn": ["Regular User"],
                "mail": ["regular@example.test"],
                "memberof_group": ["packagers"],
                "timezone": ["UTC"],
                "fasIsPrivate": ["FALSE"],
            },
        },
        "admin": {
            "password": "admin-password",
            "user": {
                "uid": ["admin"],
                "givenname": ["Admin"],
                "sn": ["User"],
                "displayname": ["Admin User"],
                "cn": ["Admin User"],
                "mail": ["admin@example.test"],
                "memberof_group": [admin_group],
                "timezone": ["UTC"],
                "fasIsPrivate": ["FALSE"],
            },
        },
    }

    for index, username in enumerate(E2E_FREEIPA_REGULAR_USERNAMES, start=1):
        suffix = f"{index:02d}"
        registry[username] = {
            "password": "password",
            "user": {
                "uid": [username],
                "givenname": [f"Regular {suffix}"],
                "sn": ["User"],
                "displayname": [f"Regular {suffix} User"],
                "cn": [f"Regular {suffix} User"],
                "mail": [f"{username}@example.test"],
                "memberof_group": ["packagers"],
                "timezone": ["UTC"],
                "fasIsPrivate": ["FALSE"],
            },
        }

    return registry


def _e2e_registry() -> dict[str, dict[str, object]]:
    global _E2E_USER_REGISTRY

    if _E2E_USER_REGISTRY is not None:
        return cast(dict[str, dict[str, object]], deepcopy(_E2E_USER_REGISTRY))

    cached = cache.get(_E2E_USER_REGISTRY_CACHE_KEY)
    if isinstance(cached, dict):
        _E2E_USER_REGISTRY = cast(dict[str, dict[str, object]], deepcopy(cached))
        return cast(dict[str, dict[str, object]], deepcopy(_E2E_USER_REGISTRY))

    registry = _build_e2e_user_registry()
    _write_e2e_user_registry(registry)
    return cast(dict[str, dict[str, object]], deepcopy(registry))


def _write_e2e_user_registry(registry: dict[str, dict[str, object]]) -> None:
    global _E2E_USER_REGISTRY

    _E2E_USER_REGISTRY = cast(dict[str, dict[str, object]], deepcopy(registry))
    cache.set(_E2E_USER_REGISTRY_CACHE_KEY, deepcopy(registry), timeout=None)


def _build_e2e_group_registry() -> dict[str, dict[str, object]]:
    admin_group = settings.FREEIPA_ADMIN_GROUP
    return {
        "packagers": {
            "cn": ["packagers"],
            "description": ["Fake E2E packagers group"],
            "member_user": [*E2E_FREEIPA_REGULAR_USERNAMES, "regular"],
            "member_group": [],
            "membermanager_user": [],
            "membermanager_group": [],
            "fasgroup": ["FALSE"],
        },
        admin_group: {
            "cn": [admin_group],
            "description": ["Fake E2E admin group"],
            "member_user": ["admin"],
            "member_group": [],
            "membermanager_user": [],
            "membermanager_group": [],
            "fasgroup": ["FALSE"],
        },
    }


def _build_e2e_stageuser_registry() -> dict[str, dict[str, object]]:
    return {}


def _e2e_stageuser_registry() -> dict[str, dict[str, object]]:
    global _E2E_STAGEUSER_REGISTRY

    if _E2E_STAGEUSER_REGISTRY is not None:
        return cast(dict[str, dict[str, object]], deepcopy(_E2E_STAGEUSER_REGISTRY))

    cached = cache.get(_E2E_STAGEUSER_REGISTRY_CACHE_KEY)
    if isinstance(cached, dict):
        _E2E_STAGEUSER_REGISTRY = cast(dict[str, dict[str, object]], deepcopy(cached))
        return cast(dict[str, dict[str, object]], deepcopy(_E2E_STAGEUSER_REGISTRY))

    registry = _build_e2e_stageuser_registry()
    _write_e2e_stageuser_registry(registry)
    return cast(dict[str, dict[str, object]], deepcopy(registry))


def _write_e2e_stageuser_registry(registry: dict[str, dict[str, object]]) -> None:
    global _E2E_STAGEUSER_REGISTRY

    _E2E_STAGEUSER_REGISTRY = cast(dict[str, dict[str, object]], deepcopy(registry))
    cache.set(_E2E_STAGEUSER_REGISTRY_CACHE_KEY, deepcopy(registry), timeout=None)


def _e2e_group_registry() -> dict[str, dict[str, object]]:
    global _E2E_GROUP_REGISTRY

    if _E2E_GROUP_REGISTRY is not None:
        return cast(dict[str, dict[str, object]], deepcopy(_E2E_GROUP_REGISTRY))

    cached = cache.get(_E2E_GROUP_REGISTRY_CACHE_KEY)
    if isinstance(cached, dict):
        _E2E_GROUP_REGISTRY = cast(dict[str, dict[str, object]], deepcopy(cached))
        return cast(dict[str, dict[str, object]], deepcopy(_E2E_GROUP_REGISTRY))

    registry = _build_e2e_group_registry()
    _write_e2e_group_registry(registry)
    return cast(dict[str, dict[str, object]], deepcopy(registry))


def _write_e2e_group_registry(registry: dict[str, dict[str, object]]) -> None:
    global _E2E_GROUP_REGISTRY

    _E2E_GROUP_REGISTRY = cast(dict[str, dict[str, object]], deepcopy(registry))
    cache.set(_E2E_GROUP_REGISTRY_CACHE_KEY, deepcopy(registry), timeout=None)


def _build_e2e_agreement_registry() -> dict[str, dict[str, object]]:
    return {}


def _e2e_agreement_registry() -> dict[str, dict[str, object]]:
    global _E2E_AGREEMENT_REGISTRY

    if _E2E_AGREEMENT_REGISTRY is not None:
        return cast(dict[str, dict[str, object]], deepcopy(_E2E_AGREEMENT_REGISTRY))

    cached = cache.get(_E2E_AGREEMENT_REGISTRY_CACHE_KEY)
    if isinstance(cached, dict):
        _E2E_AGREEMENT_REGISTRY = cast(dict[str, dict[str, object]], deepcopy(cached))
        return cast(dict[str, dict[str, object]], deepcopy(_E2E_AGREEMENT_REGISTRY))

    registry = _build_e2e_agreement_registry()
    _write_e2e_agreement_registry(registry)
    return cast(dict[str, dict[str, object]], deepcopy(registry))


def _write_e2e_agreement_registry(registry: dict[str, dict[str, object]]) -> None:
    global _E2E_AGREEMENT_REGISTRY

    _E2E_AGREEMENT_REGISTRY = cast(dict[str, dict[str, object]], deepcopy(registry))
    cache.set(_E2E_AGREEMENT_REGISTRY_CACHE_KEY, deepcopy(registry), timeout=None)


def reset_e2e_fake_freeipa_state() -> None:
    _require_e2e_fake_freeipa_enabled()

    global _E2E_USER_REGISTRY, _E2E_STAGEUSER_REGISTRY, _E2E_GROUP_REGISTRY, _E2E_AGREEMENT_REGISTRY

    existing_usernames = tuple(_e2e_registry())
    existing_stageusernames = tuple(_e2e_stageuser_registry())
    existing_group_cns = tuple(_e2e_group_registry())
    existing_agreement_cns = tuple(_e2e_agreement_registry())

    new_user_registry = _build_e2e_user_registry()
    new_stageuser_registry = _build_e2e_stageuser_registry()
    new_group_registry = _build_e2e_group_registry()
    new_agreement_registry = _build_e2e_agreement_registry()
    _E2E_USER_REGISTRY = None
    _E2E_STAGEUSER_REGISTRY = None
    _E2E_GROUP_REGISTRY = None
    _E2E_AGREEMENT_REGISTRY = None
    _write_e2e_user_registry(new_user_registry)
    _write_e2e_stageuser_registry(new_stageuser_registry)
    _write_e2e_group_registry(new_group_registry)
    _write_e2e_agreement_registry(new_agreement_registry)

    for username in {*existing_usernames, *new_user_registry}:
        _invalidate_user_cache(username)
    for username in existing_stageusernames:
        _invalidate_user_cache(username)
    for group_cn in {*existing_group_cns, *new_group_registry}:
        _invalidate_group_cache(group_cn)
    for agreement_cn in existing_agreement_cns:
        _invalidate_agreement_cache(agreement_cn)

    _invalidate_users_list_cache()
    _invalidate_groups_list_cache()
    _invalidate_agreements_list_cache()


def _agreement_membership_response() -> dict[str, object]:
    return {
        "completed": 1,
        "failed": {
            "member": {"group": [], "user": []},
            "memberuser": {"user": []},
        },
    }


def _group_membership_response() -> dict[str, object]:
    return {
        "completed": 1,
        "failed": {
            "member": {
                "group": [],
                "user": [],
                "service": [],
                "idoverrideuser": [],
            },
        },
    }


def _agreement_record(cn: str) -> dict[str, object]:
    normalized_cn = _normalize_agreement_cn(cn)
    record = _e2e_agreement_registry().get(normalized_cn)
    if record is None:
        raise exceptions.BadRequest("agreement not found")
    return record


def _set_membership_values(record: dict[str, object], key: str, values: list[str]) -> None:
    cleaned_values = [value for value in values if value]
    if cleaned_values:
        record[key] = cleaned_values
        return
    record.pop(key, None)


def _normalized_rpc_values(value: object) -> list[str]:
    if isinstance(value, str):
        values: list[object] = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []

    return [str(item).strip() for item in values if str(item).strip()]


def _registry_user(username: str) -> dict[str, object] | None:
    normalized_username = _normalize_username(username)
    record = _e2e_registry().get(normalized_username)
    if record is None:
        return None
    return cast(dict[str, object], record["user"])


def _registry_stageuser(username: str) -> dict[str, object] | None:
    normalized_username = _normalize_username(username)
    return _e2e_stageuser_registry().get(normalized_username)


class E2EFreeIPAClient:
    def _request(self, method: str, args: list[object] | None, params: dict[str, object] | None) -> dict[str, object]:
        rpc_args = args or []
        rpc_params = params or {}

        if method == "fasagreement_find":
            agreements = list(_e2e_agreement_registry().values())
            return {"count": len(agreements), "result": agreements}

        if method == "fasagreement_show":
            agreement = _agreement_record(str(rpc_args[0] if rpc_args else ""))
            return {"result": agreement}

        if method == "fasagreement_add":
            registry = _e2e_agreement_registry()
            cn = _normalize_agreement_cn(str(rpc_args[0] if rpc_args else ""))
            description = str(rpc_params.get("description") or "").strip()
            agreement: dict[str, object] = {
                "cn": [cn],
                "description": [description] if description else [],
                "ipaenabledflag": ["TRUE"],
            }
            registry[cn] = agreement
            _write_e2e_agreement_registry(registry)
            return {"result": agreement}

        if method == "fasagreement_mod":
            registry = _e2e_agreement_registry()
            cn = _normalize_agreement_cn(str(rpc_args[0] if rpc_args else ""))
            agreement = registry.get(cn)
            if agreement is None:
                raise exceptions.BadRequest("agreement not found")
            description = str(rpc_params.get("description") or "").strip()
            agreement["description"] = [description] if description else []
            _write_e2e_agreement_registry(registry)
            return {"result": agreement}

        if method == "fasagreement_enable":
            registry = _e2e_agreement_registry()
            cn = _normalize_agreement_cn(str(rpc_args[0] if rpc_args else ""))
            agreement = registry.get(cn)
            if agreement is None:
                raise exceptions.BadRequest("agreement not found")
            agreement["ipaenabledflag"] = ["TRUE"]
            _write_e2e_agreement_registry(registry)
            return {"result": agreement}

        if method == "fasagreement_disable":
            registry = _e2e_agreement_registry()
            cn = _normalize_agreement_cn(str(rpc_args[0] if rpc_args else ""))
            agreement = registry.get(cn)
            if agreement is None:
                raise exceptions.BadRequest("agreement not found")
            agreement["ipaenabledflag"] = ["FALSE"]
            _write_e2e_agreement_registry(registry)
            return {"result": agreement}

        if method == "fasagreement_add_group":
            registry = _e2e_agreement_registry()
            cn = _normalize_agreement_cn(str(rpc_args[0] if rpc_args else ""))
            agreement = registry.get(cn)
            if agreement is None:
                raise exceptions.BadRequest("agreement not found")
            group_cn = _normalize_agreement_cn(str(rpc_params.get("group") or rpc_params.get("groups") or ""))
            groups = [str(value).strip() for value in cast(list[str], agreement.get("member_group", [])) if str(value).strip()]
            if group_cn and group_cn not in groups:
                groups.append(group_cn)
            _set_membership_values(agreement, "member_group", groups)
            _write_e2e_agreement_registry(registry)
            return _agreement_membership_response()

        if method == "fasagreement_remove_group":
            registry = _e2e_agreement_registry()
            cn = _normalize_agreement_cn(str(rpc_args[0] if rpc_args else ""))
            agreement = registry.get(cn)
            if agreement is None:
                raise exceptions.BadRequest("agreement not found")
            group_cn = _normalize_agreement_cn(str(rpc_params.get("group") or rpc_params.get("groups") or ""))
            groups = [
                str(value).strip()
                for value in cast(list[str], agreement.get("member_group", []))
                if str(value).strip() and str(value).strip() != group_cn
            ]
            _set_membership_values(agreement, "member_group", groups)
            _write_e2e_agreement_registry(registry)
            return _agreement_membership_response()

        if method == "fasagreement_add_user":
            registry = _e2e_agreement_registry()
            cn = _normalize_agreement_cn(str(rpc_args[0] if rpc_args else ""))
            agreement = registry.get(cn)
            if agreement is None:
                raise exceptions.BadRequest("agreement not found")
            username = _normalize_username(str(rpc_params.get("user") or rpc_params.get("users") or ""))
            users = [str(value).strip() for value in cast(list[str], agreement.get("memberuser_user", [])) if str(value).strip()]
            if username and username not in {_normalize_username(value) for value in users}:
                users.append(username)
            _set_membership_values(agreement, "memberuser_user", users)
            _write_e2e_agreement_registry(registry)
            return _agreement_membership_response()

        if method == "fasagreement_remove_user":
            registry = _e2e_agreement_registry()
            cn = _normalize_agreement_cn(str(rpc_args[0] if rpc_args else ""))
            agreement = registry.get(cn)
            if agreement is None:
                raise exceptions.BadRequest("agreement not found")
            username = _normalize_username(str(rpc_params.get("user") or rpc_params.get("users") or ""))
            users = [
                str(value).strip()
                for value in cast(list[str], agreement.get("memberuser_user", []))
                if str(value).strip() and _normalize_username(str(value)) != username
            ]
            _set_membership_values(agreement, "memberuser_user", users)
            _write_e2e_agreement_registry(registry)
            return _agreement_membership_response()

        if method == "fasagreement_del":
            registry = _e2e_agreement_registry()
            cn = _normalize_agreement_cn(str(rpc_args[0] if rpc_args else ""))
            registry.pop(cn, None)
            _write_e2e_agreement_registry(registry)
            return {"result": {}}

        if method == "group_add_member_manager":
            group_cn = str(rpc_args[0] if rpc_args else "").strip()
            registry = _e2e_group_registry()
            group = registry.get(group_cn)
            if group is None:
                raise exceptions.NotFound("group not found")

            usernames = [
                _normalize_username(value)
                for value in _normalized_rpc_values(rpc_params.get("user") or rpc_params.get("users"))
                if _normalize_username(value)
            ]
            user_registry = _e2e_registry()
            sponsors = [
                str(value).strip()
                for value in cast(list[str], group.get("membermanager_user", []))
                if str(value).strip()
            ]
            sponsor_set = {_normalize_username(value) for value in sponsors}

            for username in usernames:
                if username not in user_registry:
                    raise exceptions.NotFound("user not found")
                if username not in sponsor_set:
                    sponsors.append(username)
                    sponsor_set.add(username)

            _set_membership_values(group, "membermanager_user", sponsors)
            registry[group_cn] = group
            _write_e2e_group_registry(registry)
            _invalidate_group_cache(group_cn)
            _invalidate_groups_list_cache()
            return _group_membership_response()

        if method == "group_remove_member_manager":
            group_cn = str(rpc_args[0] if rpc_args else "").strip()
            registry = _e2e_group_registry()
            group = registry.get(group_cn)
            if group is None:
                raise exceptions.NotFound("group not found")

            usernames = {
                _normalize_username(value)
                for value in _normalized_rpc_values(rpc_params.get("user") or rpc_params.get("users"))
                if _normalize_username(value)
            }
            sponsors = [
                str(value).strip()
                for value in cast(list[str], group.get("membermanager_user", []))
                if str(value).strip() and _normalize_username(str(value)) not in usernames
            ]

            _set_membership_values(group, "membermanager_user", sponsors)
            registry[group_cn] = group
            _write_e2e_group_registry(registry)
            _invalidate_group_cache(group_cn)
            _invalidate_groups_list_cache()
            return _group_membership_response()

        raise exceptions.BadRequest(f"unsupported e2e fake FreeIPA method: {method}")

    def user_mod(self, username: str | None = None, *args: object, **kwargs: object) -> dict[str, object]:
        del args

        target_username = _normalize_username(str(username or kwargs.pop("a_uid", "") or ""))
        if not target_username:
            raise exceptions.BadRequest("user not found")

        registry = _e2e_registry()
        record = registry.get(target_username)
        if record is None:
            raise exceptions.BadRequest("user not found")

        user = cast(dict[str, object], record["user"])
        for key, value in kwargs.items():
            attr_name = str(key)
            if attr_name.startswith("o_"):
                attr_name = attr_name[2:]

            if attr_name == "userpassword":
                record["password"] = str(value)
                continue

            if value is None or (isinstance(value, str) and not value.strip()):
                user.pop(attr_name, None)
                continue

            if isinstance(value, list):
                user[attr_name] = [str(item) for item in value if str(item).strip()]
                continue

            user[attr_name] = [str(value)]

        registry[target_username] = record
        _write_e2e_user_registry(registry)
        _invalidate_user_cache(target_username)
        _invalidate_users_list_cache()
        return {"result": user}

    def stageuser_add(self, username: str | None = None, *args: object, **kwargs: object) -> dict[str, object]:
        del args

        target_username = _normalize_username(str(username or kwargs.pop("a_uid", "") or ""))
        if not target_username:
            raise exceptions.BadRequest("stage user not found")

        registry = _e2e_stageuser_registry()
        stageuser: dict[str, object] = {"uid": [target_username]}
        for key, value in kwargs.items():
            attr_name = str(key)
            if attr_name.startswith("o_"):
                attr_name = attr_name[2:]

            if value is None or (isinstance(value, str) and not value.strip()):
                continue

            if isinstance(value, list):
                stageuser[attr_name] = [str(item) for item in value if str(item).strip()]
                continue

            stageuser[attr_name] = [str(value)]

        registry[target_username] = stageuser
        _write_e2e_stageuser_registry(registry)
        _invalidate_user_cache(target_username)
        return {"result": stageuser}

    def stageuser_show(self, username: str | None = None, *args: object, **kwargs: object) -> dict[str, object]:
        del args

        target_username = _normalize_username(str(username or kwargs.pop("a_uid", "") or ""))
        stageuser = _registry_stageuser(target_username)
        if stageuser is None:
            raise exceptions.NotFound("stage user not found")
        return {"result": stageuser}

    def stageuser_activate(self, username: str | None = None, *args: object, **kwargs: object) -> dict[str, object]:
        del args

        target_username = _normalize_username(str(username or kwargs.pop("a_uid", "") or ""))
        registry = _e2e_stageuser_registry()
        stageuser = registry.get(target_username)
        if stageuser is None:
            raise exceptions.NotFound("stage user not found")

        user_registry = _e2e_registry()
        user_registry[target_username] = {
            "password": "",
            "user": deepcopy(stageuser),
        }
        registry.pop(target_username, None)
        _write_e2e_user_registry(user_registry)
        _write_e2e_stageuser_registry(registry)
        _invalidate_user_cache(target_username)
        _invalidate_users_list_cache()
        return {"result": cast(dict[str, object], user_registry[target_username]["user"])}

    def group_add(self, cn: str, *args: object, **kwargs: object) -> dict[str, object]:
        del args
        registry = _e2e_group_registry()
        group_cn = str(cn or "").strip()
        description = str(kwargs.get("o_description") or "").strip()
        fas_group = bool(kwargs.get("fasgroup"))
        group = {
            "cn": [group_cn],
            "description": [description] if description else [],
            "member_user": [],
            "member_group": [],
            "membermanager_user": [],
            "membermanager_group": [],
            "fasgroup": ["TRUE" if fas_group else "FALSE"],
        }
        registry[group_cn] = group
        _write_e2e_group_registry(registry)
        return {"result": group}

    def group_find(self, *args: object, **kwargs: object) -> dict[str, object]:
        del args
        group_cn = str(kwargs.get("o_cn") or "").strip().lower()
        groups = _e2e_group_registry()
        if group_cn:
            matches = [group for name, group in groups.items() if name.lower() == group_cn]
            return {"count": len(matches), "result": matches}
        return {"count": len(groups), "result": list(groups.values())}

    def group_add_member(self, cn: str, *args: object, **kwargs: object) -> dict[str, object]:
        del args
        group_cn = str(cn or "").strip()
        registry = _e2e_group_registry()
        group = registry.get(group_cn)
        if group is None:
            raise exceptions.NotFound("group not found")

        usernames = [
            _normalize_username(str(value))
            for value in cast(list[object], kwargs.get("o_user") or [])
            if _normalize_username(str(value))
        ]
        user_registry = _e2e_registry()
        members = [str(value).strip() for value in cast(list[str], group.get("member_user", [])) if str(value).strip()]
        member_set = {_normalize_username(value) for value in members}

        for username in usernames:
            record = user_registry.get(username)
            if record is None:
                raise exceptions.NotFound("user not found")

            user = cast(dict[str, object], record["user"])
            if username not in member_set:
                members.append(username)
                member_set.add(username)

            current_groups = [
                str(value).strip()
                for value in cast(list[str], user.get("memberof_group", []))
                if str(value).strip()
            ]
            if group_cn not in current_groups:
                current_groups.append(group_cn)
            user["memberof_group"] = current_groups

        group["member_user"] = members
        registry[group_cn] = group
        _write_e2e_group_registry(registry)
        _write_e2e_user_registry(user_registry)
        return _group_membership_response()

    def group_remove_member(self, cn: str, *args: object, **kwargs: object) -> dict[str, object]:
        del args
        group_cn = str(cn or "").strip()
        registry = _e2e_group_registry()
        group = registry.get(group_cn)
        if group is None:
            raise exceptions.NotFound("group not found")

        usernames = {
            _normalize_username(str(value))
            for value in cast(list[object], kwargs.get("o_user") or [])
            if _normalize_username(str(value))
        }
        user_registry = _e2e_registry()
        group["member_user"] = [
            str(value).strip()
            for value in cast(list[str], group.get("member_user", []))
            if str(value).strip() and _normalize_username(str(value)) not in usernames
        ]

        for username in usernames:
            record = user_registry.get(username)
            if record is None:
                continue

            user = cast(dict[str, object], record["user"])
            user["memberof_group"] = [
                str(value).strip()
                for value in cast(list[str], user.get("memberof_group", []))
                if str(value).strip() and str(value).strip() != group_cn
            ]

        registry[group_cn] = group
        _write_e2e_group_registry(registry)
        _write_e2e_user_registry(user_registry)
        return _group_membership_response()

    def user_show(self, username: str, *args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        user = _registry_user(username)
        if user is None:
            raise exceptions.BadRequest("user not found")
        return {"result": user}

    def user_find(self, *args: object, **kwargs: object) -> dict[str, object]:
        del args
        username = _normalize_username(str(kwargs.get("o_uid") or ""))
        if username:
            user = _registry_user(username)
            if user is None:
                return {"count": 0, "result": []}
            return {"count": 1, "result": [user]}

        email = str(kwargs.get("o_mail") or "").strip().lower()
        if email:
            matches = [user for entry in _e2e_registry().values() if email in {str(value).strip().lower() for value in cast(list[str], cast(dict[str, object], entry["user"])["mail"])} for user in [cast(dict[str, object], entry["user"])]]
            return {"count": len(matches), "result": matches}

        criteria = _normalize_username(str(kwargs.get("a_criteria") or ""))
        if criteria:
            matches: list[dict[str, object]] = []
            limit_raw = kwargs.get("o_sizelimit")
            try:
                limit = int(str(limit_raw)) if limit_raw is not None else 0
            except (TypeError, ValueError):
                limit = 0

            for entry in _e2e_registry().values():
                user = cast(dict[str, object], entry["user"])
                searchable_values: list[str] = []
                for key in ("uid", "displayname", "cn", "givenname", "sn", "mail"):
                    value = user.get(key)
                    if isinstance(value, list):
                        searchable_values.extend(str(item).strip().lower() for item in value if str(item).strip())
                    elif value is not None and str(value).strip():
                        searchable_values.append(str(value).strip().lower())

                if any(criteria in value for value in searchable_values):
                    matches.append(user)
                    if limit > 0 and len(matches) >= limit:
                        break

            return {"count": len(matches), "result": matches}

        users = [cast(dict[str, object], entry["user"]) for entry in _e2e_registry().values()]
        return {"count": len(users), "result": users}


def get_e2e_auth_client(*, username: str, password: str) -> ClientMeta:
    _require_e2e_fake_freeipa_enabled()
    normalized_username = _normalize_username(username)
    record = _e2e_registry().get(normalized_username)
    if record is None or str(record["password"]) != str(password):
        raise exceptions.InvalidSessionPassword()
    return cast(ClientMeta, E2EFreeIPAClient())


def get_e2e_service_client() -> ClientMeta:
    _require_e2e_fake_freeipa_enabled()
    return cast(ClientMeta, E2EFreeIPAClient())


__all__ = [
    "E2E_FREEIPA_USERNAMES",
    "get_e2e_auth_client",
    "get_e2e_service_client",
    "is_e2e_fake_freeipa_enabled",
    "reset_e2e_fake_freeipa_state",
]
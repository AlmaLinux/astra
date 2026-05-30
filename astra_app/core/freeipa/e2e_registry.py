from typing import cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from python_freeipa import ClientMeta, exceptions

from core.freeipa.utils import (
    _invalidate_agreement_cache,
    _invalidate_agreements_list_cache,
    _invalidate_group_cache,
    _invalidate_groups_list_cache,
)

E2E_FREEIPA_REGULAR_USERNAMES = tuple(f"regular{index:02d}" for index in range(1, 11))
E2E_FREEIPA_USERNAMES = (*E2E_FREEIPA_REGULAR_USERNAMES, "regular", "admin")
_E2E_GROUP_REGISTRY: dict[str, dict[str, object]] | None = None
_E2E_AGREEMENT_REGISTRY: dict[str, dict[str, object]] | None = None


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


def _e2e_registry() -> dict[str, dict[str, object]]:
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


def _e2e_group_registry() -> dict[str, dict[str, object]]:
    global _E2E_GROUP_REGISTRY
    if _E2E_GROUP_REGISTRY is None:
        _E2E_GROUP_REGISTRY = _build_e2e_group_registry()
    return _E2E_GROUP_REGISTRY


def _build_e2e_agreement_registry() -> dict[str, dict[str, object]]:
    return {}


def _e2e_agreement_registry() -> dict[str, dict[str, object]]:
    global _E2E_AGREEMENT_REGISTRY
    if _E2E_AGREEMENT_REGISTRY is None:
        _E2E_AGREEMENT_REGISTRY = _build_e2e_agreement_registry()
    return _E2E_AGREEMENT_REGISTRY


def reset_e2e_fake_freeipa_state() -> None:
    _require_e2e_fake_freeipa_enabled()

    global _E2E_GROUP_REGISTRY, _E2E_AGREEMENT_REGISTRY
    existing_group_cns = tuple(_e2e_group_registry())
    existing_agreement_cns = tuple(_e2e_agreement_registry())

    _E2E_GROUP_REGISTRY = _build_e2e_group_registry()
    _E2E_AGREEMENT_REGISTRY = _build_e2e_agreement_registry()

    for group_cn in {*existing_group_cns, *_E2E_GROUP_REGISTRY}:
        _invalidate_group_cache(group_cn)
    for agreement_cn in existing_agreement_cns:
        _invalidate_agreement_cache(agreement_cn)

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


def _registry_user(username: str) -> dict[str, object] | None:
    normalized_username = _normalize_username(username)
    record = _e2e_registry().get(normalized_username)
    if record is None:
        return None
    return cast(dict[str, object], record["user"])


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
            cn = _normalize_agreement_cn(str(rpc_args[0] if rpc_args else ""))
            description = str(rpc_params.get("description") or "").strip()
            agreement: dict[str, object] = {
                "cn": [cn],
                "description": [description] if description else [],
                "ipaenabledflag": ["TRUE"],
            }
            _e2e_agreement_registry()[cn] = agreement
            return {"result": agreement}

        if method == "fasagreement_mod":
            agreement = _agreement_record(str(rpc_args[0] if rpc_args else ""))
            description = str(rpc_params.get("description") or "").strip()
            agreement["description"] = [description] if description else []
            return {"result": agreement}

        if method == "fasagreement_enable":
            agreement = _agreement_record(str(rpc_args[0] if rpc_args else ""))
            agreement["ipaenabledflag"] = ["TRUE"]
            return {"result": agreement}

        if method == "fasagreement_disable":
            agreement = _agreement_record(str(rpc_args[0] if rpc_args else ""))
            agreement["ipaenabledflag"] = ["FALSE"]
            return {"result": agreement}

        if method == "fasagreement_add_group":
            agreement = _agreement_record(str(rpc_args[0] if rpc_args else ""))
            group_cn = _normalize_agreement_cn(str(rpc_params.get("group") or rpc_params.get("groups") or ""))
            groups = [str(value).strip() for value in cast(list[str], agreement.get("member_group", [])) if str(value).strip()]
            if group_cn and group_cn not in groups:
                groups.append(group_cn)
            _set_membership_values(agreement, "member_group", groups)
            return _agreement_membership_response()

        if method == "fasagreement_remove_group":
            agreement = _agreement_record(str(rpc_args[0] if rpc_args else ""))
            group_cn = _normalize_agreement_cn(str(rpc_params.get("group") or rpc_params.get("groups") or ""))
            groups = [
                str(value).strip()
                for value in cast(list[str], agreement.get("member_group", []))
                if str(value).strip() and str(value).strip() != group_cn
            ]
            _set_membership_values(agreement, "member_group", groups)
            return _agreement_membership_response()

        if method == "fasagreement_add_user":
            agreement = _agreement_record(str(rpc_args[0] if rpc_args else ""))
            username = _normalize_username(str(rpc_params.get("user") or rpc_params.get("users") or ""))
            users = [str(value).strip() for value in cast(list[str], agreement.get("memberuser_user", [])) if str(value).strip()]
            if username and username not in {_normalize_username(value) for value in users}:
                users.append(username)
            _set_membership_values(agreement, "memberuser_user", users)
            return _agreement_membership_response()

        if method == "fasagreement_remove_user":
            agreement = _agreement_record(str(rpc_args[0] if rpc_args else ""))
            username = _normalize_username(str(rpc_params.get("user") or rpc_params.get("users") or ""))
            users = [
                str(value).strip()
                for value in cast(list[str], agreement.get("memberuser_user", []))
                if str(value).strip() and _normalize_username(str(value)) != username
            ]
            _set_membership_values(agreement, "memberuser_user", users)
            return _agreement_membership_response()

        if method == "fasagreement_del":
            cn = _normalize_agreement_cn(str(rpc_args[0] if rpc_args else ""))
            _e2e_agreement_registry().pop(cn, None)
            return {"result": {}}

        raise exceptions.BadRequest(f"unsupported e2e fake FreeIPA method: {method}")

    def group_add(self, cn: str, *args: object, **kwargs: object) -> dict[str, object]:
        del args
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
        _e2e_group_registry()[group_cn] = group
        return {"result": group}

    def group_find(self, *args: object, **kwargs: object) -> dict[str, object]:
        del args
        group_cn = str(kwargs.get("o_cn") or "").strip().lower()
        groups = _e2e_group_registry()
        if group_cn:
            matches = [group for name, group in groups.items() if name.lower() == group_cn]
            return {"count": len(matches), "result": matches}
        return {"count": len(groups), "result": list(groups.values())}

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

        return {"count": 0, "result": []}


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
from collections.abc import Iterable
from dataclasses import dataclass

from core.freeipa.agreement import FreeIPAFASAgreement


@dataclass(frozen=True, slots=True)
class AgreementForUser:
    cn: str
    description: str
    signed: bool
    applicable: bool
    enabled: bool
    groups: tuple[str, ...]


def has_enabled_agreements() -> bool:
    for agreement in FreeIPAFASAgreement.all():
        if agreement.enabled:
            return True
    return False


def list_agreements_for_user(
    username: str,
    *,
    user_groups: Iterable[str],
    include_disabled: bool = False,
    applicable_only: bool = False,
) -> list[AgreementForUser]:
    username = username.strip()
    groups_set = {g.lower() for g in user_groups}

    out: list[AgreementForUser] = []
    for agreement in FreeIPAFASAgreement.all():
        cn = agreement.cn
        if not cn:
            continue

        enabled = bool(agreement.enabled)

        if not include_disabled and not enabled:
            continue

        groups_source = list(agreement.groups)

        agreement_groups = {str(group).lower() for group in groups_source}
        applicable = not agreement_groups or bool(groups_set & agreement_groups)
        if applicable_only and not applicable:
            continue

        groups = tuple(sorted(groups_source, key=str.lower))

        users = list(agreement.users)

        description = str(agreement.description)

        out.append(
            AgreementForUser(
                cn=cn,
                description=description,
                signed=username in users,
                applicable=applicable,
                enabled=enabled,
                groups=groups,
            )
        )

    return sorted(out, key=lambda a: a.cn.lower())


def required_agreements_for_group(group_cn: str) -> list[str]:
    """Return enabled agreement CNs that apply to a given group.

    Group gating is based on agreements that explicitly list the group in their
    linked groups.
    """

    group_cn = group_cn.strip()
    if not group_cn:
        return []

    group_key = group_cn.lower()
    required: list[str] = []

    for agreement in FreeIPAFASAgreement.all():
        cn = agreement.cn
        if not cn:
            continue

        enabled = bool(agreement.enabled)

        if not enabled:
            continue

        groups_source = list(agreement.groups)

        agreement_groups = {str(group).lower() for group in groups_source}
        if group_key in agreement_groups:
            required.append(cn)

    return sorted(set(required), key=str.lower)


def missing_required_agreements_for_user_in_group(username: str, group_cn: str) -> list[str]:
    """Return agreement CNs the user must sign before joining a group."""

    username = username.strip()
    if not username:
        return []

    group_key = group_cn.strip().lower()
    if not group_key:
        return []

    missing: list[str] = []
    for agreement in FreeIPAFASAgreement.all():
        agreement_cn = agreement.cn
        if not agreement_cn:
            continue

        enabled = bool(agreement.enabled)

        if not enabled:
            continue

        groups_source = list(agreement.groups)

        agreement_groups = {str(group).lower() for group in groups_source}
        if group_key not in agreement_groups:
            continue

        users = list(agreement.users)

        if username not in users:
            missing.append(agreement_cn)

    return sorted(set(missing), key=str.lower)

from collections.abc import Iterable
from dataclasses import dataclass

from core.backends import FreeIPAFASAgreement


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

        full = FreeIPAFASAgreement.get(cn) or agreement
        enabled = full.enabled
        if not include_disabled and not enabled:
            continue

        agreement_groups = {g.lower() for g in full.groups}
        applicable = not agreement_groups or bool(groups_set & agreement_groups)
        if applicable_only and not applicable:
            continue

        groups = tuple(sorted(full.groups, key=str.lower))

        out.append(
            AgreementForUser(
                cn=cn,
                description=full.description,
                signed=username in full.users,
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

        full = FreeIPAFASAgreement.get(cn) or agreement
        if not full.enabled:
            continue

        agreement_groups = {g.lower() for g in full.groups}
        if group_key in agreement_groups:
            required.append(cn)

    return sorted(set(required), key=str.lower)


def missing_required_agreements_for_user_in_group(username: str, group_cn: str) -> list[str]:
    """Return agreement CNs the user must sign before joining a group."""

    username = username.strip()
    if not username:
        return []

    missing: list[str] = []
    for agreement_cn in required_agreements_for_group(group_cn):
        agreement = FreeIPAFASAgreement.get(agreement_cn)
        if not agreement or not agreement.enabled:
            continue

        if username not in set(agreement.users):
            missing.append(agreement_cn)

    return sorted(set(missing), key=str.lower)

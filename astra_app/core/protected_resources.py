from django.conf import settings
from django.db.models import QuerySet

from core.models import Election, FreeIPAPermissionGrant, MembershipType


def membership_type_group_cns() -> frozenset[str]:
    """Return non-empty membership-type group CNs."""

    group_cns: set[str] = set()
    for group_cn in MembershipType.objects.exclude(group_cn="").values_list("group_cn", flat=True).distinct():
        cn = str(group_cn or "").strip()
        if cn:
            group_cns.add(cn)
    return frozenset(group_cns)


def settings_freeipa_group_cns() -> frozenset[str]:
    """Return FreeIPA group CNs named in settings, lowercased like FreeIPA stores them.

    The app cannot rewrite its own configuration, so these groups must not be
    deleted or renamed from the UI.
    """

    group_cns = {
        settings.FREEIPA_ADMIN_GROUP,
        settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
        settings.MATERIALIZED_TEAM_LEADS_SOURCE_GROUP_CN,
        settings.MATERIALIZED_TEAM_LEADS_DESTINATION_GROUP_CN,
        *settings.FREEIPA_GROUP_PERMISSIONS,
    }
    return frozenset(cn.strip().lower() for cn in group_cns if cn and cn.strip())


def protected_freeipa_group_cns() -> frozenset[str]:
    """Return FreeIPA group CNs that must not be deleted.

    These groups are part of the app's configuration (settings) or are used as
    membership-type groups.
    """

    return settings_freeipa_group_cns() | membership_type_group_cns()


def freeipa_group_references(cn: str) -> dict[str, tuple[QuerySet, str]]:
    """Return local rows that store a FreeIPA group CN as text, keyed by kind.

    Each value is (queryset, field name) so callers can count or rewrite the
    references when the group is renamed. FreeIPA group names are
    case-insensitive.
    """

    return {
        "membership_types": (MembershipType.objects.filter(group_cn__iexact=cn), "group_cn"),
        "elections": (Election.objects.filter(eligible_group_cn__iexact=cn), "eligible_group_cn"),
        "permission_grants": (
            FreeIPAPermissionGrant.objects.filter(
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name=cn.lower(),
            ),
            "principal_name",
        ),
    }

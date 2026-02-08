from django.conf import settings

from core.models import MembershipType


def membership_type_group_cns() -> frozenset[str]:
    """Return non-empty membership-type group CNs."""

    group_cns: set[str] = set()
    for group_cn in MembershipType.objects.exclude(group_cn="").values_list("group_cn", flat=True).distinct():
        cn = str(group_cn or "").strip()
        if cn:
            group_cns.add(cn)
    return frozenset(group_cns)


def protected_freeipa_group_cns() -> frozenset[str]:
    """Return FreeIPA group CNs that must not be deleted.

    These groups are part of the app's security configuration (settings) or are
    used as membership-type groups.
    """

    protected: set[str] = set()

    admin_group = str(settings.FREEIPA_ADMIN_GROUP or "").strip()
    if admin_group:
        protected.add(admin_group)

    for name in (settings.FREEIPA_GROUP_PERMISSIONS or {}).keys():
        group_cn = str(name or "").strip()
        if group_cn:
            protected.add(group_cn)

    protected.update(membership_type_group_cns())

    return frozenset(protected)

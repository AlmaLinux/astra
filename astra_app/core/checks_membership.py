from django.core.checks import Warning, register
from django.db.models import Q

from core.models import MembershipType


@register()
def check_enabled_membership_types_have_group_cn(_app_configs=None, **_kwargs) -> list[Warning]:
    issues: list[Warning] = []
    for membership_type in MembershipType.objects.filter(enabled=True).filter(Q(group_cn="") | Q(group_cn__isnull=True)):
        issues.append(
            Warning(
                f"Enabled membership type {membership_type.code!r} has no group_cn and is excluded from requestable lists.",
                obj=membership_type.code,
                id="core.W001",
            )
        )
    return issues

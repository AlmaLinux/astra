from __future__ import annotations

from collections.abc import Iterable

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.backends import FreeIPAUser
from core.models import OrganizationSponsorship


class Command(BaseCommand):
    help = (
        "Remove expired organization sponsorships: drop representative FreeIPA group membership, "
        "delete OrganizationSponsorship rows, and clear Organization.membership_level when it matches."
    )

    def handle(self, *args, **options) -> None:
        now = timezone.now()

        expired: Iterable[OrganizationSponsorship] = (
            OrganizationSponsorship.objects.select_related("organization", "membership_type")
            .filter(expires_at__isnull=False, expires_at__lte=now)
            .order_by("organization_id", "membership_type_id")
        )

        removed = 0
        failed = 0

        for sponsorship in expired:
            org = sponsorship.organization
            membership_type = sponsorship.membership_type
            group_cn = str(membership_type.group_cn or "").strip()
            rep_username = str(org.representative or "").strip()

            self.stdout.write(
                f"Processing expired sponsorship for org {org.pk} (level={membership_type.code}) rep={rep_username!r}..."
            )

            if group_cn and rep_username:
                rep = FreeIPAUser.get(rep_username)
                if rep is None:
                    failed += 1
                elif group_cn in rep.groups_list:
                    try:
                        rep.remove_from_group(group_name=group_cn)
                    except Exception:
                        failed += 1
                        continue

            if org.membership_level_id == membership_type.code:
                org.membership_level = None
                org.save(update_fields=["membership_level"])

            sponsorship.delete()
            removed += 1

        self.stdout.write(f"Removed {removed} expired sponsorship(s); failed {failed}.")

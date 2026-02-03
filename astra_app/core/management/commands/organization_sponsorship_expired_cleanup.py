from __future__ import annotations

from collections.abc import Iterable
import logging
from typing import override

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.backends import FreeIPAUser
from core.models import OrganizationSponsorship

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Remove expired organization sponsorships: drop representative FreeIPA group membership, "
        "delete OrganizationSponsorship rows, and clear Organization.membership_level when it matches."
    )

    @override
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
            removal_failed = False

            self.stdout.write(
                f"Processing expired sponsorship for org {org.pk} (level={membership_type.code}) rep={rep_username!r}..."
            )

            if group_cn and rep_username:
                rep = FreeIPAUser.get(rep_username)
                if rep is None:
                    failed += 1
                    removal_failed = True
                    logger.warning(
                        "organization_sponsorship_expired_cleanup_failure org_id=%s membership_type=%s group_cn=%s representative=%s reason=freeipa_user_missing",
                        org.pk,
                        membership_type.code,
                        group_cn,
                        rep_username,
                    )
                elif group_cn in rep.groups_list:
                    try:
                        rep.remove_from_group(group_name=group_cn)
                    except Exception:
                        failed += 1
                        removal_failed = True
                        logger.exception(
                            "organization_sponsorship_expired_cleanup_failure org_id=%s membership_type=%s group_cn=%s representative=%s reason=freeipa_remove_failed",
                            org.pk,
                            membership_type.code,
                            group_cn,
                            rep_username,
                        )

            if removal_failed:
                self.stdout.write(
                    f"Skipping expired sponsorship cleanup for org {org.pk}; FreeIPA removal failed."
                )
                continue

            if org.membership_level_id == membership_type.code:
                org.membership_level = None
                org.save(update_fields=["membership_level"])

            sponsorship.delete()
            removed += 1

        self.stdout.write(f"Removed {removed} expired sponsorship(s); failed {failed}.")

import logging
from typing import override

import post_office.mail
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from core.backends import FreeIPAGroup, FreeIPAUser
from core.models import Membership, MembershipType, Organization, OrganizationSponsorship

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Report or fix FreeIPA membership drift for membership-type groups."

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--report",
            action="store_true",
            help="Report drift (default if no mode is specified).",
        )
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Apply changes in FreeIPA to fix drift.",
        )
        parser.add_argument(
            "--group-cn",
            dest="group_cn",
            default="",
            help="Limit reconciliation to a single FreeIPA group CN.",
        )
        parser.add_argument(
            "--limit",
            dest="limit",
            type=int,
            default=0,
            help="Limit the number of FreeIPA mutations in fix mode (0 = unlimited).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without mutating data or sending email.",
        )

    @override
    def handle(self, *args, **options) -> None:
        report: bool = bool(options.get("report"))
        fix: bool = bool(options.get("fix"))
        group_cn_filter = str(options.get("group_cn") or "").strip()
        limit: int = int(options.get("limit") or 0)
        dry_run: bool = bool(options.get("dry_run"))

        if report and fix:
            raise CommandError("Choose only one of --report or --fix.")
        if not report and not fix:
            report = True
        if limit < 0:
            raise CommandError("--limit must be zero or a positive integer.")

        if dry_run:
            if fix:
                logger.info("freeipa_membership_reconcile: dry-run overrides fix mode")
            fix = False
            report = True

        mode = "fix" if fix else "report"
        now = timezone.now()

        logger.info(
            "freeipa_membership_reconcile: start mode=%s group_cn=%s limit=%s",
            mode,
            group_cn_filter or "<all>",
            limit or "unlimited",
        )

        membership_types = MembershipType.objects.filter(enabled=True).exclude(group_cn="")
        if group_cn_filter:
            membership_types = membership_types.filter(group_cn=group_cn_filter)

        sponsorships = list(
            OrganizationSponsorship.objects.select_related("organization", "membership_type").all()
        )
        sponsorship_by_org_id: dict[int, OrganizationSponsorship] = {
            sponsorship.organization_id: sponsorship for sponsorship in sponsorships
        }

        for sponsorship in sponsorships:
            org = sponsorship.organization
            org_level = org.membership_level_id
            if org_level is None:
                logger.warning(
                    "sponsorship_divergence org_id=%s org_name=%s org_level=%s sponsorship_level=%s reason=org_missing_level",
                    org.pk,
                    org.name,
                    org_level,
                    sponsorship.membership_type_id,
                )
            elif org_level != sponsorship.membership_type_id:
                logger.warning(
                    "sponsorship_divergence org_id=%s org_name=%s org_level=%s sponsorship_level=%s reason=level_mismatch",
                    org.pk,
                    org.name,
                    org_level,
                    sponsorship.membership_type_id,
                )
            elif sponsorship.expires_at is not None and sponsorship.expires_at <= now:
                logger.warning(
                    "sponsorship_divergence org_id=%s org_name=%s org_level=%s sponsorship_level=%s reason=expired_sponsorship",
                    org.pk,
                    org.name,
                    org_level,
                    sponsorship.membership_type_id,
                )

        group_reports: list[dict[str, object]] = []
        total_missing = 0
        total_extra = 0
        total_errors = 0
        mutation_budget = limit

        for membership_type in membership_types.order_by("code"):
            group_cn = str(membership_type.group_cn or "").strip()
            if not group_cn:
                continue

            expected: set[str] = set()
            if membership_type.isIndividual:
                active_memberships = Membership.objects.filter(
                    membership_type=membership_type,
                ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))

                for username in active_memberships.values_list("target_username", flat=True):
                    normalized = str(username or "").strip()
                    if normalized:
                        expected.add(normalized)

            if membership_type.isOrganization:
                orgs = Organization.objects.filter(membership_level=membership_type).exclude(representative="")
                for org in orgs:
                    representative = str(org.representative or "").strip()
                    if representative:
                        expected.add(representative)

                    if org.pk not in sponsorship_by_org_id:
                        logger.warning(
                            "sponsorship_divergence org_id=%s org_name=%s org_level=%s sponsorship_level=%s reason=missing_sponsorship",
                            org.pk,
                            org.name,
                            org.membership_level_id,
                            None,
                        )

            group = FreeIPAGroup.get(group_cn)
            if group is None:
                total_errors += 1
                group_reports.append(
                    {
                        "group_cn": group_cn,
                        "missing_count": len(expected),
                        "extra_count": 0,
                        "missing_sample": [],
                        "extra_sample": [],
                        "errors": ["group_not_found"],
                    }
                )
                logger.error("freeipa_membership_reconcile: group_not_found group=%s", group_cn)
                continue

            actual = {str(member or "").strip() for member in group.members if str(member or "").strip()}
            missing = sorted(expected - actual, key=str.lower)
            extra = sorted(actual - expected, key=str.lower)

            errors: list[str] = []
            total_missing += len(missing)
            total_extra += len(extra)

            logger.info(
                "freeipa_membership_reconcile: group_diff group=%s missing=%s extra=%s",
                group_cn,
                len(missing),
                len(extra),
            )

            if fix and (missing or extra):
                for username in missing:
                    if mutation_budget == 0 and limit > 0:
                        errors.append("limit_reached")
                        break
                    try:
                        group.add_member(username)
                    except Exception as exc:
                        msg = f"add_failed:{username}:{exc.__class__.__name__}"
                        errors.append(msg)
                        logger.exception(
                            "freeipa_membership_reconcile: add_failed group=%s username=%s",
                            group_cn,
                            username,
                        )
                    else:
                        if limit > 0:
                            mutation_budget -= 1

                for username in extra:
                    if mutation_budget == 0 and limit > 0:
                        errors.append("limit_reached")
                        break
                    try:
                        group.remove_member(username)
                    except Exception as exc:
                        msg = f"remove_failed:{username}:{exc.__class__.__name__}"
                        errors.append(msg)
                        logger.exception(
                            "freeipa_membership_reconcile: remove_failed group=%s username=%s",
                            group_cn,
                            username,
                        )
                    else:
                        if limit > 0:
                            mutation_budget -= 1

            if errors:
                total_errors += len(errors)

            group_reports.append(
                {
                    "group_cn": group_cn,
                    "missing_count": len(missing),
                    "extra_count": len(extra),
                    "missing_sample": missing[:10],
                    "extra_sample": extra[:10],
                    "errors": errors,
                }
            )

        self.stdout.write(
            "Reconciliation complete: "
            f"groups={len(group_reports)} missing={total_missing} extra={total_extra} errors={total_errors}."
        )

        if total_missing == 0 and total_extra == 0 and total_errors == 0:
            logger.info("freeipa_membership_reconcile: no_drift")
            return

        admin_group_cn = str(settings.FREEIPA_ADMIN_GROUP or "").strip()
        admin_group = FreeIPAGroup.get(admin_group_cn) if admin_group_cn else None
        if admin_group is None:
            logger.error(
                "freeipa_membership_reconcile: admin_group_missing group=%s",
                admin_group_cn,
            )
            return

        recipients: list[str] = []
        for username in admin_group.members:
            user = FreeIPAUser.get(username)
            if user is not None and user.email:
                recipients.append(user.email)

        unique_recipients = sorted({email.strip() for email in recipients if email.strip()}, key=str.lower)
        if not unique_recipients:
            logger.error("freeipa_membership_reconcile: no_admin_recipients group=%s", admin_group_cn)
            return

        context = {
            "mode": mode,
            "group_cn_filter": group_cn_filter,
            "run_at": now.isoformat(),
            "groups": group_reports,
            "total_missing": total_missing,
            "total_extra": total_extra,
            "total_errors": total_errors,
        }

        if dry_run:
            self.stdout.write(
                "[dry-run] Would queue alert email to "
                f"{len(unique_recipients)} recipient(s)."
            )
            logger.info(
                "freeipa_membership_reconcile: dry_run_alert_suppressed recipients=%s",
                len(unique_recipients),
            )
            return

        post_office.mail.send(
            recipients=unique_recipients,
            sender=settings.DEFAULT_FROM_EMAIL,
            template=settings.FREEIPA_MEMBERSHIP_RECONCILE_ALERT_EMAIL_TEMPLATE_NAME,
            context=context,
        )

        logger.info(
            "freeipa_membership_reconcile: alert_queued recipients=%s",
            len(unique_recipients),
        )

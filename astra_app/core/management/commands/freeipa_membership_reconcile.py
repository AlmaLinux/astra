import logging
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser
from core.logging_extras import current_exception_log_fields
from core.models import Membership, MembershipRequest, MembershipType
from core.templated_email import queue_templated_email

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
        parser.add_argument(
            "--targeted",
            action="store_true",
            help="Enable targeted reconciliation mode (requires exactly one selector).",
        )
        parser.add_argument(
            "--request-id",
            type=int,
            default=None,
            help="Target one approved membership request by ID.",
        )
        parser.add_argument(
            "--username",
            default="",
            help="Target one username directly.",
        )

    @override
    def handle(self, *args, **options) -> None:
        report: bool = bool(options.get("report"))
        fix: bool = bool(options.get("fix"))
        group_cn_option = str(options.get("group_cn") or "").strip()
        group_cn_filter = group_cn_option
        limit: int = int(options.get("limit") or 0)
        dry_run: bool = bool(options.get("dry_run"))
        targeted_requested: bool = bool(options.get("targeted"))
        request_id_option = options.get("request_id")
        request_id = int(request_id_option) if request_id_option is not None else None
        username_selector = str(options.get("username") or "").strip()

        if report and fix:
            raise CommandError("Choose only one of --report or --fix.")
        if not report and not fix:
            report = True
        if limit < 0:
            raise CommandError("--limit must be zero or a positive integer.")

        has_request_selector = request_id is not None
        has_username_selector = bool(username_selector)
        targeted_mode = targeted_requested or has_request_selector or has_username_selector

        if has_request_selector and has_username_selector:
            raise CommandError(
                "Targeted mode requires exactly one selector: use either --request-id or --username."
            )
        if targeted_mode and not has_request_selector and not has_username_selector:
            raise CommandError(
                "Targeted mode requires exactly one selector: provide --request-id or --username."
            )

        selector_type = "<none>"
        selector_value = "<none>"
        target = ""
        resolved_request_id: int | None = None
        resolved_membership_type_id: str | None = None

        if has_request_selector:
            membership_request = (
                MembershipRequest.objects.select_related("membership_type", "requested_organization")
                .filter(pk=request_id)
                .first()
            )
            if membership_request is None:
                raise CommandError(f"membership request ID {request_id} does not exist")
            if membership_request.status != MembershipRequest.Status.approved:
                raise CommandError(
                    "membership request ID "
                    f"{membership_request.pk} must be approved for targeted reconcile; "
                    f"status={membership_request.status}"
                )

            resolved_group = str(membership_request.membership_type.group_cn or "").strip()
            if not resolved_group:
                raise CommandError(
                    f"membership request ID {membership_request.pk} has no configured membership group"
                )

            if group_cn_option and group_cn_option != resolved_group:
                raise CommandError(
                    f"--group-cn ({group_cn_option}) must match request group ({resolved_group})"
                )

            if membership_request.is_user_target:
                target = str(membership_request.requested_username or "").strip()
            else:
                if membership_request.requested_organization is None:
                    raise CommandError(
                        f"membership request ID {membership_request.pk} has no organization to resolve representative"
                    )
                target = str(membership_request.requested_organization.representative or "").strip()

            if not target:
                raise CommandError(
                    f"membership request ID {membership_request.pk} resolved an empty target identity"
                )

            selector_type = "request_id"
            selector_value = str(membership_request.pk)
            resolved_request_id = membership_request.pk
            resolved_membership_type_id = membership_request.membership_type_id
            group_cn_filter = resolved_group
        elif has_username_selector:
            selector_type = "username"
            selector_value = username_selector
            target = username_selector

        if dry_run:
            if fix:
                logger.info("freeipa_membership_reconcile: dry-run overrides fix mode")
            fix = False
            report = True

        mode = "fix" if fix else "report"
        now = timezone.now()

        logger.info(
            (
                "freeipa_membership_reconcile: start mode=%s targeted=%s selector_type=%s "
                "selector_value=%s target=%s group=%s request_id=%s limit=%s"
            ),
            mode,
            targeted_mode,
            selector_type,
            selector_value,
            target or "<none>",
            group_cn_filter or "<all>",
            resolved_request_id if resolved_request_id is not None else "<none>",
            limit or "unlimited",
        )

        if resolved_membership_type_id is not None:
            # Request-id targeting must reconcile the request's membership type even when disabled.
            membership_types = MembershipType.objects.filter(pk=resolved_membership_type_id)
        else:
            membership_types = MembershipType.objects.filter(enabled=True).exclude(group_cn="")
        if group_cn_filter:
            membership_types = membership_types.filter(group_cn=group_cn_filter)

        org_memberships = list(
            Membership.objects.select_related("target_organization", "membership_type")
            .filter(target_organization__isnull=False)
        )

        for org_membership in org_memberships:
            if org_membership.expires_at is not None and org_membership.expires_at <= now:
                logger.warning(
                    "sponsorship_divergence org_id=%s org_name=%s sponsorship_level=%s reason=expired_sponsorship",
                    org_membership.target_organization_id,
                    org_membership.target_organization_name,
                    org_membership.membership_type_id,
                )

        group_reports: list[dict[str, object]] = []
        total_missing = 0
        total_extra = 0
        total_errors = 0
        mutation_budget = limit
        freeipa_users_by_username: dict[str, FreeIPAUser | None] = {}

        def get_freeipa_user(username: str) -> FreeIPAUser | None:
            normalized = str(username or "").strip()
            if not normalized:
                return None
            if normalized not in freeipa_users_by_username:
                freeipa_users_by_username[normalized] = FreeIPAUser.get(normalized)
            return freeipa_users_by_username[normalized]

        for membership_type in membership_types.order_by("code"):
            group_cn = str(membership_type.group_cn or "").strip()
            if not group_cn:
                continue

            expected: set[str] = set()

            # Individual members: active Membership rows for this type.
            if membership_type.category.is_individual:
                active_memberships = Membership.objects.filter(
                    membership_type=membership_type,
                ).active(at=now)

                for username in active_memberships.values_list("target_username", flat=True):
                    normalized = str(username or "").strip()
                    if normalized:
                        expected.add(normalized)

            # Organization members: representatives of orgs with an active
            # Membership row for this type (source of truth: Membership table).
            if membership_type.category.is_organization:
                active_org_memberships = (
                    Membership.objects.filter(
                        membership_type=membership_type,
                        target_organization__isnull=False,
                    )
                    .active(at=now)
                    .select_related("target_organization")
                )
                for m in active_org_memberships:
                    org = m.target_organization
                    if org is not None:
                        representative = str(org.representative or "").strip()
                        if representative:
                            expected.add(representative)

            if targeted_mode:
                if target not in expected:
                    outcome = "noop_target_not_expected"
                    logger.info(
                        (
                            "freeipa_membership_reconcile: targeted_outcome selector_type=%s selector_value=%s "
                            "target=%s group=%s mode=%s outcome=%s request_id=%s"
                        ),
                        selector_type,
                        selector_value,
                        target,
                        group_cn,
                        mode,
                        outcome,
                        resolved_request_id if resolved_request_id is not None else "<none>",
                    )
                    group_reports.append(
                        {
                            "group_cn": group_cn,
                            "missing_count": 0,
                            "extra_count": 0,
                            "missing_sample": [],
                            "extra_sample": [],
                            "errors": [],
                            "selector_type": selector_type,
                            "selector_value": selector_value,
                            "target": target,
                            "mode": mode,
                            "outcome": outcome,
                            "request_id": resolved_request_id,
                        }
                    )
                    continue
                expected = {target}

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
                        "selector_type": selector_type,
                        "selector_value": selector_value,
                        "target": target,
                        "mode": mode,
                        "outcome": "group_not_found",
                        "request_id": resolved_request_id,
                    }
                )
                logger.error("freeipa_membership_reconcile: group_not_found group=%s", group_cn)
                continue

            actual = {str(member or "").strip() for member in group.members if str(member or "").strip()}
            missing = sorted(expected - actual, key=str.lower)
            extra = sorted(actual - expected, key=str.lower)
            if targeted_mode:
                # Targeted fix is add-only and must not remove unrelated identities.
                extra = []

            errors: list[str] = []
            missing_freeipa_users = {
                username for username in missing if get_freeipa_user(username) is None
            }
            for username in sorted(missing_freeipa_users, key=str.lower):
                errors.append(f"expected_user_missing:{username}")
                logger.warning(
                    "freeipa_membership_reconcile: expected_user_missing group=%s username=%s",
                    group_cn,
                    username,
                )
            total_missing += len(missing)
            total_extra += len(extra)

            logger.info(
                (
                    "freeipa_membership_reconcile: group_diff selector_type=%s selector_value=%s "
                    "target=%s group=%s mode=%s missing=%s extra=%s request_id=%s"
                ),
                selector_type,
                selector_value,
                target or "<none>",
                group_cn,
                mode,
                len(missing),
                len(extra),
                resolved_request_id if resolved_request_id is not None else "<none>",
            )

            mutations_performed = False
            if fix and (missing or extra):
                for username in missing:
                    if username in missing_freeipa_users:
                        continue
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
                            extra=current_exception_log_fields(),
                        )
                    else:
                        mutations_performed = True
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
                            extra=current_exception_log_fields(),
                        )
                    else:
                        mutations_performed = True
                        if limit > 0:
                            mutation_budget -= 1

            if targeted_mode:
                if errors:
                    outcome = "targeted_error"
                elif mutations_performed:
                    outcome = "mutated_add_only"
                elif missing or extra:
                    outcome = "reported_drift" if mode == "report" else "pending_remediation"
                else:
                    outcome = "noop_already_in_sync"
                logger.info(
                    (
                        "freeipa_membership_reconcile: targeted_outcome selector_type=%s selector_value=%s "
                        "target=%s group=%s mode=%s outcome=%s request_id=%s"
                    ),
                    selector_type,
                    selector_value,
                    target,
                    group_cn,
                    mode,
                    outcome,
                    resolved_request_id if resolved_request_id is not None else "<none>",
                )
            else:
                outcome = "mutated" if mutations_performed else "reported"

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
                    "selector_type": selector_type,
                    "selector_value": selector_value,
                    "target": target,
                    "mode": mode,
                    "outcome": outcome,
                    "request_id": resolved_request_id,
                }
            )

        logger.info(
            "freeipa_membership_reconcile: Reconciliation complete groups=%s missing=%s extra=%s errors=%s",
            len(group_reports),
            total_missing,
            total_extra,
            total_errors,
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
            user = get_freeipa_user(username)
            if user is not None and user.email:
                recipients.append(user.email)

        unique_recipients = sorted({email.strip() for email in recipients if email.strip()}, key=str.lower)
        if not unique_recipients:
            logger.error("freeipa_membership_reconcile: no_admin_recipients group=%s", admin_group_cn)
            return

        context = {
            "mode": mode,
            "group_cn_filter": group_cn_filter,
            "selector_type": selector_type,
            "selector_value": selector_value,
            "target": target,
            "request_id": resolved_request_id,
            "run_at": now.isoformat(),
            "groups": group_reports,
            "total_missing": total_missing,
            "total_extra": total_extra,
            "total_errors": total_errors,
        }

        if dry_run:
            logger.info(
                "[dry-run] Would queue alert email to %s recipient(s).",
                len(unique_recipients),
            )
            logger.info(
                "freeipa_membership_reconcile: dry_run_alert_suppressed recipients=%s",
                len(unique_recipients),
            )
            return

        queue_templated_email(
            recipients=unique_recipients,
            sender=settings.DEFAULT_FROM_EMAIL,
            template_name=settings.FREEIPA_MEMBERSHIP_RECONCILE_ALERT_EMAIL_TEMPLATE_NAME,
            context=context,
        )

        logger.info(
            "freeipa_membership_reconcile: alert_queued recipients=%s",
            len(unique_recipients),
        )

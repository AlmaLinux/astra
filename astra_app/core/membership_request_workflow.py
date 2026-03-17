import dataclasses
import datetime
import logging
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone
from post_office.models import EmailTemplate

from core import signals as astra_signals
from core.agreements import missing_required_agreements_for_user_in_group
from core.email_context import (
    freeform_message_email_context,
    membership_committee_email_context,
    organization_sponsor_email_context,
    system_email_context,
    user_email_context_from_user,
)
from core.freeipa.user import FreeIPAUser
from core.logging_extras import current_exception_log_fields
from core.membership import (
    FreeIPACallerMode,
    FreeIPAMissingUserPolicy,
    sync_organization_representative_membership_groups,
)
from core.membership_constants import MembershipCategoryCode
from core.membership_log_side_effects import apply_membership_log_side_effects
from core.membership_notes import add_note
from core.membership_notifications import organization_sponsor_notification_recipient_email
from core.membership_response_normalization import normalize_membership_request_responses
from core.mirror_membership_validation import (
    mirror_answers_fingerprint,
    mirror_request_answers_from_responses,
    schedule_mirror_membership_validation,
)
from core.models import (
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    Organization,
)
from core.templated_email import queue_templated_email

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class MembershipTarget:
    """Resolved notification target for a membership request.

    Encapsulates the user-vs-org differences so workflow functions can
    operate on a single code path.
    """

    email: str
    email_context: dict[str, object]
    target_username: str
    target_organization: Organization | None


def _build_membership_target(membership_request: MembershipRequest) -> MembershipTarget:
    """Resolve the notification target from a membership request."""
    if membership_request.target_kind == MembershipRequest.TargetKind.user:
        user = FreeIPAUser.get(membership_request.requested_username)
        return MembershipTarget(
            email=user.email if user is not None else "",
            email_context=user_email_context_from_user(user=user) if user is not None else {},
            target_username=membership_request.requested_username,
            target_organization=None,
        )
    org = membership_request.requested_organization
    if org is not None:
        org_email = _organization_notification_email(org)
        return MembershipTarget(
            email=org_email,
            email_context={
                **organization_sponsor_email_context(organization=org),
                "organization_name": org.name,
            },
            target_username="",
            target_organization=org,
        )
    # Orphaned org request: org FK is None but code/name may exist on the request.
    return MembershipTarget(
        email="",
        email_context={
            "organization_name": membership_request.organization_display_name,
        },
        target_username="",
        target_organization=None,
    )


def _ensure_configured_email_template_exists(*, template_name: str) -> None:
    name = str(template_name or "").strip()
    if not name:
        raise ValidationError("Configured email template name is empty")

    if not EmailTemplate.objects.filter(name=name).exists():
        raise ValidationError(
            f"Configured email template {name!r} was not found. "
            "Please recreate it (or update the relevant setting / membership type template)."
        )


def _email_id_from_sent_email(sent_email: object | None) -> int | None:
    if sent_email is None:
        return None
    if not hasattr(sent_email, "id"):
        return None
    raw_id = sent_email.id
    if isinstance(raw_id, int):
        return raw_id
    if isinstance(raw_id, str) and raw_id.isdigit():
        return int(raw_id)
    return None


def _organization_notification_email(organization: Organization) -> str:
    """Preferred recipient for org notifications.

    Prefer the organization's representative when it resolves to a
    FreeIPA user with an email address; otherwise fall back to the organization's
    primary contact email.
    """
    recipient_email, _recipient_warning = organization_sponsor_notification_recipient_email(
        organization=organization,
        notification_kind="organization workflow notification",
    )
    return recipient_email


def _resolve_approval_template_name(
    *, membership_type: MembershipType, override: str | None,
) -> str:
    """Resolve the email template for an approval notification."""
    if override:
        return override
    if membership_type.acceptance_template_id is not None:
        return membership_type.acceptance_template.name
    return settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME


def _try_add_note(
    *,
    membership_request: MembershipRequest,
    username: str,
    action: dict[str, Any],
    log_prefix: str,
) -> None:
    """Persist an action note, logging and swallowing failures."""
    try:
        add_note(membership_request=membership_request, username=username, action=action)
    except Exception:
        logger.exception(
            "%s: failed to record note request_id=%s",
            log_prefix,
            membership_request.pk,
            extra=current_exception_log_fields(),
        )


def _try_record_email_note(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    sent_email: object | None,
    email_kind: str,
    log_prefix: str,
) -> None:
    """Record a 'contacted' note if an email was successfully sent."""
    email_id = _email_id_from_sent_email(sent_email)
    if email_id is not None:
        _try_add_note(
            membership_request=membership_request,
            username=actor_username,
            action={"type": "contacted", "kind": email_kind, "email_id": email_id},
            log_prefix=log_prefix,
        )


def _is_freeipa_noop_error(*, error: Exception, is_add: bool) -> bool:
    text = str(error or "").strip().lower()
    if not text:
        return False
    if is_add:
        return "already" in text and "member" in text
    return "not" in text and "member" in text


def _send_membership_request_notification(
    *,
    target: MembershipTarget,
    membership_type: MembershipType,
    template_name: str,
    extra_context: dict[str, object] | None = None,
) -> object | None:
    if not target.email:
        return None

    context: dict[str, object] = {
        **system_email_context(),
        **target.email_context,
        **membership_committee_email_context(),
        "membership_type": membership_type.name,
        "membership_type_code": membership_type.code,
    }
    if extra_context:
        context.update(extra_context)

    return queue_templated_email(
        recipients=[target.email],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=template_name,
        context=context,
        reply_to=[settings.MEMBERSHIP_COMMITTEE_EMAIL],
    )


def _active_expires_at(queryset: QuerySet) -> datetime.datetime | None:
    """Return expires_at if the first matching record is still valid, else None."""
    current = queryset.first()
    if current is None or current.expires_at is None:
        return None
    if current.expires_at <= timezone.now():
        return None
    return current.expires_at


def _create_status_change_log(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    membership_type: MembershipType,
    action: str,
    rejection_reason: str = "",
) -> MembershipLog:
    """Create a MembershipLog for a status change, routing user vs org target."""
    organization = membership_request.requested_organization
    organization_identifier = ""
    organization_name = ""
    if membership_request.target_kind == MembershipRequest.TargetKind.organization and organization is None:
        organization_identifier = membership_request.organization_identifier
        organization_name = membership_request.organization_display_name

    log = MembershipLog._create_log(
        actor_username=actor_username,
        target_username=membership_request.requested_username,
        target_organization=organization,
        target_organization_code=organization_identifier,
        target_organization_name=organization_name,
        membership_type=membership_type,
        membership_request=membership_request,
        action=action,
        rejection_reason=rejection_reason,
    )
    # Defensive call: current status-change actions routed through this helper
    # are no-ops for membership side effects, but this preserves safety if a
    # side-effecting action is routed through this path in the future.
    apply_membership_log_side_effects(log=log)
    return log


def _emit_membership_request_signal_on_commit(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    user_signal: object,
    organization_signal: object,
) -> None:
    request_id = membership_request.pk
    if request_id is None:
        return

    if membership_request.is_organization_target:
        organization_id = membership_request.requested_organization_id
        organization_display_name = membership_request.organization_display_name

        def _send_org_signal() -> None:
            committed_request = MembershipRequest.objects.get(pk=request_id)
            organization_signal.send(
                sender=MembershipRequest,
                membership_request=committed_request,
                actor=actor_username,
                organization_id=organization_id,
                organization_display_name=organization_display_name,
            )

        transaction.on_commit(_send_org_signal)
        return

    def _send_user_signal() -> None:
        committed_request = MembershipRequest.objects.get(pk=request_id)
        user_signal.send(
            sender=MembershipRequest,
            membership_request=committed_request,
            actor=actor_username,
        )

    transaction.on_commit(_send_user_signal)


def previous_expires_at_for_extension(
    *,
    membership_request: MembershipRequest,
    membership_type: MembershipType,
) -> datetime.datetime | None:
    """Return the expiry for an active membership if it can be extended.

    Only extend active memberships; if the current row is missing or already
    expired, return None so approval starts a new term.
    """
    filters: dict[str, object] = {"membership_type": membership_type}
    if membership_request.target_kind == MembershipRequest.TargetKind.user:
        normalized_username = str(membership_request.requested_username or "").strip()
        if not normalized_username:
            return None
        filters["target_username"] = normalized_username
    else:
        organization = membership_request.requested_organization
        if organization is None:
            return None
        filters["target_organization_id"] = organization.pk

    return _active_expires_at(Membership.objects.filter(**filters))


def record_membership_request_created(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    send_submitted_email: bool,
) -> None:
    """Record the initial request audit log and optionally email the requester."""

    membership_type = membership_request.membership_type
    log_prefix = "record_membership_request_created"

    _try_add_note(
        membership_request=membership_request,
        username=actor_username,
        action={"type": "request_created"},
        log_prefix=log_prefix,
    )

    if membership_request.target_kind == MembershipRequest.TargetKind.user:
        email_error: Exception | None = None
        sent_email = None

        if send_submitted_email:
            try:
                target = FreeIPAUser.get(membership_request.requested_username)
            except Exception as e:
                logger.exception(
                    "%s: FreeIPAUser.get failed for submitted email request_id=%s target=%r",
                    log_prefix,
                    membership_request.pk,
                    membership_request.requested_username,
                    extra=current_exception_log_fields(),
                )
                email_error = e
            else:
                if target is not None and target.email:
                    try:
                        sent_email = queue_templated_email(
                            recipients=[target.email],
                            sender=settings.DEFAULT_FROM_EMAIL,
                            template_name=settings.MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME,
                            context={
                                **system_email_context(),
                                **user_email_context_from_user(user=target),
                                **membership_committee_email_context(),
                                "membership_type": membership_type.name,
                                "membership_type_code": membership_type.code,
                            },
                            reply_to=[settings.MEMBERSHIP_COMMITTEE_EMAIL],
                        )
                    except Exception as e:
                        logger.exception(
                            "%s: sending submitted email failed request_id=%s target=%r",
                            log_prefix,
                            membership_request.pk,
                            membership_request.requested_username,
                            extra=current_exception_log_fields(),
                        )
                        email_error = e

        _try_record_email_note(
            membership_request=membership_request,
            actor_username=actor_username,
            sent_email=sent_email,
            email_kind="submitted",
            log_prefix=log_prefix,
        )

        try:
            _create_status_change_log(
                membership_request=membership_request,
                actor_username=actor_username,
                membership_type=membership_type,
                action=MembershipLog.Action.requested,
            )
        except Exception:
            logger.exception(
                "%s: failed to create requested log (user) request_id=%s actor=%r target=%r membership_type=%s",
                log_prefix,
                membership_request.pk,
                actor_username,
                membership_request.requested_username,
                membership_type.code,
                extra=current_exception_log_fields(),
            )
            raise

        _emit_membership_request_signal_on_commit(
            membership_request=membership_request,
            actor_username=actor_username,
            user_signal=astra_signals.membership_request_submitted,
            organization_signal=astra_signals.organization_membership_request_submitted,
        )

        if membership_type.category_id == MembershipCategoryCode.mirror and membership_request.pk is not None:
            request_id = membership_request.pk

            def _schedule_mirror_validation_on_commit() -> None:
                try:
                    committed_request = MembershipRequest.objects.get(pk=request_id)
                    schedule_mirror_membership_validation(membership_request=committed_request)
                except Exception:
                    logger.exception(
                        "%s: failed to schedule mirror validation request_id=%s",
                        log_prefix,
                        request_id,
                        extra=current_exception_log_fields(),
                    )

            transaction.on_commit(_schedule_mirror_validation_on_commit)

        if email_error is not None:
            raise email_error
        return

    # Organization request (with or without live FK).
    email_error: Exception | None = None
    sent_email = None
    organization = membership_request.requested_organization
    if send_submitted_email and organization is not None:
        recipient_email, recipient_warning = organization_sponsor_notification_recipient_email(
            organization=organization,
            notification_kind="organization membership request submitted",
        )
        if recipient_warning:
            logger.warning(
                "%s: %s request_id=%s org_id=%s",
                log_prefix,
                recipient_warning,
                membership_request.pk,
                organization.pk,
            )
        elif recipient_email:
            try:
                sent_email = queue_templated_email(
                    recipients=[recipient_email],
                    sender=settings.DEFAULT_FROM_EMAIL,
                    template_name=settings.MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME,
                    context={
                        **system_email_context(),
                        **organization_sponsor_email_context(organization=organization),
                        **membership_committee_email_context(),
                        "organization_name": organization.name,
                        "membership_type": membership_type.name,
                        "membership_type_code": membership_type.code,
                    },
                    reply_to=[settings.MEMBERSHIP_COMMITTEE_EMAIL],
                )
            except Exception as e:
                logger.exception(
                    "%s: sending submitted email failed request_id=%s org_id=%s",
                    log_prefix,
                    membership_request.pk,
                    organization.pk,
                    extra=current_exception_log_fields(),
                )
                email_error = e

    _try_record_email_note(
        membership_request=membership_request,
        actor_username=actor_username,
        sent_email=sent_email,
        email_kind="submitted",
        log_prefix=log_prefix,
    )

    try:
        _create_status_change_log(
            membership_request=membership_request,
            actor_username=actor_username,
            membership_type=membership_type,
            action=MembershipLog.Action.requested,
        )
    except Exception:
        logger.exception(
            "%s: failed to create requested log (org) request_id=%s actor=%r membership_type=%s",
            log_prefix,
            membership_request.pk,
            actor_username,
            membership_type.code,
            extra=current_exception_log_fields(),
        )
        raise

    _emit_membership_request_signal_on_commit(
        membership_request=membership_request,
        actor_username=actor_username,
        user_signal=astra_signals.membership_request_submitted,
        organization_signal=astra_signals.organization_membership_request_submitted,
    )

    if membership_type.category_id == MembershipCategoryCode.mirror and membership_request.pk is not None:
        request_id = membership_request.pk

        def _schedule_mirror_validation_on_commit() -> None:
            try:
                committed_request = MembershipRequest.objects.get(pk=request_id)
                schedule_mirror_membership_validation(membership_request=committed_request)
            except Exception:
                logger.exception(
                    "%s: failed to schedule mirror validation request_id=%s",
                    log_prefix,
                    request_id,
                    extra=current_exception_log_fields(),
                )

        transaction.on_commit(_schedule_mirror_validation_on_commit)

    if email_error is not None:
        raise email_error


@transaction.atomic
def approve_membership_request(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    send_approved_email: bool,
    allow_on_hold_override: bool = False,
    on_hold_override_justification: str = "",
    approved_email_template_name: str | None = None,
    decided_at: datetime.datetime | None = None,
) -> MembershipLog:
    """Approve a membership request using the same code path as the UI.

    This function applies FreeIPA side-effects and records the approval log.
    It updates the request status fields and optionally emails the requester.
    """

    membership_request = (
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .select_for_update(of=("self",))
        .get(pk=membership_request.pk)
    )
    membership_type = membership_request.membership_type
    decided = decided_at or timezone.now()
    log_prefix = "approve_membership_request"

    original_status = membership_request.status
    allowed_statuses = {MembershipRequest.Status.pending}
    if allow_on_hold_override:
        allowed_statuses.add(MembershipRequest.Status.on_hold)
    if original_status not in allowed_statuses:
        if allow_on_hold_override:
            raise ValidationError("Only pending or on-hold requests can be approved")
        raise ValidationError("Only pending requests can be approved")

    group_add_payload: tuple[str, str] | None = None
    representative_sync_payload: tuple[str, tuple[str, ...], str | None] | None = None

    if membership_request.target_kind == MembershipRequest.TargetKind.organization:
        org = membership_request.requested_organization
        if org is None:
            raise ValidationError("Organization not found")

        if membership_type.group_cn and org.representative:
            missing_agreements = missing_required_agreements_for_user_in_group(
                org.representative,
                membership_type.group_cn,
            )
            if missing_agreements:
                missing_list = ", ".join(missing_agreements)
                raise ValidationError(
                    "Representative must sign required agreements before approval: "
                    f"{missing_list}"
                )

        email_recipient = _organization_notification_email(org)

        template_name: str | None = None
        if send_approved_email and email_recipient:
            template_name = _resolve_approval_template_name(
                membership_type=membership_type, override=approved_email_template_name,
            )
            _ensure_configured_email_template_exists(template_name=template_name)

        previous_expires_at = previous_expires_at_for_extension(
            membership_request=membership_request,
            membership_type=membership_type,
        )

        old_membership = (
            Membership.objects.select_related("membership_type")
            .filter(
                target_organization=org,
                membership_type__category=membership_type.category,
            )
            .first()
        )

        new_group_cn = str(membership_type.group_cn or "").strip()

        if new_group_cn and org.representative:
            try:
                representative = FreeIPAUser.get(org.representative)
            except Exception:
                logger.exception(
                    "%s: FreeIPAUser.get failed (org representative) request_id=%s org_id=%s representative=%r",
                    log_prefix,
                    membership_request.pk,
                    org.pk,
                    org.representative,
                    extra=current_exception_log_fields(),
                )
                raise

            if representative is not None:
                old_group_cn_for_cleanup: str | None = None
                if old_membership is not None and old_membership.membership_type.group_cn:
                    old_group_cn = str(old_membership.membership_type.group_cn or "").strip()
                    if old_group_cn and old_group_cn != new_group_cn:
                        old_group_cn_for_cleanup = old_group_cn
                representative_sync_payload = (
                    representative.username,
                    (new_group_cn,),
                    old_group_cn_for_cleanup,
                )

        email_context: dict[str, object] = (
            {
                **system_email_context(),
                **organization_sponsor_email_context(organization=org),
                **membership_committee_email_context(),
                "organization_name": org.name,
                "membership_type": membership_type.name,
                "membership_type_code": membership_type.code,
            }
            if template_name is not None
            else {}
        )
        log_kwargs: dict[str, object] = {"target_organization": org}

    else:
        if not membership_type.group_cn:
            raise ValidationError("This membership type is not linked to a group")

        missing_agreements = missing_required_agreements_for_user_in_group(
            membership_request.requested_username,
            membership_type.group_cn,
        )
        if missing_agreements:
            missing_list = ", ".join(missing_agreements)
            raise ValidationError(
                "User must sign required agreements before approval: "
                f"{missing_list}"
            )

        try:
            user = FreeIPAUser.get(membership_request.requested_username)
        except Exception:
            logger.exception(
                "%s: FreeIPAUser.get failed request_id=%s target=%r",
                log_prefix,
                membership_request.pk,
                membership_request.requested_username,
                extra=current_exception_log_fields(),
            )
            raise
        if user is None:
            raise ValidationError("Unable to load the requested user from FreeIPA")

        email_recipient = user.email

        template_name = None
        if send_approved_email and email_recipient:
            template_name = _resolve_approval_template_name(
                membership_type=membership_type, override=approved_email_template_name,
            )
            _ensure_configured_email_template_exists(template_name=template_name)

        previous_expires_at = previous_expires_at_for_extension(
            membership_request=membership_request,
            membership_type=membership_type,
        )
        group_add_payload = (membership_request.requested_username, membership_type.group_cn)

        email_context = (
            {
                **system_email_context(),
                **user_email_context_from_user(user=user),
                **membership_committee_email_context(),
                "membership_type": membership_type.name,
                "membership_type_code": membership_type.code,
                "group_cn": membership_type.group_cn,
            }
            if template_name is not None
            else {}
        )
        log_kwargs = {"target_username": membership_request.requested_username}

    membership_request.status = MembershipRequest.Status.approved
    membership_request.on_hold_at = None
    membership_request.decided_at = decided
    membership_request.decided_by_username = actor_username
    membership_request.save(update_fields=["status", "on_hold_at", "decided_at", "decided_by_username"])

    sent_email = None
    if template_name is not None:
        try:
            sent_email = queue_templated_email(
                recipients=[email_recipient],
                sender=settings.DEFAULT_FROM_EMAIL,
                template_name=template_name,
                context=email_context,
                reply_to=[settings.MEMBERSHIP_COMMITTEE_EMAIL],
            )
        except Exception:
            logger.exception(
                "%s: sending approved email failed request_id=%s",
                log_prefix,
                membership_request.pk,
                extra=current_exception_log_fields(),
            )
            raise

    log = MembershipLog.create_for_approval_at(
        actor_username=actor_username,
        membership_type=membership_type,
        approved_at=decided,
        previous_expires_at=previous_expires_at,
        membership_request=membership_request,
        **log_kwargs,
    )

    _try_add_note(
        membership_request=membership_request,
        username=actor_username,
        action={"type": "request_approved"},
        log_prefix=log_prefix,
    )
    if original_status == MembershipRequest.Status.on_hold:
        note_action: dict[str, str] = {"type": "on_hold_override_approved", "by": actor_username}
        justification = str(on_hold_override_justification or "").strip()
        if justification:
            note_action["actors_note"] = justification
        _try_add_note(
            membership_request=membership_request,
            username=actor_username,
            action=note_action,
            log_prefix=log_prefix,
        )
    _try_record_email_note(
        membership_request=membership_request,
        actor_username=actor_username,
        sent_email=sent_email,
        email_kind="approved",
        log_prefix=log_prefix,
    )

    if group_add_payload is not None:
        username_to_add, group_cn_to_add = group_add_payload

        def _on_commit_add_user_to_group() -> None:
            try:
                user_for_group_add = FreeIPAUser.get(username_to_add)
            except Exception:
                logger.exception(
                    "%s: on_commit FreeIPAUser.get failed request_id=%s target=%r",
                    log_prefix,
                    membership_request.pk,
                    username_to_add,
                    extra=current_exception_log_fields(),
                )
                return

            if user_for_group_add is None:
                logger.warning(
                    "%s: on_commit user missing for group add request_id=%s target=%r",
                    log_prefix,
                    membership_request.pk,
                    username_to_add,
                )
                return

            try:
                user_for_group_add.add_to_group(group_name=group_cn_to_add)
            except Exception as exc:
                if _is_freeipa_noop_error(error=exc, is_add=True):
                    logger.info(
                        "astra.membership.freeipa_group.already_member group_cn=%s outcome=noop",
                        group_cn_to_add,
                        extra={
                            "event": "astra.freeipa.group.mutation",
                            "component": "membership",
                            "outcome": "already_member",
                        },
                    )
                else:
                    logger.exception(
                        "%s: on_commit add_to_group failed request_id=%s target=%r group_cn=%r",
                        log_prefix,
                        membership_request.pk,
                        user_for_group_add.username,
                        group_cn_to_add,
                        extra=current_exception_log_fields(),
                    )

        transaction.on_commit(_on_commit_add_user_to_group)

    if representative_sync_payload is not None:
        representative_username, group_cns, old_group_cn_to_remove = representative_sync_payload

        def _on_commit_sync_representative_groups() -> None:
            sync_organization_representative_membership_groups(
                representative_username=representative_username,
                group_cns=group_cns,
                old_group_cn_to_remove=old_group_cn_to_remove,
                membership_request_id=membership_request.pk,
                log_prefix=log_prefix,
                caller_mode=FreeIPACallerMode.raise_on_error,
                missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
            )

        transaction.on_commit(_on_commit_sync_representative_groups)

    _emit_membership_request_signal_on_commit(
        membership_request=membership_request,
        actor_username=actor_username,
        user_signal=astra_signals.membership_request_approved,
        organization_signal=astra_signals.organization_membership_request_approved,
    )

    return log


def approve_on_hold_membership_request(
    *,
    request_id: int,
    actor_username: str,
    justification: str,
    send_approved_email: bool = True,
) -> MembershipLog:
    membership_request = MembershipRequest.objects.select_related("membership_type", "requested_organization").get(pk=request_id)

    if membership_request.status != MembershipRequest.Status.on_hold:
        raise ValidationError("Only on-hold requests can be approved with override")

    normalized_justification = str(justification or "").strip()
    if not normalized_justification:
        raise ValidationError("Override justification is required")

    return approve_membership_request(
        membership_request=membership_request,
        actor_username=actor_username,
        send_approved_email=send_approved_email,
        allow_on_hold_override=True,
        on_hold_override_justification=normalized_justification,
    )


@transaction.atomic
def reject_membership_request(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    rejection_reason: str,
    send_rejected_email: bool,
    decided_at: datetime.datetime | None = None,
) -> tuple[MembershipLog, Exception | None]:
    membership_request = (
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .select_for_update(of=("self",))
        .get(pk=membership_request.pk)
    )
    membership_type = membership_request.membership_type
    decided = decided_at or timezone.now()
    log_prefix = "reject_membership_request"

    if membership_request.status not in {MembershipRequest.Status.pending, MembershipRequest.Status.on_hold}:
        raise ValidationError("Only pending or on-hold requests can be rejected")

    reason = str(rejection_reason or "").strip()
    if reason:
        existing = membership_request.responses
        if isinstance(existing, list):
            responses = list(existing)
        else:
            responses = []

        # Preserve the applicant's original answers; store the committee's reason
        # as an additional response item so it is visible on request detail pages.
        responses.append({"Rejection reason": reason})
        membership_request.responses = responses

    target = _build_membership_target(membership_request)

    membership_request.status = MembershipRequest.Status.rejected
    membership_request.decided_at = decided
    membership_request.decided_by_username = actor_username
    membership_request.save(update_fields=["responses", "status", "decided_at", "decided_by_username"])

    email_error: Exception | None = None
    sent_email = None
    if send_rejected_email and target.email:
        try:
            sent_email = _send_membership_request_notification(
                target=target,
                membership_type=membership_type,
                template_name=settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME,
                extra_context=freeform_message_email_context(key="rejection_reason", value=reason),
            )
        except Exception as e:
            logger.exception(
                "%s: sending rejected email failed request_id=%s",
                log_prefix,
                membership_request.pk,
                extra=current_exception_log_fields(),
            )
            email_error = e

    log = _create_status_change_log(
        membership_request=membership_request,
        actor_username=actor_username,
        membership_type=membership_type,
        action=MembershipLog.Action.rejected,
        rejection_reason=reason,
    )

    _try_add_note(
        membership_request=membership_request,
        username=actor_username,
        action={"type": "request_rejected"},
        log_prefix=log_prefix,
    )
    _try_record_email_note(
        membership_request=membership_request,
        actor_username=actor_username,
        sent_email=sent_email,
        email_kind="rejected",
        log_prefix=log_prefix,
    )

    _emit_membership_request_signal_on_commit(
        membership_request=membership_request,
        actor_username=actor_username,
        user_signal=astra_signals.membership_request_rejected,
        organization_signal=astra_signals.organization_membership_request_rejected,
    )

    return log, email_error


@transaction.atomic
def ignore_membership_request(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    decided_at: datetime.datetime | None = None,
) -> MembershipLog:
    membership_request = (
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .select_for_update(of=("self",))
        .get(pk=membership_request.pk)
    )
    membership_type = membership_request.membership_type
    decided = decided_at or timezone.now()
    log_prefix = "ignore_membership_request"

    if membership_request.status not in {MembershipRequest.Status.pending, MembershipRequest.Status.on_hold}:
        raise ValidationError("Only pending or on-hold requests can be ignored")

    membership_request.status = MembershipRequest.Status.ignored
    membership_request.decided_at = decided
    membership_request.decided_by_username = actor_username
    membership_request.save(update_fields=["status", "decided_at", "decided_by_username"])

    log = _create_status_change_log(
        membership_request=membership_request,
        actor_username=actor_username,
        membership_type=membership_type,
        action=MembershipLog.Action.ignored,
    )

    _try_add_note(
        membership_request=membership_request,
        username=actor_username,
        action={"type": "request_ignored"},
        log_prefix=log_prefix,
    )

    return log


@transaction.atomic
def reopen_ignored_membership_request(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
) -> MembershipLog:
    """Reopen an ignored membership request, returning it to pending.

    Transition: ignored -> pending.
    Raises ValidationError if the request is not ignored, or if another
    open request already exists for the same target + membership type.
    """
    membership_request = (
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .select_for_update(of=("self",))
        .get(pk=membership_request.pk)
    )
    if membership_request.status != MembershipRequest.Status.ignored:
        raise ValidationError("Only ignored requests can be reopened")

    membership_type = membership_request.membership_type
    log_prefix = "reopen_ignored_membership_request"

    # Check uniqueness: no conflicting open request for same target + type.
    open_statuses = {MembershipRequest.Status.pending, MembershipRequest.Status.on_hold}
    if membership_request.target_kind == MembershipRequest.TargetKind.user:
        conflict = MembershipRequest.objects.filter(
            requested_username=membership_request.requested_username,
            membership_type=membership_type,
            status__in=open_statuses,
        ).exclude(pk=membership_request.pk)
    else:
        conflict = MembershipRequest.objects.filter(
            requested_organization=membership_request.requested_organization,
            membership_type=membership_type,
            status__in=open_statuses,
        ).exclude(pk=membership_request.pk)

    if conflict.exists():
        raise ValidationError(
            "Cannot reopen: an open request already exists for this target and membership type."
        )

    membership_request.status = MembershipRequest.Status.pending
    membership_request.decided_at = None
    membership_request.decided_by_username = ""
    membership_request.on_hold_at = None
    membership_request.save(update_fields=["status", "decided_at", "decided_by_username", "on_hold_at"])

    log = _create_status_change_log(
        membership_request=membership_request,
        actor_username=actor_username,
        membership_type=membership_type,
        action=MembershipLog.Action.reopened,
    )

    _try_add_note(
        membership_request=membership_request,
        username=actor_username,
        action={"type": "request_reopened"},
        log_prefix=log_prefix,
    )

    return log


@transaction.atomic
def put_membership_request_on_hold(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    rfi_message: str,
    send_rfi_email: bool,
    application_url: str,
    held_at: datetime.datetime | None = None,
) -> tuple[MembershipLog, Exception | None]:
    """Move a pending request to on-hold and (optionally) email an RFI.

    Valid transitions:
    - pending -> on_hold
    """

    membership_request = (
        MembershipRequest.objects.select_related("membership_type", "requested_organization")
        .select_for_update(of=("self",))
        .get(pk=membership_request.pk)
    )
    membership_type = membership_request.membership_type
    now = held_at or timezone.now()
    log_prefix = "put_membership_request_on_hold"

    if membership_request.status != MembershipRequest.Status.pending:
        raise ValidationError("Only pending requests can be put on hold")

    message = str(rfi_message or "").strip()

    membership_request.status = MembershipRequest.Status.on_hold
    membership_request.on_hold_at = now
    membership_request.save(update_fields=["status", "on_hold_at"])

    target = _build_membership_target(membership_request)

    email_error: Exception | None = None
    sent_email = None
    if send_rfi_email and target.email:
        try:
            sent_email = _send_membership_request_notification(
                target=target,
                membership_type=membership_type,
                template_name=settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME,
                extra_context={
                    **freeform_message_email_context(key="rfi_message", value=message),
                    "application_url": application_url,
                },
            )
        except Exception as e:
            logger.exception(
                "%s: sending RFI email failed request_id=%s",
                log_prefix,
                membership_request.pk,
                extra=current_exception_log_fields(),
            )
            email_error = e

    log = _create_status_change_log(
        membership_request=membership_request,
        actor_username=actor_username,
        membership_type=membership_type,
        action=MembershipLog.Action.on_hold,
    )

    _try_add_note(
        membership_request=membership_request,
        username=actor_username,
        action={"type": "request_on_hold"},
        log_prefix=log_prefix,
    )
    _try_record_email_note(
        membership_request=membership_request,
        actor_username=actor_username,
        sent_email=sent_email,
        email_kind="rfi",
        log_prefix=log_prefix,
    )

    _emit_membership_request_signal_on_commit(
        membership_request=membership_request,
        actor_username=actor_username,
        user_signal=astra_signals.membership_rfi_sent,
        organization_signal=astra_signals.organization_membership_rfi_sent,
    )

    return log, email_error


@transaction.atomic
def resubmit_membership_request(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    updated_responses: list[dict[str, str]],
    resubmitted_at: datetime.datetime | None = None,
) -> MembershipLog:
    """Update a request's answers and move it from on-hold back to pending."""

    membership_request = (
        MembershipRequest.objects.select_related("membership_type")
        .select_for_update(of=("self",))
        .get(pk=membership_request.pk)
    )

    if membership_request.status != MembershipRequest.Status.on_hold:
        raise ValidationError("Only on-hold requests can be resubmitted")

    is_mirror_request = membership_request.membership_type.category_id == MembershipCategoryCode.mirror
    normalized_existing_responses = normalize_membership_request_responses(
        responses=membership_request.responses,
        is_mirror_membership=is_mirror_request,
    )
    normalized_updated_responses = normalize_membership_request_responses(
        responses=updated_responses,
        is_mirror_membership=is_mirror_request,
    )
    normalized_existing_response_list = normalized_existing_responses.as_responses()
    normalized_updated_response_list = normalized_updated_responses.as_responses()
    existing_mirror_answers = mirror_request_answers_from_responses(
        membership_type=membership_request.membership_type,
        responses=normalized_existing_response_list,
    )
    updated_mirror_answers = mirror_request_answers_from_responses(
        membership_type=membership_request.membership_type,
        responses=normalized_updated_response_list,
    )

    unchanged_responses = normalized_existing_response_list == normalized_updated_response_list
    unchanged_mirror_answers = (
        existing_mirror_answers is not None
        and updated_mirror_answers is not None
        and mirror_answers_fingerprint(existing_mirror_answers) == mirror_answers_fingerprint(updated_mirror_answers)
    )

    if unchanged_responses or unchanged_mirror_answers:
        raise ValidationError("Please update your request before resubmitting it")

    membership_request.responses = normalized_updated_response_list
    membership_request.status = MembershipRequest.Status.pending
    membership_request.on_hold_at = None
    try:
        with transaction.atomic():
            membership_request.save(update_fields=["responses", "status", "on_hold_at"])
    except IntegrityError as exc:
        raise ValidationError(
            "Cannot resubmit: a conflicting open request exists for this target and membership type."
        ) from exc

    log = _create_status_change_log(
        membership_request=membership_request,
        actor_username=actor_username,
        membership_type=membership_request.membership_type,
        action=MembershipLog.Action.resubmitted,
    )

    _try_add_note(
        membership_request=membership_request,
        username=actor_username,
        action={"type": "request_resubmitted"},
        log_prefix="resubmit_membership_request",
    )

    _emit_membership_request_signal_on_commit(
        membership_request=membership_request,
        actor_username=actor_username,
        user_signal=astra_signals.membership_rfi_replied,
        organization_signal=astra_signals.organization_membership_rfi_replied,
    )

    if membership_request.membership_type.category_id == MembershipCategoryCode.mirror and membership_request.pk is not None:
        request_id = membership_request.pk

        def _schedule_mirror_validation_on_commit() -> None:
            try:
                committed_request = MembershipRequest.objects.get(pk=request_id)
                schedule_mirror_membership_validation(membership_request=committed_request)
            except Exception:
                logger.exception(
                    "resubmit_membership_request: failed to schedule mirror validation request_id=%s",
                    request_id,
                    extra=current_exception_log_fields(),
                )

        transaction.on_commit(_schedule_mirror_validation_on_commit)

    return log


def rescind_membership_request(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    rescinded_at: datetime.datetime | None = None,
) -> MembershipLog:
    """Allow the requester to rescind a pending request."""

    now = rescinded_at or timezone.now()

    if membership_request.status not in {MembershipRequest.Status.pending, MembershipRequest.Status.on_hold}:
        raise ValidationError("Only pending or on-hold requests can be rescinded")

    membership_request.status = MembershipRequest.Status.rescinded
    membership_request.decided_at = now
    membership_request.decided_by_username = actor_username
    membership_request.save(update_fields=["status", "decided_at", "decided_by_username"])

    log = _create_status_change_log(
        membership_request=membership_request,
        actor_username=actor_username,
        membership_type=membership_request.membership_type,
        action=MembershipLog.Action.rescinded,
    )

    _try_add_note(
        membership_request=membership_request,
        username=actor_username,
        action={"type": "request_rescinded"},
        log_prefix="rescind_membership_request",
    )

    _emit_membership_request_signal_on_commit(
        membership_request=membership_request,
        actor_username=actor_username,
        user_signal=astra_signals.membership_request_rescinded,
        organization_signal=astra_signals.organization_membership_request_rescinded,
    )

    return log

import dataclasses
import datetime
import logging
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.utils import timezone
from post_office.models import EmailTemplate

from core.agreements import missing_required_agreements_for_user_in_group
from core.backends import FreeIPAUser
from core.email_context import (
    freeform_message_email_context,
    membership_committee_email_context,
    organization_sponsor_email_context,
    system_email_context,
    user_email_context_from_user,
)
from core.membership import (
    FreeIPACallerMode,
    FreeIPAGroupRemovalOutcome,
    FreeIPAMissingUserPolicy,
    remove_organization_representative_from_group_if_present,
    sync_organization_representative_groups,
)
from core.membership_notes import add_note
from core.membership_notifications import organization_sponsor_notification_recipient_email
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
        logger.exception("%s: failed to record note request_id=%s", log_prefix, membership_request.pk)


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

    return MembershipLog._create_log(
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
            )
            raise

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
        )
        raise

    if email_error is not None:
        raise email_error


def approve_membership_request(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    send_approved_email: bool,
    approved_email_template_name: str | None = None,
    decided_at: datetime.datetime | None = None,
) -> MembershipLog:
    """Approve a membership request using the same code path as the UI.

    This function applies FreeIPA side-effects and records the approval log.
    It updates the request status fields and optionally emails the requester.
    """

    membership_type = membership_request.membership_type
    decided = decided_at or timezone.now()
    log_prefix = "approve_membership_request"

    if membership_request.status != MembershipRequest.Status.pending:
        raise ValidationError("Only pending requests can be approved")

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
                category=membership_type.category,
            )
            .first()
        )

        if membership_type.group_cn and org.representative:
            try:
                representative = FreeIPAUser.get(org.representative)
            except Exception:
                logger.exception(
                    "%s: FreeIPAUser.get failed (org representative) request_id=%s org_id=%s representative=%r",
                    log_prefix,
                    membership_request.pk,
                    org.pk,
                    org.representative,
                )
                raise

            if representative is not None:
                if (
                    old_membership is not None
                    and old_membership.membership_type != membership_type
                    and old_membership.membership_type.group_cn
                ):
                    old_group_cn = str(old_membership.membership_type.group_cn or "").strip()
                    if old_group_cn:
                        old_outcome = remove_organization_representative_from_group_if_present(
                            representative_username=representative.username,
                            group_cn=old_group_cn,
                            caller_mode=FreeIPACallerMode.raise_on_error,
                            missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
                        )
                        if old_outcome == FreeIPAGroupRemovalOutcome.failed:
                            raise Exception("Failed to remove user from old group")

                try:
                    sync_organization_representative_groups(
                        old_representative="",
                        new_representative=representative.username,
                        group_cns=(membership_type.group_cn,),
                        caller_mode=FreeIPACallerMode.raise_on_error,
                        missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
                    )
                except Exception:
                    logger.exception(
                        "%s: add_to_group failed (org representative) request_id=%s org_id=%s representative=%r group_cn=%r",
                        log_prefix,
                        membership_request.pk,
                        org.pk,
                        representative.username,
                        membership_type.group_cn,
                    )
                    raise

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
        try:
            user.add_to_group(group_name=membership_type.group_cn)
        except Exception:
            logger.exception(
                "%s: add_to_group failed request_id=%s target=%r group_cn=%r",
                log_prefix,
                membership_request.pk,
                user.username,
                membership_type.group_cn,
            )
            raise

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
    membership_request.decided_at = decided
    membership_request.decided_by_username = actor_username
    membership_request.save(update_fields=["status", "decided_at", "decided_by_username"])

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
    _try_record_email_note(
        membership_request=membership_request,
        actor_username=actor_username,
        sent_email=sent_email,
        email_kind="approved",
        log_prefix=log_prefix,
    )

    return log


def reject_membership_request(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    rejection_reason: str,
    send_rejected_email: bool,
    decided_at: datetime.datetime | None = None,
) -> tuple[MembershipLog, Exception | None]:
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
    return log, email_error


def ignore_membership_request(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    decided_at: datetime.datetime | None = None,
) -> MembershipLog:
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

    return log, email_error


def resubmit_membership_request(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    updated_responses: list[dict[str, str]],
    resubmitted_at: datetime.datetime | None = None,
) -> MembershipLog:
    """Update a request's answers and move it from on-hold back to pending."""

    if membership_request.status != MembershipRequest.Status.on_hold:
        raise ValidationError("Only on-hold requests can be resubmitted")

    def _normalized_responses(responses: list[dict[str, str]] | None) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for item in responses or []:
            if not isinstance(item, dict):
                continue
            for question, answer in item.items():
                question_s = str(question or "").strip()
                answer_s = str(answer or "").strip()
                if question_s and answer_s:
                    out.append({question_s: answer_s})
        return out

    if _normalized_responses(membership_request.responses) == _normalized_responses(updated_responses):
        raise ValidationError("Please update your request before resubmitting it")

    membership_request.responses = updated_responses
    membership_request.status = MembershipRequest.Status.pending
    membership_request.on_hold_at = None
    membership_request.save(update_fields=["responses", "status", "on_hold_at"])

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

    return log

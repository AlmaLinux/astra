from __future__ import annotations

import datetime
import logging

import post_office.mail
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from post_office.models import EmailTemplate

from core.backends import FreeIPAUser
from core.email_context import (
    freeform_message_email_context,
    membership_committee_email_context,
    organization_sponsor_email_context,
    user_email_context_from_user,
)
from core.membership_notes import add_note
from core.models import (
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    Organization,
    OrganizationSponsorship,
)

logger = logging.getLogger(__name__)


def _ensure_configured_email_template_exists(*, template_name: str) -> None:
    name = str(template_name or "").strip()
    if not name:
        raise ValidationError("Configured email template name is empty")

    if not EmailTemplate.objects.filter(name=name).exists():
        raise ValidationError(
            f"Configured email template {name!r} was not found. "
            "Please recreate it (or update the relevant setting / membership type template)."
        )


def _organization_notification_email(organization: Organization) -> str:
    """Preferred recipient for org notifications.

    Prefer the organization's representative when it resolves to a
    FreeIPA user with an email address; otherwise fall back to the organization's
    primary contact email.
    """

    representative_username = organization.representative
    if representative_username:
        representative = FreeIPAUser.get(representative_username)
        if representative is not None and representative.email:
            return representative.email

    return organization.primary_contact_email()


def previous_expires_at_for_extension(*, username: str, membership_type: MembershipType) -> datetime.datetime | None:
    """Return the expiry for an active membership if it can be extended.

    Only extend active memberships; if the current row is missing or already
    expired, return None so approval starts a new term.
    """

    logger.debug(
        "previous_expires_at_for_extension: start username=%r membership_type=%s",
        username,
        membership_type.code,
    )

    current = Membership.objects.filter(target_username=username, membership_type=membership_type).first()
    if current is None or current.expires_at is None:
        logger.debug(
            "previous_expires_at_for_extension: no current membership (or expires_at missing) username=%r membership_type=%s",
            username,
            membership_type.code,
        )
        return None

    now = timezone.now()
    if current.expires_at <= now:
        logger.debug(
            "previous_expires_at_for_extension: expired username=%r membership_type=%s expires_at=%s now=%s",
            username,
            membership_type.code,
            current.expires_at,
            now,
        )
        return None

    logger.debug(
        "previous_expires_at_for_extension: extendable username=%r membership_type=%s expires_at=%s now=%s",
        username,
        membership_type.code,
        current.expires_at,
        now,
    )
    return current.expires_at


def previous_expires_at_for_org_extension(*, organization_id: int) -> datetime.datetime | None:
    logger.debug(
        "previous_expires_at_for_org_extension: start organization_id=%s",
        organization_id,
    )

    current = OrganizationSponsorship.objects.filter(organization_id=organization_id).first()
    if current is None or current.expires_at is None:
        logger.debug(
            "previous_expires_at_for_org_extension: no current sponsorship (or expires_at missing) organization_id=%s",
            organization_id,
        )
        return None

    now = timezone.now()
    if current.expires_at <= now:
        logger.debug(
            "previous_expires_at_for_org_extension: expired organization_id=%s expires_at=%s now=%s",
            organization_id,
            current.expires_at,
            now,
        )
        return None

    logger.debug(
        "previous_expires_at_for_org_extension: extendable organization_id=%s expires_at=%s now=%s",
        organization_id,
        current.expires_at,
        now,
    )
    return current.expires_at


def record_membership_request_created(
    *,
    membership_request: MembershipRequest,
    actor_username: str,
    send_submitted_email: bool,
) -> None:
    """Record the initial request audit log and optionally email the requester."""

    membership_type = membership_request.membership_type

    logger.debug(
        "record_membership_request_created: start request_id=%s actor=%r membership_type=%s requested_username=%r requested_org_id=%s send_submitted_email=%s",
        membership_request.pk,
        actor_username,
        membership_type.code,
        membership_request.requested_username,
        membership_request.requested_organization_id,
        send_submitted_email,
    )

    try:
        add_note(
            membership_request=membership_request,
            username=actor_username,
            action={"type": "request_created"},
        )
    except Exception:
        # Notes are an additional durable timeline for committee review. Avoid
        # failing the entire request flow if note persistence fails.
        logger.exception(
            "record_membership_request_created: failed to record action note request_id=%s",
            membership_request.pk,
        )

    if membership_request.requested_username:
        email_error: Exception | None = None

        if send_submitted_email:
            logger.debug(
                "record_membership_request_created: sending submitted email request_id=%s target=%r membership_type=%s",
                membership_request.pk,
                membership_request.requested_username,
                membership_type.code,
            )
            try:
                target = FreeIPAUser.get(membership_request.requested_username)
            except Exception as e:
                logger.exception(
                    "record_membership_request_created: FreeIPAUser.get failed for submitted email request_id=%s target=%r",
                    membership_request.pk,
                    membership_request.requested_username,
                )
                email_error = e
            else:
                if target is not None and target.email:
                    try:
                        post_office.mail.send(
                            recipients=[target.email],
                            sender=settings.DEFAULT_FROM_EMAIL,
                            template=settings.MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME,
                            context={
                                **user_email_context_from_user(user=target),
                                **membership_committee_email_context(),
                                "membership_type": membership_type.name,
                                "membership_type_code": membership_type.code,
                            },
                            headers={"Reply-To": settings.MEMBERSHIP_COMMITTEE_EMAIL},
                        )
                    except Exception as e:
                        logger.exception(
                            "record_membership_request_created: sending submitted email failed request_id=%s target=%r",
                            membership_request.pk,
                            membership_request.requested_username,
                        )
                        email_error = e
                else:
                    logger.debug(
                        "record_membership_request_created: submitted email skipped (missing email) request_id=%s target=%r",
                        membership_request.pk,
                        membership_request.requested_username,
                    )

        try:
            log = MembershipLog.create_for_request(
                actor_username=actor_username,
                target_username=membership_request.requested_username,
                membership_type=membership_type,
                membership_request=membership_request,
            )
        except Exception:
            logger.exception(
                "record_membership_request_created: failed to create requested log (user) request_id=%s actor=%r target=%r membership_type=%s",
                membership_request.pk,
                actor_username,
                membership_request.requested_username,
                membership_type.code,
            )
            raise

        logger.debug(
            "record_membership_request_created: created requested log log_id=%s request_id=%s target=%r membership_type=%s",
            log.pk,
            membership_request.pk,
            membership_request.requested_username,
            membership_type.code,
        )

        logger.debug(
            "record_membership_request_created: done request_id=%s",
            membership_request.pk,
        )

        if email_error is not None:
            raise email_error
        return

    # Organization request.
    org = membership_request.requested_organization
    if org is not None:
        try:
            log = MembershipLog.create_for_org_request(
                actor_username=actor_username,
                target_organization=org,
                membership_type=membership_type,
                membership_request=membership_request,
            )
        except Exception:
            logger.exception(
                "record_membership_request_created: failed to create requested log (org) request_id=%s actor=%r org_id=%s membership_type=%s",
                membership_request.pk,
                actor_username,
                org.pk,
                membership_type.code,
            )
            raise

        logger.debug(
            "record_membership_request_created: created requested log (org) log_id=%s request_id=%s org_id=%s membership_type=%s",
            log.pk,
            membership_request.pk,
            org.pk,
            membership_type.code,
        )
        logger.debug(
            "record_membership_request_created: done request_id=%s",
            membership_request.pk,
        )
        return

    # A request can point to a non-existent org via requested_organization_code.
    try:
        log = MembershipLog.objects.create(
            actor_username=actor_username,
            target_username="",
            target_organization=None,
            target_organization_code=membership_request.requested_organization_code,
            target_organization_name=membership_request.requested_organization_name,
            membership_type=membership_type,
            membership_request=membership_request,
            requested_group_cn=membership_type.group_cn,
            action=MembershipLog.Action.requested,
            expires_at=None,
        )
    except Exception:
        logger.exception(
            "record_membership_request_created: failed to create requested log (org code) request_id=%s actor=%r org_code=%r membership_type=%s",
            membership_request.pk,
            actor_username,
            membership_request.requested_organization_code,
            membership_type.code,
        )
        raise

    logger.debug(
        "record_membership_request_created: created requested log (org code) log_id=%s request_id=%s org_code=%r membership_type=%s",
        log.pk,
        membership_request.pk,
        membership_request.requested_organization_code,
        membership_type.code,
    )
    logger.debug(
        "record_membership_request_created: done request_id=%s",
        membership_request.pk,
    )


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

    logger.debug(
        "approve_membership_request: start request_id=%s actor=%r membership_type=%s requested_username=%r requested_org_id=%s decided_at=%s send_approved_email=%s",
        membership_request.pk,
        actor_username,
        membership_type.code,
        membership_request.requested_username,
        membership_request.requested_organization_id,
        decided,
        send_approved_email,
    )

    if membership_request.status != MembershipRequest.Status.pending:
        raise ValidationError("Only pending requests can be approved")

    if membership_request.requested_username == "":
        org = membership_request.requested_organization
        if org is None:
            logger.debug(
                "approve_membership_request: org not found request_id=%s org_code=%r",
                membership_request.pk,
                membership_request.requested_organization_code,
            )
            raise ValidationError("Organization not found")

        logger.debug(
            "approve_membership_request: approving org request request_id=%s org_id=%s membership_type=%s",
            membership_request.pk,
            org.pk,
            membership_type.code,
        )

        org_email = _organization_notification_email(org)
        if send_approved_email and org_email:
            template_name = settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME
            if membership_type.acceptance_template_id is not None:
                template_name = membership_type.acceptance_template.name
            if approved_email_template_name:
                template_name = approved_email_template_name
            _ensure_configured_email_template_exists(template_name=template_name)

        # This must be captured before any membership/sponsorship updates.
        previous_expires_at = previous_expires_at_for_org_extension(organization_id=org.pk)

        if membership_type.group_cn and org.representative:
            try:
                representative = FreeIPAUser.get(org.representative)
            except Exception:
                logger.exception(
                    "approve_membership_request: FreeIPAUser.get failed (org representative) request_id=%s org_id=%s representative=%r",
                    membership_request.pk,
                    org.pk,
                    org.representative,
                )
                raise

            if representative is None:
                logger.debug(
                    "approve_membership_request: representative not found (org) request_id=%s org_id=%s representative=%r",
                    membership_request.pk,
                    org.pk,
                    org.representative,
                )
            else:
                logger.debug(
                    "approve_membership_request: add_to_group start (org representative) request_id=%s org_id=%s representative=%r group_cn=%r",
                    membership_request.pk,
                    org.pk,
                    representative.username,
                    membership_type.group_cn,
                )

                try:
                    representative.add_to_group(group_name=membership_type.group_cn)
                except Exception:
                    logger.exception(
                        "approve_membership_request: add_to_group failed (org representative) request_id=%s org_id=%s representative=%r group_cn=%r",
                        membership_request.pk,
                        org.pk,
                        representative.username,
                        membership_type.group_cn,
                    )
                    raise

                logger.debug(
                    "approve_membership_request: add_to_group success (org representative) request_id=%s org_id=%s representative=%r group_cn=%r",
                    membership_request.pk,
                    org.pk,
                    representative.username,
                    membership_type.group_cn,
                )

        org.membership_level = membership_type
        try:
            org.save(update_fields=["membership_level"])
        except Exception:
            logger.exception(
                "approve_membership_request: failed to save org membership_level request_id=%s org_id=%s",
                membership_request.pk,
                org.pk,
            )
            raise

        membership_request.status = MembershipRequest.Status.approved
        membership_request.decided_at = decided
        membership_request.decided_by_username = actor_username
        try:
            membership_request.save(update_fields=["status", "decided_at", "decided_by_username"])
        except Exception:
            logger.exception(
                "approve_membership_request: failed to update request status (org) request_id=%s org_id=%s",
                membership_request.pk,
                org.pk,
            )
            raise

        if send_approved_email and org_email:
            template_name = settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME
            if membership_type.acceptance_template_id is not None:
                template_name = membership_type.acceptance_template.name
            if approved_email_template_name:
                template_name = approved_email_template_name

            logger.debug(
                "approve_membership_request: sending approved email (org) request_id=%s org_id=%s membership_type=%s",
                membership_request.pk,
                org.pk,
                membership_type.code,
            )
            try:
                post_office.mail.send(
                    recipients=[org_email],
                    sender=settings.DEFAULT_FROM_EMAIL,
                    template=template_name,
                    context={
                        **organization_sponsor_email_context(organization=org),
                        **membership_committee_email_context(),
                        "organization_name": org.name,
                        "membership_type": membership_type.name,
                        "membership_type_code": membership_type.code,
                    },
                    headers={"Reply-To": settings.MEMBERSHIP_COMMITTEE_EMAIL},
                )
            except Exception:
                logger.exception(
                    "approve_membership_request: sending approved email failed (org) request_id=%s org_id=%s",
                    membership_request.pk,
                    org.pk,
                )
                raise
        else:
            logger.debug(
                "approve_membership_request: approved email skipped (org) request_id=%s send_approved_email=%s has_email=%s",
                membership_request.pk,
                send_approved_email,
                bool(org_email),
            )

        # Audit log must be the last step.
        try:
            log = MembershipLog.create_for_org_approval_at(
                actor_username=actor_username,
                target_organization=org,
                membership_type=membership_type,
                approved_at=decided,
                previous_expires_at=previous_expires_at,
                membership_request=membership_request,
            )
        except Exception:
            logger.exception(
                "approve_membership_request: failed to create org approval log request_id=%s org_id=%s membership_type=%s",
                membership_request.pk,
                org.pk,
                membership_type.code,
            )
            raise

        logger.debug(
            "approve_membership_request: created org approval log log_id=%s request_id=%s org_id=%s membership_type=%s",
            log.pk,
            membership_request.pk,
            org.pk,
            membership_type.code,
        )

        logger.debug(
            "approve_membership_request: done (org) request_id=%s log_id=%s",
            membership_request.pk,
            log.pk,
        )

        try:
            add_note(
                membership_request=membership_request,
                username=actor_username,
                action={"type": "request_approved"},
            )
        except Exception:
            logger.exception(
                "approve_membership_request: failed to record action note (org) request_id=%s",
                membership_request.pk,
            )
        return log

    if not membership_type.group_cn:
        logger.debug(
            "approve_membership_request: missing group_cn request_id=%s membership_type=%s",
            membership_request.pk,
            membership_type.code,
        )
        raise ValidationError("This membership type is not linked to a group")

    logger.debug(
        "approve_membership_request: approving user request request_id=%s target=%r group_cn=%r membership_type=%s",
        membership_request.pk,
        membership_request.requested_username,
        membership_type.group_cn,
        membership_type.code,
    )

    try:
        target = FreeIPAUser.get(membership_request.requested_username)
    except Exception:
        logger.exception(
            "approve_membership_request: FreeIPAUser.get failed request_id=%s target=%r",
            membership_request.pk,
            membership_request.requested_username,
        )
        raise
    if target is None:
        logger.debug(
            "approve_membership_request: requested user not found request_id=%s target=%r",
            membership_request.pk,
            membership_request.requested_username,
        )
        raise ValidationError("Unable to load the requested user from FreeIPA")

    if send_approved_email and target.email:
        template_name = settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME
        if membership_type.acceptance_template_id is not None:
            template_name = membership_type.acceptance_template.name
        if approved_email_template_name:
            template_name = approved_email_template_name
        _ensure_configured_email_template_exists(template_name=template_name)

    logger.debug(
        "approve_membership_request: add_to_group start request_id=%s target=%r group_cn=%r",
        membership_request.pk,
        target.username,
        membership_type.group_cn,
    )

    # This must be captured before adding the user to the group.
    previous_expires_at = previous_expires_at_for_extension(
        username=membership_request.requested_username,
        membership_type=membership_type,
    )
    try:
        target.add_to_group(group_name=membership_type.group_cn)
    except Exception:
        logger.exception(
            "approve_membership_request: add_to_group failed request_id=%s target=%r group_cn=%r",
            membership_request.pk,
            target.username,
            membership_type.group_cn,
        )
        raise

    logger.debug(
        "approve_membership_request: add_to_group success request_id=%s target=%r group_cn=%r",
        membership_request.pk,
        target.username,
        membership_type.group_cn,
    )

    membership_request.status = MembershipRequest.Status.approved
    membership_request.decided_at = decided
    membership_request.decided_by_username = actor_username
    try:
        membership_request.save(update_fields=["status", "decided_at", "decided_by_username"])
    except Exception:
        logger.exception(
            "approve_membership_request: failed to update request status (user) request_id=%s target=%r",
            membership_request.pk,
            membership_request.requested_username,
        )
        raise

    if send_approved_email and target.email:
        template_name = settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME
        if membership_type.acceptance_template_id is not None:
            template_name = membership_type.acceptance_template.name
        if approved_email_template_name:
            template_name = approved_email_template_name

        logger.debug(
            "approve_membership_request: sending approved email request_id=%s target=%r membership_type=%s",
            membership_request.pk,
            target.username,
            membership_type.code,
        )
        try:
            post_office.mail.send(
                recipients=[target.email],
                sender=settings.DEFAULT_FROM_EMAIL,
                template=template_name,
                context={
                    **user_email_context_from_user(user=target),
                    **membership_committee_email_context(),
                    "membership_type": membership_type.name,
                    "membership_type_code": membership_type.code,
                    "group_cn": membership_type.group_cn,
                },
                headers={"Reply-To": settings.MEMBERSHIP_COMMITTEE_EMAIL},
            )
        except Exception:
            logger.exception(
                "approve_membership_request: sending approved email failed request_id=%s target=%r",
                membership_request.pk,
                target.username,
            )
            raise
    else:
        logger.debug(
            "approve_membership_request: approved email skipped request_id=%s send_approved_email=%s has_email=%s",
            membership_request.pk,
            send_approved_email,
            bool(target.email),
        )

    # Audit log must be the last step.
    try:
        log = MembershipLog.create_for_approval_at(
            actor_username=actor_username,
            target_username=membership_request.requested_username,
            membership_type=membership_type,
            approved_at=decided,
            previous_expires_at=previous_expires_at,
            membership_request=membership_request,
        )
    except Exception:
        logger.exception(
            "approve_membership_request: failed to create approval log request_id=%s target=%r membership_type=%s",
            membership_request.pk,
            membership_request.requested_username,
            membership_type.code,
        )
        raise

    logger.debug(
        "approve_membership_request: created approval log log_id=%s request_id=%s target=%r membership_type=%s",
        log.pk,
        membership_request.pk,
        membership_request.requested_username,
        membership_type.code,
    )

    logger.debug(
        "approve_membership_request: done (user) request_id=%s log_id=%s",
        membership_request.pk,
        log.pk,
    )

    try:
        add_note(
            membership_request=membership_request,
            username=actor_username,
            action={"type": "request_approved"},
        )
    except Exception:
        logger.exception(
            "approve_membership_request: failed to record action note (user) request_id=%s",
            membership_request.pk,
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

    email_error: Exception | None = None

    if membership_request.requested_username == "":
        org = membership_request.requested_organization

        membership_request.status = MembershipRequest.Status.rejected
        membership_request.decided_at = decided
        membership_request.decided_by_username = actor_username
        membership_request.save(update_fields=["responses", "status", "decided_at", "decided_by_username"])

        org_email = _organization_notification_email(org) if org is not None else ""
        if send_rejected_email and org_email:
            try:
                post_office.mail.send(
                    recipients=[org_email],
                    sender=settings.DEFAULT_FROM_EMAIL,
                    template=settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME,
                    context={
                        **(organization_sponsor_email_context(organization=org) if org is not None else {}),
                        **membership_committee_email_context(),
                        **freeform_message_email_context(key="rejection_reason", value=reason),
                        "organization_name": org.name if org is not None else (membership_request.requested_organization_name or ""),
                        "membership_type": membership_type.name,
                        "membership_type_code": membership_type.code,
                    },
                    headers={"Reply-To": settings.MEMBERSHIP_COMMITTEE_EMAIL},
                )
            except Exception as e:
                logger.exception(
                    "reject_membership_request: sending rejected email failed (org) request_id=%s org_id=%s",
                    membership_request.pk,
                    org.pk if org is not None else None,
                )
                email_error = e

        if org is not None:
            log = MembershipLog.create_for_org_rejection(
                actor_username=actor_username,
                target_organization=org,
                membership_type=membership_type,
                rejection_reason=reason,
                membership_request=membership_request,
            )
        else:
            log = MembershipLog.objects.create(
                actor_username=actor_username,
                target_username="",
                target_organization=None,
                target_organization_code=membership_request.requested_organization_code,
                target_organization_name=membership_request.requested_organization_name,
                membership_type=membership_type,
                membership_request=membership_request,
                requested_group_cn=membership_type.group_cn,
                action=MembershipLog.Action.rejected,
                rejection_reason=reason,
                expires_at=None,
            )

        try:
            add_note(
                membership_request=membership_request,
                username=actor_username,
                action={"type": "request_rejected"},
            )
        except Exception:
            logger.exception(
                "reject_membership_request: failed to record action note (org) request_id=%s",
                membership_request.pk,
            )

        return log, email_error

    target = FreeIPAUser.get(membership_request.requested_username)

    membership_request.status = MembershipRequest.Status.rejected
    membership_request.decided_at = decided
    membership_request.decided_by_username = actor_username
    membership_request.save(update_fields=["responses", "status", "decided_at", "decided_by_username"])

    if send_rejected_email and target is not None and target.email:
        try:
            post_office.mail.send(
                recipients=[target.email],
                sender=settings.DEFAULT_FROM_EMAIL,
                template=settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME,
                context={
                    **user_email_context_from_user(user=target),
                    **freeform_message_email_context(key="rejection_reason", value=reason),
                    **membership_committee_email_context(),
                    "membership_type": membership_type.name,
                    "membership_type_code": membership_type.code,
                },
                headers={"Reply-To": settings.MEMBERSHIP_COMMITTEE_EMAIL},
            )
        except Exception as e:
            logger.exception(
                "reject_membership_request: sending rejected email failed (user) request_id=%s target=%r",
                membership_request.pk,
                membership_request.requested_username,
            )
            email_error = e

    log = MembershipLog.create_for_rejection(
        actor_username=actor_username,
        target_username=membership_request.requested_username,
        membership_type=membership_type,
        rejection_reason=reason,
        membership_request=membership_request,
    )

    try:
        add_note(
            membership_request=membership_request,
            username=actor_username,
            action={"type": "request_rejected"},
        )
    except Exception:
        logger.exception(
            "reject_membership_request: failed to record action note (user) request_id=%s",
            membership_request.pk,
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

    if membership_request.status not in {MembershipRequest.Status.pending, MembershipRequest.Status.on_hold}:
        raise ValidationError("Only pending or on-hold requests can be ignored")

    membership_request.status = MembershipRequest.Status.ignored
    membership_request.decided_at = decided
    membership_request.decided_by_username = actor_username
    membership_request.save(update_fields=["status", "decided_at", "decided_by_username"])

    if membership_request.requested_username == "":
        org = membership_request.requested_organization
        if org is not None:
            log = MembershipLog.create_for_org_ignore(
                actor_username=actor_username,
                target_organization=org,
                membership_type=membership_type,
                membership_request=membership_request,
            )

            try:
                add_note(
                    membership_request=membership_request,
                    username=actor_username,
                    action={"type": "request_ignored"},
                )
            except Exception:
                logger.exception(
                    "ignore_membership_request: failed to record action note (org) request_id=%s",
                    membership_request.pk,
                )

            return log

        log = MembershipLog.objects.create(
            actor_username=actor_username,
            target_username="",
            target_organization=None,
            target_organization_code=membership_request.requested_organization_code,
            target_organization_name=membership_request.requested_organization_name,
            membership_type=membership_type,
            membership_request=membership_request,
            requested_group_cn=membership_type.group_cn,
            action=MembershipLog.Action.ignored,
            expires_at=None,
        )

        try:
            add_note(
                membership_request=membership_request,
                username=actor_username,
                action={"type": "request_ignored"},
            )
        except Exception:
            logger.exception(
                "ignore_membership_request: failed to record action note (org code) request_id=%s",
                membership_request.pk,
            )

        return log

    log = MembershipLog.create_for_ignore(
        actor_username=actor_username,
        target_username=membership_request.requested_username,
        membership_type=membership_type,
        membership_request=membership_request,
    )

    try:
        add_note(
            membership_request=membership_request,
            username=actor_username,
            action={"type": "request_ignored"},
        )
    except Exception:
        logger.exception(
            "ignore_membership_request: failed to record action note (user) request_id=%s",
            membership_request.pk,
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

    if membership_request.status != MembershipRequest.Status.pending:
        raise ValidationError("Only pending requests can be put on hold")

    message = str(rfi_message or "").strip()

    membership_request.status = MembershipRequest.Status.on_hold
    membership_request.on_hold_at = now
    membership_request.save(update_fields=["status", "on_hold_at"])

    email_error: Exception | None = None

    if membership_request.requested_username == "":
        org = membership_request.requested_organization
        representative_username = org.representative if org is not None else ""
        representative = FreeIPAUser.get(representative_username) if representative_username else None

        recipient_email = ""
        user_context: dict[str, str] = {}
        if representative is not None and representative.email:
            recipient_email = representative.email
            user_context = user_email_context_from_user(user=representative)
        elif org is not None:
            recipient_email = org.primary_contact_email()

        if send_rfi_email and recipient_email:
            try:
                post_office.mail.send(
                    recipients=[recipient_email],
                    sender=settings.DEFAULT_FROM_EMAIL,
                    template=settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME,
                    context={
                        **user_context,
                        **(organization_sponsor_email_context(organization=org) if org is not None else {}),
                        **freeform_message_email_context(key="rfi_message", value=message),
                        **membership_committee_email_context(),
                        "organization_name": org.name if org is not None else (membership_request.requested_organization_name or ""),
                        "membership_type": membership_type.name,
                        "membership_type_code": membership_type.code,
                        "application_url": application_url,
                    },
                    headers={"Reply-To": settings.MEMBERSHIP_COMMITTEE_EMAIL},
                )
            except Exception as e:
                logger.exception(
                    "put_membership_request_on_hold: sending RFI email failed (org) request_id=%s org_id=%s rep=%r",
                    membership_request.pk,
                    org.pk if org is not None else None,
                    representative_username,
                )
                email_error = e

        log = MembershipLog.objects.create(
            actor_username=actor_username,
            target_username="",
            target_organization=org,
            target_organization_code=str(org.pk) if org is not None else (membership_request.requested_organization_code or ""),
            target_organization_name=org.name if org is not None else (membership_request.requested_organization_name or ""),
            membership_type=membership_type,
            membership_request=membership_request,
            requested_group_cn=membership_type.group_cn,
            action=MembershipLog.Action.on_hold,
            expires_at=None,
        )
    else:
        target = FreeIPAUser.get(membership_request.requested_username)
        if send_rfi_email and target is not None and target.email:
            try:
                post_office.mail.send(
                    recipients=[target.email],
                    sender=settings.DEFAULT_FROM_EMAIL,
                    template=settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME,
                    context={
                        **user_email_context_from_user(user=target),
                        **freeform_message_email_context(key="rfi_message", value=message),
                        **membership_committee_email_context(),
                        "membership_type": membership_type.name,
                        "membership_type_code": membership_type.code,
                        "application_url": application_url,
                    },
                    headers={"Reply-To": settings.MEMBERSHIP_COMMITTEE_EMAIL},
                )
            except Exception as e:
                logger.exception(
                    "put_membership_request_on_hold: sending RFI email failed (user) request_id=%s target=%r",
                    membership_request.pk,
                    membership_request.requested_username,
                )
                email_error = e

        log = MembershipLog.objects.create(
            actor_username=actor_username,
            target_username=membership_request.requested_username,
            target_organization=None,
            target_organization_code="",
            target_organization_name="",
            membership_type=membership_type,
            membership_request=membership_request,
            requested_group_cn=membership_type.group_cn,
            action=MembershipLog.Action.on_hold,
        )

    try:
        add_note(
            membership_request=membership_request,
            username=actor_username,
            content=message,
            action={"type": "request_on_hold"},
        )
    except Exception:
        logger.exception(
            "put_membership_request_on_hold: failed to record action note request_id=%s",
            membership_request.pk,
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

    membership_type = membership_request.membership_type
    if membership_request.requested_username == "":
        org = membership_request.requested_organization
        log = MembershipLog.objects.create(
            actor_username=actor_username,
            target_username="",
            target_organization=org,
            target_organization_code=str(org.pk) if org is not None else (membership_request.requested_organization_code or ""),
            target_organization_name=org.name if org is not None else (membership_request.requested_organization_name or ""),
            membership_type=membership_type,
            membership_request=membership_request,
            requested_group_cn=membership_type.group_cn,
            action=MembershipLog.Action.resubmitted,
            expires_at=None,
        )
    else:
        log = MembershipLog.objects.create(
            actor_username=actor_username,
            target_username=membership_request.requested_username,
            target_organization=None,
            target_organization_code="",
            target_organization_name="",
            membership_type=membership_type,
            membership_request=membership_request,
            requested_group_cn=membership_type.group_cn,
            action=MembershipLog.Action.resubmitted,
        )

    try:
        add_note(
            membership_request=membership_request,
            username=actor_username,
            action={"type": "request_resubmitted"},
        )
    except Exception:
        logger.exception(
            "resubmit_membership_request: failed to record action note request_id=%s",
            membership_request.pk,
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

    membership_type = membership_request.membership_type
    if membership_request.requested_username == "":
        org = membership_request.requested_organization
        log = MembershipLog.objects.create(
            actor_username=actor_username,
            target_username="",
            target_organization=org,
            target_organization_code=str(org.pk) if org is not None else (membership_request.requested_organization_code or ""),
            target_organization_name=org.name if org is not None else (membership_request.requested_organization_name or ""),
            membership_type=membership_type,
            membership_request=membership_request,
            requested_group_cn=membership_type.group_cn,
            action=MembershipLog.Action.rescinded,
            expires_at=None,
        )
    else:
        log = MembershipLog.objects.create(
            actor_username=actor_username,
            target_username=membership_request.requested_username,
            target_organization=None,
            target_organization_code="",
            target_organization_name="",
            membership_type=membership_type,
            membership_request=membership_request,
            requested_group_cn=membership_type.group_cn,
            action=MembershipLog.Action.rescinded,
        )

    try:
        add_note(
            membership_request=membership_request,
            username=actor_username,
            action={"type": "request_rescinded"},
        )
    except Exception:
        logger.exception(
            "rescind_membership_request: failed to record action note request_id=%s",
            membership_request.pk,
        )

    return log

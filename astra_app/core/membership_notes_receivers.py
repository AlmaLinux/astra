"""Signal receivers that write MembershipRequest notes for profile-change events."""

import logging

from core import signals as astra_signals
from core.country_codes import (
    country_label_from_code,
    embargoed_country_match_from_country_code,
    embargoed_country_match_from_user_data,
)
from core.freeipa.user import FreeIPAUser
from core.membership_notes import CUSTOS, add_note
from core.models import MembershipRequest, Organization
from core.signal_receivers import safe_receiver

logger = logging.getLogger(__name__)

_CONNECTED: bool = False


def record_country_change_notes_for_pending_membership_requests(
    sender: object,
    *,
    username: str,
    old_country: str,
    new_country: str,
    actor: str,
    **kwargs: object,
) -> None:
    _ = (sender, actor, kwargs)

    if not (
        isinstance(username, str)
        and username
        and isinstance(old_country, str)
        and old_country
        and isinstance(new_country, str)
        and new_country
    ):
        return
    if old_country == new_country:
        return

    pending = MembershipRequest.objects.filter(
        requested_username=username,
        status=MembershipRequest.Status.pending,
    ).only("pk")

    for membership_request in pending:
        try:
            add_note(
                membership_request=membership_request,
                username=CUSTOS,
                content=(
                    f"{username} updated their country from {country_label_from_code(old_country)} "
                    f"to {country_label_from_code(new_country)}."
                ),
            )
        except Exception:
            logger.exception(
                "membership_request.note.error",
                extra={
                    "event_key": "user_country_changed",
                    "request_id": membership_request.pk,
                    "username": username,
                },
            )


def record_embargoed_country_note_for_user_submission(
    sender: object,
    *,
    membership_request: MembershipRequest,
    actor: str,
    **kwargs: object,
) -> None:
    _ = (sender, actor, kwargs)

    requested_username = membership_request.requested_username
    if not (isinstance(requested_username, str) and requested_username):
        return

    try:
        freeipa_user = FreeIPAUser.get(requested_username)
    except Exception:
        logger.exception(
            "membership_request.note.error",
            extra={
                "event_key": "membership_request_submitted",
                "request_id": membership_request.pk,
                "username": requested_username,
            },
        )
        return

    if freeipa_user is None:
        return

    embargoed_match = embargoed_country_match_from_user_data(user_data=freeipa_user._user_data)
    if embargoed_match is None:
        return

    try:
        add_note(
            membership_request=membership_request,
            username=CUSTOS,
            content=(
                "This user's declared country, "
                f"{embargoed_match.label}, is on the list of embargoed countries."
            ),
        )
    except Exception:
        logger.exception(
            "membership_request.note.error",
            extra={
                "event_key": "membership_request_submitted",
                "request_id": membership_request.pk,
                "username": requested_username,
            },
        )


def record_embargoed_country_note_for_org_submission(
    sender: object,
    *,
    membership_request: MembershipRequest,
    actor: str,
    organization_id: int | None,
    organization_display_name: str,
    **kwargs: object,
) -> None:
    _ = (sender, actor, organization_display_name, kwargs)

    organization_pk = organization_id
    if organization_pk is None:
        organization_pk = membership_request.requested_organization_id
    if organization_pk is None:
        logger.debug(
            "membership_request.organization.missing_id",
            extra={
                "event_key": "organization_membership_request_submitted",
                "request_id": membership_request.pk,
            },
        )
        return

    try:
        organization = Organization.objects.get(pk=organization_pk)
    except Organization.DoesNotExist:
        logger.warning(
            "membership_request.organization.not_found",
            extra={
                "event_key": "organization_membership_request_submitted",
                "request_id": membership_request.pk,
                "organization_id": organization_pk,
            },
        )
        return
    except Exception:
        logger.exception(
            "membership_request.note.error",
            extra={
                "event_key": "organization_membership_request_submitted",
                "request_id": membership_request.pk,
                "organization_id": organization_pk,
            },
        )
        return

    try:
        embargoed_org_country = embargoed_country_match_from_country_code(organization.country_code)
        if embargoed_org_country is not None:
            try:
                add_note(
                    membership_request=membership_request,
                    username=CUSTOS,
                    content=(
                        "This organization's declared country, "
                        f"{embargoed_org_country.label}, is on the list of embargoed countries."
                    ),
                )
            except Exception:
                logger.exception(
                    "membership_request.note.error",
                    extra={
                        "event_key": "organization_membership_request_submitted",
                        "request_id": membership_request.pk,
                        "organization_id": organization.pk,
                    },
                )
    except Exception:
        logger.exception(
            "membership_request.note.error",
            extra={
                "event_key": "organization_membership_request_submitted",
                "request_id": membership_request.pk,
                "organization_id": organization.pk,
            },
        )

    try:
        representative_username = organization.representative
        if not (isinstance(representative_username, str) and representative_username):
            return

        try:
            representative_user = FreeIPAUser.get(representative_username)
        except Exception:
            logger.exception(
                "membership_request.note.error",
                extra={
                    "event_key": "organization_membership_request_submitted",
                    "request_id": membership_request.pk,
                    "organization_id": organization.pk,
                    "username": representative_username,
                },
            )
            return

        if representative_user is None:
            return

        embargoed_rep_country = embargoed_country_match_from_user_data(
            user_data=representative_user._user_data,
        )
        if embargoed_rep_country is None:
            return

        try:
            add_note(
                membership_request=membership_request,
                username=CUSTOS,
                content=(
                    "This organization's representative's declared country, "
                    f"{embargoed_rep_country.label}, is on the list of embargoed countries."
                ),
            )
        except Exception:
            logger.exception(
                "membership_request.note.error",
                extra={
                    "event_key": "organization_membership_request_submitted",
                    "request_id": membership_request.pk,
                    "organization_id": organization.pk,
                    "username": representative_username,
                },
            )
    except Exception:
        logger.exception(
            "membership_request.note.error",
            extra={
                "event_key": "organization_membership_request_submitted",
                "request_id": membership_request.pk,
                "organization_id": organization.pk,
            },
        )


def connect_membership_notes_receivers() -> None:
    global _CONNECTED
    if _CONNECTED:
        return

    wrapped_receiver = safe_receiver("user_country_changed")(
        record_country_change_notes_for_pending_membership_requests,
    )
    astra_signals.user_country_changed.connect(
        wrapped_receiver,
        dispatch_uid="core.membership_notes_receivers.user_country_changed",
    )

    wrapped_user_submission = safe_receiver("membership_request_submitted")(
        record_embargoed_country_note_for_user_submission,
    )
    astra_signals.membership_request_submitted.connect(
        wrapped_user_submission,
        dispatch_uid="core.membership_notes_receivers.membership_request_submitted",
    )

    wrapped_org_submission = safe_receiver("organization_membership_request_submitted")(
        record_embargoed_country_note_for_org_submission,
    )
    astra_signals.organization_membership_request_submitted.connect(
        wrapped_org_submission,
        dispatch_uid="core.membership_notes_receivers.organization_membership_request_submitted",
    )

    _CONNECTED = True

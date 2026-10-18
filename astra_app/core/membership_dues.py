"""Dues waivers for in-kind sponsors.

The membership committee may charge a sponsor less than its tier's default dues
(down to nothing) in exchange for goods or services. The amount lives on the
membership and on every membership log entry that sets its term, so renewals and
committee edits carry it over; a tier change drops it. Every change is recorded in
the membership log and as a committee note with a mandatory reason.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from core.membership import last_term_log, term_membership_request
from core.membership_notes import add_note
from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Note, Organization


def payable_dues_usd(membership_type: MembershipType, dues_usd: int | None) -> int:
    """What a sponsor pays: the committee-set dues, or the tier's standard price."""
    return membership_type.annual_dues_usd if dues_usd is None else dues_usd


def effective_dues_usd(membership: Membership) -> int:
    return payable_dues_usd(membership.membership_type, membership.dues_usd)


def normalize_dues_usd(membership_type: MembershipType, dues_usd: int) -> int | None:
    """Validate committee-set dues; charging the tier default means no waiver (None)."""
    default = membership_type.annual_dues_usd
    if default <= 0:
        raise ValidationError(f"{membership_type.name} has no dues to waive.")
    if not 0 <= dues_usd <= default:
        raise ValidationError(f"Dues for {membership_type.name} must be between $0 and ${default:,}.")
    return None if dues_usd == default else dues_usd


def discount_percent(default: int, due: int) -> str:
    """The in-kind discount as a percentage with up to two decimals, e.g. "75" or "99.99".

    Truncated, so a partial discount never reads as 100%, and at least 0.01, so a
    real discount never reads as 0%.
    """
    if not default or due >= default:
        return "0"
    hundredths = max((default - due) * 10_000 // default, 1)
    whole, fraction = divmod(hundredths, 100)
    return f"{whole}.{fraction:02d}".rstrip("0").rstrip(".")


def _required_reason(reason: str) -> str:
    reason = reason.strip()
    if not reason:
        raise ValidationError("Give a reason for the dues change, e.g. the in-kind agreement it reflects.")
    return reason


def describe_dues(membership_type: MembershipType, dues_usd: int | None) -> str:
    """E.g. "pays $5,000 of $20,000 (75% / $15,000 in-kind discount)"."""
    default = membership_type.annual_dues_usd
    due = payable_dues_usd(membership_type, dues_usd)
    if due == default:
        return f"pays the default ${default:,}"
    return f"pays ${due:,} of ${default:,} ({discount_percent(default, due)}% / ${default - due:,} in-kind discount)"


def record_dues_change(
    *,
    organization: Organization,
    membership_type: MembershipType,
    old_dues_usd: int,
    new_dues_usd: int,
    reason: str,
    actor_username: str,
    membership_request: MembershipRequest,
) -> Note:
    """Add the committee note for a dues change (amounts are what the sponsor pays)."""
    default = membership_type.annual_dues_usd
    return add_note(
        membership_request=membership_request,
        username=actor_username,
        content=reason,
        action={
            "type": "dues_changed",
            "membership_type": membership_type.code,
            "default_dues_usd": default,
            "old_dues_usd": old_dues_usd,
            "new_dues_usd": new_dues_usd,
            "discount_percent": discount_percent(default, new_dues_usd),
        },
    )


def change_membership_dues(*, membership: Membership, dues_usd: int, reason: str, actor_username: str) -> MembershipLog:
    """Committee edit of an active sponsorship's dues, keeping its term."""
    reason = _required_reason(reason)
    new_dues_usd = normalize_dues_usd(membership.membership_type, dues_usd)
    old_dues_usd = effective_dues_usd(membership)
    # A no-op would still log and note a "change", muddying the audit trail.
    if dues_usd == old_dues_usd:
        raise ValidationError(f"{membership.membership_type.name} dues are already ${old_dues_usd:,}.")
    term_request = term_membership_request(
        membership_type=membership.membership_type,
        target_organization=membership.target_organization,
    )
    if term_request is None:
        raise ValueError(f"Organization {membership.target_organization_id} has no approved request for its sponsorship.")

    with transaction.atomic():
        log = MembershipLog.create_for_dues_change(
            actor_username=actor_username,
            membership_type=membership.membership_type,
            target_organization=membership.target_organization,
            membership_request=term_request,
            expires_at=membership.expires_at,
            dues_usd=new_dues_usd,
        )
        record_dues_change(
            organization=membership.target_organization,
            membership_type=membership.membership_type,
            old_dues_usd=old_dues_usd,
            new_dues_usd=dues_usd,
            reason=reason,
            actor_username=actor_username,
            membership_request=term_request,
        )
    return log


@dataclass(frozen=True)
class DuesChange:
    """A change in what the sponsor pays, to be noted for the committee."""

    old_dues_usd: int
    new_dues_usd: int
    reason: str


def resolve_approval_dues(
    *,
    membership_request: MembershipRequest,
    dues_usd: int | None,
    reason: str,
) -> tuple[int | None, DuesChange | None]:
    """Decide the dues of the term being approved: (dues for the log, change to note).

    Without committee input a same-tier renewal keeps its waiver and a tier change
    drops it; committee-set dues that differ from that need a reason.
    """
    requested_type = membership_request.membership_type
    current = None
    if membership_request.requested_organization_id is not None:
        current = (
            Membership.objects.select_related("membership_type")
            .filter(
                target_organization_id=membership_request.requested_organization_id,
                membership_type__category_id=requested_type.category_id,
            )
            .active()
            .first()
        )
    is_same_tier = current is not None and current.membership_type_id == requested_type.code
    carried = current.dues_usd if is_same_tier else None
    old_effective = effective_dues_usd(current) if current is not None else requested_type.annual_dues_usd

    new_dues_usd = carried if dues_usd is None else normalize_dues_usd(requested_type, dues_usd)
    if new_dues_usd != carried:
        return new_dues_usd, DuesChange(
            old_dues_usd=old_effective,
            new_dues_usd=payable_dues_usd(requested_type, new_dues_usd),
            reason=_required_reason(reason),
        )
    # However the default was reached, a tier change silently drops the old tier's waiver.
    if current is not None and not is_same_tier and current.dues_usd is not None:
        return None, DuesChange(
            old_dues_usd=old_effective,
            new_dues_usd=requested_type.annual_dues_usd,
            reason=(
                f"Tier changed from {current.membership_type.name}, where this sponsor "
                f"{describe_dues(current.membership_type, current.dues_usd)}. "
                f"{requested_type.name}'s default dues apply."
            ),
        )
    return carried, None


def dues_waiver_review_message(membership_request: MembershipRequest) -> str | None:
    """Explain an existing or previous waiver to the committee reviewing a request.

    Uses the category's latest term in the membership log, so a lapsed sponsor's
    expired (and deleted) membership is still found.
    """
    organization = membership_request.requested_organization
    if organization is None:
        return None
    requested_type = membership_request.membership_type
    last_term = last_term_log(organization=organization, category_id=requested_type.category_id)
    if last_term is None or last_term.dues_usd is None:
        return None

    waiver = describe_dues(last_term.membership_type, last_term.dues_usd)
    is_active = Membership.objects.filter(
        target_organization=organization,
        membership_type__category_id=requested_type.category_id,
    ).active().exists()
    if not is_active:
        return (
            f"In its previous {last_term.membership_type.name} term this sponsor {waiver}. "
            f"{requested_type.name}'s default dues of ${requested_type.annual_dues_usd:,} apply "
            "unless the committee sets a waiver at approval."
        )
    if last_term.membership_type_id == requested_type.code:
        return (
            f"Renewal of a discounted sponsorship: this sponsor {waiver} on {requested_type.name}. "
            "Confirm the in-kind agreement is still valid before approving."
        )
    return (
        f"Tier change from {last_term.membership_type.name} to {requested_type.name}: this sponsor currently "
        f"{waiver} on {last_term.membership_type.name}. {requested_type.name}'s default dues of "
        f"${requested_type.annual_dues_usd:,} apply unless the committee sets a waiver at approval."
    )


def request_dues_payloads(requests: Iterable[MembershipRequest]) -> dict[int, dict[str, int | None]]:
    """For organization requests on paid tiers: the tier default and the waiver a renewal would carry.

    A non-null "current_usd" marks a discounted renewal, which the committee must
    approve individually after confirming the in-kind agreement.
    """
    paid_requests = [
        membership_request
        for membership_request in requests
        if membership_request.requested_organization_id is not None
        and membership_request.membership_type.annual_dues_usd > 0
    ]
    waivers = {
        (organization_id, membership_type_id): dues_usd
        for organization_id, membership_type_id, dues_usd in Membership.objects.filter(
            target_organization_id__in={membership_request.requested_organization_id for membership_request in paid_requests},
            dues_usd__isnull=False,
        )
        .active()
        .values_list("target_organization_id", "membership_type_id", "dues_usd")
    }
    return {
        membership_request.pk: {
            "default_usd": membership_request.membership_type.annual_dues_usd,
            "current_usd": waivers.get((membership_request.requested_organization_id, membership_request.membership_type_id)),
        }
        for membership_request in paid_requests
    }


def parse_dues_usd(raw: object) -> int | None:
    """Parse committee-entered dues (form string or JSON number); blank means no committee decision."""
    value = "" if raw is None else str(raw).strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValidationError("Dues must be a whole number of US dollars.") from exc

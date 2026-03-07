from core import signals as astra_signals
from core.mirror_membership_validation import schedule_mirror_membership_validation
from core.models import MembershipRequest
from core.signal_receivers import connect_once, safe_receiver


def schedule_mirror_validation_from_signal(
    sender: object,
    *,
    membership_request: MembershipRequest,
    actor: str,
    **kwargs: object,
) -> None:
    _ = (sender, actor, kwargs)
    schedule_mirror_membership_validation(membership_request=membership_request)


@connect_once
def connect_mirror_membership_validation_receivers() -> None:
    for event_key, signal, dispatch_uid in [
        (
            "membership_request_submitted",
            astra_signals.membership_request_submitted,
            "core.mirror_membership_validation_receivers.membership_request_submitted",
        ),
        (
            "organization_membership_request_submitted",
            astra_signals.organization_membership_request_submitted,
            "core.mirror_membership_validation_receivers.organization_membership_request_submitted",
        ),
        (
            "membership_rfi_replied",
            astra_signals.membership_rfi_replied,
            "core.mirror_membership_validation_receivers.membership_rfi_replied",
        ),
        (
            "organization_membership_rfi_replied",
            astra_signals.organization_membership_rfi_replied,
            "core.mirror_membership_validation_receivers.organization_membership_rfi_replied",
        ),
    ]:
        signal.connect(
            safe_receiver(event_key)(schedule_mirror_validation_from_signal),
            dispatch_uid=dispatch_uid,
        )

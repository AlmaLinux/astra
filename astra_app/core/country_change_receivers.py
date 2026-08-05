"""Derived signals for country changes involving an embargoed country."""

from core import signals as astra_signals
from core.country_codes import country_change_involves_embargoed_country
from core.signal_receivers import connect_once, safe_receiver


def emit_user_embargoed_country_changed(
    sender: object,
    *,
    username: str,
    old_country: str,
    new_country: str,
    actor: str,
    **kwargs: object,
) -> None:
    _ = kwargs
    if not country_change_involves_embargoed_country(old_country=old_country, new_country=new_country):
        return

    astra_signals.user_embargoed_country_changed.send(
        sender=sender,
        username=username,
        old_country=old_country,
        new_country=new_country,
        actor=actor,
    )


def emit_organization_embargoed_country_changed(
    sender: object,
    *,
    organization: object,
    old_country: str,
    new_country: str,
    actor: str,
    **kwargs: object,
) -> None:
    _ = kwargs
    if not country_change_involves_embargoed_country(old_country=old_country, new_country=new_country):
        return

    astra_signals.organization_embargoed_country_changed.send(
        sender=sender,
        organization=organization,
        old_country=old_country,
        new_country=new_country,
        actor=actor,
    )


@connect_once
def connect_country_change_receivers() -> None:
    wrapped_user_receiver = safe_receiver("user_country_changed")(
        emit_user_embargoed_country_changed,
    )
    astra_signals.user_country_changed.connect(
        wrapped_user_receiver,
        dispatch_uid="core.country_change_receivers.user_embargoed_country_changed",
    )

    wrapped_organization_receiver = safe_receiver("organization_country_changed")(
        emit_organization_embargoed_country_changed,
    )
    astra_signals.organization_country_changed.connect(
        wrapped_organization_receiver,
        dispatch_uid="core.country_change_receivers.organization_embargoed_country_changed",
    )
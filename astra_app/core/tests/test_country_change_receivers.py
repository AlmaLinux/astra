from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from core import signals as astra_signals
from core.country_change_receivers import (
    emit_organization_embargoed_country_changed,
    emit_user_embargoed_country_changed,
)


@override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["IR", "CU"])
class CountryChangeReceiversTests(TestCase):
    def test_user_country_change_emits_for_transition_to_embargoed_country(self) -> None:
        sender = SimpleNamespace()

        with patch.object(astra_signals.user_embargoed_country_changed, "send") as send_mock:
            emit_user_embargoed_country_changed(
                sender=sender,
                username="alice",
                old_country="US",
                new_country="IR",
                actor="alice",
            )

        send_mock.assert_called_once_with(
            sender=sender,
            username="alice",
            old_country="US",
            new_country="IR",
            actor="alice",
        )

    def test_user_country_change_emits_for_transition_from_embargoed_country(self) -> None:
        sender = SimpleNamespace()

        with patch.object(astra_signals.user_embargoed_country_changed, "send") as send_mock:
            emit_user_embargoed_country_changed(
                sender=sender,
                username="alice",
                old_country="CU",
                new_country="DE",
                actor="alice",
            )

        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.kwargs["old_country"], "CU")
        self.assertEqual(send_mock.call_args.kwargs["new_country"], "DE")

    def test_user_country_change_does_not_emit_for_non_embargoed_transition(self) -> None:
        sender = SimpleNamespace()

        with patch.object(astra_signals.user_embargoed_country_changed, "send") as send_mock:
            emit_user_embargoed_country_changed(
                sender=sender,
                username="alice",
                old_country="US",
                new_country="DE",
                actor="alice",
            )

        send_mock.assert_not_called()

    def test_user_country_change_does_not_emit_when_embargoed_country_is_unchanged(self) -> None:
        sender = SimpleNamespace()

        with patch.object(astra_signals.user_embargoed_country_changed, "send") as send_mock:
            emit_user_embargoed_country_changed(
                sender=sender,
                username="alice",
                old_country="IR",
                new_country="ir",
                actor="alice",
            )

        send_mock.assert_not_called()

    def test_user_country_change_emits_for_first_embargoed_country_assignment(self) -> None:
        sender = SimpleNamespace()

        with patch.object(astra_signals.user_embargoed_country_changed, "send") as send_mock:
            emit_user_embargoed_country_changed(
                sender=sender,
                username="alice",
                old_country="",
                new_country="IR",
                actor="alice",
            )

        send_mock.assert_called_once()

    def test_user_country_signal_is_connected_to_embargoed_country_signal(self) -> None:
        sender = object()

        with (
            patch.object(astra_signals.user_embargoed_country_changed, "send") as send_mock,
            patch("core.mattermost_webhooks.dispatch_mattermost_event"),
        ):
            astra_signals.user_country_changed.send(
                sender=sender,
                username="alice",
                old_country="US",
                new_country="IR",
                actor="alice",
            )

        send_mock.assert_called_once_with(
            sender=sender,
            username="alice",
            old_country="US",
            new_country="IR",
            actor="alice",
        )

    def test_organization_country_change_emits_targeted_signal(self) -> None:
        sender = SimpleNamespace()
        organization = SimpleNamespace(pk=42)

        with patch.object(astra_signals.organization_embargoed_country_changed, "send") as send_mock:
            emit_organization_embargoed_country_changed(
                sender=sender,
                organization=organization,
                old_country="US",
                new_country="CU",
                actor="alice",
            )

        send_mock.assert_called_once_with(
            sender=sender,
            organization=organization,
            old_country="US",
            new_country="CU",
            actor="alice",
        )

    def test_organization_country_signal_is_connected_to_embargoed_country_signal(self) -> None:
        sender = object()
        organization = SimpleNamespace(pk=42)

        with (
            patch.object(astra_signals.organization_embargoed_country_changed, "send") as send_mock,
            patch("core.mattermost_webhooks.dispatch_mattermost_event"),
        ):
            astra_signals.organization_country_changed.send(
                sender=sender,
                organization=organization,
                old_country="US",
                new_country="CU",
                actor="alice",
            )

        send_mock.assert_called_once_with(
            sender=sender,
            organization=organization,
            old_country="US",
            new_country="CU",
            actor="alice",
        )
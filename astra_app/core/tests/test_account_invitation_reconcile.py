import datetime
import importlib
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core import signing
from django.test import TestCase
from django.utils import timezone

from core.account_invitation_reconcile import (
    load_account_invitation_from_token,
    reconcile_account_invitation_for_username,
    schedule_account_invitation_accepted_signal,
)
from core.models import AccountInvitation, Organization
from core.tokens import _make_signed_token_legacy


class AccountInvitationReconcileTests(TestCase):
    def test_schedule_account_invitation_accepted_signal_sends_after_commit(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
            accepted_at=timezone.now(),
            accepted_username="alice",
            freeipa_matched_usernames=["alice"],
        )
        signal_module = importlib.import_module("core.signals")

        with patch.object(signal_module.account_invitation_accepted, "send", autospec=True) as send_mock:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                schedule_account_invitation_accepted_signal(invitation_id=invitation.pk, actor="alice")

            self.assertEqual(len(callbacks), 1)
            send_mock.assert_not_called()
            callbacks[0]()
            send_mock.assert_called_once()

        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs.get("sender"), AccountInvitation)
        self.assertEqual(kwargs.get("actor"), "alice")
        self.assertEqual(kwargs.get("account_invitation").pk, invitation.pk)

    def test_legacy_token_is_accepted_via_fallback(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="legacy-invitee@example.com",
            full_name="Legacy Invitee",
            invited_by_username="committee",
            invitation_token="placeholder-token",
        )
        legacy_token = _make_signed_token_legacy({"invitation_id": invitation.pk})
        invitation.invitation_token = legacy_token
        invitation.save(update_fields=["invitation_token"])

        loaded = load_account_invitation_from_token(legacy_token)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.pk, invitation.pk)

    def test_legacy_token_with_invalid_payload_returns_none(self) -> None:
        legacy_token = signing.dumps("not-a-dict", salt=settings.SECRET_KEY)

        loaded = load_account_invitation_from_token(legacy_token)

        self.assertIsNone(loaded)

    def test_legacy_token_db_mismatch_returns_none(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="mismatch-invitee@example.com",
            full_name="Mismatch Invitee",
            invited_by_username="committee",
            invitation_token="different-token",
        )
        legacy_token = _make_signed_token_legacy({"invitation_id": invitation.pk})

        loaded = load_account_invitation_from_token(legacy_token)

        self.assertIsNone(loaded)

    def test_load_account_invitation_from_token_requires_row_token_match(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
        )

        with patch(
            "core.account_invitation_reconcile.read_account_invitation_token_unbounded",
            return_value={"invitation_id": invitation.pk},
        ):
            loaded = load_account_invitation_from_token("forged-token")

        self.assertIsNone(loaded)

    def test_load_account_invitation_from_token_returns_matching_invitation(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
        )

        loaded = load_account_invitation_from_token(str(invitation.invitation_token))

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.pk, invitation.pk)

    def test_reconcile_account_invitation_for_username_is_idempotent_for_non_org(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
            freeipa_matched_usernames=["zara"],
        )

        first_now = timezone.make_aware(datetime.datetime(2026, 2, 15, 10, 0, 0), datetime.UTC)
        second_now = timezone.make_aware(datetime.datetime(2026, 2, 15, 11, 0, 0), datetime.UTC)

        reconcile_account_invitation_for_username(invitation=invitation, username="alice", now=first_now)
        reconcile_account_invitation_for_username(invitation=invitation, username="alice", now=second_now)

        invitation.refresh_from_db()
        self.assertEqual(invitation.accepted_at, first_now)
        self.assertEqual(invitation.accepted_username, "alice")
        self.assertEqual(invitation.freeipa_last_checked_at, second_now)
        self.assertEqual(invitation.freeipa_matched_usernames, ["alice", "zara"])

    def test_reconcile_account_invitation_for_username_emits_signal_only_for_first_non_org_acceptance(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
        )
        first_now = timezone.make_aware(datetime.datetime(2026, 2, 15, 10, 0, 0), datetime.UTC)
        second_now = timezone.make_aware(datetime.datetime(2026, 2, 15, 11, 0, 0), datetime.UTC)
        signal_module = importlib.import_module("core.signals")

        with (
            patch.object(signal_module.account_invitation_accepted, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            reconcile_account_invitation_for_username(invitation=invitation, username="alice", now=first_now)
            reconcile_account_invitation_for_username(invitation=invitation, username="alice", now=second_now)

        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs.get("sender"), AccountInvitation)
        self.assertEqual(kwargs.get("actor"), "alice")
        self.assertEqual(kwargs.get("account_invitation").pk, invitation.pk)

    def test_reconcile_account_invitation_for_username_stale_instance_does_not_double_emit_signal(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
        )
        first_copy = AccountInvitation.objects.get(pk=invitation.pk)
        stale_copy = AccountInvitation.objects.get(pk=invitation.pk)
        first_now = timezone.make_aware(datetime.datetime(2026, 2, 15, 10, 0, 0), datetime.UTC)
        second_now = timezone.make_aware(datetime.datetime(2026, 2, 15, 11, 0, 0), datetime.UTC)
        signal_module = importlib.import_module("core.signals")

        with (
            patch.object(signal_module.account_invitation_accepted, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            reconcile_account_invitation_for_username(invitation=first_copy, username="alice", now=first_now)
            reconcile_account_invitation_for_username(invitation=stale_copy, username="alice", now=second_now)

        invitation.refresh_from_db()
        self.assertEqual(invitation.accepted_at, first_now)
        self.assertEqual(invitation.accepted_username, "alice")
        self.assertEqual(invitation.freeipa_last_checked_at, second_now)
        self.assertEqual(invitation.freeipa_matched_usernames, ["alice"])
        send_mock.assert_called_once()

    def test_reconcile_account_invitation_for_username_does_not_overwrite_existing_accepted_username(self) -> None:
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
            accepted_username="zara",
        )
        now = timezone.make_aware(datetime.datetime(2026, 2, 15, 10, 0, 0), datetime.UTC)

        reconcile_account_invitation_for_username(invitation=invitation, username="alice", now=now)

        invitation.refresh_from_db()
        self.assertEqual(invitation.accepted_username, "zara")
        self.assertEqual(invitation.accepted_at, now)
        self.assertEqual(invitation.freeipa_matched_usernames, ["alice"])

    def test_reconcile_account_invitation_for_username_normalizes_username_to_lowercase(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 2, 15, 10, 0, 0), datetime.UTC)
        invitation = SimpleNamespace(
            pk=None,
            organization_id=None,
            accepted_at=None,
            accepted_username="",
            freeipa_matched_usernames=[],
            freeipa_last_checked_at=None,
        )

        def _save(*, update_fields: list[str]) -> None:
            _ = update_fields

        invitation.save = _save

        reconcile_account_invitation_for_username(invitation=invitation, username="Alice", now=now)

        self.assertEqual(invitation.accepted_username, "alice")
        self.assertIn("alice", invitation.freeipa_matched_usernames)

    def test_reconcile_account_invitation_for_username_keeps_org_invitation_pending(self) -> None:
        organization = Organization.objects.create(
            name="Pending Claim Org",
            business_contact_email="invitee@example.com",
        )
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
            organization=organization,
        )
        now = timezone.make_aware(datetime.datetime(2026, 2, 15, 10, 0, 0), datetime.UTC)

        reconcile_account_invitation_for_username(invitation=invitation, username="alice", now=now)

        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        self.assertEqual(invitation.accepted_username, "")
        self.assertEqual(invitation.freeipa_last_checked_at, now)
        self.assertEqual(invitation.freeipa_matched_usernames, ["alice"])

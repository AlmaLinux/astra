from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase

from core.maintenance_shell import build_maintenance_shell_banner, build_maintenance_shell_namespace
from core.models import MembershipRequest, MembershipType


class MaintenanceShellNamespaceTests(TestCase):
    def _membership_type(self) -> MembershipType:
        membership_type, _created = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        return membership_type

    def test_namespace_exports_approved_helpers(self) -> None:
        namespace = build_maintenance_shell_namespace()

        self.assertIs(namespace["MembershipRequest"], MembershipRequest)
        self.assertIn("get_membership_request", namespace)
        self.assertIn("preview_reset_rejected_request", namespace)
        self.assertIn("apply_reset_rejected_request", namespace)
        self.assertIn("reset_rejected_membership_request_to_pending", namespace)
        self.assertIn("inspect_cache", namespace)
        self.assertIn("clear_cache", namespace)
        self.assertIn("send_test_email", namespace)
        self.assertIn("run_send_queued_mail", namespace)

    def test_preview_helper_uses_existing_repair_service(self) -> None:
        membership_type = self._membership_type()
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
            status=MembershipRequest.Status.rejected,
            responses=[
                {"Contributions": "Old"},
                {"Rejection reason": "No."},
            ],
        )

        namespace = build_maintenance_shell_namespace()
        result = namespace["preview_reset_rejected_request"](membership_request.pk)

        self.assertEqual(result.request_id, membership_request.pk)
        self.assertTrue(result.dry_run)
        self.assertTrue(result.trimmed_rejection_reason)

    def test_banner_points_operators_to_guided_command_first(self) -> None:
        banner = build_maintenance_shell_banner()

        self.assertIn("maintenance_shell", banner)
        self.assertIn("membership_request_repair", banner)
        self.assertIn("Use the guided command first", banner)
        self.assertIn("preview_reset_rejected_request", banner)
        self.assertIn("inspect_cache", banner)
        self.assertIn("send_test_email", banner)

    def test_inspect_cache_reports_live_process_keys_and_previews(self) -> None:
        cache.set("freeipa_user_alice", {"mail": "alice@example.com"}, timeout=60)

        namespace = build_maintenance_shell_namespace()
        payload = namespace["inspect_cache"](prefix="freeipa_", key="freeipa_user_alice")

        self.assertTrue(payload["supports_key_listing"])
        self.assertIn("freeipa_user_alice", payload["keys"])
        self.assertEqual(payload["key"], "freeipa_user_alice")
        self.assertIn("alice@example.com", payload["value_preview"])

    def test_clear_cache_clears_existing_entries(self) -> None:
        cache.set("freeipa_user_alice", "present", timeout=60)

        namespace = build_maintenance_shell_namespace()
        result = namespace["clear_cache"]()

        self.assertTrue(result["cleared"])
        self.assertIsNone(cache.get("freeipa_user_alice"))

    def test_send_test_email_queues_email_via_ssot_helper(self) -> None:
        queued_email = MagicMock()
        queued_email.id = 17

        namespace = build_maintenance_shell_namespace()
        with patch("core.maintenance_shell.queue_composed_email", return_value=queued_email) as queue_mock:
            result = namespace["send_test_email"]("alice@example.com")

        queue_mock.assert_called_once()
        kwargs = queue_mock.call_args.kwargs
        self.assertEqual(kwargs["recipients"], ["alice@example.com"])
        self.assertEqual(kwargs["sender"], settings.DEFAULT_FROM_EMAIL)
        self.assertIn("Astra test email", kwargs["subject_source"])
        self.assertFalse(result["delivered"])
        self.assertEqual(result["email_id"], 17)

    def test_send_test_email_accepts_custom_subject_and_content(self) -> None:
        queued_email = MagicMock()
        queued_email.id = 19

        namespace = build_maintenance_shell_namespace()
        with patch("core.maintenance_shell.queue_composed_email", return_value=queued_email) as queue_mock:
            namespace["send_test_email"](
                "alice@example.com",
                subject="Operator smoke test",
                content="Custom plain-text test body.",
            )

        queue_mock.assert_called_once()
        kwargs = queue_mock.call_args.kwargs
        self.assertEqual(kwargs["subject_source"], "Operator smoke test")
        self.assertEqual(kwargs["text_source"], "Custom plain-text test body.")

    def test_send_test_email_can_trigger_send_queued_mail(self) -> None:
        queued_email = MagicMock()
        queued_email.id = 18

        namespace = build_maintenance_shell_namespace()
        with (
            patch("core.maintenance_shell.queue_composed_email", return_value=queued_email),
            patch("core.maintenance_shell.call_command") as call_command_mock,
        ):
            result = namespace["send_test_email"]("alice@example.com", deliver_queued=True)

        call_command_mock.assert_called_once_with("send_queued_mail")
        self.assertTrue(result["delivered"])


class MaintenanceShellCommandTests(TestCase):
    def test_command_opens_interactive_console_with_preloaded_namespace(self) -> None:
        with patch("core.management.commands.maintenance_shell.code.interact") as interact_mock:
            call_command("maintenance_shell")

        interact_mock.assert_called_once()
        _args, kwargs = interact_mock.call_args
        self.assertIn("local", kwargs)
        self.assertIn("banner", kwargs)
        self.assertIn("preview_reset_rejected_request", kwargs["local"])
        self.assertIn("membership_request_repair", kwargs["banner"])
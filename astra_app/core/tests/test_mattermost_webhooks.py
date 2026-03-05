import datetime
import json
import warnings
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.mattermost_webhooks import (
    _build_payload,
    _default_payload,
    _post_to_endpoint,
    _render_template,
    dispatch_mattermost_event,
)
from core.models import MattermostWebhookEndpoint
from core.tokens import election_genesis_chain_hash


class MattermostWebhookModelValidationTests(SimpleTestCase):
    def test_clean_rejects_non_https_url(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="http://hooks.example.invalid/abc",
            events=["election_opened"],
        )

        with self.assertRaises(ValidationError):
            endpoint.clean()

    def test_clean_rejects_non_list_events(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events={"event": "election_opened"},
        )

        with self.assertRaises(ValidationError):
            endpoint.clean()

    def test_clean_warns_for_unknown_event_keys_but_allows_forward_compat(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_opened", "future_event_key"],
        )

        with warnings.catch_warnings(record=True) as captured_warnings:
            warnings.simplefilter("always")
            endpoint.clean()

        warning_messages = [str(item.message) for item in captured_warnings]
        self.assertTrue(
            any("Unknown event keys in MattermostWebhookEndpoint" in message for message in warning_messages)
        )

    def test_clean_rejects_non_string_event_items(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_opened", 123],
        )

        with self.assertRaises(ValidationError):
            endpoint.clean()


class MattermostWebhookTemplateAndPayloadTests(SimpleTestCase):
    def test_render_template_returns_empty_string_on_error_and_logs(self) -> None:
        with self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured:
            rendered = _render_template("{{ name|definitely_unknown_filter }}", {"name": "alice"})

        self.assertEqual(rendered, "")
        self.assertTrue(captured.records)

    def test_build_payload_applies_merge_order(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["membership_expired"],
            text="Custom text {{ count }}",
            attachments=[
                {
                    "title": "Override {{ membership_type }}",
                    "fields": [{"title": "Count", "value": "{{ count }}", "short": True}],
                }
            ],
            props={"from_endpoint": "yes"},
            priority={"priority": "important", "requested_ack": False},
            channel="town-square",
            username="Astra Bot",
            icon_url="https://example.invalid/icon.png",
        )

        payload = _build_payload(
            endpoint=endpoint,
            event_key="membership_expired",
            kwargs={"count": 4, "membership_type": "sponsorship"},
        )

        self.assertEqual(payload["text"], "Custom text 4")
        self.assertEqual(payload["attachments"][0]["title"], "Override sponsorship")
        self.assertEqual(payload["attachments"][0]["fields"][0]["value"], "4")
        self.assertEqual(payload["props"]["from_endpoint"], "yes")
        self.assertEqual(payload["priority"]["priority"], "important")
        self.assertEqual(payload["channel"], "town-square")
        self.assertEqual(payload["username"], "Astra Bot")
        self.assertEqual(payload["icon_url"], "https://example.invalid/icon.png")

    def test_default_payload_uses_expected_color_for_event_family(self) -> None:
        payload = _default_payload("membership_request_approved", {"actor": "alice"})
        self.assertEqual(payload["attachments"][0]["color"], "#36a64f")

    def test_build_payload_falls_back_on_bad_template(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_opened"],
            text="{{ unclosed",
        )

        payload = _build_payload(endpoint, "election_opened", {"actor": "test"})

        self.assertIn("text", payload)
        self.assertTrue(str(payload["text"]).strip())

    def test_build_payload_exposes_membership_request_details_in_template_context(self) -> None:
        membership_request = SimpleNamespace(
            pk=42,
            requested_username="carol",
            requested_organization_id=None,
            organization_display_name="",
            membership_type=SimpleNamespace(code="sponsorship", name="Sponsorship"),
            target_kind=SimpleNamespace(value="user"),
        )
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["membership_request_submitted"],
            text="type={{ membership_type_code }} name={{ membership_type_name }} target={{ membership_target_kind }}",
        )

        payload = _build_payload(
            endpoint,
            "membership_request_submitted",
            {"membership_request": membership_request, "actor": "alice"},
        )

        self.assertEqual(payload["text"], "type=sponsorship name=Sponsorship target=user")

    def test_build_payload_exposes_election_opened_genesis_and_end_datetime(self) -> None:
        election = SimpleNamespace(
            pk=7,
            name="Board 2026",
            end_datetime=datetime.datetime(2026, 5, 1, 12, 0, tzinfo=datetime.UTC),
            tally_result={},
        )
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_opened"],
            text="genesis={{ election_genesis_hash }} end={{ election_end_datetime_iso }}",
        )

        payload = _build_payload(endpoint, "election_opened", {"election": election, "actor": "alice"})

        self.assertIn(election_genesis_chain_hash(7), str(payload["text"]))
        self.assertIn("2026-05-01T12:00:00+00:00", str(payload["text"]))

    @patch("core.mattermost_webhooks.Ballot.objects.latest_chain_head_hash_for_election", return_value="deadbeef")
    def test_build_payload_exposes_election_closed_final_chain_hash(self, _chain_hash_mock: Mock) -> None:
        election = SimpleNamespace(
            pk=8,
            name="Board 2026",
            end_datetime=datetime.datetime(2026, 5, 1, 12, 0, tzinfo=datetime.UTC),
            tally_result={},
        )
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_closed"],
            text="final={{ election_final_chain_hash }}",
        )

        payload = _build_payload(endpoint, "election_closed", {"election": election, "actor": "alice"})

        self.assertEqual(payload["text"], "final=deadbeef")

    def test_build_payload_exposes_election_tallied_winners(self) -> None:
        election = SimpleNamespace(
            pk=9,
            name="Board 2026",
            end_datetime=datetime.datetime(2026, 5, 1, 12, 0, tzinfo=datetime.UTC),
            tally_result={"elected": ["alice", "bob"]},
        )
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_tallied"],
            text="winners={{ election_winners|join:', ' }}",
        )

        payload = _build_payload(endpoint, "election_tallied", {"election": election, "actor": "alice"})

        self.assertEqual(payload["text"], "winners=alice, bob")

    def test_payload_organization_country_changed(self) -> None:
        org = SimpleNamespace(pk=123, name="Example Org")
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["organization_country_changed"],
        )

        payload = _build_payload(
            endpoint,
            "organization_country_changed",
            {
                "organization": org,
                "old_country": "US",
                "new_country": "DE",
                "actor": "alice",
            },
        )

        self.assertEqual(payload["text"], "Organization country changed")

        fields = payload["attachments"][0]["fields"]
        by_title = {str(field.get("title")): str(field.get("value")) for field in fields}
        self.assertEqual(by_title.get("Old country"), "US")
        self.assertEqual(by_title.get("New country"), "DE")

    def test_payload_user_country_changed(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["user_country_changed"],
        )

        payload = _build_payload(
            endpoint,
            "user_country_changed",
            {
                "username": "carol",
                "old_country": "US",
                "new_country": "FR",
                "actor": "carol",
            },
        )

        self.assertEqual(payload["text"], "User country changed")

        fields = payload["attachments"][0]["fields"]
        by_title = {str(field.get("title")): str(field.get("value")) for field in fields}
        self.assertEqual(by_title.get("Username"), "carol")
        self.assertEqual(by_title.get("Old country"), "US")
        self.assertEqual(by_title.get("New country"), "FR")


@override_settings(MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=3)
class MattermostWebhookPostTests(SimpleTestCase):
    def _endpoint(self, *, url: str = "https://hooks.example.invalid/abc") -> MattermostWebhookEndpoint:
        return MattermostWebhookEndpoint(label="Ops", url=url, events=["election_opened"], enabled=True)

    def test_post_to_endpoint_success_2xx(self) -> None:
        response = Mock()
        response.status_code = 200
        response.text = "ok"

        with patch("core.mattermost_webhooks.requests.post", return_value=response) as post_mock:
            _post_to_endpoint(self._endpoint(), {"text": "hello"})

        post_mock.assert_called_once()
        _, kwargs = post_mock.call_args
        self.assertEqual(kwargs["timeout"], 3)
        self.assertFalse(kwargs["allow_redirects"])

    def test_post_to_endpoint_http_error_logged_and_swallowed(self) -> None:
        response = Mock()
        response.status_code = 500
        response.text = "boom"

        with (
            patch("core.mattermost_webhooks.requests.post", return_value=response),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(self._endpoint(), {"text": "hello"})

        self.assertTrue(captured.records)
        self.assertTrue(any("response_excerpt=boom" in record.getMessage() for record in captured.records))
        self.assertTrue(any(getattr(record, "response_excerpt", None) == "boom" for record in captured.records))

    def test_post_to_endpoint_exception_logged_and_swallowed(self) -> None:
        with (
            patch("core.mattermost_webhooks.requests.post", side_effect=requests.Timeout("timeout")),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(self._endpoint(), {"text": "hello"})

        self.assertTrue(captured.records)

    def test_post_to_endpoint_blocks_non_https(self) -> None:
        endpoint = self._endpoint(url="http://hooks.example.invalid/insecure")

        with (
            patch("core.mattermost_webhooks.requests.post") as post_mock,
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(endpoint, {"text": "hello"})

        post_mock.assert_not_called()
        self.assertTrue(captured.records)

    def test_post_to_endpoint_treats_redirect_as_failure(self) -> None:
        response = Mock()
        response.status_code = 302
        response.text = "redirect"

        with (
            patch("core.mattermost_webhooks.requests.post", return_value=response),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(self._endpoint(), {"text": "hello"})

        self.assertTrue(captured.records)

    @override_settings(
        MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=3,
        MATTERMOST_WEBHOOK_DEFAULT_USERNAME="AlmaLinux Astra",
        MATTERMOST_WEBHOOK_DEFAULT_ICON_URL="/core/static/core/images/almalinux_astra_small.png",
        PUBLIC_BASE_URL="https://accounts.example.org",
    )
    def test_post_to_endpoint_applies_default_identity_from_settings(self) -> None:
        response = Mock()
        response.status_code = 200
        response.text = "ok"

        with patch("core.mattermost_webhooks.requests.post", return_value=response) as post_mock:
            _post_to_endpoint(self._endpoint(), {"text": "hello"})

        _, kwargs = post_mock.call_args
        payload = kwargs["json"]
        self.assertEqual(payload["username"], "AlmaLinux Astra")
        self.assertEqual(
            payload["icon_url"],
            "https://accounts.example.org/core/static/core/images/almalinux_astra_small.png",
        )

    @override_settings(
        MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=3,
        MATTERMOST_WEBHOOK_DEFAULT_USERNAME="AlmaLinux Astra",
        MATTERMOST_WEBHOOK_DEFAULT_ICON_URL="/core/static/core/images/almalinux_astra_small.png",
    )
    def test_post_to_endpoint_keeps_explicit_payload_identity(self) -> None:
        response = Mock()
        response.status_code = 200
        response.text = "ok"

        with patch("core.mattermost_webhooks.requests.post", return_value=response) as post_mock:
            _post_to_endpoint(
                self._endpoint(),
                {
                    "text": "hello",
                    "username": "Custom Username",
                    "icon_url": "https://cdn.example.org/custom-icon.png",
                },
            )

        _, kwargs = post_mock.call_args
        payload = kwargs["json"]
        self.assertEqual(payload["username"], "Custom Username")
        self.assertEqual(payload["icon_url"], "https://cdn.example.org/custom-icon.png")

    def test_post_to_endpoint_never_logs_raw_url(self) -> None:
        endpoint = self._endpoint(url="https://hooks.example.invalid/secret-token-value")

        with (
            patch("core.mattermost_webhooks.requests.post", side_effect=requests.Timeout("timeout")),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(endpoint, {"text": "hello"})

        output = "\n".join(captured.output)
        self.assertNotIn(endpoint.url, output)

    def test_url_not_in_log_record_extra_on_transport_error(self) -> None:
        secret_url = "https://hooks.mattermost.example/secret-token-12345"
        endpoint = self._endpoint(url=secret_url)

        with (
            patch(
                "core.mattermost_webhooks.requests.post",
                side_effect=requests.exceptions.ConnectionError(f"Failed to connect to {secret_url}"),
            ),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(endpoint, {"text": "test"})

        for record in captured.records:
            for val in record.__dict__.values():
                self.assertNotIn(secret_url, str(val), f"URL leaked in log record field: {val!r}")


class MattermostWebhookDispatchTests(TestCase):
    def test_dispatch_only_posts_for_enabled_matching_event(self) -> None:
        enabled_match = MattermostWebhookEndpoint.objects.create(
            label="Enabled match",
            url="https://hooks.example.invalid/match",
            enabled=True,
            events=["election_opened"],
        )
        MattermostWebhookEndpoint.objects.create(
            label="Disabled",
            url="https://hooks.example.invalid/disabled",
            enabled=False,
            events=["election_opened"],
        )
        MattermostWebhookEndpoint.objects.create(
            label="Wrong event",
            url="https://hooks.example.invalid/other",
            enabled=True,
            events=["membership_expired"],
        )

        with patch("core.mattermost_webhooks._post_to_endpoint") as post_mock:
            dispatch_mattermost_event("election_opened", actor="alice")

        post_mock.assert_called_once()
        endpoint_arg = post_mock.call_args.args[0]
        self.assertEqual(endpoint_arg.pk, enabled_match.pk)

    def test_dispatch_logs_error_and_skips_when_events_field_is_malformed(self) -> None:
        MattermostWebhookEndpoint.objects.create(
            label="Malformed",
            url="https://hooks.example.invalid/malformed",
            enabled=True,
            events={"bad": "shape"},
        )

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            dispatch_mattermost_event("election_opened", actor="alice")

        post_mock.assert_not_called()
        self.assertTrue(captured.records)

    def test_dispatch_logs_warning_and_skips_unknown_event_keys_in_endpoint_events(self) -> None:
        MattermostWebhookEndpoint.objects.create(
            label="Unknown route key",
            url="https://hooks.example.invalid/unknown",
            enabled=True,
            events=["event_from_future_registry"],
        )

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self.assertLogs("core.mattermost_webhooks", level="WARNING") as captured,
        ):
            dispatch_mattermost_event("election_opened", actor="alice")

        post_mock.assert_not_called()
        self.assertTrue(captured.records)

    @patch("core.mattermost_webhooks.MattermostWebhookEndpoint.objects.filter", side_effect=RuntimeError("DB down"))
    def test_dispatch_never_raises_on_db_error(self, _mock_filter: Mock) -> None:
        dispatch_mattermost_event("election_opened", actor="test")


class MattermostWebhookAdminTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _admin_user(self, username: str = "alice") -> FreeIPAUser:
        return FreeIPAUser(username, {"uid": [username], "memberof_group": ["admins"]})

    def _endpoint(self) -> MattermostWebhookEndpoint:
        return MattermostWebhookEndpoint.objects.create(
            label="Ops endpoint",
            url="https://hooks.example.invalid/test",
            enabled=True,
            events=["election_opened"],
        )

    def test_change_page_renders_send_test_notification_button(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Send test notification")
        test_url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
        self.assertContains(response, f'formaction="{test_url}"')

    def test_change_page_renders_events_as_checkbox_multiselect(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_events_0"')
        self.assertNotContains(response, '<textarea name="events"')

    def test_change_page_renders_help_text_hints_for_message_fields(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Django template syntax")
        self.assertContains(response, "JSON list of Mattermost attachments")
        self.assertContains(response, "JSON object merged into Mattermost props")

    def test_change_page_renders_template_variable_reference_for_selected_events(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Template variables by selected events")
        self.assertContains(response, "Event: election_opened")
        self.assertContains(response, "election.name")
        self.assertContains(response, "election_genesis_hash")

    def test_change_page_renders_priority_controls_instead_of_raw_priority_json(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_priority_level"')
        self.assertContains(response, 'id="id_priority_requested_ack"')
        self.assertContains(response, 'id="id_priority_persistent_notifications"')
        self.assertNotContains(response, '<textarea name="priority"')

    def test_change_view_saves_priority_controls_to_priority_json(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            change_url = reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk])
            result = self.client.post(
                change_url,
                data={
                    "label": endpoint.label,
                    "url": endpoint.url,
                    "enabled": "on",
                    "events": ["election_opened"],
                    "channel": "",
                    "username": "",
                    "icon_url": "",
                    "text": "",
                    "attachments": "",
                    "props": "",
                    "priority_level": "important",
                    "priority_requested_ack": "on",
                    "_save": "Save",
                },
                follow=True,
            )

        self.assertEqual(result.status_code, 200)
        endpoint.refresh_from_db()
        self.assertEqual(endpoint.priority, {"priority": "important", "requested_ack": True})

    def test_change_view_rejects_invalid_priority_control_combination(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            change_url = reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk])
            result = self.client.post(
                change_url,
                data={
                    "label": endpoint.label,
                    "url": endpoint.url,
                    "enabled": "on",
                    "events": ["election_opened"],
                    "channel": "",
                    "username": "",
                    "icon_url": "",
                    "text": "",
                    "attachments": "",
                    "props": "",
                    "priority_level": "standard",
                    "priority_requested_ack": "on",
                    "priority_persistent_notifications": "on",
                    "_save": "Save",
                },
                follow=True,
            )

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Requested acknowledgement is only available for Important or Urgent.")
        self.assertContains(result, "Persistent notifications are only available for Urgent.")
        endpoint.refresh_from_db()
        self.assertIsNone(endpoint.priority)

    @override_settings(MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=5)
    def test_test_notification_view_success(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()
        response = Mock()
        response.status_code = 200
        response.text = "ok"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()),
            patch("core.mattermost_webhooks.requests.post", return_value=response),
            self.assertLogs("core.mattermost_webhooks", level="INFO") as captured,
        ):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.post(url, data={"post": "1"}, follow=True)

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Test notification sent successfully")
        self.assertTrue(any(record.getMessage().startswith("mattermost.admin_test_start") for record in captured.records))
        success_records = [record for record in captured.records if record.getMessage().startswith("mattermost.admin_test_success")]
        self.assertTrue(success_records)
        success_message = success_records[0].getMessage()
        self.assertIn("ref=", success_message)
        self.assertIn(f"endpoint_id={endpoint.pk}", success_message)
        self.assertIn("duration_ms=", success_message)

        for record in captured.records:
            for value in record.__dict__.values():
                self.assertNotIn(endpoint.url, str(value))

    @override_settings(MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=5)
    def test_test_notification_view_http_failure(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()
        response = Mock()
        response.status_code = 500
        response.text = f"server failed while posting to {endpoint.url}"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()),
            patch("core.mattermost_webhooks.requests.post", return_value=response),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.post(url, data={"post": "1"}, follow=True)

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Test failed: HTTP 500")
        self.assertContains(result, "Response:")
        self.assertContains(result, "server failed while posting to [redacted-url]")
        self.assertContains(result, "Ref:")
        message_texts = [str(message) for message in result.context["messages"]]
        self.assertTrue(any("Test failed: HTTP 500" in message for message in message_texts))
        self.assertFalse(any(endpoint.url in message for message in message_texts))
        self.assertTrue(any(record.getMessage().startswith("mattermost.admin_test_failed") for record in captured.records))
        self.assertTrue(any(getattr(record, "endpoint_id", None) == endpoint.pk for record in captured.records))
        self.assertTrue(any(getattr(record, "status_code", None) == 500 for record in captured.records))
        self.assertTrue(any("ref=" in record.getMessage() for record in captured.records))
        self.assertTrue(any("response_excerpt=server failed while posting to [redacted-url]" in record.getMessage() for record in captured.records))
        self.assertTrue(any("payload_json=" in record.getMessage() for record in captured.records))
        self.assertTrue(any("duration_ms=" in record.getMessage() for record in captured.records))

        failed_records = [record for record in captured.records if record.getMessage().startswith("mattermost.admin_test_failed")]
        self.assertTrue(failed_records)
        payload_json = getattr(failed_records[0], "payload_json", None)
        self.assertIsNotNone(payload_json)
        parsed_payload = json.loads(str(payload_json))
        self.assertIn("text", parsed_payload)
        self.assertTrue(str(parsed_payload["text"]).startswith("[TEST] "))

        for record in captured.records:
            for value in record.__dict__.values():
                self.assertNotIn(endpoint.url, str(value))

    @override_settings(MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=5)
    def test_test_notification_view_exception_failure_does_not_show_url(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()),
            patch(
                "core.mattermost_webhooks.requests.post",
                side_effect=requests.exceptions.ConnectionError(f"Failed to connect to {endpoint.url}"),
            ),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.post(url, data={"post": "1"}, follow=True)

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Test failed:")
        self.assertContains(result, "Ref:")
        message_texts = [str(message) for message in result.context["messages"]]
        self.assertTrue(any("Test failed:" in message for message in message_texts))
        self.assertFalse(any(endpoint.url in message for message in message_texts))
        self.assertTrue(any(record.getMessage().startswith("mattermost.admin_test_failed") for record in captured.records))
        self.assertTrue(any(getattr(record, "error", None) == "connection_error" for record in captured.records))
        self.assertTrue(any("ref=" in record.getMessage() for record in captured.records))
        self.assertTrue(any("duration_ms=" in record.getMessage() for record in captured.records))

        for record in captured.records:
            for value in record.__dict__.values():
                self.assertNotIn(endpoint.url, str(value))

    def test_test_notification_view_get_redirects_with_warning(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.get(url, follow=True)

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Test notifications must be sent with POST")

    def test_test_notification_view_payload_is_labeled_as_test(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()),
            patch(
                "core.mattermost_webhooks.post_mattermost_payload",
                return_value=(True, 200, "", None, None),
            ) as post_mock,
        ):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.post(url, data={"post": "1"}, follow=True)

        self.assertEqual(result.status_code, 200)
        payload = post_mock.call_args.args[1]
        self.assertIn("text", payload)

        text = str(payload["text"])
        self.assertTrue(text.startswith("[TEST] "))
        self.assertIn(str(endpoint.label), text)

        timestamp = text.rsplit(" - ", 1)[1]
        parsed = datetime.datetime.fromisoformat(timestamp)
        self.assertIsNotNone(parsed.tzinfo)

    def test_test_notification_view_payload_applies_endpoint_overrides(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = MattermostWebhookEndpoint.objects.create(
            label="Ops endpoint",
            url="https://hooks.example.invalid/test",
            enabled=True,
            events=["election_opened"],
            text="[TEST configured] {{ actor }} ref={{ test_ref }}",
            attachments=[
                {
                    "title": "{{ endpoint_label }}",
                    "text": "test ref {{ test_ref }}",
                }
            ],
            props={"source": "admin-test"},
            priority={"priority": "urgent", "requested_ack": True, "persistent_notifications": True},
            channel="ops-alerts",
            username="Ops Bot",
            icon_url="https://example.invalid/icon.png",
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()),
            patch(
                "core.mattermost_webhooks.post_mattermost_payload",
                return_value=(True, 200, "", None, None),
            ) as post_mock,
        ):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.post(url, data={"post": "1"}, follow=True)

        self.assertEqual(result.status_code, 200)
        payload = post_mock.call_args.args[1]
        self.assertTrue(str(payload["text"]).startswith("[TEST configured]"))
        self.assertEqual(payload["priority"], {"priority": "urgent", "requested_ack": True, "persistent_notifications": True})
        self.assertEqual(payload["channel"], "ops-alerts")
        self.assertEqual(payload["username"], "Ops Bot")
        self.assertEqual(payload["icon_url"], "https://example.invalid/icon.png")
        self.assertEqual(payload["props"], {"source": "admin-test"})
        self.assertEqual(payload["attachments"][0]["title"], "Ops endpoint")

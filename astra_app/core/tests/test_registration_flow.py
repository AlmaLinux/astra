
import re
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import quote, unquote

import requests
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from python_freeipa import exceptions

from core import views_registration
from core.freeipa.exceptions import FreeIPAUnavailableError
from core.freeipa.user import FreeIPAUser
from core.logging_extras import exception_log_fields
from core.models import AccountInvitation
from core.tests.utils_test_data import ensure_email_templates
from core.tokens import make_registration_activation_token, read_registration_activation_token
from core.views_auth import PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY

REGISTRATION_TEMPORARILY_UNAVAILABLE_MESSAGE = (
    "Registration is temporarily unavailable. Please try again in a few minutes. "
    "If the problem continues, contact support."
)
REGISTRATION_ACTIVATION_TEMPORARY_VERIFICATION_FAILURE_MESSAGE = (
    "We could not verify your registration right now. Please try the activation link again in a few minutes."
)
REGISTRATION_CONFIRM_TEMPORARY_VERIFICATION_FAILURE_MESSAGE = (
    "We could not verify your registration right now. Please try again in a few minutes or use the link from your email again."
)
REGISTRATION_ACTIVATION_FOLLOW_UP_WARNING_MESSAGE = (
    "Your account may already be ready. Please try signing in. If you cannot sign in yet, wait a few minutes and try again."
)


class RegistrationFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        ensure_email_templates()

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_get_invite_prefill_uses_invitation_loader(self) -> None:
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        token = str(invitation.invitation_token)

        with patch(
            "core.views_registration.load_account_invitation_from_token",
            return_value=invitation,
        ) as load_mock:
            resp = client.get(f"/register/?invite={token}")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["form"].initial.get("invitation_token"), token)
        load_mock.assert_called_once_with(token)

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_get_invite_stashes_pending_token_in_session(self) -> None:
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        token = str(invitation.invitation_token)

        response = client.get(f"/register/?invite={token}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.session.get(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY), token)

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_get_uses_session_pending_invite_when_query_absent(self) -> None:
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        token = str(invitation.invitation_token)

        first_response = client.get(f"/register/?invite={token}")
        self.assertEqual(first_response.status_code, 200)

        second_response = client.get("/register/")

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.context["form"].initial.get("invitation_token"), token)

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_get_invalid_invite_token_is_not_stashed_or_used(self) -> None:
        client = Client()

        invalid_query_response = client.get("/register/?invite=invalid-token")

        self.assertEqual(invalid_query_response.status_code, 200)
        self.assertNotIn(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY, client.session)
        self.assertEqual(invalid_query_response.context["form"].initial.get("invitation_token"), "")

        session = client.session
        session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = "invalid-token"
        session.save()

        fallback_response = client.get("/register/")

        self.assertEqual(fallback_response.status_code, 200)
        self.assertEqual(fallback_response.context["form"].initial.get("invitation_token"), "")

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_confirm_resend_preserves_session_invitation_token_in_activation_payload(self) -> None:
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        invitation_token = str(invitation.invitation_token)

        session = client.session
        session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = invitation_token
        session.save()

        ipa_client = SimpleNamespace()
        ipa_client.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
            }
        }

        with (
            patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client),
            patch("core.views_registration.queue_templated_email", autospec=True) as queue_email_mock,
        ):
            response = client.post(
                "/register/confirm/?username=alice",
                data={"username": "alice"},
                follow=False,
            )

        self.assertEqual(response.status_code, 302)

        activate_url = queue_email_mock.call_args.kwargs.get("context", {}).get("activate_url", "")
        token_match = re.search(r"token=([^\s&]+)", activate_url)
        self.assertIsNotNone(token_match)
        assert token_match is not None
        activation_payload = read_registration_activation_token(unquote(token_match.group(1)))
        self.assertEqual(activation_payload.get("i"), invitation_token)

    @override_settings(REGISTRATION_OPEN=True)
    def test_confirm_unknown_username_renders_same_template_without_redirect(self) -> None:
        client = Client()

        def _raise_not_found(*args, **kwargs):
            _ = args, kwargs
            raise exceptions.NotFound

        ipa_client = SimpleNamespace(stageuser_show=_raise_not_found)
        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            resp = client.get("/register/confirm/?username=ghost-user")

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "core/register_confirm.html")
        self.assertEqual(resp.context["username"], "ghost-user")
        self.assertIsNone(resp.context["email"])

    def test_load_registration_stage_data_retries_service_login_unauthorized(self) -> None:
        recovered_client = SimpleNamespace()
        recovered_client.stageuser_show = Mock(
            return_value={
                "result": {
                    "uid": ["alice"],
                    "mail": ["alice@example.com"],
                }
            }
        )

        with patch(
            "core.views_registration.FreeIPAUser.get_client",
            autospec=True,
            side_effect=[exceptions.Unauthorized(), recovered_client],
        ) as get_client_mock:
            stage_data = views_registration._load_registration_stage_data("alice")

        self.assertEqual(stage_data, {"uid": ["alice"], "mail": ["alice@example.com"]})
        self.assertEqual(get_client_mock.call_count, 2)

    def test_load_registration_stage_data_retries_stage_lookup_unauthorized(self) -> None:
        first_client = SimpleNamespace()
        second_client = SimpleNamespace()
        first_client.stageuser_show = Mock(side_effect=exceptions.Unauthorized())
        second_client.stageuser_show = Mock(
            return_value={
                "result": {
                    "uid": ["alice"],
                    "mail": ["alice@example.com"],
                }
            }
        )

        with patch(
            "core.views_registration.FreeIPAUser.get_client",
            autospec=True,
            side_effect=[first_client, second_client],
        ) as get_client_mock:
            stage_data = views_registration._load_registration_stage_data("alice")

        self.assertEqual(stage_data, {"uid": ["alice"], "mail": ["alice@example.com"]})
        self.assertEqual(get_client_mock.call_count, 2)

    @override_settings(REGISTRATION_OPEN=True)
    def test_confirm_get_service_login_unauthorized_redirects_with_temporary_verification_message(self) -> None:
        client = Client()
        lookup_error = exceptions.Unauthorized("confirm lookup boom")

        with (
            patch(
                "core.views_registration.FreeIPAUser.get_client",
                autospec=True,
                side_effect=lookup_error,
            ),
            patch("core.views_registration.logger.exception") as exception_mock,
        ):
            response = client.get("/register/confirm/?username=alice", follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/register/")
        exception_mock.assert_called_once()
        self.assertEqual(
            exception_mock.call_args.args,
            (
                "Registration confirm verification failed username=%s error_class=%s error=%s",
                "alice",
                "Unauthorized",
                lookup_error,
            ),
        )
        self.assertEqual(
            exception_mock.call_args.kwargs["extra"],
            {
                "event": "astra.registration.confirm.verification_failed",
                "component": "registration",
                "outcome": "error",
                "endpoint": "register-confirm",
                "username": "alice",
                "error_type": "Unauthorized",
                "error_message": "confirm lookup boom",
                "error_repr": "Unauthorized('confirm lookup boom')",
                "error_args": "('confirm lookup boom',)",
            },
        )

        follow_response = client.get(response["Location"])
        messages_list = [message.message for message in get_messages(follow_response.wsgi_request)]
        self.assertIn(REGISTRATION_CONFIRM_TEMPORARY_VERIFICATION_FAILURE_MESSAGE, messages_list)

    @override_settings(REGISTRATION_OPEN=True)
    def test_confirm_get_freeipa_unavailable_reaches_503_middleware(self) -> None:
        client = Client(raise_request_exception=False)

        with patch(
            "core.views_registration.FreeIPAUser.get_client",
            autospec=True,
            side_effect=FreeIPAUnavailableError("confirm service unavailable"),
        ):
            response = client.get("/register/confirm/?username=alice", follow=False)

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "temporarily unavailable", status_code=503)

    def test_registration_email_template_exists(self):
        from post_office.models import EmailTemplate

        self.assertTrue(EmailTemplate.objects.filter(name="registration-email-validation").exists())

    def test_account_invite_email_template_exists(self):
        from post_office.models import EmailTemplate

        self.assertTrue(EmailTemplate.objects.filter(name="account-invite").exists())

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_get_renders(self):
        client = Client()
        resp = client.get("/register/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Create account")

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_get_renders_vue_shell_contract(self) -> None:
        response = self.client.get(reverse("register"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-register-root=""')
        self.assertContains(response, f'data-register-api-url="{reverse("api-register-detail")}"')
        self.assertContains(response, f'data-register-login-url="{reverse("login")}"')
        self.assertContains(response, f'data-register-register-url="{reverse("register")}"')
        self.assertContains(response, f'data-register-submit-url="{reverse("register")}"')
        self.assertNotContains(response, 'id="id_username"')

    @override_settings(REGISTRATION_OPEN=True)
    def test_register_detail_api_returns_data_only_payload(self) -> None:
        response = self.client.get(reverse("api-register-detail"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["registration_open"])
        self.assertFalse(payload["form"]["is_bound"])
        self.assertEqual(payload["form"]["fields"][0]["name"], "username")
        self.assertEqual(payload["form"]["fields"][-1]["name"], "invitation_token")
        self.assertNotIn("login_url", payload)
        self.assertNotIn("submit_url", payload)
        self.assertNotIn("step_label", payload)

    @override_settings(REGISTRATION_OPEN=True)
    def test_registration_detail_endpoints_are_public_but_neighboring_register_api_paths_still_require_auth(self) -> None:
        token = make_registration_activation_token({"u": "alice", "e": "alice@example.com"})
        ipa_client = SimpleNamespace()
        ipa_client.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
            }
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            register_response = self.client.get(reverse("api-register-detail"), HTTP_ACCEPT="application/json")
            confirm_response = self.client.get(
                f'{reverse("api-register-confirm-detail")}?username=alice',
                HTTP_ACCEPT="application/json",
            )
            activate_response = self.client.get(
                f'{reverse("api-register-activate-detail")}?token={token}',
                HTTP_ACCEPT="application/json",
            )

        blocked_response = self.client.get("/api/v1/register/probe", HTTP_ACCEPT="application/json")

        self.assertEqual(register_response.status_code, 200)
        self.assertEqual(confirm_response.status_code, 200)
        self.assertEqual(activate_response.status_code, 200)
        self.assertEqual(blocked_response.status_code, 403)
        self.assertEqual(blocked_response.json(), {"ok": False, "error": "Authentication required."})

    @override_settings(REGISTRATION_OPEN=True)
    def test_confirm_get_renders_vue_shell_contract(self) -> None:
        ipa_client = SimpleNamespace()
        ipa_client.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
            }
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            response = self.client.get(f'{reverse("register-confirm")}?username=alice')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-register-confirm-root=""')
        self.assertContains(
            response,
            f'data-register-confirm-api-url="{reverse("api-register-confirm-detail")}?username=alice"',
        )
        self.assertContains(response, f'data-register-confirm-submit-url="{reverse("register-confirm")}?username=alice"')
        self.assertContains(response, f'data-register-confirm-login-url="{reverse("login")}"')
        self.assertNotContains(response, 'type="submit" class="btn btn-secondary"')

    @override_settings(REGISTRATION_OPEN=True)
    def test_confirm_detail_api_returns_data_only_payload(self) -> None:
        ipa_client = SimpleNamespace()
        ipa_client.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
            }
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            response = self.client.get(f'{reverse("api-register-confirm-detail")}?username=alice')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["username"], "alice")
        self.assertEqual(payload["email"], "alice@example.com")
        self.assertFalse(payload["form"]["is_bound"])
        self.assertEqual(payload["form"]["fields"][0]["name"], "username")
        self.assertNotIn("login_url", payload)
        self.assertNotIn("submit_url", payload)

    @override_settings(REGISTRATION_OPEN=True)
    def test_activate_get_renders_vue_shell_contract(self) -> None:
        token = make_registration_activation_token({"u": "alice", "e": "alice@example.com"})
        ipa_client = SimpleNamespace()
        ipa_client.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
            }
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            response = self.client.get(f'{reverse("register-activate")}?token={token}')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-register-activate-root=""')
        self.assertContains(
            response,
            f'data-register-activate-api-url="{reverse("api-register-activate-detail")}?token={quote(token)}"',
        )
        self.assertContains(
            response,
            f'data-register-activate-submit-url="{reverse("register-activate")}?token={token}"',
        )
        self.assertContains(response, f'data-register-activate-start-over-url="{reverse("register")}"')
        self.assertNotContains(response, 'id="id_password"')

    @override_settings(REGISTRATION_OPEN=True)
    def test_activate_detail_api_returns_data_only_payload(self) -> None:
        token = make_registration_activation_token({"u": "alice", "e": "alice@example.com"})
        ipa_client = SimpleNamespace()
        ipa_client.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
            }
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            response = self.client.get(f'{reverse("api-register-activate-detail")}?token={token}')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["username"], "alice")
        self.assertFalse(payload["form"]["is_bound"])
        self.assertEqual(payload["form"]["fields"][0]["name"], "password")
        self.assertEqual(payload["form"]["fields"][1]["name"], "password_confirm")
        self.assertNotIn("start_over_url", payload)
        self.assertNotIn("submit_url", payload)

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_register_post_creates_stage_user_and_sends_email(self):
        client = Client()

        ipa_client = SimpleNamespace()
        ipa_client.stageuser_add = lambda *args, **kwargs: {
            "result": {"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"], "mail": ["alice@example.com"]}
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            with patch("core.views_registration.queue_templated_email", autospec=True) as post_office_send_mock:
                post_office_send_mock.return_value = None

                resp = client.post(
                    "/register/",
                    data={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "email": "alice@example.com",
                        "over_16": "on",
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("/register/confirm"))
        # Registration email must use django-post-office's EmailTemplate feature
        self.assertEqual(post_office_send_mock.call_count, 1)
        self.assertEqual(post_office_send_mock.call_args.kwargs.get("template_name"), "registration-email-validation")

        ctx = post_office_send_mock.call_args.kwargs.get("context") or {}
        self.assertEqual(ctx.get("username"), "alice")
        self.assertEqual(ctx.get("first_name"), "Alice")
        self.assertEqual(ctx.get("last_name"), "User")
        self.assertIn("full_name", ctx)
        self.assertNotIn("displayname", ctx)

    @override_settings(REGISTRATION_OPEN=True, DEBUG=False)
    def test_register_post_service_login_unauthorized_shows_service_message_and_logs_metadata(self) -> None:
        client = Client()
        unauthorized_error = exceptions.Unauthorized()

        with (
            patch(
                "core.views_registration.FreeIPAUser.get_client",
                autospec=True,
                side_effect=unauthorized_error,
            ),
            patch("core.views_registration.logger.warning") as warning_mock,
        ):
            response = client.post(
                "/register/",
                data={
                    "username": "alice",
                    "first_name": "Alice",
                    "last_name": "User",
                    "email": "alice@example.com",
                    "over_16": "on",
                },
                HTTP_X_REQUEST_ID="req-169-service-login",
                follow=False,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, REGISTRATION_TEMPORARILY_UNAVAILABLE_MESSAGE)
        self.assertNotContains(response, "An error occurred while creating the account, please try again.")
        warning_mock.assert_called_once()
        log_extra = warning_mock.call_args.kwargs["extra"]
        expected_error_fields = exception_log_fields(unauthorized_error)
        self.assertEqual(log_extra["freeipa_phase"], "service_login")
        self.assertEqual(log_extra["freeipa_exception_class"], "Unauthorized")
        self.assertEqual(log_extra["error_type"], expected_error_fields["error_type"])
        self.assertEqual(log_extra["error_message"], expected_error_fields["error_message"])
        self.assertEqual(log_extra["error_repr"], expected_error_fields["error_repr"])
        self.assertEqual(log_extra["error_args"], expected_error_fields["error_args"])
        self.assertTrue(log_extra["retry_attempted"])
        self.assertEqual(log_extra["retry_outcome"], "failed")
        self.assertEqual(log_extra["request_id"], "req-169-service-login")
        self.assertNotIn("alice@example.com", str(log_extra).lower())

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_register_post_service_login_unauthorized_recovery_logs_service_login_phase(self) -> None:
        client = Client()

        recovered_client = SimpleNamespace()
        recovered_client.stageuser_add = Mock(
            return_value={
                "result": {
                    "uid": ["alice"],
                    "givenname": ["Alice"],
                    "sn": ["User"],
                    "mail": ["alice@example.com"],
                }
            }
        )

        with (
            patch(
                "core.views_registration.FreeIPAUser.get_client",
                autospec=True,
                side_effect=[exceptions.Unauthorized(), recovered_client],
            ) as get_client_mock,
            patch("core.views_registration._send_registration_email", autospec=True) as send_email_mock,
            patch("core.views_registration.logger.info") as info_mock,
            patch("core.views_registration.logger.warning") as warning_mock,
        ):
            response = client.post(
                "/register/",
                data={
                    "username": "alice",
                    "first_name": "Alice",
                    "last_name": "User",
                    "email": "alice@example.com",
                    "over_16": "on",
                },
                HTTP_X_REQUEST_ID="req-169-service-login-recovered",
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/register/confirm"))
        self.assertEqual(get_client_mock.call_count, 2)
        send_email_mock.assert_called_once()
        warning_mock.assert_not_called()
        info_mock.assert_called_once()
        log_extra = info_mock.call_args.kwargs["extra"]
        self.assertEqual(log_extra["freeipa_phase"], "service_login")
        self.assertEqual(log_extra["freeipa_exception_class"], "Unauthorized")
        self.assertTrue(log_extra["retry_attempted"])
        self.assertEqual(log_extra["retry_outcome"], "recovered")
        self.assertEqual(log_extra["request_id"], "req-169-service-login-recovered")
        self.assertNotIn("alice@example.com", str(log_extra).lower())

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_register_post_stageuser_add_unauthorized_retries_once_and_logs_recovery(self) -> None:
        client = Client()

        first_client = SimpleNamespace()
        second_client = SimpleNamespace()
        first_client.stageuser_add = Mock(side_effect=exceptions.Unauthorized())
        second_client.stageuser_add = Mock(
            return_value={
                "result": {
                    "uid": ["alice"],
                    "givenname": ["Alice"],
                    "sn": ["User"],
                    "mail": ["alice@example.com"],
                }
            }
        )

        with (
            patch(
                "core.views_registration.FreeIPAUser.get_client",
                autospec=True,
                side_effect=[first_client, second_client],
            ) as get_client_mock,
            patch("core.views_registration._send_registration_email", autospec=True) as send_email_mock,
            patch("core.views_registration.logger.info") as info_mock,
            patch("core.views_registration.logger.warning") as warning_mock,
        ):
            response = client.post(
                "/register/",
                data={
                    "username": "alice",
                    "first_name": "Alice",
                    "last_name": "User",
                    "email": "alice@example.com",
                    "over_16": "on",
                },
                HTTP_X_REQUEST_ID="req-169-stageuser-recovered",
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/register/confirm"))
        self.assertEqual(get_client_mock.call_count, 2)
        send_email_mock.assert_called_once()
        warning_mock.assert_not_called()
        info_mock.assert_called_once()
        log_extra = info_mock.call_args.kwargs["extra"]
        self.assertEqual(log_extra["freeipa_phase"], "stageuser_add")
        self.assertEqual(log_extra["freeipa_exception_class"], "Unauthorized")
        self.assertTrue(log_extra["retry_attempted"])
        self.assertEqual(log_extra["retry_outcome"], "recovered")
        self.assertEqual(log_extra["request_id"], "req-169-stageuser-recovered")
        self.assertNotIn("alice@example.com", str(log_extra).lower())

    @override_settings(REGISTRATION_OPEN=True, DEBUG=False)
    def test_register_post_stageuser_add_unauthorized_after_retry_shows_service_message(self) -> None:
        client = Client()

        first_client = SimpleNamespace()
        second_client = SimpleNamespace()
        first_unauthorized_error = exceptions.Unauthorized()
        second_unauthorized_error = exceptions.Unauthorized()
        first_client.stageuser_add = Mock(side_effect=first_unauthorized_error)
        second_client.stageuser_add = Mock(side_effect=second_unauthorized_error)

        with (
            patch(
                "core.views_registration.FreeIPAUser.get_client",
                autospec=True,
                side_effect=[first_client, second_client],
            ) as get_client_mock,
            patch("core.views_registration._send_registration_email", autospec=True) as send_email_mock,
            patch("core.views_registration.logger.warning") as warning_mock,
        ):
            response = client.post(
                "/register/",
                data={
                    "username": "alice",
                    "first_name": "Alice",
                    "last_name": "User",
                    "email": "alice@example.com",
                    "over_16": "on",
                },
                HTTP_X_REQUEST_ID="req-169-stageuser-failed",
                follow=False,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_client_mock.call_count, 2)
        send_email_mock.assert_not_called()
        self.assertContains(response, REGISTRATION_TEMPORARILY_UNAVAILABLE_MESSAGE)
        self.assertNotContains(response, "An error occurred while creating the account, please try again.")
        warning_mock.assert_called_once()
        log_extra = warning_mock.call_args.kwargs["extra"]
        expected_error_fields = exception_log_fields(second_unauthorized_error)
        self.assertEqual(log_extra["freeipa_phase"], "stageuser_add")
        self.assertEqual(log_extra["freeipa_exception_class"], "Unauthorized")
        self.assertEqual(log_extra["error_type"], expected_error_fields["error_type"])
        self.assertEqual(log_extra["error_message"], expected_error_fields["error_message"])
        self.assertEqual(log_extra["error_repr"], expected_error_fields["error_repr"])
        self.assertEqual(log_extra["error_args"], expected_error_fields["error_args"])
        self.assertTrue(log_extra["retry_attempted"])
        self.assertEqual(log_extra["retry_outcome"], "failed")
        self.assertEqual(log_extra["request_id"], "req-169-stageuser-failed")
        self.assertNotIn("alice@example.com", str(log_extra).lower())

    @override_settings(
        REGISTRATION_OPEN=True,
        DEFAULT_FROM_EMAIL="noreply@example.com",
        EMAIL_VALIDATION_TOKEN_TTL_SECONDS=3600,
    )
    def test_register_email_context_includes_full_utc_date_and_time_for_expiry(self) -> None:
        client = Client()

        ipa_client = SimpleNamespace()
        ipa_client.stageuser_add = lambda *args, **kwargs: {
            "result": {"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"], "mail": ["alice@example.com"]}
        }

        with (
            patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client),
            patch("core.views_registration.queue_templated_email", autospec=True) as post_office_send_mock,
        ):
            response = client.post(
                "/register/",
                data={
                    "username": "alice",
                    "first_name": "Alice",
                    "last_name": "User",
                    "email": "alice@example.com",
                    "over_16": "on",
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        ctx = post_office_send_mock.call_args.kwargs.get("context") or {}
        self.assertRegex(str(ctx.get("valid_until_utc", "")), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$")

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_register_post_requires_over_16_checkbox(self):
        client = Client()

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True) as get_client_mock:
            with patch("core.views_registration.queue_templated_email", autospec=True) as post_office_send_mock:
                resp = client.post(
                    "/register/",
                    data={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "email": "alice@example.com",
                        # Missing: over_16
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "You must be over 16 years old to create an account")
        get_client_mock.assert_not_called()
        post_office_send_mock.assert_not_called()

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_register_post_links_invitation_by_token_on_activate(self):
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        token = str(invitation.invitation_token)

        ipa_client = SimpleNamespace()
        ipa_client.stageuser_add = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["different@example.com"],
            }
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            with patch("core.views_registration.queue_templated_email", autospec=True) as post_office_send_mock:
                resp = client.post(
                    f"/register/?invite={token}",
                    data={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "email": "different@example.com",
                        "over_16": "on",
                        "invitation_token": token,
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        activate_url = post_office_send_mock.call_args.kwargs.get("context", {}).get("activate_url", "")
        token_match = re.search(r"token=([^\s&]+)", activate_url)
        self.assertIsNotNone(token_match)
        assert token_match is not None
        activation_token = token_match.group(1)

        ipa_client2 = SimpleNamespace()
        ipa_client2.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["different@example.com"],
            }
        }
        ipa_client2.stageuser_activate = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}
        ipa_client2.user_mod = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client2):
            with patch("core.views_registration._build_freeipa_client", autospec=True) as mocked_build:
                client_meta = mocked_build.return_value
                client_meta.change_password.return_value = None

                activation_post = client.post(
                    f"/register/activate/?token={activation_token}",
                    data={"password": "S3curePassword!", "password_confirm": "S3curePassword!"},
                    follow=False,
                )

        self.assertEqual(activation_post.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertIn("alice", invitation.freeipa_matched_usernames)

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_register_activate_with_org_linked_invite_keeps_invitation_pending(self) -> None:
        from core.models import Organization

        client = Client()

        organization = Organization.objects.create(
            name="Pending Claim Org",
            business_contact_email="invitee@example.com",
        )
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
            organization=organization,
        )
        token = str(invitation.invitation_token)

        ipa_client = SimpleNamespace()
        ipa_client.stageuser_add = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["invitee@example.com"],
            }
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            with patch("core.views_registration.queue_templated_email", autospec=True) as post_office_send_mock:
                resp = client.post(
                    f"/register/?invite={token}",
                    data={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "email": "invitee@example.com",
                        "over_16": "on",
                        "invitation_token": token,
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        activate_url = post_office_send_mock.call_args.kwargs.get("context", {}).get("activate_url", "")
        token_match = re.search(r"token=([^\s&]+)", activate_url)
        self.assertIsNotNone(token_match)
        assert token_match is not None
        activation_token = token_match.group(1)

        ipa_client2 = SimpleNamespace()
        ipa_client2.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["invitee@example.com"],
            }
        }
        ipa_client2.stageuser_activate = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}
        ipa_client2.user_mod = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client2):
            with patch("core.views_registration._build_freeipa_client", autospec=True) as mocked_build:
                client_meta = mocked_build.return_value
                client_meta.change_password.return_value = None

                activation_post = client.post(
                    f"/register/activate/?token={activation_token}",
                    data={"password": "S3curePassword!", "password_confirm": "S3curePassword!"},
                    follow=False,
                )

        self.assertEqual(activation_post.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        self.assertIn("alice", invitation.freeipa_matched_usernames)

    @override_settings(REGISTRATION_OPEN=True)
    def test_activate_invite_flow_uses_reconcile_helpers(self) -> None:
        from core.tokens import make_registration_activation_token

        client = Client()

        invitation = AccountInvitation.objects.create(
            email="alice@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        activation_token = make_registration_activation_token(
            {
                "p": "registration-activate",
                "u": "alice",
                "e": "alice@example.com",
                "i": str(invitation.invitation_token),
            }
        )

        session = client.session
        session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = str(invitation.invitation_token)
        session.save()

        ipa_client = SimpleNamespace()
        ipa_client.stageuser_show = lambda *args, **kwargs: {
            "result": {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
            }
        }
        ipa_client.stageuser_activate = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}
        ipa_client.user_mod = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}

        with (
            patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client),
            patch("core.views_registration._build_freeipa_client", autospec=True) as mocked_build,
            patch(
                "core.views_registration.load_account_invitation_from_token",
                return_value=invitation,
            ) as load_mock,
            patch("core.views_registration.reconcile_account_invitation_for_username") as reconcile_mock,
        ):
            mocked_build.return_value.change_password.return_value = None
            activation_post = client.post(
                f"/register/activate/?token={activation_token}",
                data={"password": "S3curePassword!", "password_confirm": "S3curePassword!"},
                follow=False,
            )

        self.assertEqual(activation_post.status_code, 302)
        load_mock.assert_called_once_with(str(invitation.invitation_token))
        self.assertEqual(reconcile_mock.call_count, 1)
        self.assertEqual(reconcile_mock.call_args.kwargs["invitation"], invitation)
        self.assertEqual(reconcile_mock.call_args.kwargs["username"], "alice")
        self.assertNotIn(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY, client.session)

    @override_settings(REGISTRATION_OPEN=True, DEFAULT_FROM_EMAIL="noreply@example.com")
    def test_activate_flow_happy_path(self):
        client = Client()

        # Arrange: register to generate an email that contains the token.
        ipa_client = SimpleNamespace()
        ipa_client.stageuser_add = lambda *args, **kwargs: {
            "result": {"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"], "mail": ["alice@example.com"]}
        }

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client):
            with patch("core.views_registration.queue_templated_email", autospec=True) as post_office_send_mock:
                post_office_send_mock.return_value = None

                resp = client.post(
                    "/register/",
                    data={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "email": "alice@example.com",
                        "over_16": "on",
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        activate_url = post_office_send_mock.call_args.kwargs.get("context", {}).get("activate_url", "")
        token_match = re.search(r"token=([^\s&]+)", activate_url)
        self.assertIsNotNone(token_match)
        assert token_match is not None
        token = token_match.group(1)

        # Activation GET renders a password form.
        ipa_client2 = SimpleNamespace()
        ipa_client2.stageuser_show = lambda *args, **kwargs: {
            "result": {"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"], "mail": ["alice@example.com"]}
        }
        ipa_client2.stageuser_activate = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}
        ipa_client2.user_mod = lambda *args, **kwargs: {"result": {"uid": ["alice"]}}

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client2):
            activation_get = client.get(f"/register/activate/?token={token}")
        self.assertEqual(activation_get.status_code, 200)
        self.assertContains(activation_get, 'data-register-activate-root=""')
        self.assertContains(
            activation_get,
            f'data-register-activate-api-url="{reverse("api-register-activate-detail")}?token={token}"',
        )
        self.assertNotContains(activation_get, 'id="id_password"')

        # Activation POST activates stage user and sets password.

        with patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=ipa_client2):
            with patch("core.views_registration._build_freeipa_client", autospec=True) as mocked_build:
                client_meta = mocked_build.return_value
                client_meta.change_password.return_value = None

                activation_post = client.post(
                    f"/register/activate/?token={token}",
                    data={"password": "S3curePassword!", "password_confirm": "S3curePassword!"},
                    follow=False,
                )

        self.assertEqual(activation_post.status_code, 302)
        self.assertEqual(activation_post["Location"], "/login/")

        # Success message is stored in the session.
        follow = client.get(activation_post["Location"])
        msgs = [m.message for m in get_messages(follow.wsgi_request)]
        self.assertTrue(any("account" in m.lower() and "created" in m.lower() for m in msgs))

    def test_activate_post_stage_lookup_unauthorized_redirects_with_temporary_verification_message(self) -> None:
        client = Client()
        activation_token = make_registration_activation_token({"u": "alice", "e": "alice@example.com"})
        lookup_error = exceptions.Unauthorized("activate lookup boom")

        with (
            patch(
                "core.views_registration.FreeIPAUser.get_client",
                autospec=True,
                side_effect=lookup_error,
            ),
            patch("core.views_registration.logger.exception") as exception_mock,
        ):
            response = client.post(
                f"/register/activate/?token={activation_token}",
                data={"password": "S3curePassword!", "password_confirm": "S3curePassword!"},
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/register/")
        exception_mock.assert_called_once()
        self.assertEqual(
            exception_mock.call_args.args,
            (
                "Registration activation verification failed username=%s error_class=%s error=%s",
                "alice",
                "Unauthorized",
                lookup_error,
            ),
        )
        self.assertEqual(
            exception_mock.call_args.kwargs["extra"],
            {
                "event": "astra.registration.activate.verification_failed",
                "component": "registration",
                "outcome": "error",
                "endpoint": "register-activate",
                "username": "alice",
                "error_type": "Unauthorized",
                "error_message": "activate lookup boom",
                "error_repr": "Unauthorized('activate lookup boom')",
                "error_args": "('activate lookup boom',)",
            },
        )

        follow_response = client.get(response["Location"])
        messages_list = [message.message for message in get_messages(follow_response.wsgi_request)]
        self.assertIn(REGISTRATION_ACTIVATION_TEMPORARY_VERIFICATION_FAILURE_MESSAGE, messages_list)

    def test_activate_post_connection_error_reaches_503_middleware(self) -> None:
        client = Client(raise_request_exception=False)
        activation_token = make_registration_activation_token({"u": "alice", "e": "alice@example.com"})

        with patch(
            "core.views_registration.FreeIPAUser.get_client",
            autospec=True,
            side_effect=requests.exceptions.ConnectionError("activate network down"),
        ):
            response = client.post(
                f"/register/activate/?token={activation_token}",
                data={"password": "S3curePassword!", "password_confirm": "S3curePassword!"},
                follow=False,
            )

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "temporarily unavailable", status_code=503)

    def test_activate_post_reconcile_failure_preserves_pending_invitation_for_login_recovery(self) -> None:
        client = Client()
        invitation = AccountInvitation.objects.create(
            email="alice@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        invitation_token = str(invitation.invitation_token)
        activation_token = make_registration_activation_token(
            {"u": "alice", "e": "alice@example.com", "i": invitation_token}
        )

        session = client.session
        session[PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY] = invitation_token
        session.save()

        stage_client = SimpleNamespace(
            stageuser_show=lambda *args, **kwargs: {
                "result": {
                    "uid": ["alice"],
                    "mail": ["alice@example.com"],
                }
            },
            stageuser_activate=lambda *args, **kwargs: {"result": {"uid": ["alice"]}},
            user_mod=lambda *args, **kwargs: {"result": {"uid": ["alice"]}},
        )
        password_client = SimpleNamespace(change_password=lambda *args, **kwargs: None)
        user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
            },
        )
        reconcile_error = RuntimeError("reconcile boom")

        with (
            patch("core.views_registration.FreeIPAUser.get_client", autospec=True, return_value=stage_client),
            patch("core.views_registration._build_freeipa_client", autospec=True, return_value=password_client),
            patch("core.views_registration.load_account_invitation_from_token", return_value=invitation),
            patch(
                "core.views_registration.reconcile_account_invitation_for_username",
                side_effect=reconcile_error,
            ),
            patch("core.views_registration.logger.exception") as registration_exception_mock,
            patch("django.contrib.auth.forms.authenticate", return_value=user),
            patch("core.views_auth.load_account_invitation_from_token", return_value=invitation) as auth_load_mock,
            patch("core.views_auth.reconcile_account_invitation_for_username") as auth_reconcile_mock,
        ):
            response = client.post(
                f"/register/activate/?token={activation_token}",
                data={"password": "S3curePassword!", "password_confirm": "S3curePassword!"},
                follow=False,
            )

            self.assertEqual(client.session.get(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY), invitation_token)

            login_response = client.post(
                "/login/",
                data={"username": "alice", "password": "pw"},
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login/")
        registration_exception_mock.assert_called_once()
        self.assertEqual(
            registration_exception_mock.call_args.args,
            (
                "Registration activation post-success follow-up failed username=%s error_class=%s error=%s",
                "alice",
                "RuntimeError",
                reconcile_error,
            ),
        )
        self.assertEqual(
            registration_exception_mock.call_args.kwargs["extra"],
            {
                "event": "astra.registration.activate.follow_up_failed",
                "component": "registration",
                "outcome": "error",
                "endpoint": "register-activate",
                "username": "alice",
                "error_type": "RuntimeError",
                "error_message": "reconcile boom",
                "error_repr": "RuntimeError('reconcile boom')",
                "error_args": "('reconcile boom',)",
            },
        )
        self.assertEqual(login_response.status_code, 302)
        auth_load_mock.assert_called_once_with(invitation_token)
        auth_reconcile_mock.assert_called_once()
        self.assertNotIn(PENDING_ACCOUNT_INVITATION_TOKEN_SESSION_KEY, client.session)

        follow_response = client.get(response["Location"])
        messages_list = [message.message for message in get_messages(follow_response.wsgi_request)]
        self.assertIn(REGISTRATION_ACTIVATION_FOLLOW_UP_WARNING_MESSAGE, messages_list)

    def test_login_with_invite_token_links_invitation(self) -> None:
        client = Client()

        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            note="",
            invited_by_username="committee",
        )
        token = str(invitation.invitation_token)

        user = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
            },
        )

        with patch("django.contrib.auth.forms.authenticate", return_value=user):
            resp = client.post(
                f"/login/?invite={token}",
                data={
                    "username": "alice",
                    "password": "pw",
                    "invite": token,
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        self.assertIn("alice", invitation.freeipa_matched_usernames)


from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse
from python_freeipa import exceptions

from core.views_auth import password_expired


class PasswordExpiredViewTests(TestCase):
    def test_get_renders_vue_shell_contract(self) -> None:
        response = self.client.get(reverse("password-expired"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-auth-recovery-password-expired-root=""')
        self.assertContains(
            response,
            f'data-auth-recovery-password-expired-api-url="{reverse("api-password-expired-detail")}"',
        )
        self.assertContains(
            response,
            f'data-auth-recovery-password-expired-submit-url="{reverse("password-expired")}"',
        )
        self.assertContains(
            response,
            f'data-auth-recovery-password-expired-login-url="{reverse("login")}"',
        )
        self.assertNotContains(response, 'id="id_username"')

    def test_password_expired_detail_api_returns_data_only_payload(self) -> None:
        session = self.client.session
        session["_freeipa_pwexp_username"] = "alice"
        session.save()

        response = self.client.get(reverse("api-password-expired-detail"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["form"]["is_bound"])
        self.assertEqual(payload["form"]["fields"][0]["name"], "username")
        self.assertEqual(payload["form"]["fields"][0]["value"], "alice")
        self.assertNotIn("submit_url", payload)
        self.assertNotIn("login_url", payload)
        self.assertEqual(response["Cache-Control"], "private, no-cache")

    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        # Attach messages framework storage
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_success_redirects_and_clears_session_username(self):
        factory = RequestFactory()
        request = factory.post(
            "/password-expired/",
            data={
                "username": "alice",
                "current_password": "oldpw",
                "new_password": "newpw",
                "confirm_new_password": "newpw",
            },
        )
        self._add_session_and_messages(request)
        request.user = AnonymousUser()
        request.session["_freeipa_pwexp_username"] = "alice"
        request.session.save()

        with patch("core.views_auth._build_freeipa_client", autospec=True) as mocked_build:
            mocked_client = mocked_build.return_value
            mocked_client.change_password.return_value = None

            response = password_expired(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("login"))
        self.assertIsNone(request.session.get("_freeipa_pwexp_username"))

        msgs = [m.message for m in get_messages(request)]
        self.assertIn("Password changed. Please log in.", msgs)

    def test_policy_error_shows_form_error(self):
        factory = RequestFactory()
        request = factory.post(
            "/password-expired/",
            data={
                "username": "alice",
                "current_password": "oldpw",
                "new_password": "weak",
                "confirm_new_password": "weak",
            },
        )
        self._add_session_and_messages(request)
        request.user = AnonymousUser()

        captured = {}

        def fake_render(req, template, context, status=200):
            captured["form"] = context["form"]
            # Any HttpResponse is fine; we only care about the form errors.
            from django.http import HttpResponse

            return HttpResponse("ok", status=status)

        with patch("core.views_auth.render", side_effect=fake_render, autospec=True):
            with patch("core.views_auth._build_freeipa_client", autospec=True) as mocked_build:
                mocked_client = mocked_build.return_value
                mocked_client.change_password.side_effect = exceptions.PWChangePolicyError("policy")

                response = password_expired(request)

        self.assertEqual(response.status_code, 200)
        form = captured["form"]
        self.assertTrue(form.errors)
        self.assertIn("Password change rejected by policy", str(form.errors))

    def test_invalid_current_password_marks_field_error(self):
        factory = RequestFactory()
        request = factory.post(
            "/password-expired/",
            data={
                "username": "alice",
                "current_password": "wrongpw",
                "new_password": "newpw",
                "confirm_new_password": "newpw",
            },
        )
        self._add_session_and_messages(request)
        request.user = AnonymousUser()

        captured = {}

        def fake_render(req, template, context, status=200):
            captured["form"] = context["form"]
            from django.http import HttpResponse

            return HttpResponse("ok", status=status)

        with patch("core.views_auth.render", side_effect=fake_render, autospec=True):
            with patch("core.views_auth._build_freeipa_client", autospec=True) as mocked_build:
                mocked_client = mocked_build.return_value
                mocked_client.change_password.side_effect = exceptions.PWChangeInvalidPassword("bad")

                response = password_expired(request)

        self.assertEqual(response.status_code, 200)
        form = captured["form"]
        self.assertIn("current_password", form.errors)

    def test_freeipa_error_logs_structured_extra(self):
        factory = RequestFactory()
        request = factory.post(
            "/password-expired/",
            data={
                "username": "alice",
                "current_password": "oldpw",
                "new_password": "newpw",
                "confirm_new_password": "newpw",
            },
        )
        self._add_session_and_messages(request)
        request.user = AnonymousUser()

        captured = {}

        def fake_render(req, template, context, status=200):
            captured["form"] = context["form"]
            from django.http import HttpResponse

            return HttpResponse("ok", status=status)

        with (
            patch("core.views_auth.render", side_effect=fake_render, autospec=True),
            patch("core.views_auth._build_freeipa_client", autospec=True) as mocked_build,
            patch("core.views_auth.logger.warning", autospec=True) as mocked_warning,
        ):
            mocked_client = mocked_build.return_value
            mocked_client.change_password.side_effect = exceptions.FreeIPAError("service down")
            response = password_expired(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(captured["form"].errors)
        mocked_warning.assert_called_once()
        log_kwargs = mocked_warning.call_args.kwargs
        self.assertEqual(log_kwargs["extra"]["event"], "astra.auth.password_expired.freeipa_error")
        self.assertEqual(log_kwargs["extra"]["component"], "auth")
        self.assertEqual(log_kwargs["extra"]["outcome"], "error")
        self.assertEqual(log_kwargs["extra"]["username"], "alice")
        self.assertEqual(log_kwargs["extra"]["error_type"], "FreeIPAError")
        self.assertEqual(log_kwargs["extra"]["error_message"], "service down")
        self.assertIn("service down", log_kwargs["extra"]["error_repr"])
        self.assertEqual(log_kwargs["extra"]["error_args"], "('service down',)")

    def test_rate_limit_denial_returns_429_and_skips_freeipa_call(self) -> None:
        with (
            patch("core.views_auth.allow_request", return_value=False, create=True) as allow_mock,
            patch("core.views_auth._build_freeipa_client", autospec=True) as build_client_mock,
            patch("core.views_auth.logger.warning", autospec=True) as warning_mock,
        ):
            response = self.client.post(
                reverse("password-expired"),
                data={
                    "username": "alice",
                    "current_password": "oldpw",
                    "new_password": "newpw12345",
                    "confirm_new_password": "newpw12345",
                },
                REMOTE_ADDR="198.51.100.41",
                HTTP_X_FORWARDED_FOR="203.0.113.41, 198.51.100.41",
            )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many password change attempts", status_code=429)
        build_client_mock.assert_not_called()
        allow_mock.assert_called_once()
        warning_mock.assert_called_once()

        allow_kwargs = allow_mock.call_args.kwargs
        self.assertEqual(allow_kwargs["scope"], "auth.password_expired")
        self.assertEqual(allow_kwargs["key_parts"], ["203.0.113.41", "alice"])

        log_extra = warning_mock.call_args.kwargs["extra"]
        self.assertEqual(log_extra["event"], "astra.security.rate_limit.denied")
        self.assertEqual(log_extra["component"], "auth")
        self.assertEqual(log_extra["outcome"], "denied")
        self.assertEqual(log_extra["http_method"], "POST")
        self.assertIn("ip_hash", log_extra)
        self.assertIn("subject_hash", log_extra)
        self.assertNotIn("203.0.113.41", str(log_extra))
        self.assertNotIn("alice", str(log_extra).lower())

    def test_unexpected_error_logs_structured_extra(self):
        factory = RequestFactory()
        request = factory.post(
            "/password-expired/",
            data={
                "username": "alice",
                "current_password": "oldpw",
                "new_password": "newpw",
                "confirm_new_password": "newpw",
            },
        )
        self._add_session_and_messages(request)
        request.user = AnonymousUser()

        captured = {}

        def fake_render(req, template, context, status=200):
            captured["form"] = context["form"]
            from django.http import HttpResponse

            return HttpResponse("ok", status=status)

        with (
            patch("core.views_auth.render", side_effect=fake_render, autospec=True),
            patch("core.views_auth._build_freeipa_client", autospec=True) as mocked_build,
            patch("core.views_auth.logger.exception", autospec=True) as mocked_exception,
        ):
            mocked_client = mocked_build.return_value
            mocked_client.change_password.side_effect = RuntimeError("boom")
            response = password_expired(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(captured["form"].errors)
        mocked_exception.assert_called_once()
        log_kwargs = mocked_exception.call_args.kwargs
        self.assertEqual(log_kwargs["extra"]["event"], "astra.auth.password_expired.unexpected_error")
        self.assertEqual(log_kwargs["extra"]["component"], "auth")
        self.assertEqual(log_kwargs["extra"]["outcome"], "error")
        self.assertEqual(log_kwargs["extra"]["username"], "alice")
        self.assertEqual(log_kwargs["extra"]["error_type"], "RuntimeError")
        self.assertEqual(log_kwargs["extra"]["error_message"], "boom")
        self.assertIn("boom", log_kwargs["extra"]["error_repr"])
        self.assertEqual(log_kwargs["extra"]["error_args"], "('boom',)")

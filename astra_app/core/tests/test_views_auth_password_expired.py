from __future__ import annotations

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

        with patch("core.views_auth.ClientMeta", autospec=True) as mocked_client_cls:
            mocked_client = mocked_client_cls.return_value
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

        def fake_render(req, template, context):
            captured["form"] = context["form"]
            # Any HttpResponse is fine; we only care about the form errors.
            from django.http import HttpResponse

            return HttpResponse("ok")

        with patch("core.views_auth.render", side_effect=fake_render, autospec=True):
            with patch("core.views_auth.ClientMeta", autospec=True) as mocked_client_cls:
                mocked_client = mocked_client_cls.return_value
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

        def fake_render(req, template, context):
            captured["form"] = context["form"]
            from django.http import HttpResponse

            return HttpResponse("ok")

        with patch("core.views_auth.render", side_effect=fake_render, autospec=True):
            with patch("core.views_auth.ClientMeta", autospec=True) as mocked_client_cls:
                mocked_client = mocked_client_cls.return_value
                mocked_client.change_password.side_effect = exceptions.PWChangeInvalidPassword("bad")

                response = password_expired(request)

        self.assertEqual(response.status_code, 200)
        form = captured["form"]
        self.assertIn("current_password", form.errors)

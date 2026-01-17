from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pyotp
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from core.views_settings import OTP_KEY_LENGTH, settings_root


class SettingsOTPViewTests(TestCase):
    def _add_session_and_messages(self, request: Any) -> Any:
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _auth_user(self, username: str = "alice") -> Any:
        return SimpleNamespace(is_authenticated=True, get_username=lambda: username)

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
    )
    def test_get_populates_tokens(self):
        factory = RequestFactory()
        request = factory.get("/settings/")
        self._add_session_and_messages(request)
        request.user = self._auth_user()

        captured: dict[str, Any] = {}

        def fake_render(req, template, context):
            captured["context"] = context
            return HttpResponse("ok")

        fake_fu = SimpleNamespace(
            first_name="Alice",
            last_name="Test",
            full_name="Alice Test",
            email="alice@example.org",
            groups_list=[],
            _user_data={"fasstatusnote": ["US"]},
        )

        with patch("core.views_settings.render", side_effect=fake_render, autospec=True):
            with patch("core.views_settings.settings_context", return_value={}, autospec=True):
                with patch("core.views_settings.has_enabled_agreements", return_value=False, autospec=True):
                    with patch("core.views_settings._get_full_user", return_value=fake_fu, autospec=True):
                        with patch("core.views_settings.FreeIPAUser.get_client", autospec=True) as mocked_get_client:
                            mocked_get_client.return_value.otptoken_find.return_value = {"result": []}

                            with patch("core.views_settings.ClientMeta", autospec=True) as mocked_client_cls:
                                mocked_client = mocked_client_cls.return_value
                                mocked_client.login.return_value = None
                                mocked_client.otptoken_find.return_value = {
                                    "result": [
                                        {"ipatokenuniqueid": ["t2"], "description": "b"},
                                        {"ipatokenuniqueid": ["t1"], "description": "a"},
                                    ]
                                }

                                response = settings_root(request)

        self.assertEqual(response.status_code, 200)
        tokens = captured["context"]["otp_tokens"]
        self.assertEqual(len(tokens), 2)
        self.assertEqual(tokens[0]["ipatokenuniqueid"][0], "t1")

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
    )
    def test_get_normalizes_list_description_to_string(self):
        factory = RequestFactory()
        request = factory.get("/settings/")
        self._add_session_and_messages(request)
        request.user = self._auth_user()

        captured: dict[str, Any] = {}

        def fake_render(req, template, context):
            captured["context"] = context
            return HttpResponse("ok")

        fake_fu = SimpleNamespace(
            first_name="Alice",
            last_name="Test",
            full_name="Alice Test",
            email="alice@example.org",
            groups_list=[],
            _user_data={"fasstatusnote": ["US"]},
        )

        with patch("core.views_settings.settings_context", return_value={}, autospec=True):
            with patch("core.views_settings.render", side_effect=fake_render, autospec=True):
                with patch("core.views_settings.has_enabled_agreements", return_value=False, autospec=True):
                    with patch("core.views_settings._get_full_user", return_value=fake_fu, autospec=True):
                        with patch("core.views_settings.FreeIPAUser.get_client", autospec=True) as mocked_get_client:
                            mocked_get_client.return_value.otptoken_find.return_value = {"result": []}

                            with patch("core.views_settings.ClientMeta", autospec=True) as mocked_client_cls:
                                mocked_client = mocked_client_cls.return_value
                                mocked_client.login.return_value = None
                                mocked_client.otptoken_find.return_value = {
                                    "result": [
                                        {"ipatokenuniqueid": ["t1"], "description": ["bitwarden alma members test"]},
                                    ]
                                }

                                response = settings_root(request)

        self.assertEqual(response.status_code, 200)
        tokens = captured["context"]["otp_tokens"]
        self.assertEqual(tokens[0]["description"], "bitwarden alma members test")

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
    )
    def test_post_add_step_generates_secret_and_uri(self):
        factory = RequestFactory()
        request = factory.post(
            "/settings/",
            data={
                "tab": "security",
                "add-description": "my phone",
                "add-password": "pw",
                "add-submit": "1",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user()

        captured = {}

        def fake_render(req, template, context):
            captured["context"] = context
            return HttpResponse("ok")

        # First ClientMeta instance: service client (list tokens)
        # Second ClientMeta instance: user client (reauth)
        svc_client = SimpleNamespace(
            login=lambda *a, **k: None,
            otptoken_find=lambda **k: {"result": []},
        )
        user_client = SimpleNamespace(login=lambda *a, **k: None)

        fake_fu = SimpleNamespace(
            first_name="Alice",
            last_name="Test",
            full_name="Alice Test",
            email="alice@example.org",
            groups_list=[],
            _user_data={"fasstatusnote": ["US"]},
        )

        with patch("core.views_settings.render", side_effect=fake_render, autospec=True):
            with patch("core.views_settings.settings_context", return_value={}, autospec=True):
                with patch("core.views_settings.has_enabled_agreements", return_value=False, autospec=True):
                    with patch("core.views_settings._get_full_user", return_value=fake_fu, autospec=True):
                        with patch("core.views_settings.FreeIPAUser.get_client", autospec=True) as mocked_get_client:
                            mocked_get_client.return_value.otptoken_find.return_value = {"result": []}

                            with patch("core.views_settings.ClientMeta", autospec=True, side_effect=[svc_client, user_client]):
                                with patch("core.views_settings.os.urandom", return_value=b"A" * OTP_KEY_LENGTH):
                                    response = settings_root(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(captured["context"]["otp_uri"].startswith("otpauth://"))

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
    )
    def test_post_confirm_step_creates_token_and_redirects(self):
        secret = pyotp.random_base32()
        code = pyotp.TOTP(secret).now()

        factory = RequestFactory()
        request = factory.post(
            "/settings/",
            data={
                "tab": "security",
                "confirm-secret": secret,
                "confirm-description": "my phone",
                "confirm-code": code,
                "confirm-submit": "1",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user()

        svc_client = SimpleNamespace(
            login=lambda *a, **k: None,
            otptoken_find=lambda **k: {"result": []},
            otptoken_add=lambda **k: {"result": {"ipatokenuniqueid": ["t1"]}},
        )

        fake_fu = SimpleNamespace(
            first_name="Alice",
            last_name="Test",
            full_name="Alice Test",
            email="alice@example.org",
            groups_list=[],
            _user_data={"fasstatusnote": ["US"]},
        )

        with patch("core.views_settings.settings_context", return_value={}, autospec=True):
            with patch("core.views_settings.has_enabled_agreements", return_value=False, autospec=True):
                with patch("core.views_settings._get_full_user", return_value=fake_fu, autospec=True):
                    with patch("core.views_settings.FreeIPAUser.get_client", autospec=True) as mocked_get_client:
                        mocked_get_client.return_value.otptoken_find.return_value = {"result": []}
                        with patch("core.views_settings.ClientMeta", autospec=True, side_effect=[svc_client, svc_client]):
                            response = settings_root(request)

        self.assertEqual(response.status_code, 302)
        msgs = [m.message for m in get_messages(request)]
        self.assertTrue(any("token has been created" in m.lower() for m in msgs))

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
    )
    def test_post_confirm_invalid_does_not_trigger_add_modal(self):
        """Regression test: confirm POST should not bind add_form.

        If add_form is bound on confirm POST, it becomes invalid and the template
        will open the Add Token modal alongside the confirm modal.
        """

        secret = pyotp.random_base32()

        factory = RequestFactory()
        request = factory.post(
            "/settings/",
            data={
                "tab": "security",
                "confirm-secret": secret,
                "confirm-description": "my phone",
                "confirm-code": "000000",  # invalid
                "confirm-submit": "1",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user()

        captured = {}

        def fake_render(req, template, context):
            captured["context"] = context
            return HttpResponse("ok")

        svc_client = SimpleNamespace(
            login=lambda *a, **k: None,
            otptoken_find=lambda **k: {"result": []},
        )

        fake_fu = SimpleNamespace(
            first_name="Alice",
            last_name="Test",
            full_name="Alice Test",
            email="alice@example.org",
            groups_list=[],
            _user_data={"fasstatusnote": ["US"]},
        )

        with patch("core.views_settings.render", side_effect=fake_render, autospec=True):
            with patch("core.views_settings.settings_context", return_value={}, autospec=True):
                with patch("core.views_settings.has_enabled_agreements", return_value=False, autospec=True):
                    with patch("core.views_settings._get_full_user", return_value=fake_fu, autospec=True):
                        with patch("core.views_settings.FreeIPAUser.get_client", autospec=True) as mocked_get_client:
                            mocked_get_client.return_value.otptoken_find.return_value = {"result": []}

                            with patch("core.views_settings.ClientMeta", autospec=True, side_effect=[svc_client, svc_client]):
                                response = settings_root(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(captured["context"]["otp_uri"].startswith("otpauth://"))
        self.assertEqual(captured["context"]["otp_add_form"].errors, {})


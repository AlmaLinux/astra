from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from python_freeipa import exceptions

from core import views_settings


class SelfServiceSettingsPagesTests(TestCase):
    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _auth_user(self, username: str = "alice"):
        return SimpleNamespace(is_authenticated=True, get_username=lambda: username, email=f"{username}@example.org")

    def test_settings_profile_get_populates_initial_values(self):
        factory = RequestFactory()

        fake_user = SimpleNamespace(
            username="alice",
            email="a@example.org",
            is_authenticated=True,
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
            },
        )

        request = factory.get("/settings/")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        captured: dict[str, object] = {}

        def fake_render(_request, template, context):
            captured["template"] = template
            captured["context"] = context
            return HttpResponse("ok")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings.render", autospec=True, side_effect=fake_render):
                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 200)
        ctx = captured["context"]
        form = ctx["profile_form"]
        self.assertEqual(form["givenname"].value(), "Alice")
        self.assertEqual(form["sn"].value(), "User")
        self.assertFalse(form.is_bound)

    def test_settings_profile_get_accepts_boolean_fasisprivate(self):
        factory = RequestFactory()

        fake_user = SimpleNamespace(
            username="alice",
            first_name="Alice",
            last_name="User",
            email="a@example.org",
            is_authenticated=True,
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                # Reproduces the real-world crash: value comes back as a bool.
                "fasIsPrivate": [True],
            },
        )

        request = factory.get("/settings/")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        captured: dict[str, object] = {}

        def fake_render(_request, template, context):
            captured["template"] = template
            captured["context"] = context
            return HttpResponse("ok")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings.render", autospec=True, side_effect=fake_render):
                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 200)
        ctx = captured.get("context")
        self.assertIsNotNone(ctx)
        form = ctx["form"]
        self.assertTrue(form.initial.get("fasIsPrivate"))

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
        SELF_SERVICE_ADDRESS_COUNTRY_ATTR="fasstatusnote",
    )
    def test_settings_save_all_invalid_form_forces_tab_visible(self):
        """When save-all validation fails, ensure the response forces the tab with errors.

        Without this, the URL hash (e.g. #profile) can keep the user on the wrong
        tab, hiding errors and making it look like the save silently did nothing.
        """

        factory = RequestFactory()

        fake_user = SimpleNamespace(
            username="alice",
            email="a@example.org",
            is_authenticated=True,
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                "fasLocale": ["en_US"],
                "fasTimezone": ["UTC"],
                "mail": ["alice@example.org"],
                "fasstatusnote": [""],
            },
        )

        request = factory.post(
            "/settings/",
            data={
                # Save-all profile submit
                "tab": "profile",
                "save_all": "1",
                "country_code": "CH",
                # Keep emails unchanged so we don't fail there.
                "mail": "alice@example.org",
                "fasRHBZEmail": "",
                # Force profile to be changed *and* invalid.
                "fasLocale": "xx_YY",
                "fasTimezone": "UTC",
                "givenname": "Alice",
                "sn": "User",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        captured: dict[str, object] = {}

        def fake_render(_request, template, context):
            captured["template"] = template
            captured["context"] = context
            return HttpResponse("ok")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings.render", autospec=True, side_effect=fake_render):
                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 200)
        ctx = captured["context"]
        self.assertEqual(ctx["active_tab"], "profile")
        self.assertEqual(ctx.get("force_tab"), "profile")

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
    )
    def test_settings_profile_post_no_changes_short_circuits(self):
        factory = RequestFactory()

        fake_user = SimpleNamespace(
            username="alice",
            first_name="Alice",
            last_name="User",
            email="a@example.org",
            is_authenticated=True,
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                "c": ["US"],
                "fasLocale": ["en_US"],
                "fasTimezone": ["UTC"],
                "fasIsPrivate": ["FALSE"],
            },
        )

        request = factory.post(
            "/settings/",
            data={
                "tab": "profile",
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasPronoun": "",
                "fasLocale": "en_US",
                "fasTimezone": "UTC",
                "fasWebsiteUrl": "",
                "fasRssUrl": "",
                "fasIRCNick": "",
                "fasGitHubUsername": "",
                "fasGitLabUsername": "",
                "fasIsPrivate": "",  # unchecked
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings._update_user_attrs", autospec=True) as mocked_update:
                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#profile")
        msgs = [m.message for m in get_messages(request)]
        self.assertIn("No changes to save.", msgs)
        mocked_update.assert_not_called()

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
        SELF_SERVICE_ADDRESS_COUNTRY_ATTR="c",
    )
    def test_settings_save_all_applies_multiple_tabs(self):
        factory = RequestFactory()

        fake_user = SimpleNamespace(
            username="alice",
            email="a@example.org",
            is_authenticated=True,
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                "c": ["US"],
                "mail": ["a@example.org"],
                "fasRHBZEmail": ["a@example.org"],
                "fasGPGKeyId": ["0123456789ABCDEF"],
                "ipasshpubkey": ["ssh-ed25519 AAAA... alice@example"],
            },
        )

        request = factory.post(
            "/settings/",
            data={
                "save_all": "1",
                "tab": "profile",
                # Profile
                "givenname": "Alicia",
                "sn": "User",
                "country_code": "US",
                "fasPronoun": "",
                "fasLocale": "",
                "fasTimezone": "",
                "fasWebsiteUrl": "",
                "fasRssUrl": "",
                "fasIRCNick": "",
                "fasGitHubUsername": "",
                "fasGitLabUsername": "",
                "fasIsPrivate": "",
                # Emails (required mail)
                "mail": "a@example.org",
                "fasRHBZEmail": "a@example.org",
                # Keys
                "fasGPGKeyId": "0123456789ABCDEF\nFEDCBA9876543210",
                "ipasshpubkey": "ssh-ed25519 AAAA... alice@example",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings._update_user_attrs", autospec=True) as mocked_update:
                mocked_update.side_effect = [([], True), ([], True)]
                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#profile")
        self.assertEqual(mocked_update.call_count, 2)

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
        SELF_SERVICE_ADDRESS_COUNTRY_ATTR="fasstatusnote",
    )
    def test_settings_save_all_country_update_unblocks_other_changes(self):
        factory = RequestFactory()

        # Country code missing in user_data initially (compliance block would trigger).
        fake_user = SimpleNamespace(
            username="alice",
            email="a@example.org",
            is_authenticated=True,
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                # Note: no fasstatusnote
                "mail": ["a@example.org"],
                "fasRHBZEmail": ["a@example.org"],
            },
        )

        request = factory.post(
            "/settings/",
            data={
                "save_all": "1",
                "tab": "profile",
                # Profile change
                "givenname": "Alicia",
                "sn": "User",
                "country_code": "US",
                "fasPronoun": "",
                "fasLocale": "",
                "fasTimezone": "",
                "fasWebsiteUrl": "",
                "fasRssUrl": "",
                "fasIRCNick": "",
                "fasGitHubUsername": "",
                "fasGitLabUsername": "",
                "fasIsPrivate": "",
                # Emails required
                "mail": "a@example.org",
                "fasRHBZEmail": "a@example.org",
                # Keys present but unchanged
                "fasGPGKeyId": "",
                "ipasshpubkey": "",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings._update_user_attrs", autospec=True) as mocked_update:
                mocked_update.side_effect = [([], True), ([], True)]
                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#profile")
        self.assertEqual(mocked_update.call_count, 2)

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
        SELF_SERVICE_ADDRESS_COUNTRY_ATTR="c",
    )
    def test_settings_save_all_profile_country_can_save_when_other_tabs_invalid_but_unchanged(self):
        factory = RequestFactory()

        fake_user = SimpleNamespace(
            username="alice",
            email="a@example.org",
            is_authenticated=True,
            _user_data={
                # Profile required fields exist but unchanged.
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                # Emails required field is missing/empty.
                "mail": [""],
                "fasRHBZEmail": [""],
                # Country initially unset.
                "c": [""],
            },
        )

        request = factory.post(
            "/settings/",
            data={
                "save_all": "1",
                "tab": "profile",
                # Profile: set country code
                "country_code": "ES",
                # Other tabs are submitted (as in the real page) but unchanged/empty
                "givenname": "Alice",
                "sn": "User",
                "fasPronoun": "",
                "fasLocale": "",
                "fasTimezone": "",
                "fasWebsiteUrl": "",
                "fasRssUrl": "",
                "fasIRCNick": "",
                "fasGitHubUsername": "",
                "fasGitLabUsername": "",
                "fasIsPrivate": "",
                "mail": "",
                "fasRHBZEmail": "",
                "fasGPGKeyId": "",
                "ipasshpubkey": "",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings._update_user_attrs", autospec=True) as mocked_update:
                mocked_update.return_value = ([], True)
                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#profile")
        self.assertEqual(mocked_update.call_count, 1)

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
        SELF_SERVICE_ADDRESS_COUNTRY_ATTR="fasstatusnote",
    )
    def test_settings_save_all_profile_accepts_bcp47_locale_from_freeipa(self):
        factory = RequestFactory()

        fake_user = SimpleNamespace(
            username="alice",
            email="a@example.org",
            is_authenticated=True,
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                # FreeIPA returns BCP47-ish form.
                "fasLocale": ["en-US"],
                "fasTimezone": ["Australia/Brisbane"],
                "mail": ["alice@example.org"],
                "fasRHBZEmail": [""],
                "fasstatusnote": [""],
            },
        )

        request = factory.post(
            "/settings/",
            data={
                "save_all": "1",
                "tab": "profile",
                # Profile change provides a valid country code.
                # Profile values are submitted (combined form post) but unchanged.
                "givenname": "Alice",
                "sn": "User",
                "country_code": "CH",
                "fasPronoun": "",
                "fasLocale": "en-US",
                "fasTimezone": "Australia/Brisbane",
                "fasWebsiteUrl": "",
                "fasRssUrl": "",
                "fasIRCNick": "",
                "fasGitHubUsername": "",
                "fasGitLabUsername": "",
                "fasIsPrivate": "",
                # Emails present but unchanged.
                "mail": "alice@example.org",
                "fasRHBZEmail": "",
                # Keys present but unchanged.
                "fasGPGKeyId": "",
                "ipasshpubkey": "",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings._update_user_attrs", autospec=True) as mocked_update:
                mocked_update.return_value = ([], True)
                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#profile")
        self.assertEqual(mocked_update.call_count, 1)

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
        SELF_SERVICE_ADDRESS_COUNTRY_ATTR="fasstatusnote",
    )
    def test_settings_profile_post_changes_blocked_without_country_code(self):
        factory = RequestFactory()

        fake_user = SimpleNamespace(
            username="alice",
            first_name="Alice",
            last_name="User",
            email="a@example.org",
            is_authenticated=True,
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                "fasLocale": ["en_US"],
                "fasTimezone": ["UTC"],
                "fasIsPrivate": ["FALSE"],
                # Missing fasstatusnote -> no country code on file.
            },
        )

        request = factory.post(
            "/settings/",
            data={
                "tab": "profile",
                "givenname": "Alicia",
                "sn": "User",
                "fasPronoun": "",
                "fasLocale": "en_US",
                "fasTimezone": "UTC",
                "fasWebsiteUrl": "",
                "fasRssUrl": "",
                "fasIRCNick": "",
                "fasGitHubUsername": "",
                "fasGitLabUsername": "",
                "fasIsPrivate": "",  # unchecked
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        captured: dict[str, object] = {}

        def fake_render(_request, template, context):
            captured["template"] = template
            captured["context"] = context
            return HttpResponse("ok")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings._update_user_attrs", autospec=True) as mocked_update:
                with patch("core.views_settings.render", autospec=True, side_effect=fake_render):
                    response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 200)
        mocked_update.assert_not_called()
        ctx = captured["context"]
        form = ctx["profile_form"]
        self.assertIn("country_code", form.errors)

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
    )
    def test_settings_emails_post_no_changes_short_circuits(self):
        factory = RequestFactory()

        fake_user = SimpleNamespace(
            username="alice",
            email="a@example.org",
            is_authenticated=True,
            _user_data={"mail": ["a@example.org"], "fasRHBZEmail": ["a@example.org"]},
        )

        request = factory.post(
            "/settings/",
            data={
                "tab": "emails",
                "mail": "a@example.org",
                "fasRHBZEmail": "a@example.org",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings._update_user_attrs", autospec=True) as mocked_update:
                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#emails")
        msgs = [m.message for m in get_messages(request)]
        self.assertIn("No changes to save.", msgs)
        mocked_update.assert_not_called()

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
    )
    def test_settings_keys_post_no_changes_short_circuits(self):
        factory = RequestFactory()

        fake_user = SimpleNamespace(
            username="alice",
            is_authenticated=True,
            _user_data={
                "fasGPGKeyId": ["0123456789ABCDEF"],
                "ipasshpubkey": ["ssh-ed25519 AAAA... alice@example"],
            },
        )

        request = factory.post(
            "/settings/",
            data={
                "tab": "keys",
                "fasGPGKeyId": "0123456789ABCDEF",
                "ipasshpubkey": "ssh-ed25519 AAAA... alice@example",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings._update_user_attrs", autospec=True) as mocked_update:
                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#keys")
        msgs = [m.message for m in get_messages(request)]
        self.assertIn("No changes to save.", msgs)
        mocked_update.assert_not_called()

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
    )
    def test_settings_password_uses_change_password(self):
        factory = RequestFactory()
        request = factory.post(
            "/settings/",
            data={
                "tab": "security",
                "current_password": "oldpw",
                "new_password": "newpw",
                "confirm_new_password": "newpw",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fake_user = SimpleNamespace(_user_data={"fasstatusnote": ["US"]})

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings.ClientMeta", autospec=True) as mocked_client_cls:
                mocked_client = mocked_client_cls.return_value
                mocked_client.change_password.return_value = None

                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#security")
        mocked_client.change_password.assert_called_once_with("alice", "newpw", "oldpw", otp=None)

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
    )
    def test_settings_password_includes_otp_when_provided(self):
        factory = RequestFactory()
        request = factory.post(
            "/settings/",
            data={
                "tab": "security",
                "current_password": "oldpw",
                "otp": "123456",
                "new_password": "newpw",
                "confirm_new_password": "newpw",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fake_user = SimpleNamespace(_user_data={"fasstatusnote": ["US"]})

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings.ClientMeta", autospec=True) as mocked_client_cls:
                mocked_client = mocked_client_cls.return_value
                mocked_client.change_password.return_value = None

                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#security")
        mocked_client.change_password.assert_called_once_with("alice", "newpw", "oldpw", otp="123456")

    @override_settings(
        DEBUG=True,
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
    )
    def test_settings_password_wrong_current_password_shows_single_non_debug_message(self):
        factory = RequestFactory()
        request = factory.post(
            "/settings/",
            data={
                "tab": "security",
                "current_password": "wrongpw",
                "new_password": "newpw",
                "confirm_new_password": "newpw",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fake_user = SimpleNamespace(_user_data={"fasstatusnote": ["US"]})

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings.ClientMeta", autospec=True) as mocked_client_cls:
                mocked_client = mocked_client_cls.return_value
                mocked_client.change_password.side_effect = exceptions.PWChangeInvalidPassword("bad")

                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#security")
        msgs = [m.message for m in get_messages(request)]
        self.assertEqual(len(msgs), 1)
        self.assertNotIn("(debug)", msgs[0])
        self.assertIn("Incorrect current password", msgs[0])

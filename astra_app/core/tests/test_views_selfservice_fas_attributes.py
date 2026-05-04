
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings

from core.views_settings import settings_root
from core.views_users import user_profile_detail_api


@dataclass
class _DummyFreeIPAUser:
    username: str = "alice"
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    _user_data: dict | None = None
    groups_list: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name or self.username

    def get_full_name(self) -> str:
        return self.full_name


class FASAttributesTests(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.factory = RequestFactory()
        self._agreements_enabled_patcher = patch(
            "core.views_settings.has_enabled_agreements",
            autospec=True,
            return_value=False,
        )
        self._agreements_enabled_patcher.start()
        self.addCleanup(self._agreements_enabled_patcher.stop)

    def _seed_valid_country_code(self, fu: _DummyFreeIPAUser, *, code: str = "US") -> None:
        data = fu._user_data or {}
        country_attr = settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR
        data[country_attr] = [code]
        fu._user_data = data

    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _auth_user(self, username: str = "alice"):
        return SimpleNamespace(is_authenticated=True, get_username=lambda: username)

    def _load_profile(self, fu: _DummyFreeIPAUser):
        req = self.factory.get(f"/user/{fu.username}/")
        self._add_session_and_messages(req)
        req.user = self._auth_user(fu.username)

        with patch("core.views_users._get_full_user", autospec=True, return_value=fu):
            with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                    with patch(
                        "core.views_users.resolve_avatar_urls_for_users",
                        autospec=True,
                        return_value=({}, 0, 0),
                    ):
                        with patch(
                            "core.views_users.membership_review_permissions",
                            autospec=True,
                            return_value={
                                "membership_can_view": False,
                                "membership_can_add": False,
                                "membership_can_change": False,
                                "membership_can_delete": False,
                            },
                        ):
                            resp = user_profile_detail_api(req, fu.username)

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        summary = payload["summary"]
        return {
            "pronouns": summary.get("pronouns", ""),
            "fu": SimpleNamespace(
                full_name=summary.get("fullName", ""),
                email=summary.get("email", ""),
            ),
        }

    @staticmethod
    def _apply_user_mod_to_dummy(fu: _DummyFreeIPAUser, call_kwargs: dict):
        """Apply user_mod-style updates to our dummy user.

        This lets tests verify that a subsequent profile load reflects changes.
        """

        data = fu._user_data or {}

        # direct updates like o_givenname
        for k, v in call_kwargs.items():
            if not k.startswith("o_"):
                continue
            if k in {"o_addattr", "o_setattr", "o_delattr"}:
                continue
            attr = k[2:]
            if attr == "mail":
                fu.email = str(v)
            if attr == "givenname":
                fu.first_name = str(v)
            if attr == "sn":
                fu.last_name = str(v)
            data[attr] = [str(v)]

        def _ensure_list(attr: str) -> list[str]:
            cur = data.get(attr)
            if cur is None:
                data[attr] = []
                return data[attr]
            if isinstance(cur, list):
                return cur
            data[attr] = [str(cur)]
            return data[attr]

        # setattrs like ["fasLocale=en_US", "fasTimezone="]
        for item in (call_kwargs.get("o_setattr", []) or []):
            if "=" not in item:
                continue
            attr, value = item.split("=", 1)
            if value == "":
                data.pop(attr, None)
            else:
                data[attr] = [value]

        # addattrs like ["fasPronoun=she/her", ...]
        for item in (call_kwargs.get("o_addattr", []) or []):
            if "=" not in item:
                continue
            attr, value = item.split("=", 1)
            cur_list = _ensure_list(attr)
            if value not in cur_list:
                cur_list.append(value)

        # delattrs like ["fasPronoun=she/her", "fasLocale="]
        for item in (call_kwargs.get("o_delattr", []) or []):
            if "=" not in item:
                continue
            attr, value = item.split("=", 1)
            if value == "":
                data.pop(attr, None)
                continue
            cur_list = _ensure_list(attr)
            data[attr] = [v for v in cur_list if v != value]
            if not data[attr]:
                data.pop(attr, None)

        fu._user_data = data

    def _assert_user_mod_called_with_sets(self, user_mod: Mock, username: str, *, add=None, set_=None, del_=None, direct=None):
        add = set(add or [])
        set_ = set(set_ or [])
        del_ = set(del_ or [])
        direct = dict(direct or {})

        self.assertTrue(user_mod.called)
        call_args, call_kwargs = user_mod.call_args
        self.assertEqual(call_args[0], username)

        for k, v in direct.items():
            self.assertEqual(call_kwargs.get(k), v)

        got_add = set(call_kwargs.get("o_addattr", []) or [])
        got_set = set(call_kwargs.get("o_setattr", []) or [])
        got_del = set(call_kwargs.get("o_delattr", []) or [])

        self.assertEqual(got_add, add)
        self.assertEqual(got_set, set_)
        self.assertEqual(got_del, del_)

    @patch("core.forms_selfservice.get_timezone_options", autospec=True, return_value=["UTC"])
    @patch("core.forms_selfservice.get_locale_options", autospec=True, return_value=["en-US"])
    def test_profile_set_all_fas_fields(self, _mock_locales, _mock_tzs):
        fu = _DummyFreeIPAUser(
            first_name="",
            last_name="",
            email="",
            _user_data={},
        )
        self._seed_valid_country_code(fu)

        client = SimpleNamespace(user_mod=Mock())
        client.user_mod.side_effect = lambda _username, **kwargs: self._apply_user_mod_to_dummy(fu, kwargs)

        before = self._load_profile(fu)
        self.assertEqual(before.get("pronouns", ""), "")

        req = self.factory.post(
            "/settings/",
            data={
                "tab": "profile",
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasPronoun": "she/her",
                "fasLocale": "en-US",
                "fasTimezone": "UTC",
                "fasWebsiteUrl": "https://example.com",
                "fasRssUrl": "https://example.com/rss.xml",
                "fasIRCNick": "alice\nmatrix://matrix.example/alice",
                "fasGitHubUsername": "alice-1",
                "fasGitLabUsername": "alice_1",
            },
        )
        self._add_session_and_messages(req)
        req.user = self._auth_user()

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu):
                with patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client):
                    resp = settings_root(req)

        self.assertEqual(resp.status_code, 302)
        self._assert_user_mod_called_with_sets(
            client.user_mod,
            "alice",
            add={
                "fasPronoun=she/her",
                "fasWebsiteUrl=https://example.com",
                "fasRssUrl=https://example.com/rss.xml",
                "fasIRCNick=irc://alice",
                "fasIRCNick=matrix://matrix.example/alice",
            },
            set_={
                "fasLocale=en-US",
                "fasTimezone=UTC",
                "fasGitHubUsername=alice-1",
                "fasGitLabUsername=alice_1",
            },
            del_=set(),
            direct={
                "o_givenname": "Alice",
                "o_sn": "User",
                "o_cn": "Alice User",
            },
        )

        after = self._load_profile(fu)
        self.assertEqual(after.get("pronouns"), "she/her")
        self.assertEqual(after["fu"].full_name, "Alice User")

    @override_settings(SELF_SERVICE_ADDRESS_COUNTRY_ATTR="fasstatusnote")
    def test_profile_country_set_uses_configured_country_attr(self):
        fu = _DummyFreeIPAUser(
            first_name="Alice",
            last_name="User",
            email="alice@example.com",
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
            },
        )

        client = SimpleNamespace(user_mod=Mock())
        client.user_mod.side_effect = lambda _username, **kwargs: self._apply_user_mod_to_dummy(fu, kwargs)

        req = self.factory.post(
            "/settings/",
            data={
                "tab": "profile",
                "givenname": "Alice",
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
            },
        )
        self._add_session_and_messages(req)
        req.user = self._auth_user("alice")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu):
                with patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client):
                    resp = settings_root(req)

        self.assertEqual(resp.status_code, 302)
        self._assert_user_mod_called_with_sets(
            client.user_mod,
            "alice",
            set_={
                "fasstatusnote=US",
            },
            add=set(),
            del_=set(),
            direct={},
        )
        self.assertEqual((fu._user_data or {}).get("fasstatusnote"), ["US"])

    @patch("core.forms_selfservice.get_timezone_options", autospec=True, return_value=["UTC"])
    @patch("core.forms_selfservice.get_locale_options", autospec=True, return_value=["en-US"])
    def test_profile_country_change_sends_user_country_changed_signal(self, _mock_locales, _mock_tzs):
        import importlib

        from core.models import IPAUser

        signal_module = importlib.import_module("core.signals")

        fu = _DummyFreeIPAUser(
            username="alice",
            first_name="Alice",
            last_name="User",
            email="alice@example.com",
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
            },
        )
        self._seed_valid_country_code(fu, code="US")

        client = SimpleNamespace(user_mod=Mock())
        client.user_mod.side_effect = lambda _username, **kwargs: self._apply_user_mod_to_dummy(fu, kwargs)

        req = self.factory.post(
            "/settings/",
            data={
                "tab": "profile",
                "givenname": "Alice",
                "sn": "User",
                "country_code": "FR",
                "fasPronoun": "",
                "fasLocale": "",
                "fasTimezone": "",
                "fasWebsiteUrl": "",
                "fasRssUrl": "",
                "fasIRCNick": "",
                "fasGitHubUsername": "",
                "fasGitLabUsername": "",
                "fasIsPrivate": "",
            },
        )
        self._add_session_and_messages(req)
        req.user = self._auth_user("alice")

        with (
            patch("core.views_settings._get_full_user", autospec=True, return_value=fu),
            patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu),
            patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client),
            patch.object(signal_module.user_country_changed, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            resp = settings_root(req)

        self.assertEqual(resp.status_code, 302)
        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs.get("sender"), IPAUser)
        self.assertEqual(kwargs.get("username"), "alice")
        self.assertEqual(kwargs.get("old_country"), "US")
        self.assertEqual(kwargs.get("new_country"), "FR")
        self.assertEqual(kwargs.get("actor"), "alice")

    @patch("core.forms_selfservice.get_timezone_options", autospec=True, return_value=["UTC"])
    @patch("core.forms_selfservice.get_locale_options", autospec=True, return_value=["en-US"])
    def test_profile_country_change_does_not_write_membership_notes_inline(self, _mock_locales, _mock_tzs):
        import importlib

        from core.models import MembershipRequest, MembershipType, MembershipTypeCategory, Note

        signal_module = importlib.import_module("core.signals")

        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 0},
        )
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )

        fu = _DummyFreeIPAUser(
            username="alice",
            first_name="Alice",
            last_name="User",
            email="alice@example.com",
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
            },
        )
        self._seed_valid_country_code(fu, code="US")

        client = SimpleNamespace(user_mod=Mock())
        client.user_mod.side_effect = lambda _username, **kwargs: self._apply_user_mod_to_dummy(fu, kwargs)

        req = self.factory.post(
            "/settings/",
            data={
                "tab": "profile",
                "givenname": "Alice",
                "sn": "User",
                "country_code": "FR",
                "fasPronoun": "",
                "fasLocale": "",
                "fasTimezone": "",
                "fasWebsiteUrl": "",
                "fasRssUrl": "",
                "fasIRCNick": "",
                "fasGitHubUsername": "",
                "fasGitLabUsername": "",
                "fasIsPrivate": "",
            },
        )
        self._add_session_and_messages(req)
        req.user = self._auth_user("alice")

        with (
            patch("core.views_settings._get_full_user", autospec=True, return_value=fu),
            patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu),
            patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client),
            patch.object(signal_module.user_country_changed, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            resp = settings_root(req)

        self.assertEqual(resp.status_code, 302)
        send_mock.assert_called_once()
        self.assertEqual(Note.objects.filter(membership_request=membership_request).count(), 0)

    @override_settings(SELF_SERVICE_ADDRESS_COUNTRY_ATTR="fasstatusnote")
    def test_user_profile_detail_self_shows_danger_when_missing_country_code(self):
        fu = _DummyFreeIPAUser(
            username="alice",
            first_name="Alice",
            last_name="User",
            email="alice@example.com",
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                # Missing fasstatusnote -> no country
            },
        )

        req = self.factory.get("/user/alice/")
        self._add_session_and_messages(req)
        req.user = self._auth_user("alice")

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=fu),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False),
            patch(
                "core.views_users.membership_review_permissions",
                autospec=True,
                return_value={
                    "membership_can_view": False,
                    "membership_can_add": False,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
        ):
            resp = user_profile_detail_api(req, "alice")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload["summary"]["isSelf"])
        required_action_ids = {action["id"] for action in payload["accountSetup"]["requiredActions"]}
        self.assertIn("country-code-missing-alert", required_action_ids)


    @patch("core.forms_selfservice.get_timezone_options", autospec=True, return_value=["UTC", "Europe/Paris"])
    @patch("core.forms_selfservice.get_locale_options", autospec=True, return_value=["en-US", "fr-FR"])
    def test_profile_edit_all_fas_fields(self, _mock_locales, _mock_tzs):
        fu = _DummyFreeIPAUser(
            first_name="Alice",
            last_name="User",
            email="alice@example.com",
            _user_data={
                "cn": ["Alice User"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "fasPronoun": ["she/her"],
                "fasLocale": ["en_US"],
                "fasTimezone": ["UTC"],
                "fasWebsiteUrl": ["https://old.example.com"],
                "fasRssUrl": ["https://old.example.com/rss.xml"],
                "fasIRCNick": ["irc://alice", "matrix://matrix.old/alice"],
                "fasGitHubUsername": ["alice-old"],
                "fasGitLabUsername": ["alice_old"],
                "fasIsPrivate": ["TRUE"],
            },
        )
        self._seed_valid_country_code(fu)

        client = SimpleNamespace(user_mod=Mock())
        client.user_mod.side_effect = lambda _username, **kwargs: self._apply_user_mod_to_dummy(fu, kwargs)

        before = self._load_profile(fu)
        self.assertIn("she/her", before.get("pronouns", ""))

        req = self.factory.post(
            "/settings/",
            data={
                "tab": "profile",
                "givenname": "Alicia",
                "sn": "User",
                "country_code": "US",
                "fasPronoun": "they/them",
                "fasLocale": "fr-FR",
                "fasTimezone": "Europe/Paris",
                "fasWebsiteUrl": "https://new.example.com",
                "fasRssUrl": "https://old.example.com/rss.xml\nhttps://new.example.com/rss.xml",
                "fasIRCNick": "alice:new.irc.example\n@alice:matrix.example",
                "fasGitHubUsername": "alice-1",
                "fasGitLabUsername": "alice_1",
            },
        )
        self._add_session_and_messages(req)
        req.user = self._auth_user()

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu):
                with patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client):
                    resp = settings_root(req)

        self.assertEqual(resp.status_code, 302)
        self._assert_user_mod_called_with_sets(
            client.user_mod,
            "alice",
            add={
                "fasPronoun=they/them",
                "fasWebsiteUrl=https://new.example.com",
                "fasRssUrl=https://new.example.com/rss.xml",
                "fasIRCNick=irc://new.irc.example/alice",
                "fasIRCNick=matrix://matrix.example/alice",
            },
            set_={
                "fasLocale=fr-FR",
                "fasTimezone=Europe/Paris",
                "fasGitHubUsername=alice-1",
                "fasGitLabUsername=alice_1",
            },
            del_={
                "fasPronoun=she/her",
                "fasWebsiteUrl=https://old.example.com",
                "fasIRCNick=irc://alice",
                "fasIRCNick=matrix://matrix.old/alice",
            },
            direct={
                "o_givenname": "Alicia",
                "o_cn": "Alicia User",
            },
        )

        after = self._load_profile(fu)
        self.assertIn("they/them", after.get("pronouns", ""))
        self.assertEqual(after["fu"].full_name, "Alicia User")

    @patch("core.forms_selfservice.get_timezone_options", autospec=True, return_value=["UTC"])
    @patch("core.forms_selfservice.get_locale_options", autospec=True, return_value=["en-US"])
    def test_profile_save_does_not_clear_private_profile_when_toggle_moved(self, _mock_locales, _mock_tzs):
        fu = _DummyFreeIPAUser(
            first_name="Alice",
            last_name="User",
            email="alice@example.com",
            _user_data={
                "cn": ["Alice User"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "fasIsPrivate": ["TRUE"],
            },
        )
        self._seed_valid_country_code(fu)

        client = SimpleNamespace(user_mod=Mock())
        client.user_mod.side_effect = lambda _username, **kwargs: self._apply_user_mod_to_dummy(fu, kwargs)

        req = self.factory.post(
            "/settings/",
            data={
                "tab": "profile",
                "givenname": "Alice",
                "sn": "User-Updated",
                "country_code": "US",
                "fasPronoun": "",
                "fasLocale": "",
                "fasTimezone": "",
                "fasWebsiteUrl": "",
                "fasRssUrl": "",
                "fasIRCNick": "",
                "fasGitHubUsername": "",
                "fasGitLabUsername": "",
            },
        )
        self._add_session_and_messages(req)
        req.user = self._auth_user()

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu):
                with patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client):
                    resp = settings_root(req)

        self.assertEqual(resp.status_code, 302)
        self._assert_user_mod_called_with_sets(
            client.user_mod,
            "alice",
            add=set(),
            set_=set(),
            del_=set(),
            direct={
                "o_sn": "User-Updated",
                "o_cn": "Alice User-Updated",
            },
        )
        self.assertEqual((fu._user_data or {}).get("fasIsPrivate"), ["TRUE"])

    @patch("core.forms_selfservice.get_timezone_options", autospec=True, return_value=["UTC"])
    @patch("core.forms_selfservice.get_locale_options", autospec=True, return_value=["en_US"])
    def test_profile_clear_all_fas_fields(self, _mock_locales, _mock_tzs):
        fu = _DummyFreeIPAUser(
            first_name="Alice",
            last_name="User",
            email="alice@example.com",
            _user_data={
                "cn": ["Alice User"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "fasPronoun": ["she/her", "they/them"],
                "fasLocale": ["en_US"],
                "fasTimezone": ["UTC"],
                "fasWebsiteUrl": ["https://example.com"],
                "fasRssUrl": ["https://example.com/rss.xml"],
                "fasIRCNick": ["irc://alice", "matrix://matrix.example/alice"],
                "fasGitHubUsername": ["alice-1"],
                "fasGitLabUsername": ["alice_1"],
                "fasIsPrivate": ["TRUE"],
            },
        )
        self._seed_valid_country_code(fu)

        client = SimpleNamespace(user_mod=Mock())
        client.user_mod.side_effect = lambda _username, **kwargs: self._apply_user_mod_to_dummy(fu, kwargs)

        before = self._load_profile(fu)
        self.assertTrue(before.get("pronouns"))

        req = self.factory.post(
            "/settings/",
            data={
                "tab": "profile",
                "givenname": "Alice",
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
            },
        )
        self._add_session_and_messages(req)
        req.user = self._auth_user()

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu):
                with patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client):
                    resp = settings_root(req)

        self.assertEqual(resp.status_code, 302)
        self._assert_user_mod_called_with_sets(
            client.user_mod,
            "alice",
            add=set(),
            set_=set(),
            del_={
                "fasPronoun=she/her",
                "fasPronoun=they/them",
                "fasLocale=",
                "fasTimezone=",
                "fasWebsiteUrl=https://example.com",
                "fasRssUrl=https://example.com/rss.xml",
                "fasIRCNick=irc://alice",
                "fasIRCNick=matrix://matrix.example/alice",
                "fasGitHubUsername=",
                "fasGitLabUsername=",
            },
        )

        after = self._load_profile(fu)
        self.assertEqual(after.get("pronouns", ""), "")

    def test_emails_set_edit_clear_fasrhbzemail(self):
        fu = _DummyFreeIPAUser(
            first_name="",
            last_name="",
            email="",
            _user_data={},
        )
        self._seed_valid_country_code(fu)
        client = SimpleNamespace(user_mod=Mock())
        client.user_mod.side_effect = lambda _username, **kwargs: self._apply_user_mod_to_dummy(fu, kwargs)

        self._load_profile(fu)

        # set
        req = self.factory.post(
            "/settings/",
            data={
                "tab": "emails",
                "mail": "alice@example.com",
                "fasRHBZEmail": "alice@bugzilla.example",
            },
        )
        self._add_session_and_messages(req)
        req.user = self._auth_user()

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu):
                with patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client):
                    with patch("core.views_settings.user_email_context", autospec=True, return_value={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "full_name": "Alice User",
                        "email": "alice@example.com",
                    }):
                        with patch("post_office.mail.send", autospec=True) as send_mock:
                            resp = settings_root(req)

        self.assertEqual(resp.status_code, 302)
        # Email changes are deferred until validated; no direct FreeIPA updates here.
        client.user_mod.assert_not_called()
        self.assertEqual(send_mock.call_count, 2)

        after_set = self._load_profile(fu)
        self.assertEqual(after_set["fu"].email, "")

        # edit
        fu.email = "alice@example.com"
        fu._user_data = {
            "mail": ["alice@example.com"],
            "fasRHBZEmail": ["alice@bugzilla.example"],
        }
        self._seed_valid_country_code(fu)
        client.user_mod.reset_mock()

        req2 = self.factory.post(
            "/settings/",
            data={
                "tab": "emails",
                "mail": "alice2@example.com",
                "fasRHBZEmail": "alice2@bugzilla.example",
            },
        )
        self._add_session_and_messages(req2)
        req2.user = self._auth_user()

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu):
                with patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client):
                    with patch("core.views_settings.user_email_context", autospec=True, return_value={
                        "username": "alice",
                        "first_name": "Alice",
                        "last_name": "User",
                        "full_name": "Alice User",
                        "email": "alice@example.com",
                    }):
                        with patch("post_office.mail.send", autospec=True) as send_mock2:
                            resp2 = settings_root(req2)

        self.assertEqual(resp2.status_code, 302)
        client.user_mod.assert_not_called()
        self.assertEqual(send_mock2.call_count, 2)

        after_edit = self._load_profile(fu)
        # Still unchanged until validation.
        self.assertEqual(after_edit["fu"].email, "alice@example.com")

        # clear
        fu.email = "alice2@example.com"
        fu._user_data = {
            "mail": ["alice2@example.com"],
            "fasRHBZEmail": ["alice2@bugzilla.example"],
        }
        self._seed_valid_country_code(fu)
        client.user_mod.reset_mock()

        req3 = self.factory.post(
            "/settings/",
            data={
                "tab": "emails",
                "mail": "alice2@example.com",
                "fasRHBZEmail": "",
            },
        )
        self._add_session_and_messages(req3)
        req3.user = self._auth_user()

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu):
                with patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client):
                    resp3 = settings_root(req3)

        self.assertEqual(resp3.status_code, 302)
        call_args, call_kwargs = client.user_mod.call_args
        self.assertEqual(call_args[0], "alice")
        self.assertEqual(set(call_kwargs.get("o_delattr", []) or []), {"fasRHBZEmail="})

        after_clear = self._load_profile(fu)
        self.assertEqual(after_clear["fu"].email, "alice2@example.com")

    def test_keys_set_edit_clear_fas_keys(self):
        fu = _DummyFreeIPAUser(
            first_name="Alice",
            last_name="User",
            email="alice@example.com",
            _user_data={
                "fasGPGKeyId": [],
                "ipasshpubkey": [],
            },
        )
        self._seed_valid_country_code(fu)
        client = SimpleNamespace(user_mod=Mock())
        client.user_mod.side_effect = lambda _username, **kwargs: self._apply_user_mod_to_dummy(fu, kwargs)

        self._load_profile(fu)

        # set
        req = self.factory.post(
            "/settings/",
            data={
                "tab": "keys",
                "fasGPGKeyId": "0123456789ABCDEF\nFEDCBA9876543210",
                "ipasshpubkey": "ssh-ed25519 AAAA alice@laptop\nssh-rsa AAAA alice@desktop",
            },
        )
        self._add_session_and_messages(req)
        req.user = self._auth_user()

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu):
                with patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client):
                    resp = settings_root(req)

        self.assertEqual(resp.status_code, 302)
        self._assert_user_mod_called_with_sets(
            client.user_mod,
            "alice",
            add={
                "fasGPGKeyId=0123456789ABCDEF",
                "fasGPGKeyId=FEDCBA9876543210",
                "ipasshpubkey=ssh-ed25519 AAAA alice@laptop",
                "ipasshpubkey=ssh-rsa AAAA alice@desktop",
            },
            set_=set(),
            del_=set(),
        )

        self._load_profile(fu)

        # edit
        fu._user_data["fasGPGKeyId"] = ["0123456789ABCDEF", "FEDCBA9876543210"]
        fu._user_data["ipasshpubkey"] = ["ssh-ed25519 AAAA alice@laptop", "ssh-rsa AAAA alice@desktop"]
        client.user_mod.reset_mock()

        req2 = self.factory.post(
            "/settings/",
            data={
                "tab": "keys",
                "fasGPGKeyId": "0123456789ABCDEF\nAAAAAAAAAAAAAAAA",
                "ipasshpubkey": "ssh-ed25519 AAAA alice@laptop\nssh-ed25519 AAAA alice@phone",
            },
        )
        self._add_session_and_messages(req2)
        req2.user = self._auth_user()

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu):
                with patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client):
                    resp2 = settings_root(req2)

        self.assertEqual(resp2.status_code, 302)
        self._assert_user_mod_called_with_sets(
            client.user_mod,
            "alice",
            add={
                "fasGPGKeyId=AAAAAAAAAAAAAAAA",
                "ipasshpubkey=ssh-ed25519 AAAA alice@phone",
            },
            set_=set(),
            del_={
                "fasGPGKeyId=FEDCBA9876543210",
                "ipasshpubkey=ssh-rsa AAAA alice@desktop",
            },
        )

        self._load_profile(fu)

        # clear
        client.user_mod.reset_mock()
        req3 = self.factory.post(
            "/settings/",
            data={
                "tab": "keys",
                "fasGPGKeyId": "",
                "ipasshpubkey": "",
            },
        )
        self._add_session_and_messages(req3)
        req3.user = self._auth_user()

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.ipa_user_attrs.FreeIPAUser.get", autospec=True, return_value=fu):
                with patch("core.ipa_user_attrs.FreeIPAUser.get_client", autospec=True, return_value=client):
                    resp3 = settings_root(req3)

        self.assertEqual(resp3.status_code, 302)
        call_args, call_kwargs = client.user_mod.call_args
        self.assertEqual(call_args[0], "alice")
        self.assertEqual(
            set(call_kwargs.get("o_delattr", []) or []),
            {
                "fasGPGKeyId=0123456789ABCDEF",
                "fasGPGKeyId=AAAAAAAAAAAAAAAA",
                "ipasshpubkey=ssh-ed25519 AAAA alice@laptop",
                "ipasshpubkey=ssh-ed25519 AAAA alice@phone",
            },
        )

        self._load_profile(fu)

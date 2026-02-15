
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core import views_settings, views_users
from core.backends import FreeIPAFASAgreement


class AgreementsSelfServiceTests(TestCase):
    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _auth_user(self, username: str = "alice"):
        return SimpleNamespace(is_authenticated=True, get_username=lambda: username)

    def test_profile_includes_signed_agreements(self):
        factory = RequestFactory()
        request = factory.get("/user/alice/")
        request.user = self._auth_user("alice")

        fu = SimpleNamespace(
            username="alice",
            email="a@example.org",
            is_authenticated=True,
            get_username=lambda: "alice",
            groups_list=[],
            _user_data={"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"]},
        )

        agreements = [FreeIPAFASAgreement("cla", {"cn": ["cla"], "ipaenabledflag": ["TRUE"]})]
        agreement_detail = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "memberuser_user": ["alice"],
                "description": ["CLA text"],
            },
        )

        captured: dict[str, object] = {}

        def fake_render(_request, template, context):
            captured["template"] = template
            captured["context"] = context
            return HttpResponse("ok")

        with patch("core.views_users._get_full_user", autospec=True, return_value=fu):
            with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]):
                with patch("core.backends.FreeIPAFASAgreement.all", autospec=True, return_value=agreements):
                    with patch(
                        "core.backends.FreeIPAFASAgreement.get",
                        autospec=True,
                        return_value=agreement_detail,
                    ):
                        with patch("core.views_users.render", autospec=True, side_effect=fake_render):
                            resp = views_users.user_profile(request, "alice")

        self.assertEqual(resp.status_code, 200)
        ctx = captured["context"]
        self.assertEqual(len(ctx["agreements"]), 1)
        self.assertIn("cla", ctx["agreements"])

    def test_profile_shows_missing_required_agreements_for_member_group_with_link_for_self(self):
        factory = RequestFactory()
        request = factory.get("/user/alice/")
        request.user = self._auth_user("alice")

        fu = SimpleNamespace(
            username="alice",
            email="",
            is_authenticated=True,
            get_username=lambda: "alice",
            get_full_name=lambda: "Alice User",
            groups_list=["packagers"],
            _user_data={"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"]},
        )

        # This agreement gates the 'packagers' group and the user has not signed it.
        agreement_summary = SimpleNamespace(cn="cla", enabled=True, groups=["packagers"], users=[])
        agreement_full = SimpleNamespace(
            cn="cla",
            enabled=True,
            groups=["packagers"],
            users=[],
            description="CLA text",
        )

        fas_group = SimpleNamespace(cn="packagers", fas_group=True, sponsors=[])

        captured: dict[str, object] = {}

        def fake_render(_request, template, context):
            captured["template"] = template
            captured["context"] = context
            return HttpResponse("ok")

        with patch("core.views_users._get_full_user", autospec=True, return_value=fu):
            with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[fas_group]):
                with patch("core.agreements.FreeIPAFASAgreement.all", autospec=True, return_value=[agreement_summary]):
                    with patch(
                        "core.agreements.FreeIPAFASAgreement.get",
                        autospec=True,
                        return_value=agreement_full,
                    ):
                        with patch("core.views_users.render", autospec=True, side_effect=fake_render):
                            resp = views_users.user_profile(request, "alice")

        self.assertEqual(resp.status_code, 200)
        ctx = cast(dict[str, object], captured["context"])
        self.assertEqual(len(ctx["missing_agreements"]), 1)
        missing = cast(list[dict[str, object]], ctx["missing_agreements"])
        self.assertEqual(missing[0]["cn"], "cla")
        self.assertEqual(
            missing[0]["settings_url"],
            reverse("settings") + "?tab=agreements&agreement=cla",
        )

    def test_settings_agreements_lists_enabled_agreements(self):
        factory = RequestFactory()
        request = factory.get("/settings/?tab=agreements")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fu = SimpleNamespace(
            username="alice",
            is_authenticated=True,
            get_username=lambda: "alice",
            groups_list=[],
            _user_data={"uid": ["alice"]},
        )

        agreements = [FreeIPAFASAgreement("cla", {"cn": ["cla"], "ipaenabledflag": ["TRUE"]})]
        agreement_detail = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                # The user isn't in this group yet, but they still must be able to
                # sign agreements ahead of joining.
                "member_group": ["packagers"],
                "memberuser_user": [],
                "description": ["CLA text"],
            },
        )

        captured: dict[str, object] = {}

        def fake_render(_request, template, context):
            captured["template"] = template
            captured["context"] = context
            return HttpResponse("ok")

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.backends.FreeIPAFASAgreement.all", autospec=True, return_value=agreements):
                with patch(
                    "core.backends.FreeIPAFASAgreement.get",
                    autospec=True,
                    return_value=agreement_detail,
                ):
                    with patch("core.views_settings.render", autospec=True, side_effect=fake_render):
                        resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 200)
        ctx = captured["context"]
        self.assertEqual([a.cn for a in ctx["agreements"]], ["cla"])

    def test_settings_agreements_renders_required_for_group_and_danger_not_signed_badge(self):
        factory = RequestFactory()
        request = factory.get("/settings/?tab=agreements")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fu = SimpleNamespace(
            username="alice",
            is_authenticated=True,
            get_username=lambda: "alice",
            groups_list=[],
            _user_data={"uid": ["alice"]},
        )

        agreements = [FreeIPAFASAgreement("cla", {"cn": ["cla"], "ipaenabledflag": ["TRUE"]})]
        agreement_detail = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "member_group": ["packagers"],
                "memberuser_user": [],
                "description": ["CLA text"],
            },
        )

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=True):
                with patch("core.backends.FreeIPAFASAgreement.all", autospec=True, return_value=agreements):
                    with patch(
                        "core.backends.FreeIPAFASAgreement.get",
                        autospec=True,
                        return_value=agreement_detail,
                    ):
                        resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")

        self.assertIn('badge badge-danger', content)
        self.assertIn('Not signed', content)
        self.assertIn('Required for:', content)
        self.assertIn(
            f'href="{reverse("group-detail", kwargs={"name": "packagers"})}"',
            content,
        )
        self.assertIn('>packagers<', content)

    def test_settings_agreement_detail_renders_required_for_group_and_danger_not_signed_badge(self):
        factory = RequestFactory()
        request = factory.get("/settings/?tab=agreements&agreement=cla")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fu = SimpleNamespace(
            username="alice",
            is_authenticated=True,
            get_username=lambda: "alice",
            groups_list=[],
            _user_data={"uid": ["alice"], "fasstatusnote": ["US"]},
        )

        agreement_detail = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "member_group": ["packagers"],
                "memberuser_user": [],
                "description": ["CLA text"],
            },
        )

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=True):
                with patch("core.views_settings.list_agreements_for_user", autospec=True, return_value=[]):
                    with patch(
                        "core.backends.FreeIPAFASAgreement.get",
                        autospec=True,
                        return_value=agreement_detail,
                    ):
                        resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")

        self.assertIn('badge badge-danger', content)
        self.assertIn('Not signed', content)
        self.assertIn('Required for:', content)
        self.assertIn(
            f'href="{reverse("group-detail", kwargs={"name": "packagers"})}"',
            content,
        )
        self.assertIn('>packagers<', content)

    def test_settings_agreements_post_signs_agreement(self):
        factory = RequestFactory()
        request = factory.post(
            "/settings/",
            data={"tab": "agreements", "action": "sign", "cn": "cla"},
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fu = SimpleNamespace(
            username="alice",
            is_authenticated=True,
            get_username=lambda: "alice",
            groups_list=[],
            _user_data={"uid": ["alice"]},
        )

        agreement = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "member_group": ["packagers"],
                "memberuser_user": [],
                "description": ["CLA text"],
            },
        )

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=True):
                with patch("core.views_settings.list_agreements_for_user", autospec=True, return_value=[]):
                    with patch("core.backends.FreeIPAFASAgreement.get", autospec=True, return_value=agreement):
                        with patch.object(agreement, "add_user", autospec=True) as mocked_add:
                            resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("settings") + "?tab=agreements")
        mocked_add.assert_called_once_with("alice")
        msgs = [m.message for m in get_messages(request)]
        self.assertTrue(any("signed" in m.lower() for m in msgs))

    def test_settings_agreements_redirects_when_no_enabled_agreements(self):
        factory = RequestFactory()
        request = factory.get("/settings/?tab=agreements")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fu = SimpleNamespace(
            username="alice",
            is_authenticated=True,
            get_username=lambda: "alice",
            groups_list=[],
            _user_data={"uid": ["alice"]},
        )

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.backends.FreeIPAFASAgreement.all", autospec=True, return_value=[]):
                resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("settings") + "?tab=profile")

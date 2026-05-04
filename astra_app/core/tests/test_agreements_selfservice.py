
import json
import re
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core import views_settings, views_users, views_utils
from core.agreements import AgreementForUser
from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.exceptions import FreeIPAOperationFailed


class AgreementsSelfServiceTests(TestCase):
    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _auth_user(self, username: str = "alice"):
        return SimpleNamespace(is_authenticated=True, get_username=lambda: username)

    def _settings_payload(self, response: HttpResponse) -> dict[str, object]:
        match = re.search(
            r'<script id="settings-initial-payload" type="application/json">(.*?)</script>',
            response.content.decode("utf-8"),
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        return cast(dict[str, object], json.loads(match.group(1)))

    def test_user_profile_detail_includes_signed_agreements(self):
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["alice"]))
        request.user = self._auth_user("alice")

        fu = SimpleNamespace(
            username="alice",
            email="a@example.org",
            is_authenticated=True,
            get_username=lambda: "alice",
            get_full_name=lambda: "Alice User",
            groups_list=[],
            _user_data={"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"]},
        )

        agreements = [
            FreeIPAFASAgreement(
                "cla",
                {
                    "cn": ["cla"],
                    "ipaenabledflag": ["TRUE"],
                    "memberuser_user": ["alice"],
                    "description": ["CLA text"],
                },
            )
        ]
        agreement_detail = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "memberuser_user": ["alice"],
                "description": ["CLA text"],
            },
        )

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=fu),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[]),
            patch("core.freeipa.agreement.FreeIPAFASAgreement.all", autospec=True, return_value=agreements),
            patch("core.freeipa.agreement.FreeIPAFASAgreement.get", autospec=True, return_value=agreement_detail),
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
            resp = views_users.user_profile_detail_api(request, "alice")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertEqual(payload["groups"]["agreements"], ["cla"])

    def test_user_profile_detail_shows_missing_required_agreements_for_member_group(self):
        factory = RequestFactory()
        request = factory.get(reverse("api-user-profile-detail", args=["alice"]))
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
        agreement_summary = SimpleNamespace(
            cn="cla",
            enabled=True,
            groups=["packagers"],
            users=[],
            description="CLA text",
        )
        agreement_full = SimpleNamespace(
            cn="cla",
            enabled=True,
            groups=["packagers"],
            users=[],
            description="CLA text",
        )

        fas_group = SimpleNamespace(cn="packagers", fas_group=True, sponsors=[])

        with (
            patch("core.views_users._get_full_user", autospec=True, return_value=fu),
            patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[fas_group]),
            patch("core.agreements.FreeIPAFASAgreement.all", autospec=True, return_value=[agreement_summary]),
            patch("core.agreements.FreeIPAFASAgreement.get", autospec=True, return_value=agreement_full),
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
            resp = views_users.user_profile_detail_api(request, "alice")

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        missing = payload["groups"]["missingAgreements"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0], {"cn": "cla", "requiredBy": ["packagers"]})

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

        agreements = [
            FreeIPAFASAgreement(
                "cla",
                {
                    "cn": ["cla"],
                    "ipaenabledflag": ["TRUE"],
                    "member_group": ["packagers"],
                    "memberuser_user": [],
                    "description": ["CLA text"],
                },
            )
        ]
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
            with patch("core.freeipa.agreement.FreeIPAFASAgreement.all", autospec=True, return_value=agreements):
                with patch(
                    "core.freeipa.agreement.FreeIPAFASAgreement.get",
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

        agreements = [
            FreeIPAFASAgreement(
                "cla",
                {
                    "cn": ["cla"],
                    "ipaenabledflag": ["TRUE"],
                    "member_group": ["packagers"],
                    "memberuser_user": [],
                    "description": ["CLA text"],
                },
            )
        ]
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
                with patch("core.freeipa.agreement.FreeIPAFASAgreement.all", autospec=True, return_value=agreements):
                    with patch(
                        "core.freeipa.agreement.FreeIPAFASAgreement.get",
                        autospec=True,
                        return_value=agreement_detail,
                    ):
                        resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 200)
        payload = self._settings_payload(resp)
        self.assertEqual(payload["active_tab"], "agreements")
        self.assertEqual(
            payload["agreements"],
            {
                "agreement": None,
                "agreements": [
                    {
                        "cn": "cla",
                        "groups": ["packagers"],
                        "signed": False,
                    }
                ],
            },
        )

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

        agreement_detail = AgreementForUser(
            cn="cla",
            description="CLA text",
            signed=False,
            applicable=True,
            enabled=True,
            groups=("packagers",),
        )

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fu):
            with patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=True):
                with patch(
                    "core.views_settings.list_agreements_for_user",
                    autospec=True,
                    return_value=[agreement_detail],
                ):
                        resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 200)
        payload = self._settings_payload(resp)
        self.assertEqual(payload["active_tab"], "agreements")
        self.assertEqual(
            payload["agreements"],
            {
                "agreement": {
                    "cn": "cla",
                    "description_markdown": "CLA text",
                    "groups": ["packagers"],
                    "signed": False,
                },
                "agreements": [
                    {
                        "cn": "cla",
                        "groups": ["packagers"],
                        "signed": False,
                    }
                ],
            },
        )

    def test_settings_agreement_detail_and_sign_post_use_shared_signed_state(self):
        factory = RequestFactory()
        detail_request = factory.get("/api/v1/settings/detail?tab=agreements&agreement=cla")
        detail_request.user = self._auth_user("alice")

        post_request = factory.post(
            "/settings/",
            data={"tab": "agreements", "action": "sign", "cn": "cla"},
        )
        self._add_session_and_messages(post_request)
        post_request.user = self._auth_user("alice")

        fu = SimpleNamespace(
            username="alice",
            is_authenticated=True,
            get_username=lambda: "alice",
            groups_list=[],
            _user_data={"uid": ["alice"], "fasstatusnote": ["US"]},
        )

        listed_agreement = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "member_group": ["packagers"],
                "memberuser_user": ["alice"],
                "description": ["CLA text"],
            },
        )
        stale_agreement = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "member_group": ["packagers"],
                "memberuser_user": [],
                "description": ["CLA text"],
            },
        )

        with (
            patch("core.views_settings._get_full_user", autospec=True, return_value=fu),
            patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=True),
            patch("core.agreements.FreeIPAFASAgreement.all", autospec=True, return_value=[listed_agreement]),
            patch("core.freeipa.agreement.FreeIPAFASAgreement.get", autospec=True, return_value=stale_agreement),
        ):
            detail_resp = views_settings.settings_detail_api(detail_request)

            with patch.object(stale_agreement, "add_user", autospec=True) as mocked_add:
                post_resp = views_settings.settings_root(post_request)

        self.assertEqual(detail_resp.status_code, 200)
        detail_payload = json.loads(detail_resp.content)
        self.assertTrue(detail_payload["agreements"]["agreement"]["signed"])

        self.assertEqual(post_resp.status_code, 302)
        self.assertEqual(post_resp["Location"], reverse("settings") + "?tab=agreements")
        mocked_add.assert_not_called()
        msgs = [m.message for m in get_messages(post_request)]
        self.assertTrue(any("already signed" in m.lower() for m in msgs))

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
                    with patch("core.freeipa.agreement.FreeIPAFASAgreement.get", autospec=True, return_value=agreement):
                        with patch.object(agreement, "add_user", autospec=True) as mocked_add:
                            resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("settings") + "?tab=agreements")
        mocked_add.assert_called_once_with("alice")
        msgs = [m.message for m in get_messages(request)]
        self.assertTrue(any("signed" in m.lower() for m in msgs))

    def test_settings_agreements_post_duplicate_add_rechecks_signed_state(self):
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

        shared_unsigned = AgreementForUser(
            cn="cla",
            description="CLA text",
            signed=False,
            applicable=True,
            enabled=True,
            groups=("packagers",),
        )
        stale_agreement = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "member_group": ["packagers"],
                "memberuser_user": [],
                "description": ["CLA text"],
            },
        )
        signed_agreement = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "member_group": ["packagers"],
                "memberuser_user": ["alice"],
                "description": ["CLA text"],
            },
        )

        with (
            patch("core.views_settings._get_full_user", autospec=True, return_value=fu),
            patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=True),
            patch("core.views_settings.get_agreement_for_user", autospec=True, return_value=shared_unsigned),
            patch(
                "core.freeipa.agreement.FreeIPAFASAgreement.get",
                autospec=True,
                side_effect=[stale_agreement, signed_agreement],
            ) as mocked_get,
            patch.object(
                stale_agreement,
                "add_user",
                autospec=True,
                side_effect=FreeIPAOperationFailed(
                    "FreeIPA fasagreement_add_user did not persist (agreement=cla user=alice)"
                ),
            ) as mocked_add,
        ):
            resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("settings") + "?tab=agreements")
        self.assertEqual(mocked_get.call_count, 2)
        mocked_add.assert_called_once_with("alice")
        msgs = [m.message for m in get_messages(request)]
        self.assertTrue(any("already signed" in m.lower() for m in msgs))
        self.assertFalse(any("internal error" in m.lower() for m in msgs))

    def test_mixed_case_session_username_uses_signed_coc_state_across_settings_post_and_gate(self):
        factory = RequestFactory()

        detail_request = factory.get("/api/v1/settings/detail?tab=agreements&agreement=cla")
        self._add_session_and_messages(detail_request)
        detail_request.session["_freeipa_username"] = "Linuxmonger"
        detail_request.user = self._auth_user("linuxmonger")

        post_request = factory.post(
            "/settings/",
            data={"tab": "agreements", "action": "sign", "cn": "cla"},
        )
        self._add_session_and_messages(post_request)
        post_request.session["_freeipa_username"] = "Linuxmonger"
        post_request.user = self._auth_user("linuxmonger")

        gate_request = factory.get("/membership/request/")
        self._add_session_and_messages(gate_request)
        gate_request.session["_freeipa_username"] = "Linuxmonger"
        gate_request.user = self._auth_user("linuxmonger")

        fu = SimpleNamespace(
            username="linuxmonger",
            is_authenticated=True,
            get_username=lambda: "linuxmonger",
            groups_list=[],
            _user_data={"uid": ["linuxmonger"], "fasstatusnote": ["US"]},
        )

        agreement = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "memberuser_user": ["linuxmonger"],
                "description": ["CLA text"],
            },
        )

        with (
            patch("core.views_settings._get_full_user", autospec=True, return_value=fu),
            patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=True),
            patch("core.views_utils.settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN", "cla"),
            patch("core.agreements.FreeIPAFASAgreement.all", autospec=True, return_value=[agreement]),
            patch("core.freeipa.agreement.FreeIPAFASAgreement.get", autospec=True, return_value=agreement),
            patch.object(
                agreement,
                "add_user",
                autospec=True,
                side_effect=FreeIPAOperationFailed(
                    "FreeIPA fasagreement_add_user did not persist (agreement=cla user=Linuxmonger)"
                ),
            ) as mocked_add,
        ):
            detail_resp = views_settings.settings_detail_api(detail_request)
            post_resp = views_settings.settings_root(post_request)
            gate_resp = views_utils.block_action_without_coc(
                gate_request,
                username=views_utils.get_username(gate_request),
                action_label="request or renew memberships",
            )

        self.assertEqual(detail_resp.status_code, 200)
        detail_payload = json.loads(detail_resp.content)
        self.assertTrue(detail_payload["agreements"]["agreement"]["signed"])

        self.assertEqual(post_resp.status_code, 302)
        self.assertEqual(post_resp["Location"], reverse("settings") + "?tab=agreements")
        mocked_add.assert_not_called()
        post_messages = [message.message for message in get_messages(post_request)]
        self.assertTrue(any("already signed" in message.lower() for message in post_messages))
        self.assertFalse(any("internal error" in message.lower() for message in post_messages))

        self.assertIsNone(gate_resp)

    def test_settings_agreements_post_duplicate_add_treats_mixed_case_detail_membership_as_already_signed(self):
        factory = RequestFactory()
        request = factory.post(
            "/settings/",
            data={"tab": "agreements", "action": "sign", "cn": "cla"},
        )
        self._add_session_and_messages(request)
        request.session["_freeipa_username"] = "Linuxmonger"
        request.user = self._auth_user("linuxmonger")

        fu = SimpleNamespace(
            username="linuxmonger",
            is_authenticated=True,
            get_username=lambda: "linuxmonger",
            groups_list=[],
            _user_data={"uid": ["linuxmonger"]},
        )

        shared_unsigned = AgreementForUser(
            cn="cla",
            description="CLA text",
            signed=False,
            applicable=True,
            enabled=True,
            groups=("packagers",),
        )
        stale_agreement = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "member_group": ["packagers"],
                "memberuser_user": [],
                "description": ["CLA text"],
            },
        )
        refreshed_mixed_case_agreement = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "ipaenabledflag": ["TRUE"],
                "member_group": ["packagers"],
                "memberuser_user": ["Linuxmonger"],
                "description": ["CLA text"],
            },
        )

        with (
            patch("core.views_settings._get_full_user", autospec=True, return_value=fu),
            patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=True),
            patch("core.views_settings.get_agreement_for_user", autospec=True, return_value=shared_unsigned),
            patch(
                "core.freeipa.agreement.FreeIPAFASAgreement.get",
                autospec=True,
                side_effect=[stale_agreement, refreshed_mixed_case_agreement],
            ) as mocked_get,
            patch.object(
                stale_agreement,
                "add_user",
                autospec=True,
                side_effect=FreeIPAOperationFailed(
                    "FreeIPA fasagreement_add_user did not persist (agreement=cla user=Linuxmonger)"
                ),
            ) as mocked_add,
        ):
            response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "?tab=agreements")
        self.assertEqual(mocked_get.call_count, 2)
        mocked_add.assert_called_once_with("linuxmonger")
        messages = [message.message for message in get_messages(request)]
        self.assertTrue(any("already signed" in message.lower() for message in messages))
        self.assertFalse(any("internal error" in message.lower() for message in messages))

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
            with patch("core.freeipa.agreement.FreeIPAFASAgreement.all", autospec=True, return_value=[]):
                resp = views_settings.settings_root(request)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("settings") + "?tab=profile")

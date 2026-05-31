import io
import json
from importlib import import_module
from urllib.parse import quote, unquote

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.e2e_registry import get_e2e_service_client
from core.models import FreeIPAPermissionGrant, Membership, MembershipRequest, MembershipType, Note, Organization
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP
from core.rate_limit import (
    _rate_limit_cache_key,
    _rate_limit_subject_index_key,
    allow_request,
    clear_subject_rate_limit,
)
from core.tokens import (
    read_password_reset_token,
    read_registration_activation_token,
    read_settings_email_validation_token,
)


class AuthProfileResetCommandTests(TestCase):
    def _create_session(self, *, username: str | None = None) -> str:
        session_store = import_module("django.contrib.sessions.backends.db").SessionStore()
        if username is not None:
            session_store["_freeipa_username"] = username
        session_store.save()
        return session_store.session_key

    def _normalized_reset_payload(self, payload: dict[str, object]) -> dict[str, object]:
        routes = dict(payload["routes"])

        password_reset_route = str(routes["password_reset_confirm"])
        password_reset_token = unquote(password_reset_route.split("token=", 1)[1])
        routes["password_reset_confirm"] = {
            "route": reverse("password-reset-confirm"),
            "payload": read_password_reset_token(password_reset_token),
        }

        primary_validate_route = str(routes["settings_email_validate_primary"])
        primary_validate_token = unquote(primary_validate_route.split("token=", 1)[1])
        routes["settings_email_validate_primary"] = {
            "route": reverse("settings-email-validate"),
            "payload": read_settings_email_validation_token(primary_validate_token),
        }

        bugzilla_validate_route = str(routes["settings_email_validate_bugzilla"])
        bugzilla_validate_token = unquote(bugzilla_validate_route.split("token=", 1)[1])
        routes["settings_email_validate_bugzilla"] = {
            "route": reverse("settings-email-validate"),
            "payload": read_settings_email_validation_token(bugzilla_validate_token),
        }

        register_activate_route = str(routes["register_activate"])
        register_activate_token = unquote(register_activate_route.split("token=", 1)[1])
        routes["register_activate"] = {
            "route": reverse("register-activate"),
            "payload": read_registration_activation_token(register_activate_token),
        }

        return {
            **payload,
            "routes": routes,
        }

    @override_settings(ASTRA_E2E_MODE=False, ASTRA_E2E_FAKE_FREEIPA_ENABLED=False)
    def test_command_rejects_runs_outside_fake_freeipa_e2e_mode(self) -> None:
        with self.assertRaisesMessage(CommandError, "ASTRA_E2E_FAKE_FREEIPA_ENABLED"):
            call_command("auth_profile_reset")

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_only_clears_fake_auth_sessions_and_converges_without_creating_unrelated_data(self) -> None:
        stdout_first = io.StringIO()
        stdout_second = io.StringIO()
        client = get_e2e_service_client()
        regular_session_key = self._create_session(username="regular")
        numbered_regular_session_key = self._create_session(username="regular07")
        admin_session_key = self._create_session(username="ADMIN")
        other_session_key = self._create_session(username="other-user")
        anonymous_session_key = self._create_session()

        client.group_add("new-membership-group", o_description="Membership type group")
        FreeIPAFASAgreement.create("AlmaLinux Community Code of Conduct", description="CoC")

        call_command("auth_profile_reset", stdout=stdout_first)
        first_payload = json.loads(stdout_first.getvalue())

        call_command("auth_profile_reset", stdout=stdout_second)
        second_payload = json.loads(stdout_second.getvalue())

        self.assertEqual(first_payload["scenario"], "auth-profile")
        self.assertEqual(second_payload["scenario"], "auth-profile")
        self.assertEqual(
            self._normalized_reset_payload(first_payload),
            self._normalized_reset_payload(second_payload),
        )
        self.assertFalse(Session.objects.filter(session_key=regular_session_key).exists())
        self.assertFalse(Session.objects.filter(session_key=numbered_regular_session_key).exists())
        self.assertFalse(Session.objects.filter(session_key=admin_session_key).exists())
        self.assertTrue(Session.objects.filter(session_key=other_session_key).exists())
        self.assertTrue(Session.objects.filter(session_key=anonymous_session_key).exists())
        self.assertEqual(client.group_find(o_cn="new-membership-group", o_all=True, o_no_members=False), {"count": 0, "result": []})
        self.assertEqual(
            sorted(agreement.cn for agreement in FreeIPAFASAgreement.all()),
            ["AlmaLinux Community Code of Conduct", "e2e-contributor-agreement"],
        )
        self.assertEqual(client.group_find(o_cn="packagers", o_all=True, o_no_members=False)["count"], 1)
        self.assertEqual(Note.objects.count(), 0)
        self.assertEqual(Organization.objects.count(), 0)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_clears_login_rate_limit_state_for_fake_e2e_users(self) -> None:
        client_ip = "203.0.113.10"

        self.assertTrue(
            allow_request(
                scope="auth.login",
                key_parts=[client_ip, "regular01"],
                limit=2,
                window_seconds=60,
            )
        )
        self.assertTrue(
            allow_request(
                scope="auth.login",
                key_parts=[client_ip, "regular01"],
                limit=2,
                window_seconds=60,
            )
        )
        self.assertFalse(
            allow_request(
                scope="auth.login",
                key_parts=[client_ip, "regular01"],
                limit=2,
                window_seconds=60,
            )
        )

        call_command("auth_profile_reset")

        self.assertTrue(
            allow_request(
                scope="auth.login",
                key_parts=[client_ip, "regular01"],
                limit=2,
                window_seconds=60,
            )
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_clears_legacy_unindexed_login_rate_limit_state(self) -> None:
        client_ip = "203.0.113.11"

        self.assertTrue(
            allow_request(
                scope="auth.login",
                key_parts=[client_ip, "admin"],
                limit=1,
                window_seconds=60,
            )
        )
        self.assertFalse(
            allow_request(
                scope="auth.login",
                key_parts=[client_ip, "admin"],
                limit=1,
                window_seconds=60,
            )
        )

        cache.delete(_rate_limit_subject_index_key("auth.login", "admin"))

        call_command("auth_profile_reset")

        self.assertTrue(
            allow_request(
                scope="auth.login",
                key_parts=[client_ip, "admin"],
                limit=1,
                window_seconds=60,
            )
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_emits_deterministic_playwright_reset_state_for_auth_profile_flows(self) -> None:
        stdout = io.StringIO()

        call_command("auth_profile_reset", stdout=stdout)

        payload = json.loads(stdout.getvalue())

        self.assertEqual(payload["scenario"], "auth-profile")
        self.assertEqual(payload["status"], "reset")
        self.assertEqual(
            payload["actors"]["regular03"],
            {
                "username": "regular03",
                "password": "password",
                "profile_route": reverse("user-profile", kwargs={"username": "regular03"}),
                "settings_route": f'{reverse("settings")}?tab=profile',
            },
        )
        self.assertEqual(
            payload["actors"]["admin"],
            {
                "username": "admin",
                "password": "admin-password",
                "profile_route": reverse("user-profile", kwargs={"username": "admin"}),
                "settings_route": f'{reverse("settings")}?tab=profile',
            },
        )
        self.assertEqual(payload["routes"]["login"], reverse("login"))
        self.assertEqual(payload["routes"]["password_reset_request"], reverse("password-reset"))
        self.assertEqual(payload["routes"]["password_expired"], reverse("password-expired"))
        self.assertEqual(payload["routes"]["otp_sync"], reverse("otp-sync"))
        self.assertEqual(payload["routes"]["register"], reverse("register"))
        self.assertEqual(payload["routes"]["settings_profile"], f'{reverse("settings")}?tab=profile')
        self.assertEqual(payload["routes"]["settings_emails"], f'{reverse("settings")}?tab=emails')
        self.assertEqual(payload["routes"]["settings_keys"], f'{reverse("settings")}?tab=keys')
        self.assertEqual(payload["routes"]["settings_security"], f'{reverse("settings")}?tab=security')
        self.assertEqual(payload["routes"]["settings_privacy"], f'{reverse("settings")}?tab=privacy')
        self.assertEqual(payload["routes"]["settings_agreements"], f'{reverse("settings")}?tab=agreements')

        password_reset_route = str(payload["routes"]["password_reset_confirm"])
        self.assertTrue(password_reset_route.startswith(f'{reverse("password-reset-confirm")}?token='))
        password_reset_token = unquote(password_reset_route.split("token=", 1)[1])
        self.assertEqual(
            read_password_reset_token(password_reset_token),
            {
                "u": "regular02",
                "e": "regular02@example.test",
                "lpc": "",
            },
        )

        primary_validate_route = str(payload["routes"]["settings_email_validate_primary"])
        self.assertTrue(primary_validate_route.startswith(f'{reverse("settings-email-validate")}?token='))
        primary_validate_token = unquote(primary_validate_route.split("token=", 1)[1])
        self.assertEqual(
            read_settings_email_validation_token(primary_validate_token),
            {
                "u": "regular08",
                "a": "mail",
                "v": "updated-regular08@example.test",
            },
        )

        bugzilla_validate_route = str(payload["routes"]["settings_email_validate_bugzilla"])
        self.assertTrue(bugzilla_validate_route.startswith(f'{reverse("settings-email-validate")}?token='))
        bugzilla_validate_token = unquote(bugzilla_validate_route.split("token=", 1)[1])
        self.assertEqual(
            read_settings_email_validation_token(bugzilla_validate_token),
            {
                "u": "regular08",
                "a": "fasRHBZEmail",
                "v": "updated-bugzilla-regular08@example.test",
            },
        )

        self.assertEqual(
            payload["agreements"]["required_coc"],
            {
                "cn": settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN,
                "route": (
                    f'{reverse("settings")}?tab=agreements&agreement='
                    f'{quote(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN)}'
                ),
            },
        )
        self.assertEqual(
            payload["agreements"]["optional_unsigned"],
            {
                "cn": "e2e-contributor-agreement",
                "route": f'{reverse("settings")}?tab=agreements&agreement={quote("e2e-contributor-agreement")}',
            },
        )

        client = get_e2e_service_client()
        regular03 = client.user_show("regular03")["result"]
        self.assertEqual(regular03[settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR], ["US"])
        self.assertEqual(regular03["fasPronoun"], ["they/them"])
        self.assertEqual(regular03["fasLocale"], ["en-US"])
        self.assertEqual(regular03["fasTimezone"], ["UTC"])
        self.assertEqual(regular03["fasRHBZEmail"], ["bugs.regular03@example.test"])

        coc = FreeIPAFASAgreement.get(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN)
        optional = FreeIPAFASAgreement.get("e2e-contributor-agreement")

        self.assertIsNotNone(coc)
        self.assertIsNotNone(optional)
        assert coc is not None
        assert optional is not None
        self.assertIn("packagers", coc.groups)
        self.assertIn("regular03", coc.users)
        self.assertNotIn("regular01", coc.users)
        self.assertNotIn("regular03", optional.users)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_registration_confirm_and_activate_routes_with_staged_users(self) -> None:
        stdout = io.StringIO()

        call_command("auth_profile_reset", stdout=stdout)

        payload = json.loads(stdout.getvalue())

        self.assertEqual(
            payload["routes"]["register_confirm"],
            f'{reverse("register-confirm")}?username=signup-confirm-01',
        )

        activation_route = str(payload["routes"]["register_activate"])
        self.assertTrue(activation_route.startswith(f'{reverse("register-activate")}?token='))
        activation_token = unquote(activation_route.split("token=", 1)[1])
        self.assertEqual(
            read_registration_activation_token(activation_token),
            {
                "u": "signup-activate-01",
                "e": "signup-activate-01@example.test",
            },
        )

        client = get_e2e_service_client()
        confirm_stageuser = client.stageuser_show("signup-confirm-01")["result"]
        activate_stageuser = client.stageuser_show("signup-activate-01")["result"]

        self.assertEqual(confirm_stageuser["uid"], ["signup-confirm-01"])
        self.assertEqual(confirm_stageuser["mail"], ["signup-confirm-01@example.test"])
        self.assertEqual(confirm_stageuser["givenname"], ["Signup"])
        self.assertEqual(confirm_stageuser["sn"], ["Confirm"])
        self.assertEqual(activate_stageuser["uid"], ["signup-activate-01"])
        self.assertEqual(activate_stageuser["mail"], ["signup-activate-01@example.test"])

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_private_profile_and_membership_settings_state_for_auth_playwright(self) -> None:
        call_command("auth_profile_reset")

        client = get_e2e_service_client()
        private_view_target = client.user_show("regular07")["result"]
        profile_owner = client.user_show("regular03")["result"]
        membership_reviewer = client.user_show("regular01")["result"]

        self.assertEqual(private_view_target["fasIsPrivate"], ["TRUE"])
        self.assertEqual(private_view_target["fasPronoun"], ["they/them"])
        self.assertEqual(private_view_target[settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR], ["US"])
        self.assertEqual(profile_owner["fasRHBZEmail"], ["bugs.regular03@example.test"])
        self.assertIn(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP, membership_reviewer["memberof_group"])

        self.assertTrue(MembershipType.objects.filter(code="individual", enabled=True).exists())
        self.assertTrue(MembershipType.objects.filter(code="mirror", enabled=True).exists())
        self.assertTrue(Membership.objects.filter(target_username="regular03", membership_type_id="mirror").exists())
        self.assertTrue(
            MembershipRequest.objects.filter(
                requested_username="regular03",
                membership_type_id="individual",
                status=MembershipRequest.Status.pending,
            ).exists()
        )

        self.assertTrue(
            FreeIPAPermissionGrant.objects.filter(
                permission=ASTRA_VIEW_MEMBERSHIP,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name="regular01",
            ).exists()
        )
        self.assertTrue(
            FreeIPAPermissionGrant.objects.filter(
                permission=ASTRA_CHANGE_MEMBERSHIP,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name="regular01",
            ).exists()
        )
        self.assertTrue(
            FreeIPAPermissionGrant.objects.filter(
                permission=ASTRA_DELETE_MEMBERSHIP,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name="regular01",
            ).exists()
        )

    def test_subject_index_key_hashes_long_email_subject_without_exposing_raw_value(self) -> None:
        subject = f"{'very-long-local-part-' * 12}@example.test"

        index_key = _rate_limit_subject_index_key("auth.register", subject)

        self.assertTrue(index_key.startswith("astra:rl-index:auth.register:"))
        self.assertNotIn(subject.lower(), index_key)
        self.assertLessEqual(len(index_key), 128)

    def test_clear_subject_rate_limit_uses_hashed_subject_index_for_long_email(self) -> None:
        subject = f"{'signup-user-' * 16}@example.test"
        client_ip = "203.0.113.12"
        cache.clear()

        self.assertTrue(
            allow_request(
                scope="auth.register",
                key_parts=[client_ip, subject],
                limit=1,
                window_seconds=60,
            )
        )

        cache_key = _rate_limit_cache_key("auth.register", [client_ip, subject])
        index_key = _rate_limit_subject_index_key("auth.register", subject)

        self.assertEqual(cache.get(index_key), [cache_key])
        self.assertEqual(cache.get(cache_key), 1)

        clear_subject_rate_limit(scope="auth.register", subject=subject)

        self.assertIsNone(cache.get(index_key))
        self.assertIsNone(cache.get(cache_key))
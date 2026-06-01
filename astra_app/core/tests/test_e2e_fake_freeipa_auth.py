from unittest.mock import Mock, patch

from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from core.freeipa import e2e_registry
from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.auth_backend import FreeIPAAuthBackend
from core.freeipa.client import _get_freeipa_client
from core.freeipa.e2e_registry import get_e2e_auth_client, get_e2e_service_client, reset_e2e_fake_freeipa_state
from core.middleware import FreeIPAAuthenticationMiddleware


@override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True, FREEIPA_ADMIN_GROUP="admins")
class E2EFakeFreeIPAAuthTests(TestCase):
    def _add_session(self, request) -> None:
        middleware = SessionMiddleware(lambda _request: HttpResponse())
        middleware.process_request(request)
        request.session.save()

    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_e2e_mode_authenticate_accepts_allowlisted_regular_credentials_and_rejects_unknown_credentials(self) -> None:
        request = RequestFactory().post("/login/")
        self._add_session(request)

        backend = FreeIPAAuthBackend()

        regular_user = backend.authenticate(request, username="Regular", password="regular-password")

        self.assertIsNotNone(regular_user)
        assert regular_user is not None
        self.assertEqual(regular_user.username, "regular")
        self.assertEqual(request.session.get("_freeipa_username"), "regular")
        self.assertIsNone(backend.authenticate(request, username="regular", password="wrong-password"))

    def test_e2e_mode_authenticate_accepts_numbered_regular_credentials_with_shared_password(self) -> None:
        request = RequestFactory().post("/login/")
        self._add_session(request)

        backend = FreeIPAAuthBackend()

        regular_user = backend.authenticate(request, username="Regular07", password="password")

        self.assertIsNotNone(regular_user)
        assert regular_user is not None
        self.assertEqual(regular_user.username, "regular07")
        self.assertEqual(request.session.get("_freeipa_username"), "regular07")

        client = get_e2e_auth_client(username="regular07", password="password")

        self.assertEqual(client.user_show("regular07")["result"]["uid"], ["regular07"])
        self.assertEqual(client.user_show("regular07")["result"]["mail"], ["regular07@example.test"])

    def test_e2e_mode_middleware_restores_admin_identity_with_admin_flags(self) -> None:
        request = RequestFactory().get("/user/admin/")
        self._add_session(request)
        request.session["_freeipa_username"] = "admin"
        request.session.save()

        middleware = FreeIPAAuthenticationMiddleware(lambda req: req.user)

        user = middleware(request)

        self.assertTrue(getattr(user, "is_authenticated", False))
        self.assertEqual(getattr(user, "username", None), "admin")
        self.assertTrue(getattr(user, "is_staff", False))
        self.assertTrue(getattr(user, "is_superuser", False))
        self.assertEqual(getattr(user, "displayname", None), "Admin User")
        self.assertEqual(getattr(user, "email", None), "admin@example.test")

    def test_e2e_mode_profile_shell_and_detail_api_resolve_same_regular_identity_without_live_freeipa(self) -> None:
        self._login_as_freeipa("regular")

        with (
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.resolve_avatar_urls_for_users", return_value=({"regular": ""}, 0, 0)),
            patch(
                "core.views_users.membership_review_permissions",
                return_value={
                    "membership_can_view": False,
                    "membership_can_add": False,
                    "membership_can_change": False,
                    "membership_can_delete": False,
                },
            ),
            patch("core.views_users._is_membership_committee_viewer", return_value=False),
        ):
            shell_response = self.client.get(reverse("user-profile", args=["regular"]))
            api_response = self.client.get(reverse("api-user-profile-detail", args=["regular"]))

        self.assertEqual(shell_response.status_code, 200)
        self.assertContains(shell_response, "data-user-profile-root")
        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(api_response.json()["summary"]["username"], "regular")

    def test_e2e_service_client_supports_startup_membership_group_sync(self) -> None:
        client = get_e2e_service_client()

        existing_group = client.group_find(o_cn="packagers", o_all=True, o_no_members=False)

        self.assertEqual(existing_group["count"], 1)
        self.assertEqual(existing_group["result"][0]["cn"], ["packagers"])
        self.assertEqual(client.group_find(o_cn="new-membership-group", o_all=True, o_no_members=False), {"count": 0, "result": []})

        client.group_add("new-membership-group", o_description="Membership type group")

        created_group = client.group_find(o_cn="new-membership-group", o_all=True, o_no_members=False)

        self.assertEqual(created_group["count"], 1)
        self.assertEqual(created_group["result"][0]["cn"], ["new-membership-group"])
        self.assertEqual(created_group["result"][0]["description"], ["Membership type group"])
        self.assertEqual(created_group["result"][0]["member_user"], [])

    def test_e2e_service_client_supports_fasagreement_rpc_flow_used_by_migrations(self) -> None:
        self.assertEqual(FreeIPAFASAgreement.all(), [])

        created = FreeIPAFASAgreement.create("AlmaLinux Community Code of Conduct", description="CoC")

        self.assertEqual(created.cn, "AlmaLinux Community Code of Conduct")
        self.assertEqual(created.description, "CoC")
        self.assertEqual(created.users, [])
        self.assertEqual([agreement.cn for agreement in FreeIPAFASAgreement.all()], ["AlmaLinux Community Code of Conduct"])

    def test_fake_freeipa_agreement_and_group_state_survive_simulated_new_process(self) -> None:
        client = get_e2e_service_client()
        agreement = FreeIPAFASAgreement.create("AlmaLinux Community Code of Conduct", description="CoC")
        agreement.add_user("regular01")
        client.group_add("new-membership-group", o_description="Membership type group")

        e2e_registry._E2E_AGREEMENT_REGISTRY = None
        e2e_registry._E2E_GROUP_REGISTRY = None

        restored_agreement = FreeIPAFASAgreement.get("AlmaLinux Community Code of Conduct")
        restored_group = client.group_find(o_cn="new-membership-group", o_all=True, o_no_members=False)

        self.assertIsNotNone(restored_agreement)
        assert restored_agreement is not None
        self.assertIn("regular01", restored_agreement.users)
        self.assertEqual(restored_group["count"], 1)

    @override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.dummy.DummyCache",
            }
        }
    )
    def test_fake_freeipa_agreement_updates_persist_even_when_cache_backend_does_not_store_values(self) -> None:
        agreement = FreeIPAFASAgreement.create("AlmaLinux Community Code of Conduct", description="CoC")

        agreement.add_user("regular01")

        restored_agreement = FreeIPAFASAgreement.get("AlmaLinux Community Code of Conduct")

        self.assertIsNotNone(restored_agreement)
        assert restored_agreement is not None
        self.assertIn("regular01", restored_agreement.users)

    def test_fake_freeipa_user_mod_persists_profile_updates_across_simulated_new_process(self) -> None:
        client = get_e2e_service_client()

        client.user_mod("regular01", c="US")

        refreshed_before_reset = client.user_show("regular01")["result"]
        e2e_registry._E2E_GROUP_REGISTRY = None
        e2e_registry._E2E_AGREEMENT_REGISTRY = None
        refreshed_after_reset = get_e2e_service_client().user_show("regular01")["result"]

        self.assertEqual(refreshed_before_reset.get("c"), ["US"])
        self.assertEqual(refreshed_after_reset.get("c"), ["US"])

    def test_reset_e2e_fake_freeipa_state_restores_mutable_registries_to_baseline(self) -> None:
        client = get_e2e_service_client()

        client.group_add("new-membership-group", o_description="Membership type group")
        FreeIPAFASAgreement.create("AlmaLinux Community Code of Conduct", description="CoC")

        self.assertEqual(client.group_find(o_cn="new-membership-group", o_all=True, o_no_members=False)["count"], 1)
        self.assertEqual([agreement.cn for agreement in FreeIPAFASAgreement.all()], ["AlmaLinux Community Code of Conduct"])

        reset_e2e_fake_freeipa_state()

        self.assertEqual(client.group_find(o_cn="new-membership-group", o_all=True, o_no_members=False), {"count": 0, "result": []})
        self.assertEqual(FreeIPAFASAgreement.all(), [])
        self.assertEqual(client.group_find(o_cn="packagers", o_all=True, o_no_members=False)["count"], 1)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=False)
    def test_fake_registry_refuses_activation_without_explicit_fake_freeipa_allow_flag(self) -> None:
        with self.assertRaisesMessage(ImproperlyConfigured, "ASTRA_E2E_FAKE_FREEIPA_ENABLED"):
            get_e2e_auth_client(username="regular", password="regular-password")

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=False)
    def test_client_factory_uses_live_freeipa_path_when_only_e2e_mode_leaks(self) -> None:
        fake_client = Mock()

        with patch("core.freeipa.client._build_freeipa_client", return_value=fake_client) as build_client:
            _get_freeipa_client("regular", "real-password")

        build_client.assert_called_once_with()
        fake_client.login.assert_called_once_with("regular", "real-password")
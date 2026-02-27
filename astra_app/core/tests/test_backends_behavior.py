
from unittest.mock import patch

import requests
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import Client, RequestFactory, TestCase

from core.freeipa.auth_backend import FreeIPAAuthBackend
from core.freeipa.client import _build_freeipa_client, _FreeIPATimeoutSession
from core.freeipa.group import FreeIPAGroup
from core.freeipa.user import FreeIPAUser


class FreeIPABackendBehaviorTests(TestCase):
    def _add_session(self, request):
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()
        return request

    def test_authenticate_persists_freeipa_username_in_session(self):
        factory = RequestFactory()
        request = factory.post("/login/")
        self._add_session(request)

        backend = FreeIPAAuthBackend()

        with patch("core.freeipa.client.ClientMeta", autospec=True) as mocked_client_cls:
            mocked_client = mocked_client_cls.return_value
            mocked_client.login.return_value = None

            with patch("core.freeipa.user.FreeIPAUser._fetch_full_user", autospec=True) as mocked_fetch:
                mocked_fetch.return_value = {"uid": ["alice"], "givenname": ["Alice"], "sn": ["User"]}

                user = backend.authenticate(request, username="alice", password="pw")

        self.assertIsNotNone(user)
        self.assertEqual(request.session.get("_freeipa_username"), "alice")

    def test_authenticate_no_longer_writes_session_uid_cache_mapping(self):
        factory = RequestFactory()
        request = factory.post("/login/")
        self._add_session(request)

        backend = FreeIPAAuthBackend()

        with patch("django.core.cache.cache.set", autospec=True) as mocked_cache_set:
            with patch("core.freeipa.client.ClientMeta", autospec=True) as mocked_client_cls:
                mocked_client = mocked_client_cls.return_value
                mocked_client.login.return_value = None

                with patch("core.freeipa.user.FreeIPAUser._fetch_full_user", autospec=True) as mocked_fetch:
                    mocked_fetch.return_value = {"uid": ["alice"]}
                    backend.authenticate(request, username="alice", password="pw")

        # cache.set may be used elsewhere in this backend; assert it never wrote the old mapping key.
        for call in mocked_cache_set.call_args_list:
            key = call.args[0] if call.args else None
            if isinstance(key, str):
                self.assertFalse(key.startswith("freeipa_session_uid_"))

    def test_get_user_is_intentionally_disabled(self):
        backend = FreeIPAAuthBackend()
        self.assertIsNone(backend.get_user(123))
        self.assertIsNone(backend.get_user("123"))

    def test_authenticate_connection_error_sets_unavailable_message(self):
        factory = RequestFactory()
        request = factory.post("/login/")
        self._add_session(request)

        backend = FreeIPAAuthBackend()

        with patch(
            "core.freeipa.auth_backend._get_freeipa_client",
            autospec=True,
            side_effect=requests.exceptions.ConnectionError(),
        ):
            user = backend.authenticate(request, username="alice", password="pw")

        self.assertIsNone(user)
        self.assertEqual(
            getattr(request, "_freeipa_auth_error", None),
            "We cannot sign you in right now because AlmaLinux Accounts is temporarily unavailable. "
            "Please try again in a few minutes.",
        )

    def test_login_connection_error_shows_only_freeipa_unavailable_message(self) -> None:
        client = Client()

        with patch(
            "core.freeipa.auth_backend._get_freeipa_client",
            autospec=True,
            side_effect=requests.exceptions.ConnectionError(),
        ):
            response = client.post(
                "/login/",
                data={"username": "alice", "password": "pw"},
            )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        errors = form.non_field_errors()
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "We cannot sign you in right now because AlmaLinux Accounts is temporarily unavailable. "
            "Please try again in a few minutes.",
            errors,
        )
        self.assertNotIn(
            "Please enter a correct username and password. Note that both fields may be case-sensitive.",
            errors,
        )

    def test_add_to_group_is_idempotent_when_already_member(self) -> None:
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "memberof_group": ["almalinux-individual"],
            },
        )

        # FreeIPA sometimes reports "already a member" as a structured failure.
        # Extending an active membership should tolerate this and proceed.
        freeipa_duplicate_member_response = {
            "failed": {
                "member": {
                    "user": ["This entry is already a member"],
                    "group": [],
                    "service": [],
                    "idoverrideuser": [],
                }
            }
        }

        with (
            patch(
                "core.freeipa.user._with_freeipa_service_client_retry",
                autospec=True,
                return_value=freeipa_duplicate_member_response,
            ),
            patch("core.freeipa.user._invalidate_user_cache", autospec=True),
            patch("core.freeipa.group._invalidate_group_cache", autospec=True),
            patch("core.freeipa.group._invalidate_groups_list_cache", autospec=True),
            patch("core.freeipa.group.FreeIPAGroup.get", autospec=True),
            patch("core.freeipa.user.FreeIPAUser.get", autospec=True, return_value=alice),
        ):
            alice.add_to_group("almalinux-individual")

    def test_freeipa_client_injects_timeout_session(self) -> None:
        class FakeClient:
            def __init__(self, host: str | None = None, verify_ssl: bool = True) -> None:
                self.host = host
                self.verify_ssl = verify_ssl
                self._session = None

        with patch("core.freeipa.client.ClientMeta", new=FakeClient):
            client = _build_freeipa_client()

        self.assertIsInstance(client._session, _FreeIPATimeoutSession)
        self.assertEqual(client._session.default_timeout, 10)

    def test_fetch_full_user_does_not_swallow_typeerror(self) -> None:
        class _Client:
            def user_show(self, *_args, **_kwargs):
                raise TypeError("signature mismatch")

            def user_find(self, *_args, **_kwargs):
                return {"count": 1, "result": [{"uid": ["alice"]}]}

        with self.assertRaises(TypeError):
            FreeIPAUser._fetch_full_user(_Client(), "alice")

    def test_add_member_group_does_not_fallback_on_typeerror(self) -> None:
        class _Client:
            def group_add_member(self, *_args, **_kwargs):
                raise TypeError("signature mismatch")

        group = FreeIPAGroup("parent", {"cn": ["parent"]})

        with (
            patch("core.freeipa.group._with_freeipa_service_client_retry", side_effect=lambda _get_client, fn: fn(_Client())),
            patch("core.freeipa.group.FreeIPAGroup.get", autospec=True, return_value=group),
            patch("core.freeipa.group._invalidate_group_cache", autospec=True),
            patch("core.freeipa.group._invalidate_groups_list_cache", autospec=True),
        ):
            with self.assertRaises(TypeError):
                group.add_member_group("child")

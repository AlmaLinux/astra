
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone
from django.utils.functional import SimpleLazyObject

from core.freeipa.circuit_breaker import _open_freeipa_circuit, _reset_freeipa_circuit_failures
from core.freeipa.exceptions import FreeIPAUnavailableError
from core.freeipa.user import DegradedFreeIPAUser
from core.middleware import FreeIPAAuthenticationMiddleware, FreeIPAUnavailableMiddleware
from core.templatetags.core_membership_notes import _current_username_from_request
from core.views_utils import get_username


class FreeIPAMiddlewareRestoreTests(TestCase):
    def _add_session(self, request):
        # Attach a working session to the request.
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()
        return request

    def test_restores_freeipa_user_from_session_username(self):
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session(request)
        request.session["_freeipa_username"] = "alice"
        request.session.save()

        fake_user = SimpleNamespace(is_authenticated=True, username="alice")

        with patch("core.middleware.FreeIPAUser.get", autospec=True) as mocked_get:
            mocked_get.return_value = fake_user

            middleware = FreeIPAAuthenticationMiddleware(lambda req: req.user)
            user = middleware(request)

        self.assertTrue(getattr(user, "is_authenticated", False))
        self.assertEqual(getattr(user, "username", None), "alice")
        mocked_get.assert_called_once_with("alice")

    def test_restores_anonymous_when_freeipa_user_missing(self):
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session(request)
        request.session["_freeipa_username"] = "missing"
        request.session.save()

        with patch("core.middleware.FreeIPAUser.get", autospec=True) as mocked_get:
            mocked_get.return_value = None
            middleware = FreeIPAAuthenticationMiddleware(lambda req: req.user)
            user = middleware(request)

        self.assertIsInstance(user, AnonymousUser)
        mocked_get.assert_called_once_with("missing")

    def test_activates_and_deactivates_timezone_from_user_data(self):
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session(request)
        request.session["_freeipa_username"] = "alice"
        request.session.save()

        fake_user = SimpleNamespace(
            is_authenticated=True,
            username="alice",
            _user_data={"fasTimezone": "Europe/Paris"},
        )

        observed = {}

        def get_response(req):
            observed["in_request_tz"] = timezone.get_current_timezone_name()
            return req.user

        before = timezone.get_current_timezone_name()
        with patch("core.middleware.FreeIPAUser.get", autospec=True) as mocked_get:
            mocked_get.return_value = fake_user
            middleware = FreeIPAAuthenticationMiddleware(get_response)
            user = middleware(request)

        after = timezone.get_current_timezone_name()

        self.assertEqual(getattr(user, "username", None), "alice")
        self.assertEqual(observed.get("in_request_tz"), "Europe/Paris")
        # Middleware should deactivate, restoring the previous timezone.
        self.assertEqual(after, before)

    def test_invalid_timezone_falls_back_to_utc(self):
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session(request)
        request.session["_freeipa_username"] = "alice"
        request.session.save()

        fake_user = SimpleNamespace(
            is_authenticated=True,
            username="alice",
            _user_data={"fasTimezone": "Not/AZone"},
        )

        observed = {}

        def get_response(req):
            observed["in_request_tz"] = timezone.get_current_timezone_name()
            return req.user

        with patch("core.middleware.FreeIPAUser.get", autospec=True) as mocked_get:
            mocked_get.return_value = fake_user
            middleware = FreeIPAAuthenticationMiddleware(get_response)
            middleware(request)

        self.assertEqual(observed.get("in_request_tz"), "UTC")

    def test_does_not_call_freeipa_when_django_user_authenticated(self):
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session(request)

        # Simulate Django already having an authenticated user.
        request.user = SimpleNamespace(is_authenticated=True, _user_data={"fasTimezone": "UTC"})

        with patch("core.middleware.FreeIPAUser.get", autospec=True) as mocked_get:
            middleware = FreeIPAAuthenticationMiddleware(lambda req: req.user)
            user = middleware(request)

        self.assertTrue(getattr(user, "is_authenticated", False))
        mocked_get.assert_not_called()

    def test_preserves_authenticated_user_and_still_applies_timezone(self):
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session(request)

        request.user = SimpleNamespace(
            is_authenticated=True,
            username="already",
            _user_data={"fasTimezone": "Europe/Paris"},
        )

        observed = {}

        def get_response(req):
            observed["in_request_tz"] = timezone.get_current_timezone_name()
            return req.user

        with patch("core.middleware.FreeIPAUser.get", autospec=True) as mocked_get:
            middleware = FreeIPAAuthenticationMiddleware(get_response)
            user = middleware(request)

        self.assertEqual(getattr(user, "username", None), "already")
        self.assertEqual(observed.get("in_request_tz"), "Europe/Paris")
        mocked_get.assert_not_called()

    def test_uses_degraded_user_when_circuit_open(self):
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session(request)
        request.session["_freeipa_username"] = "alice"
        request.session.save()

        _open_freeipa_circuit()

        def get_response(req):
            return req.user

        with patch("core.middleware.FreeIPAUser.get", autospec=True) as mocked_get:
            with patch("core.middleware.timezone.activate", side_effect=AssertionError("timezone activated")):
                middleware = FreeIPAAuthenticationMiddleware(get_response)
                user = middleware(request)

        self.assertIsInstance(user, DegradedFreeIPAUser)
        mocked_get.assert_not_called()
        _reset_freeipa_circuit_failures()

    def test_freeipa_unavailable_middleware_returns_503(self):
        factory = RequestFactory()
        request = factory.get("/user/alice/")

        middleware = FreeIPAUnavailableMiddleware(lambda _req: HttpResponse("ok"))
        response = middleware.process_exception(request, FreeIPAUnavailableError("open"))

        self.assertEqual(response.status_code, 503)
        self.assertIn(
            b"AlmaLinux Accounts is temporarily unavailable",
            response.content,
        )

    def test_avoids_evaluating_lazy_user_when_circuit_open(self):
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session(request)
        request.session["_freeipa_username"] = "alice"
        request.session.save()

        request.user = SimpleLazyObject(
            lambda: (_ for _ in ()).throw(FreeIPAUnavailableError("open"))
        )
        _open_freeipa_circuit()

        middleware = FreeIPAAuthenticationMiddleware(lambda req: req.user)
        user = middleware(request)

        self.assertIsInstance(user, DegradedFreeIPAUser)
        _reset_freeipa_circuit_failures()

    def test_username_ssot_session_authoritative_across_layers_without_lazy_evaluation(self):
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session(request)
        request.session["_freeipa_username"] = "alice"
        request.session.save()

        lazy_eval_count = {"count": 0}

        def _build_lazy_user():
            lazy_eval_count["count"] += 1
            return SimpleNamespace(is_authenticated=True, get_username=lambda: "bob", username="bob")

        request.user = SimpleLazyObject(_build_lazy_user)

        middleware = FreeIPAAuthenticationMiddleware(lambda req: HttpResponse("ok"))

        with patch("core.middleware.set_current_viewer_username", autospec=True) as mocked_set_viewer:
            response = middleware(request)

        self.assertEqual(response.status_code, 200)
        mocked_set_viewer.assert_called_once_with("alice")
        self.assertEqual(get_username(request), "alice")
        self.assertEqual(_current_username_from_request(request), "alice")
        self.assertEqual(lazy_eval_count["count"], 0)

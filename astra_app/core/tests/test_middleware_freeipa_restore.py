
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone
from django.utils.functional import SimpleLazyObject

from config.logging_context import get_request_log_context
from core.freeipa.circuit_breaker import _open_freeipa_circuit, _reset_freeipa_circuit_failures
from core.freeipa.exceptions import FreeIPAUnavailableError
from core.freeipa.user import DegradedFreeIPAUser
from core.middleware import (
    FreeIPAAuthenticationMiddleware,
    FreeIPAUnavailableMiddleware,
    SentryRequestContextMiddleware,
    StructuredAccessLogMiddleware,
)
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
        with patch("core.middleware.logger.warning", autospec=True) as mocked_warning:
            response = middleware.process_exception(request, FreeIPAUnavailableError("open"))

        self.assertEqual(response.status_code, 503)
        self.assertIn(
            b"AlmaLinux Accounts is temporarily unavailable",
            response.content,
        )
        mocked_warning.assert_called_once()

        warning_kwargs = mocked_warning.call_args.kwargs
        self.assertEqual(warning_kwargs["extra"]["event"], "astra.freeipa.unavailable")
        self.assertEqual(warning_kwargs["extra"]["component"], "middleware")
        self.assertEqual(warning_kwargs["extra"]["outcome"], "error")
        self.assertEqual(warning_kwargs["extra"]["request_path"], "/user/alice/")
        self.assertEqual(warning_kwargs["extra"]["error_type"], "FreeIPAUnavailableError")
        self.assertEqual(warning_kwargs["extra"]["error_message"], "open")
        self.assertIn("open", warning_kwargs["extra"]["error_repr"])
        self.assertEqual(warning_kwargs["extra"]["error_args"], "('open',)")

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

    def test_sentry_request_context_middleware_sets_user_and_tags(self):
        factory = RequestFactory()
        request = factory.get(
            "/organizations/",
            HTTP_X_FORWARDED_FOR="203.0.113.8, 10.0.0.1",
            HTTP_X_REQUEST_ID="req-42",
        )
        request.user = SimpleNamespace(is_authenticated=True, username="alice")

        observed_context = {}

        def get_response(_req):
            observed_context.update(get_request_log_context() or {})
            return HttpResponse("ok")

        with patch("core.middleware.sentry_sdk.set_user", autospec=True) as mocked_set_user:
            with patch("core.middleware.sentry_sdk.set_tag", autospec=True) as mocked_set_tag:
                middleware = SentryRequestContextMiddleware(get_response)
                response = middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed_context["client_ip"], "203.0.113.8")
        self.assertEqual(observed_context["user_id"], "alice")
        self.assertEqual(observed_context["request_id"], "req-42")
        self.assertEqual(observed_context["request_path"], "/organizations/")
        self.assertEqual(observed_context["request_method"], "GET")
        self.assertIsNone(get_request_log_context())

        mocked_set_user.assert_called_once_with({"id": "alice", "username": "alice"})
        mocked_set_tag.assert_any_call("client_ip", "203.0.113.8")
        mocked_set_tag.assert_any_call("request_id", "req-42")

    def test_sentry_request_context_middleware_clears_user_for_anonymous(self):
        factory = RequestFactory()
        request = factory.get("/login")
        request.user = AnonymousUser()

        with patch("core.middleware.sentry_sdk.set_user", autospec=True) as mocked_set_user:
            middleware = SentryRequestContextMiddleware(lambda _req: HttpResponse("ok"))
            response = middleware(request)

        self.assertEqual(response.status_code, 200)
        mocked_set_user.assert_called_once_with(None)

    def test_structured_access_log_middleware_logs_authenticated_request(self):
        factory = RequestFactory()
        request = factory.get(
            "/organizations/?q=alma",
            HTTP_X_REQUEST_ID="req-123",
            HTTP_USER_AGENT="pytest-agent",
            HTTP_REFERER="https://accounts.almalinux.org/",
            REMOTE_ADDR="198.51.100.14",
        )
        request.user = SimpleNamespace(is_authenticated=True, username="alice")

        with (
            patch("core.middleware.time.monotonic", side_effect=[10.0, 10.145], autospec=True),
            patch("core.middleware._format_access_log_timestamp", return_value="[14/Mar/2026:19:30:00 +0000]"),
            patch("core.middleware.access_logger.info", autospec=True) as mocked_info,
        ):
            middleware = StructuredAccessLogMiddleware(lambda _req: HttpResponse("ok", status=201))
            response = middleware(request)

        self.assertEqual(response.status_code, 201)
        mocked_info.assert_called_once()
        self.assertEqual(
            mocked_info.call_args.args[0],
            '198.51.100.14 - alice [14/Mar/2026:19:30:00 +0000] "GET /organizations/?q=alma HTTP/1.1" 201 2 "https://accounts.almalinux.org/" "pytest-agent"',
        )
        extra = mocked_info.call_args.kwargs["extra"]
        self.assertEqual(extra["event"], "astra.http.access")
        self.assertEqual(extra["component"], "http")
        self.assertEqual(extra["outcome"], "success")
        self.assertEqual(extra["http_status"], 201)
        self.assertEqual(extra["request_method"], "GET")
        self.assertEqual(extra["request_path"], "/organizations/")
        self.assertEqual(extra["request_query"], "q=alma")
        self.assertEqual(extra["duration_ms"], 145)
        self.assertEqual(extra["user_id"], "alice")
        self.assertEqual(extra["client_ip"], "198.51.100.14")
        self.assertEqual(extra["request_id"], "req-123")

    def test_structured_access_log_middleware_logs_exception_context(self):
        factory = RequestFactory()
        request = factory.get("/settings", HTTP_USER_AGENT="pytest-agent", REMOTE_ADDR="198.51.100.15")
        request.user = AnonymousUser()

        def _boom(_request):
            raise RuntimeError("middleware boom")

        with (
            patch("core.middleware.time.monotonic", side_effect=[20.0, 20.200], autospec=True),
            patch("core.middleware._format_access_log_timestamp", return_value="[14/Mar/2026:19:31:00 +0000]"),
            patch("core.middleware.access_logger.info", autospec=True) as mocked_info,
        ):
            middleware = StructuredAccessLogMiddleware(_boom)
            with self.assertRaises(RuntimeError):
                middleware(request)

        mocked_info.assert_called_once()
        self.assertEqual(
            mocked_info.call_args.args[0],
            '198.51.100.15 - - [14/Mar/2026:19:31:00 +0000] "GET /settings HTTP/1.1" 500 - "-" "pytest-agent"',
        )
        extra = mocked_info.call_args.kwargs["extra"]
        self.assertEqual(extra["http_status"], 500)
        self.assertEqual(extra["outcome"], "server_error")
        self.assertEqual(extra["error_type"], "RuntimeError")
        self.assertEqual(extra["error_message"], "middleware boom")

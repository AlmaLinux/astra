
import logging

from django.conf import settings
from django.test import SimpleTestCase

from config.logging_context import RequestLogContext, reset_request_log_context, set_request_log_context
from core.logging_extras import exception_log_fields


class LoggingFilterTests(SimpleTestCase):
    def test_astra_access_logger_uses_access_and_context_filters(self) -> None:
        configured_filters = settings.LOGGING["loggers"]["astra.access"]["filters"]
        self.assertEqual(
            configured_filters,
            ["health_endpoint", "hetrix_access", "request_context", "quiet_request_paths"],
        )

    def test_core_and_django_request_loggers_use_quiet_request_path_filter(self) -> None:
        self.assertIn("quiet_request_paths", settings.LOGGING["loggers"]["core"]["filters"])
        self.assertIn("quiet_request_paths", settings.LOGGING["loggers"]["django.request"]["filters"])
        self.assertIn("quiet_request_paths", settings.LOGGING["loggers"]["django.server"]["filters"])

    def test_django_server_logger_suppresses_info_access_lines(self) -> None:
        self.assertEqual(settings.LOGGING["loggers"]["django.server"]["level"], "WARNING")

    def test_health_endpoint_filter(self) -> None:
        from config.logging_filters import HealthEndpointFilter

        filt = HealthEndpointFilter()

        record_ok = logging.LogRecord(
            name="django.server",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='"GET /healthz HTTP/1.1" 200 12',
            args=(),
            exc_info=None,
        )
        self.assertFalse(filt.filter(record_ok))

        record_error = logging.LogRecord(
            name="django.server",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='"GET /healthz HTTP/1.1" 503 12',
            args=(),
            exc_info=None,
        )
        self.assertTrue(filt.filter(record_error))

        record_other = logging.LogRecord(
            name="django.server",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='"GET /users HTTP/1.1" 200 12',
            args=(),
            exc_info=None,
        )
        self.assertTrue(filt.filter(record_other))

    def test_health_endpoint_filter_handles_gunicorn_format(self) -> None:
        from config.logging_filters import HealthEndpointFilter

        filt = HealthEndpointFilter()

        record_ok = logging.LogRecord(
            name="gunicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='- - - [27/Jan/2026:10:49:08 +0000] "GET /readyz HTTP/1.1" 200 37 "-" "Go-http-client/1.1"',
            args=(),
            exc_info=None,
        )
        self.assertFalse(filt.filter(record_ok))

    def test_hetrix_access_filter_drops_root_and_login_checks(self) -> None:
        from config.logging_filters import HetrixAccessFilter

        filt = HetrixAccessFilter()

        root_check = logging.LogRecord(
            name="gunicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='- - - [27/Jan/2026:10:49:08 +0000] "GET / HTTP/1.1" 200 37 "-" "HetrixTools Uptime Monitoring Bot. https://hetrix.tools/uptime-monitoring-bot.html"',
            args=(),
            exc_info=None,
        )
        self.assertFalse(filt.filter(root_check))

        login_check = logging.LogRecord(
            name="gunicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='- - - [27/Jan/2026:10:49:08 +0000] "GET /login HTTP/1.1" 200 37 "-" "HetrixTools Uptime Monitoring Bot. https://hetrix.tools/uptime-monitoring-bot.html"',
            args=(),
            exc_info=None,
        )
        self.assertFalse(filt.filter(login_check))

        login_next_check = logging.LogRecord(
            name="gunicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='- - - [27/Jan/2026:10:49:08 +0000] "GET /login/?next=/ HTTP/1.1" 200 37 "https://accounts.almalinux.org/" "HetrixTools Uptime Monitoring Bot. https://hetrix.tools/uptime-monitoring-bot.html"',
            args=(),
            exc_info=None,
        )
        self.assertFalse(filt.filter(login_next_check))

    def test_hetrix_access_filter_keeps_other_paths_and_agents(self) -> None:
        from config.logging_filters import HetrixAccessFilter

        filt = HetrixAccessFilter()

        hetrix_other_path = logging.LogRecord(
            name="gunicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='- - - [27/Jan/2026:10:49:08 +0000] "GET /register HTTP/1.1" 200 37 "-" "HetrixTools Uptime Monitoring Bot. https://hetrix.tools/uptime-monitoring-bot.html"',
            args=(),
            exc_info=None,
        )
        self.assertTrue(filt.filter(hetrix_other_path))

        non_hetrix_root = logging.LogRecord(
            name="gunicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='- - - [27/Jan/2026:10:49:08 +0000] "GET / HTTP/1.1" 200 37 "-" "Mozilla/5.0"',
            args=(),
            exc_info=None,
        )
        self.assertTrue(filt.filter(non_hetrix_root))

    def test_quiet_request_path_filter_drops_ci_tunnel_logs_from_message(self) -> None:
        from config.logging_filters import QuietRequestPathFilter

        filt = QuietRequestPathFilter()

        record_tunnel = logging.LogRecord(
            name="django.server",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='- - - [27/Jan/2026:10:49:08 +0000] "POST /_ci/envelope/ HTTP/1.1" 200 0 "-" "sentry"',
            args=(),
            exc_info=None,
        )
        self.assertFalse(filt.filter(record_tunnel))

        record_other = logging.LogRecord(
            name="django.server",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='- - - [27/Jan/2026:10:49:08 +0000] "POST /users/ HTTP/1.1" 200 0 "-" "browser"',
            args=(),
            exc_info=None,
        )
        self.assertTrue(filt.filter(record_other))

    def test_quiet_request_path_filter_drops_ci_tunnel_logs_from_request_context(self) -> None:
        from config.logging_filters import QuietRequestPathFilter

        filt = QuietRequestPathFilter()
        record = logging.LogRecord(
            name="core.views_sentry",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Failed to forward Sentry envelope",
            args=(),
            exc_info=None,
        )
        record.request_path = "/_ci/envelope/"

        self.assertFalse(filt.filter(record))

    def test_request_context_filter_populates_searchable_log_fields(self) -> None:
        from config.logging_filters import RequestContextFilter

        token = set_request_log_context(
            RequestLogContext(
                client_ip="203.0.113.8",
                user_id="alice",
                request_id="req-42",
                request_path="/organizations/",
                request_method="GET",
            )
        )
        self.addCleanup(reset_request_log_context, token)

        filt = RequestContextFilter()
        record = logging.LogRecord(
            name="core.views",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="membership lookup",
            args=(),
            exc_info=None,
        )

        self.assertTrue(filt.filter(record))
        self.assertEqual(record.client_ip, "203.0.113.8")
        self.assertEqual(record.user_id, "alice")
        self.assertEqual(record.request_id, "req-42")
        self.assertEqual(record.request_path, "/organizations/")
        self.assertEqual(record.request_method, "GET")

    def test_request_context_filter_keeps_existing_extra_fields(self) -> None:
        from config.logging_filters import RequestContextFilter

        token = set_request_log_context(
            RequestLogContext(
                client_ip="203.0.113.8",
                user_id="alice",
                request_id="req-42",
                request_path="/organizations/",
                request_method="GET",
            )
        )
        self.addCleanup(reset_request_log_context, token)

        filt = RequestContextFilter()
        record = logging.LogRecord(
            name="core.views",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="membership lookup",
            args=(),
            exc_info=None,
        )
        record.user_id = "explicit-user"
        record.client_ip = "198.51.100.9"

        self.assertTrue(filt.filter(record))
        self.assertEqual(record.user_id, "explicit-user")
        self.assertEqual(record.client_ip, "198.51.100.9")

    def test_request_context_filter_adds_exception_fields_from_exc_info(self) -> None:
        from config.logging_filters import RequestContextFilter

        filt = RequestContextFilter()
        try:
            raise ValueError("boom")
        except ValueError as error:
            record = logging.LogRecord(
                name="core.views",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="request failed",
                args=(),
                exc_info=(type(error), error, error.__traceback__),
            )

        self.assertTrue(filt.filter(record))
        self.assertEqual(record.error_type, "ValueError")
        self.assertEqual(record.error_message, "boom")
        self.assertEqual(record.error_repr, "ValueError('boom')")
        self.assertEqual(record.error_args, "('boom',)")

    def test_request_context_filter_adds_exception_fields_from_record_args(self) -> None:
        from config.logging_filters import RequestContextFilter

        filt = RequestContextFilter()
        error = RuntimeError("arg failure")
        record = logging.LogRecord(
            name="core.views",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed: %s",
            args=(error,),
            exc_info=None,
        )

        self.assertTrue(filt.filter(record))
        self.assertEqual(record.error_type, "RuntimeError")
        self.assertEqual(record.error_message, "arg failure")
        self.assertEqual(record.error_repr, "RuntimeError('arg failure')")
        self.assertEqual(record.error_args, "('arg failure',)")

    def test_request_context_filter_keeps_explicit_exception_fields(self) -> None:
        from config.logging_filters import RequestContextFilter

        filt = RequestContextFilter()
        error = RuntimeError("arg failure")
        record = logging.LogRecord(
            name="core.views",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed: %s",
            args=(error,),
            exc_info=None,
        )
        explicit = {
            "error_type": "ExplicitError",
            "error_message": "explicit-message",
            "error_repr": "ExplicitError('explicit-message')",
            "error_args": "('explicit-message',)",
        }
        for key, value in explicit.items():
            setattr(record, key, value)

        self.assertTrue(filt.filter(record))
        self.assertEqual(
            {
                "error_type": record.error_type,
                "error_message": record.error_message,
                "error_repr": record.error_repr,
                "error_args": record.error_args,
            },
            explicit,
        )
        self.assertNotEqual(
            {
                "error_type": record.error_type,
                "error_message": record.error_message,
                "error_repr": record.error_repr,
                "error_args": record.error_args,
            },
            exception_log_fields(error),
        )
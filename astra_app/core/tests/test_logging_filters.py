
import logging

from django.test import SimpleTestCase


class LoggingFilterTests(SimpleTestCase):
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
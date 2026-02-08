
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
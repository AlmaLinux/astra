from __future__ import annotations

import logging

from django.test import SimpleTestCase


class LoggingFilterTests(SimpleTestCase):
    def test_health_endpoint_filter(self) -> None:
        from config.settings import HealthEndpointFilter

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
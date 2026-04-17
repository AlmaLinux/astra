import threading
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from core.freeipa.client import (
    _annotate_freeipa_response_span,
    _FreeIPATimeoutSession,
    clear_freeipa_service_client_cache,
)


class FreeIPAClientTests(SimpleTestCase):
    def test_timeout_session_registers_sentry_response_hook(self) -> None:
        session = _FreeIPATimeoutSession(default_timeout=10.0)

        self.assertIn(_annotate_freeipa_response_span, session.hooks["response"])

    def test_response_hook_adds_freeipa_rpc_method_to_sentry_span(self) -> None:
        span = Mock()
        span.description = "POST https://ipa02.awsuseast1.ipa.almalinux.org/ipa/session/json"
        connection = Mock()
        connection._sentrysdk_span = span
        response = Mock()
        response.raw.connection = connection
        response.headers = {"Content-Length": "321"}
        response.request.body = (
            b'{"method": "user_show", "params": [["alice"], {"all": true, "raw": false}], "id": 0}'
        )

        returned = _annotate_freeipa_response_span(response)

        self.assertIs(returned, response)
        self.assertEqual(
            span.description,
            "POST https://ipa02.awsuseast1.ipa.almalinux.org/ipa/session/json [user_show]",
        )
        span.set_tag.assert_called_once_with("freeipa.rpc_method", "user_show")
        span.update_data.assert_called_once_with(
            {
                "freeipa.rpc_method": "user_show",
                "freeipa.rpc_arg_count": 1,
                "freeipa.rpc_option_keys": ["all", "raw"],
                "freeipa.rpc_response_bytes": 321,
            }
        )

    def test_response_hook_falls_back_to_buffered_response_size_when_content_length_is_invalid(self) -> None:
        span = Mock()
        span.description = "POST https://ipa02.awsuseast1.ipa.almalinux.org/ipa/session/json"
        connection = Mock()
        connection._sentrysdk_span = span
        response = Mock()
        response.raw.connection = connection
        response.headers = {"Content-Length": "not-a-number"}
        response._content = b'{"result": [{"uid": ["alice"]}]}'
        response.request.body = (
            b'{"method": "user_find", "params": [[], {"uid": "alice"}], "id": 0}'
        )

        _annotate_freeipa_response_span(response)

        span.update_data.assert_called_once_with(
            {
                "freeipa.rpc_method": "user_find",
                "freeipa.rpc_arg_count": 0,
                "freeipa.rpc_option_keys": ["uid"],
                "freeipa.rpc_response_bytes": len(response._content),
            }
        )

    def test_reset_freeipa_client_clears_thread_local_service_client(self) -> None:
        clear_freeipa_service_client_cache()

        with patch("core.freeipa.client._service_client_local", new=threading.local()):
            from core.freeipa.client import _service_client_local as patched_service_client_local

            patched_service_client_local.client = Mock()
            self.assertTrue(hasattr(patched_service_client_local, "client"))

            from core.freeipa.client import reset_freeipa_client

            reset_freeipa_client()

            self.assertFalse(hasattr(patched_service_client_local, "client"))

import threading
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from core.freeipa.client import clear_freeipa_service_client_cache


class FreeIPAClientTests(SimpleTestCase):
    def test_reset_freeipa_client_clears_thread_local_service_client(self) -> None:
        clear_freeipa_service_client_cache()

        with patch("core.freeipa.client._service_client_local", new=threading.local()):
            from core.freeipa.client import _service_client_local as patched_service_client_local

            patched_service_client_local.client = Mock()
            self.assertTrue(hasattr(patched_service_client_local, "client"))

            from core.freeipa.client import reset_freeipa_client

            reset_freeipa_client()

            self.assertFalse(hasattr(patched_service_client_local, "client"))

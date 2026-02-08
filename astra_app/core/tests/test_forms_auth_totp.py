
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from core.forms_auth import FreeIPAAuthenticationForm


class FreeIPAAuthenticationFormTests(TestCase):
    def test_appends_otp_to_password(self):
        request = RequestFactory().post("/login/")

        user = SimpleNamespace(is_active=True)

        with patch("django.contrib.auth.forms.authenticate", autospec=True) as authenticate:
            authenticate.return_value = user
            form = FreeIPAAuthenticationForm(
                request=request,
                data={
                    "username": "alice",
                    "password": "pw",
                    "otp": "123456",
                },
            )
            self.assertTrue(form.is_valid())

            authenticate.assert_called_with(request, username="alice", password="pw123456")

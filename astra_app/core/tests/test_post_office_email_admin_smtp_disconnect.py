
import smtplib
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from core.freeipa.user import FreeIPAUser


class PostOfficeEmailAdminSMTPDisconnectTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_admin_email_change_view_does_not_500_if_smtp_disconnects(self) -> None:
        from post_office.models import Email

        email = Email.objects.create(
            from_email=settings.DEFAULT_FROM_EMAIL,
            to="alice@example.com",
            subject="Test",
            message="Hello",
            status=Email.STATUS_CHOICES[2][0],
            backend_alias="default",
        )

        admin_user = FreeIPAUser(
            "admin",
            {
                "uid": ["admin"],
                "mail": ["admin@example.com"],
                "memberof_group": [settings.FREEIPA_ADMIN_GROUP],
            },
        )

        self._login_as_freeipa_user("admin")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=admin_user):
            with patch(
                "django.core.mail.backends.smtp.EmailBackend.open",
                side_effect=smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
            ):
                resp = self.client.get(f"/admin/post_office/email/{email.pk}/change/")

        self.assertEqual(resp.status_code, 200)

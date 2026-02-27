
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser


class AdminLogEntryIntegrationTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_admin_change_creates_logentry_with_shadow_user(self):
        # Use a model that is registered in admin and allows change.
        from django_ses.models import BlacklistedEmail

        obj = BlacklistedEmail.objects.create(email="a@example.org")

        username = "alice"
        freeipa_user = FreeIPAUser(username, {"uid": [username], "memberof_group": ["admins"]})

        self._login_as_freeipa_admin(username)

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=freeipa_user):
            url = reverse("admin:django_ses_blacklistedemail_change", args=[obj.pk])
            resp = self.client.post(
                url,
                data={
                    "email": "b@example.org",
                    "_save": "Save",
                },
                follow=False,
            )

        # Successful admin save redirects back to changelist or change page.
        self.assertEqual(resp.status_code, 302)

        from django.contrib.auth import get_user_model

        shadow_user = get_user_model().objects.get(username=username)

        # Verify a log entry was created for this change and is attributed to the shadow user.
        entry = LogEntry.objects.order_by("-action_time").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.user_id, shadow_user.pk)
        self.assertEqual(entry.object_id, str(obj.pk))

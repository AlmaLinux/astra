
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import DataError, IntegrityError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from core.freeipa.user import FreeIPAUser


class AdminShadowUserLogEntryTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _content_type(self) -> ContentType:
        # Use a guaranteed migrated model to avoid content type edge-cases.
        return ContentType.objects.get_for_model(get_user_model())

    def test_logentry_fails_with_freeipa_user_pk_without_shadow_user(self):
        user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})

        ct = self._content_type()
        # Without shadow users, FreeIPAUser.pk may not correspond to a DB row and may
        # not even fit in the DB column type.
        with self.assertRaises((DataError, IntegrityError)):
            LogEntry.objects.create(
                user_id=user.pk,
                content_type=ct,
                object_id="alice",
                object_repr="alice",
                action_flag=ADDITION,
                change_message="",
            )

    def test_shadow_user_middleware_allows_logentry_for_admin_paths(self):
        # Import inside the test so this file cleanly shows the failure before the feature exists.
        from core.middleware_admin_log import AdminShadowUserLogEntryMiddleware

        user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})

        request = self.factory.get("/admin/")
        request.user = user

        middleware = AdminShadowUserLogEntryMiddleware(lambda req: HttpResponse("ok"))
        middleware(request)

        # The middleware should have wrapped request.user so pk points at a real DB user row.
        ct = self._content_type()
        entry = LogEntry.objects.create(
            user_id=request.user.pk,
            content_type=ct,
            object_id="alice",
            object_repr="alice",
            action_flag=ADDITION,
            change_message="",
        )

        self.assertEqual(entry.user_id, request.user.pk)

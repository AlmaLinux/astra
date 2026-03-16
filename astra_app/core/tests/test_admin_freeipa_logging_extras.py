from __future__ import annotations

from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase

from core.admin import FreeIPAModelAdmin
from core.models import IPAUser


class _DummyAdmin(FreeIPAModelAdmin):
    pass


class AdminFreeIPALoggingExtrasTests(TestCase):
    def test_changeform_view_unhandled_exception_logs_exception_fields(self) -> None:
        admin_site = AdminSite()
        model_admin = _DummyAdmin(IPAUser, admin_site)
        model_admin.message_user = Mock()

        request = RequestFactory().get("/admin/auth/ipauser/alice/change/")

        with (
            patch(
                "django.contrib.admin.options.ModelAdmin.changeform_view",
                side_effect=Exception("kaboom"),
            ),
            patch("core.admin.logger") as logger,
        ):
            resp = model_admin.changeform_view(request, object_id="alice")

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(logger.exception.called)
        _args, kwargs = logger.exception.call_args
        self.assertIn("extra", kwargs)
        self.assertEqual(
            kwargs["extra"],
            {
                "error_type": "Exception",
                "error_message": "kaboom",
                "error_repr": "Exception('kaboom')",
                "error_args": "('kaboom',)",
            },
        )

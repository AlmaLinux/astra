from django.test import SimpleTestCase

from core.permissions import _has_permission


class _RaisesRuntimeErrorUser:
    def has_perm(self, permission: str) -> bool:
        raise RuntimeError("boom")


class _NoHasPermUser:
    pass


class PermissionHelpersTests(SimpleTestCase):
    def test_has_permission_logs_unexpected_exception_and_returns_false(self) -> None:
        user = _RaisesRuntimeErrorUser()

        with self.assertLogs("core.permissions", level="ERROR") as log_capture:
            allowed = _has_permission(user=user, permission="astra.view_membership")

        self.assertEqual(allowed, False)
        self.assertTrue(any("Unexpected error in _has_permission" in line for line in log_capture.output))

    def test_has_permission_handles_user_like_stub_without_logging(self) -> None:
        user = _NoHasPermUser()

        with self.assertNoLogs("core.permissions", level="ERROR"):
            allowed = _has_permission(user=user, permission="astra.view_membership")

        self.assertEqual(allowed, False)

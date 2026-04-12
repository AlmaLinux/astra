from django.test import SimpleTestCase

from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    _has_permission,
    membership_review_permissions,
)


class _RaisesRuntimeErrorUser:
    def has_perm(self, permission: str) -> bool:
        raise RuntimeError("boom")


class _NoHasPermUser:
    pass


class _GrantedPermissionUser:
    def __init__(self, granted_permissions: set[str]) -> None:
        self.granted_permissions = granted_permissions

    def has_perm(self, permission: str) -> bool:
        return permission in self.granted_permissions


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

    def test_membership_review_permissions_manage_permission_implies_membership_can_view(self) -> None:
        for permission in (ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP):
            permissions = membership_review_permissions(user=_GrantedPermissionUser({permission}))

            self.assertTrue(permissions["membership_can_view"], msg=permission)

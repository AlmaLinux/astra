import logging

from django.contrib.auth import get_user_model

from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)


class _AdminShadowUserProxy:
    """Proxy that preserves FreeIPA-backed behavior but uses a DB user id.

    Django admin's LogEntry writing uses `request.user.pk` / `request.user.id`.
    For FreeIPA users, those are not guaranteed to be a DB-backed PK. This
    proxy keeps permission/group logic on the FreeIPA user object, while
    presenting a DB-backed integer id for admin/audit logging.
    """

    def __init__(self, base_user, shadow_user_id: int):
        self._base_user = base_user
        self.pk = shadow_user_id
        self.id = shadow_user_id

    def __getattr__(self, name: str):
        return getattr(self._base_user, name)

    def __str__(self) -> str:  # pragma: no cover
        return str(self._base_user)


def _get_or_create_shadow_user_id(username: str, *, is_staff: bool, is_superuser: bool) -> int | None:
    """Return DB user id for username, creating a minimal shadow row if needed."""

    UserModel = get_user_model()
    try:
        user_obj, created = UserModel.objects.get_or_create(
            username=username,
            defaults={
                "is_active": True,
                "is_staff": is_staff,
                "is_superuser": is_superuser,
            },
        )

        # Ensure local auth cannot be used for these shadow users.
        if created or user_obj.has_usable_password():
            user_obj.set_unusable_password()

        # Keep derived flags up to date (derived from FreeIPA groups).
        changed = False
        desired_staff = is_staff
        desired_superuser = is_superuser
        if getattr(user_obj, "is_staff", False) != desired_staff:
            user_obj.is_staff = desired_staff
            changed = True
        if getattr(user_obj, "is_superuser", False) != desired_superuser:
            user_obj.is_superuser = desired_superuser
            changed = True

        if created or changed:
            user_obj.save(update_fields=["password", "is_staff", "is_superuser"])

        return int(user_obj.pk)
    except Exception:
        # Don't break admin pages if the auth tables aren't available or DB is down.
        logger.exception("Failed to create/find shadow admin user username=%s", username)
        return None


class AdminShadowUserLogEntryMiddleware:
    """Ensure FreeIPA users can write Django admin LogEntry rows.

    For requests under /admin/, if the authenticated user is FreeIPA-backed (no
    DB row), create/use a minimal DB shadow user and wrap request.user so
    `request.user.pk` points at the DB user id.

    This keeps FreeIPA as the source of truth: the DB user row is only an audit
    identity anchor for LogEntry.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            if not request.path.startswith("/admin/"):
                return self.get_response(request)

            user = getattr(request, "user", None)
            if not getattr(user, "is_authenticated", False):
                return self.get_response(request)

            # If it's already a DB-backed auth user instance, nothing to do.
            UserModel = get_user_model()
            if isinstance(user, UserModel):
                return self.get_response(request)

            username = None
            if hasattr(user, "get_username"):
                username = user.get_username()
            username = _normalize_str(username) or _normalize_str(getattr(user, "username", None))
            if not username:
                return self.get_response(request)

            shadow_user_id = _get_or_create_shadow_user_id(
                username,
                is_staff=bool(getattr(user, "is_staff", False)),
                is_superuser=bool(getattr(user, "is_superuser", False)),
            )
            if shadow_user_id is not None:
                request.user = _AdminShadowUserProxy(user, shadow_user_id)

            return self.get_response(request)
        except Exception:
            logger.exception("AdminShadowUserLogEntryMiddleware failed")
            return self.get_response(request)

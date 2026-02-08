
from io import BytesIO
from pathlib import Path
from tempfile import mkdtemp
from types import SimpleNamespace

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core import views_settings
from core.avatar_storage import avatar_path_handler


class SettingsAvatarUploadAndDeleteTests(TestCase):
    _test_media_root = Path(mkdtemp(prefix="alx_test_media_avatars_"))

    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _auth_user(self, username: str = "alice", email: str = "alice@example.org"):
        return SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: username,
            username=username,
            email=email,
        )

    def _make_upload(
        self,
        *,
        color: tuple[int, int, int],
        size: tuple[int, int] = (8, 8),
    ) -> SimpleUploadedFile:
        buf = BytesIO()
        Image.new("RGB", size, color=color).save(buf, format="PNG")
        return SimpleUploadedFile(
            "avatar.png",
            buf.getvalue(),
            content_type="image/png",
        )

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
        MEDIA_ROOT=_test_media_root,
        AVATAR_STORAGE_DIR="avatars",
        AVATAR_PROVIDERS=(
            "core.avatar_providers.LocalS3AvatarProvider",
            "avatar.providers.GravatarAvatarProvider",
            "avatar.providers.DefaultAvatarProvider",
        ),
    )
    def test_avatar_upload_overwrites_previous_upload(self) -> None:
        upload_url = reverse("settings-avatar-upload")
        delete_url = reverse("settings-avatar-delete")

        user = self._auth_user("alice", "alice@example.org")

        key = avatar_path_handler(instance=SimpleNamespace(user=user), ext="png")
        full_path = Path(default_storage.path(key))

        factory = RequestFactory()

        req1 = factory.post(upload_url, data={"avatar": self._make_upload(color=(10, 20, 30))})
        req1.user = user
        self._add_session_and_messages(req1)
        resp1 = views_settings.avatar_upload(req1)
        self.assertEqual(resp1.status_code, 302)
        self.assertTrue(full_path.exists())
        first_bytes = full_path.read_bytes()

        req2 = factory.post(upload_url, data={"avatar": self._make_upload(color=(200, 10, 10))})
        req2.user = user
        self._add_session_and_messages(req2)
        resp2 = views_settings.avatar_upload(req2)
        self.assertEqual(resp2.status_code, 302)
        self.assertTrue(full_path.exists())
        second_bytes = full_path.read_bytes()
        self.assertNotEqual(first_bytes, second_bytes)

        req3 = factory.post(delete_url)
        req3.user = user
        self._add_session_and_messages(req3)
        resp3 = views_settings.avatar_delete(req3)
        self.assertEqual(resp3.status_code, 302)
        self.assertFalse(full_path.exists())

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
        MEDIA_ROOT=_test_media_root,
        AVATAR_STORAGE_DIR="avatars",
        AVATAR_PROVIDERS=(
            "core.avatar_providers.LocalS3AvatarProvider",
            "avatar.providers.GravatarAvatarProvider",
            "avatar.providers.DefaultAvatarProvider",
        ),
    )
    def test_detect_avatar_provider_prefers_local_when_present(self) -> None:
        user = self._auth_user("alice", "alice@example.org")

        key = avatar_path_handler(instance=SimpleNamespace(user=user), ext="png")
        default_storage.save(key, self._make_upload(color=(10, 20, 30)))

        provider_path, avatar_url = views_settings._detect_avatar_provider(user)
        self.assertEqual(provider_path, "core.avatar_providers.LocalS3AvatarProvider")
        self.assertTrue(avatar_url)

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
        MEDIA_ROOT=_test_media_root,
        AVATAR_STORAGE_DIR="avatars",
        AVATAR_PROVIDERS=(
            "core.avatar_providers.LocalS3AvatarProvider",
            "avatar.providers.GravatarAvatarProvider",
            "avatar.providers.DefaultAvatarProvider",
        ),
        AVATAR_CACHE_ENABLED=True,
        AVATAR_CACHE_TIMEOUT=3600,
    )
    def test_avatar_upload_invalidates_django_avatar_cache(self) -> None:
        from avatar.templatetags.avatar_tags import avatar_url

        cache.clear()

        upload_url = reverse("settings-avatar-upload")
        user = self._auth_user("alice", "alice@example.org")

        before = avatar_url(user, 50, 50)
        self.assertTrue(before)

        factory = RequestFactory()
        req = factory.post(upload_url, data={"avatar": self._make_upload(color=(10, 20, 30))})
        req.user = user
        self._add_session_and_messages(req)
        resp = views_settings.avatar_upload(req)
        self.assertEqual(resp.status_code, 302)

        after = avatar_url(user, 50, 50)
        self.assertTrue(after)
        self.assertNotEqual(before, after)

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
        MEDIA_ROOT=_test_media_root,
        AVATAR_STORAGE_DIR="avatars",
        AVATAR_PROVIDERS=(
            "core.avatar_providers.LocalS3AvatarProvider",
            "avatar.providers.GravatarAvatarProvider",
            "avatar.providers.DefaultAvatarProvider",
        ),
    )
    def test_avatar_upload_resizes_large_images(self) -> None:
        upload_url = reverse("settings-avatar-upload")

        user = self._auth_user("alice", "alice@example.org")

        key = avatar_path_handler(instance=SimpleNamespace(user=user), ext="png")
        full_path = Path(default_storage.path(key))

        factory = RequestFactory()

        req = factory.post(
            upload_url,
            data={"avatar": self._make_upload(color=(10, 20, 30), size=(2400, 1600))},
        )
        req.user = user
        self._add_session_and_messages(req)
        resp = views_settings.avatar_upload(req)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(full_path.exists())

        with Image.open(full_path) as stored:
            width, height = stored.size
        self.assertLessEqual(max(width, height), 512)

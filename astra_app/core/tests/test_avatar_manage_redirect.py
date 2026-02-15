
from types import SimpleNamespace

from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, override_settings

from core import views_settings


class AvatarManageRedirectTests(TestCase):
    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _auth_user(self, username: str = "alice", email: str = "a@example.org"):
        return SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: username,
            username=username,
            email=email,
        )

    @override_settings(
        AVATAR_PROVIDERS=(
            "avatar.providers.LibRAvatarProvider",
            "avatar.providers.GravatarAvatarProvider",
            "avatar.providers.DefaultAvatarProvider",
        )
    )
    def test_redirects_to_libravatar_when_first_provider_is_libravatar(self):
        request = RequestFactory().get("/settings/avatar/")
        self._add_session_and_messages(request)
        request.user = self._auth_user()

        response = views_settings.avatar_manage(request)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://www.libravatar.org/"))

    @override_settings(
        AVATAR_PROVIDERS=(
            "avatar.providers.GravatarAvatarProvider",
            "avatar.providers.DefaultAvatarProvider",
        )
    )
    def test_redirects_to_gravatar_when_gravatar_is_first_provider(self):
        request = RequestFactory().get("/settings/avatar/")
        self._add_session_and_messages(request)
        request.user = self._auth_user()

        response = views_settings.avatar_manage(request)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://gravatar.com/"))

    @override_settings(
        AVATAR_PROVIDERS=("avatar.providers.DefaultAvatarProvider",),
        AVATAR_DEFAULT_URL="",
    )
    def test_falls_back_to_settings_profile_when_no_manage_url(self):
        request = RequestFactory().get("/settings/avatar/")
        self._add_session_and_messages(request)
        request.user = self._auth_user(email="")

        response = views_settings.avatar_manage(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/settings/?tab=profile")
        msgs = [m.message for m in get_messages(request)]
        self.assertTrue(any("does not support" in m for m in msgs))

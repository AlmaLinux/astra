
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, override_settings
from django.utils.functional import SimpleLazyObject

from core import views_users
from core.backends import FreeIPAUser


class ProfileAvatarRenderingTests(TestCase):
    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _auth_user(self, username: str = "alice", email: str = "a@example.org"):
        # Minimal shape for templates + avatar providers.
        return SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: username,
            username=username,
            email=email,
        )

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
        AVATAR_PROVIDERS=(
            "avatar.providers.GravatarAvatarProvider",
            "avatar.providers.DefaultAvatarProvider",
        ),
    )
    def test_profile_renders_gravatar_url_when_email_present(self):
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session_and_messages(request)
        request.user = self._auth_user(email="a@example.org")

        fake_user = FreeIPAUser(
            "alice",
            user_data={
                "uid": ["alice"],
                "mail": ["a@example.org"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "memberof_group": [],
            },
        )

        with patch("core.views_users._get_full_user", autospec=True) as mocked_get_full_user:
            mocked_get_full_user.return_value = fake_user
            response = views_users.user_profile(request, "alice")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("gravatar.com/avatar", content)

    @override_settings(
        FREEIPA_HOST="ipa.test",
        FREEIPA_VERIFY_SSL=False,
        FREEIPA_SERVICE_USER="svc",
        FREEIPA_SERVICE_PASSWORD="pw",
        AVATAR_PROVIDERS=(
            "avatar.providers.GravatarAvatarProvider",
            "avatar.providers.DefaultAvatarProvider",
        ),
    )
    def test_profile_avatar_tags_work_with_freeipa_lazy_user(self):
        factory = RequestFactory()
        request = factory.get("/")
        self._add_session_and_messages(request)

        fu = FreeIPAUser(
            "alice",
            user_data={
                "uid": ["alice"],
                "mail": ["a@example.org"],
                "givenname": ["Alice"],
                "sn": ["User"],
            },
        )

        # This matches production: AuthenticationMiddleware sets request.user
        # to a SimpleLazyObject.
        request.user = SimpleLazyObject(lambda: fu)

        with patch("core.views_users._get_full_user", autospec=True) as mocked_get_full_user:
            mocked_get_full_user.return_value = fu
            response = views_users.user_profile(request, "alice")

        self.assertEqual(response.status_code, 200)
        self.assertIn("gravatar.com/avatar", response.content.decode("utf-8"))

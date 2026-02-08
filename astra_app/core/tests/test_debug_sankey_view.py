
from types import SimpleNamespace

from django.test import RequestFactory, TestCase

from core.debug_views import sankey_debug_view


class SankeyDebugViewTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

    def _superuser(self) -> SimpleNamespace:
        return SimpleNamespace(
            is_authenticated=True,
            is_superuser=True,
            get_username=lambda: "admin",
        )

    def _user(self) -> SimpleNamespace:
        return SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            get_username=lambda: "viewer",
        )

    def test_superuser_can_view_sankey_debug_page(self) -> None:
        request = self.factory.get("/__debug__/sankey/")
        request.user = self._superuser()

        response = sankey_debug_view(request)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("debug-sankey-data", html)
        self.assertIn("debug-sankey-elected", html)
        self.assertIn("debug-sankey-eliminated", html)
        self.assertIn("Pear", html)
        self.assertIn("Voters", html)
        self.assertIn("Round 1", html)
        self.assertIn("Wikipedia", html)
        self.assertIn("Tally completed", html)
        self.assertIn("Tally round 1", html)

    def test_non_superuser_is_redirected(self) -> None:
        request = self.factory.get("/__debug__/sankey/")
        request.user = self._user()

        response = sankey_debug_view(request)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

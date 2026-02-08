
from django.contrib.staticfiles.storage import staticfiles_storage
from django.test import TestCase


class StaticViewsTests(TestCase):
    def test_robots_txt_disallows_all(self) -> None:
        resp = self.client.get("/robots.txt")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/plain")
        self.assertEqual(resp.content.decode("utf-8"), "User-agent: *\nDisallow: /\n")

    def test_favicon_redirects_to_static(self) -> None:
        resp = self.client.get("/favicon.ico")
        self.assertEqual(resp.status_code, 301)
        expected = staticfiles_storage.url("core/images/fav/favicon.ico")
        self.assertEqual(resp["Location"], expected)

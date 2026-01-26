from __future__ import annotations

from django.test import TestCase


class StaticViewsTests(TestCase):
    def test_robots_txt_disallows_all(self) -> None:
        resp = self.client.get("/robots.txt")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/plain")
        self.assertEqual(resp.content.decode("utf-8"), "User-agent: *\nDisallow: /\n")

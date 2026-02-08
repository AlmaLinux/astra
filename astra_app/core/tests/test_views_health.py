
from unittest.mock import patch

from django.test import TestCase


class HealthViewsTests(TestCase):
    def test_healthz_returns_ok(self) -> None:
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_readyz_returns_ok(self) -> None:
        resp = self.client.get("/readyz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertEqual(resp.json(), {"status": "ready", "database": "ok"})

    def test_readyz_returns_503_when_db_unavailable(self) -> None:
        with patch("django.db.connection.ensure_connection", side_effect=RuntimeError("db down")):
            resp = self.client.get("/readyz")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertEqual(resp.json(), {"status": "not ready", "error": "db down"})
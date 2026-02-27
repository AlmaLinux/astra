
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser


class ElectionAlgorithmDocsPageTests(TestCase):
    def _login_as_freeipa(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_algorithm_docs_page_renders(self) -> None:
        self._login_as_freeipa("alice")

        user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=user):
            resp = self.client.get(reverse("election-algorithm"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Meek STV (High-Precision Variant)")
        self.assertContains(resp, "80-digit precision")

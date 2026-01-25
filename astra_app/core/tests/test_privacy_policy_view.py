from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class PrivacyPolicyViewTests(TestCase):
    def test_privacy_policy_page_renders_markdown(self) -> None:
        response = self.client.get(reverse("privacy-policy"))
        self.assertEqual(response.status_code, 200)

        policy_path = Path(settings.BASE_DIR).parent / "docs" / "privacy-policy.md"
        raw = policy_path.read_text(encoding="utf-8")
        first_line = next(line for line in raw.splitlines() if line.strip())
        heading = first_line.lstrip("#").strip()
        self.assertContains(response, heading)

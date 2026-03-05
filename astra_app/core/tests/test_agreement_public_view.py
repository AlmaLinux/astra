from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


def _make_agreement(cn: str, description: str = "", enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(cn=cn, description=description, enabled=enabled)


class AgreementPublicViewTests(TestCase):
    def test_returns_200_and_shows_title(self) -> None:
        agreement = _make_agreement("Test Agreement", description="## Hello world\n\nSome text.")
        with patch("core.views_static.FreeIPAFASAgreement.get", return_value=agreement):
            response = self.client.get(reverse("agreement-detail", kwargs={"cn": "Test Agreement"}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Agreement")

    def test_renders_description_as_markdown(self) -> None:
        agreement = _make_agreement("My Agreement", description="## Section Header\n\nBody text.")
        with patch("core.views_static.FreeIPAFASAgreement.get", return_value=agreement):
            response = self.client.get(reverse("agreement-detail", kwargs={"cn": "My Agreement"}))
        self.assertEqual(response.status_code, 200)
        # Markdown heading should be rendered as an HTML <h2> tag.
        self.assertContains(response, "<h2>")
        self.assertContains(response, "Section Header")

    def test_unknown_agreement_returns_404(self) -> None:
        with patch("core.views_static.FreeIPAFASAgreement.get", return_value=None):
            response = self.client.get(reverse("agreement-detail", kwargs={"cn": "nonexistent"}))
        self.assertEqual(response.status_code, 404)

    def test_disabled_agreement_returns_404(self) -> None:
        agreement = _make_agreement("Disabled", enabled=False)
        with patch("core.views_static.FreeIPAFASAgreement.get", return_value=agreement):
            response = self.client.get(reverse("agreement-detail", kwargs={"cn": "Disabled"}))
        self.assertEqual(response.status_code, 404)

    def test_publicly_accessible_without_login(self) -> None:
        """Unauthenticated requests must not be redirected to the login page."""
        agreement = _make_agreement("Public Agreement", description="Open text.")
        with patch("core.views_static.FreeIPAFASAgreement.get", return_value=agreement):
            response = self.client.get(reverse("agreement-detail", kwargs={"cn": "Public Agreement"}))
        # 200, not a redirect to /login/
        self.assertEqual(response.status_code, 200)

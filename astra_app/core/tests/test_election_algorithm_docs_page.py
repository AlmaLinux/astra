
import hashlib
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.user import FreeIPAUser


class ElectionAlgorithmDocsPageTests(TestCase):
    def _generate_signing_material(self) -> tuple[str, str, str]:
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        public_key = private_key.public_key()
        public_key_pem = public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        public_key_der = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        public_key_fingerprint = hashlib.sha256(public_key_der).hexdigest()
        return private_key_pem, public_key_pem, public_key_fingerprint

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

    def test_algorithm_docs_page_links_to_generated_audit_verifier_download(self) -> None:
        self._login_as_freeipa("alice")

        user = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=user):
            resp = self.client.get(reverse("election-algorithm"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("verify-audit-log-download"))

    @override_settings(ELECTION_REKOR_SIGNING_KEY="")
    def test_downloaded_audit_verifier_uses_configured_signing_key(self) -> None:
        private_key_pem, expected_public_key_pem, expected_fingerprint = self._generate_signing_material()

        with self.settings(ELECTION_REKOR_SIGNING_KEY=private_key_pem):
            resp = self.client.get(reverse("verify-audit-log-download"))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Disposition"], 'attachment; filename="verify-audit-log.py"')
        self.assertIn(expected_public_key_pem.strip(), resp.content.decode("utf-8"))
        self.assertIn(
            f'trusted_public_key_sha256: str = "{expected_fingerprint}"',
            resp.content.decode("utf-8"),
        )

    @override_settings(ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----invalid")
    def test_downloaded_audit_verifier_fails_closed_when_signing_key_is_invalid(self) -> None:
        resp = self.client.get(reverse("verify-audit-log-download"))

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp["Content-Type"], "text/plain; charset=utf-8")
        self.assertNotIn("Content-Disposition", resp)
        self.assertContains(resp, "Audit verifier download is temporarily unavailable.", status_code=503)
        self.assertNotContains(resp, "trusted_public_key_sha256: str = \"99f3b7b90a6d81ac36e8aaf8066d3da0d7ccd49dab06995ad6eeb83384a0dd12\"", status_code=503)

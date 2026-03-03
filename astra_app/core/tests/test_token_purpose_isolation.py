from django.core import signing
from django.test import SimpleTestCase

from core.tokens import (
    make_account_invitation_token,
    make_organization_claim_token,
    make_password_reset_token,
    make_registration_activation_token,
    make_settings_email_validation_token,
    read_account_invitation_token_unbounded,
    read_organization_claim_token,
    read_password_reset_token,
    read_registration_activation_token,
    read_settings_email_validation_token,
)


class TokenPurposeIsolationTests(SimpleTestCase):
    def test_same_purpose_readers_accept_tokens_without_payload_purpose_key(self) -> None:
        password_reset_payload = {"u": "alice", "e": "alice@example.org", "lpc": ""}
        registration_payload = {"u": "alice", "e": "alice@example.org"}
        settings_payload = {"u": "alice", "a": "mail", "v": "alice@example.org"}
        org_claim_payload = {"org_id": 1, "claim_secret": "secret-value"}
        invitation_payload = {"invitation_id": 1}

        self.assertEqual(
            read_password_reset_token(make_password_reset_token(password_reset_payload)),
            password_reset_payload,
        )
        self.assertEqual(
            read_registration_activation_token(make_registration_activation_token(registration_payload)),
            registration_payload,
        )
        self.assertEqual(
            read_settings_email_validation_token(make_settings_email_validation_token(settings_payload)),
            settings_payload,
        )
        self.assertEqual(
            read_organization_claim_token(make_organization_claim_token(org_claim_payload)),
            org_claim_payload,
        )
        self.assertEqual(
            read_account_invitation_token_unbounded(make_account_invitation_token(invitation_payload)),
            invitation_payload,
        )

    def test_tokens_are_rejected_by_other_purpose_readers(self) -> None:
        issued_tokens = {
            "password-reset": make_password_reset_token(
                {"u": "alice", "e": "alice@example.org", "lpc": ""}
            ),
            "registration-activate": make_registration_activation_token(
                {"u": "alice", "e": "alice@example.org"}
            ),
            "settings-email-validate": make_settings_email_validation_token(
                {"u": "alice", "a": "mail", "v": "alice@example.org"}
            ),
            "org_claim": make_organization_claim_token(
                {"org_id": 1, "claim_secret": "secret-value"}
            ),
            "account-invitation": make_account_invitation_token(
                {"invitation_id": 1}
            ),
        }

        readers = {
            "password-reset": read_password_reset_token,
            "registration-activate": read_registration_activation_token,
            "settings-email-validate": read_settings_email_validation_token,
            "org_claim": read_organization_claim_token,
            "account-invitation": read_account_invitation_token_unbounded,
        }

        for token_purpose, token in issued_tokens.items():
            for reader_purpose, reader in readers.items():
                if token_purpose == reader_purpose:
                    continue
                with self.subTest(token_purpose=token_purpose, reader_purpose=reader_purpose):
                    with self.assertRaises((signing.BadSignature, signing.SignatureExpired)):
                        reader(token)

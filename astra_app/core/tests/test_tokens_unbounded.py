from django.core import signing
from django.test import SimpleTestCase

from core.tokens import make_account_invitation_token, read_account_invitation_token_unbounded


class TokensUnboundedTests(SimpleTestCase):
    def test_read_account_invitation_token_unbounded_round_trip(self) -> None:
        payload = {"invitation_id": 123, "email": "alice@example.com"}
        token = make_account_invitation_token(payload)

        self.assertEqual(read_account_invitation_token_unbounded(token), dict(payload))

    def test_read_account_invitation_token_unbounded_raises_on_bad_token(self) -> None:
        with self.assertRaises(signing.BadSignature):
            read_account_invitation_token_unbounded("not-a-token")

    def test_read_account_invitation_token_unbounded_allows_payloads_without_purpose_key(self) -> None:
        payload = {"invitation_id": 456, "username": "bob"}
        token = make_account_invitation_token(payload)

        self.assertEqual(read_account_invitation_token_unbounded(token), dict(payload))

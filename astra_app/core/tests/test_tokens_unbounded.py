from django.conf import settings
from django.core import signing
from django.test import SimpleTestCase

from core.tokens import make_signed_token, read_signed_token_unbounded


class TokensUnboundedTests(SimpleTestCase):
    def test_read_signed_token_unbounded_round_trip(self) -> None:
        payload = {"invitation_id": 123, "email": "alice@example.com"}
        token = make_signed_token(payload)

        self.assertEqual(read_signed_token_unbounded(token), dict(payload))

    def test_read_signed_token_unbounded_raises_on_bad_token(self) -> None:
        with self.assertRaises(signing.BadSignature):
            read_signed_token_unbounded("not-a-token")

    def test_read_signed_token_unbounded_matches_signing_loads_behavior(self) -> None:
        payload = {"invitation_id": 456, "username": "bob"}
        token = make_signed_token(payload)

        self.assertEqual(
            read_signed_token_unbounded(token),
            signing.loads(token, salt=settings.SECRET_KEY),
        )

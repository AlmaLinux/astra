from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from core.tokens import make_organization_claim_token, read_organization_claim_token


class OrganizationClaimTokenTtlTests(SimpleTestCase):
    @override_settings(ORGANIZATION_CLAIM_TOKEN_TTL_SECONDS=1234)
    def test_read_organization_claim_token_uses_configured_ttl(self) -> None:
        payload = {"org_id": 42, "claim_secret": "secret-value"}
        token = make_organization_claim_token(payload)

        with patch("core.tokens.signing.loads", return_value=dict(payload)) as loads_mock:
            self.assertEqual(read_organization_claim_token(token), dict(payload))

        self.assertEqual(loads_mock.call_args.kwargs["max_age"], 1234)

import datetime
from dataclasses import dataclass

from django.conf import settings
from django.core import signing
from django.http import HttpRequest
from django.urls import reverse

from core.models import Organization
from core.tokens import make_signed_token, read_signed_token
from core.views_utils import _normalize_str

ORGANIZATION_CLAIM_TOKEN_PURPOSE = "org_claim"
ORGANIZATION_CLAIM_TOKEN_TTL_SECONDS = int(datetime.timedelta(days=7).total_seconds())


@dataclass(frozen=True, slots=True)
class OrganizationClaimTokenPayload:
    organization_id: int
    claim_secret: str


def make_organization_claim_token(organization: Organization) -> str:
    return make_signed_token(
        {
            "p": ORGANIZATION_CLAIM_TOKEN_PURPOSE,
            "org_id": organization.pk,
            "claim_secret": organization.claim_secret,
        }
    )


def build_organization_claim_url(*, organization: Organization, request: HttpRequest | None = None) -> str:
    token = make_organization_claim_token(organization)
    path = reverse("organization-claim", args=[token])
    if request is not None:
        return request.build_absolute_uri(path)

    base = str(settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not base:
        raise ValueError("PUBLIC_BASE_URL must be configured to build absolute organization claim links.")
    return f"{base}{path}"


def read_organization_claim_token(token: str) -> OrganizationClaimTokenPayload:
    payload = read_signed_token(token, max_age_seconds=ORGANIZATION_CLAIM_TOKEN_TTL_SECONDS)
    if _normalize_str(payload.get("p")) != ORGANIZATION_CLAIM_TOKEN_PURPOSE:
        raise signing.BadSignature("Wrong token purpose")

    organization_id_raw = _normalize_str(payload.get("org_id"))
    if not organization_id_raw.isdigit():
        raise signing.BadSignature("Missing organization id")

    claim_secret = _normalize_str(payload.get("claim_secret"))
    if not claim_secret:
        raise signing.BadSignature("Missing claim secret")

    return OrganizationClaimTokenPayload(
        organization_id=int(organization_id_raw),
        claim_secret=claim_secret,
    )

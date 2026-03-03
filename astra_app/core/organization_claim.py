from dataclasses import dataclass

from django.core import signing
from django.http import HttpRequest
from django.urls import reverse

from core.models import Organization
from core.public_urls import build_public_absolute_url
from core.tokens import (
    make_organization_claim_token as _make_organization_claim_token_payload,
)
from core.tokens import (
    read_organization_claim_token as _read_organization_claim_token_payload,
)
from core.views_utils import _normalize_str


@dataclass(frozen=True, slots=True)
class OrganizationClaimTokenPayload:
    organization_id: int
    claim_secret: str


def make_organization_claim_token(organization: Organization) -> str:
    return _make_organization_claim_token_payload(
        {
            "org_id": organization.pk,
            "claim_secret": organization.claim_secret,
        }
    )


def build_organization_claim_url(*, organization: Organization, request: HttpRequest | None = None) -> str:
    token = make_organization_claim_token(organization)
    path = reverse("organization-claim", args=[token])
    if request is not None:
        return request.build_absolute_uri(path)
    return build_public_absolute_url(path, on_missing="raise")


def read_organization_claim_token(token: str) -> OrganizationClaimTokenPayload:
    payload = _read_organization_claim_token_payload(token)

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

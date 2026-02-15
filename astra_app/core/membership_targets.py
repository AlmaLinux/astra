from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MembershipTargetKind(StrEnum):
    user = "user"
    organization = "organization"


@dataclass(frozen=True, slots=True)
class MembershipTargetIdentity:
    kind: MembershipTargetKind
    identifier: str
    organization_identifier: str = ""
    organization_display_name: str = ""

    @classmethod
    def for_user(cls, username: str) -> MembershipTargetIdentity:
        normalized_username = str(username or "")
        return cls(
            kind=MembershipTargetKind.user,
            identifier=normalized_username,
            organization_identifier="",
            organization_display_name="",
        )

    @classmethod
    def for_organization(
        cls,
        *,
        organization_identifier: str,
        organization_display_name: str = "",
    ) -> MembershipTargetIdentity:
        normalized_identifier = str(organization_identifier or "")
        return cls(
            kind=MembershipTargetKind.organization,
            identifier=normalized_identifier,
            organization_identifier=normalized_identifier,
            organization_display_name=str(organization_display_name or ""),
        )

    @classmethod
    def from_target_fields(
        cls,
        *,
        username: str,
        organization_id: int | None,
        organization_code: str,
        organization_name: str,
        organization_fk_name: str,
    ) -> MembershipTargetIdentity:
        normalized_username = str(username or "")
        if normalized_username:
            return cls.for_user(normalized_username)

        normalized_org_code = str(organization_code or "")
        if normalized_org_code:
            organization_identifier = normalized_org_code
        elif organization_id is not None:
            organization_identifier = str(organization_id)
        else:
            organization_identifier = ""

        normalized_org_name = str(organization_name or "") or str(organization_fk_name or "")
        return cls.for_organization(
            organization_identifier=organization_identifier,
            organization_display_name=normalized_org_name,
        )

    def for_membership_request_filter(self) -> dict[str, object]:
        if self.kind == MembershipTargetKind.user:
            return {"requested_username": self.identifier}
        return _organization_filter("requested_organization", self.organization_identifier)

    def for_membership_filter(self) -> dict[str, object]:
        if self.kind == MembershipTargetKind.user:
            return {"target_username": self.identifier}
        return _organization_filter("target_organization", self.organization_identifier)

    def for_membership_log_filter(self) -> dict[str, object]:
        if self.kind == MembershipTargetKind.user:
            return {"target_username": self.identifier}
        return _organization_filter("target_organization", self.organization_identifier)


def _organization_filter(prefix: str, organization_identifier: str) -> dict[str, object]:
    normalized_identifier = str(organization_identifier or "")
    if normalized_identifier.isdigit():
        return {f"{prefix}_id": int(normalized_identifier)}
    return {f"{prefix}_code": normalized_identifier}

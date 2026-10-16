from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import Organization


class MembershipTargetKind(StrEnum):
    user = "user"
    organization = "organization"


@dataclass(frozen=True, slots=True)
class MembershipTargetIdentity:
    kind: MembershipTargetKind
    identifier: str
    organization_id: int | None = None
    organization_display_name: str = ""

    @classmethod
    def for_user(cls, username: str) -> MembershipTargetIdentity:
        return cls(kind=MembershipTargetKind.user, identifier=username)

    @classmethod
    def for_organization(
        cls,
        *,
        organization_id: int | None,
        organization_display_name: str = "",
    ) -> MembershipTargetIdentity:
        return cls(
            kind=MembershipTargetKind.organization,
            identifier=str(organization_id) if organization_id is not None else "",
            organization_id=organization_id,
            organization_display_name=organization_display_name,
        )

    @classmethod
    def from_target_fields(
        cls,
        *,
        username: str,
        organization: Organization | None,
        deleted_organization_id: int | None = None,
        deleted_organization_name: str = "",
    ) -> MembershipTargetIdentity:
        if username:
            return cls.for_user(username)
        # The deleted-organization snapshot is only filled once the FK has been nulled.
        if organization is not None:
            return cls.for_organization(organization_id=organization.pk, organization_display_name=organization.name)
        return cls.for_organization(
            organization_id=deleted_organization_id,
            organization_display_name=deleted_organization_name,
        )

    def for_membership_request_filter(self) -> dict[str, object]:
        if self.kind == MembershipTargetKind.user:
            return {"requested_username": self.identifier}
        return {"requested_organization_id": self.organization_id}

    def for_membership_filter(self) -> dict[str, object]:
        if self.kind == MembershipTargetKind.user:
            return {"target_username": self.identifier}
        return {"target_organization_id": self.organization_id}

    def for_membership_log_filter(self) -> dict[str, object]:
        if self.kind == MembershipTargetKind.user:
            return {"target_username": self.identifier}
        return {"target_organization_id": self.organization_id}

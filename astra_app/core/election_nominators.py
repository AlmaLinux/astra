"""Typed nominator identifier helpers.

Legacy nominators are plain FreeIPA usernames.
Organization nominators are encoded as `org:<organization_id>`.
"""

from dataclasses import dataclass
from enum import StrEnum

ORGANIZATION_NOMINATOR_PREFIX = "org:"


class NominatorKind(StrEnum):
    user = "user"
    organization = "organization"


@dataclass(frozen=True)
class ParsedNominatorIdentifier:
    raw: str
    kind: NominatorKind
    username: str
    organization_id: int | None


def organization_nominator_identifier(*, organization_id: int) -> str:
    return f"{ORGANIZATION_NOMINATOR_PREFIX}{organization_id}"


def organization_nominator_label(*, organization_name: str) -> str:
    name = str(organization_name or "").strip()
    if not name:
        return "Organization"
    return f"{name} (organization)"


def parse_nominator_identifier(value: str) -> ParsedNominatorIdentifier:
    raw = str(value or "").strip()
    if not raw:
        return ParsedNominatorIdentifier(
            raw="",
            kind=NominatorKind.user,
            username="",
            organization_id=None,
        )

    if raw.startswith(ORGANIZATION_NOMINATOR_PREFIX):
        organization_id_text = raw.removeprefix(ORGANIZATION_NOMINATOR_PREFIX).strip()
        if organization_id_text.isdigit():
            organization_id = int(organization_id_text)
            if organization_id > 0:
                return ParsedNominatorIdentifier(
                    raw=raw,
                    kind=NominatorKind.organization,
                    username="",
                    organization_id=organization_id,
                )

    return ParsedNominatorIdentifier(
        raw=raw,
        kind=NominatorKind.user,
        username=raw,
        organization_id=None,
    )
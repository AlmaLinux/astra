import csv
import io
import logging
from collections.abc import Callable

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from core.freeipa.user import FreeIPAUser

logger = logging.getLogger(__name__)


def normalize_invitation_email(value: str) -> str:
    return str(value or "").strip().lower()


def build_freeipa_email_lookup() -> dict[str, set[str]]:
    users = FreeIPAUser.all()
    if not users:
        logger.warning(
            "Account invitation FreeIPA lookup: FreeIPAUser.all() returned 0 users; falling back to per-email lookup"
        )
        return {}

    mapping: dict[str, set[str]] = {}
    for user in users:
        email = normalize_invitation_email(user.email)
        username = str(user.username or "").strip().lower()
        if not email or not username:
            continue
        mapping.setdefault(email, set()).add(username)

    return mapping


def parse_invitation_csv(content: str, *, max_rows: int) -> list[dict[str, str]]:
    raw = str(content or "")
    if not raw.strip():
        raise ValueError("CSV is empty.")

    sample = raw[:64 * 1024]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except Exception:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(raw), dialect)
    rows = [row for row in reader if any(str(cell or "").strip() for cell in row)]
    if not rows:
        raise ValueError("CSV is empty.")

    header = ["".join(ch for ch in str(cell or "").strip().lower() if ch.isalnum()) for cell in rows[0]]

    header_map: dict[int, str] = {}
    if "email" in header:
        for idx, name in enumerate(header):
            if name == "email":
                header_map[idx] = "email"
            elif name == "fullname":
                header_map[idx] = "full_name"
            elif name in {"note", "notes"}:
                header_map[idx] = "note"
    else:
        header_map = {0: "email", 1: "full_name", 2: "note"}

    data_rows = rows[1:] if "email" in header else rows
    if max_rows > 0 and len(data_rows) > max_rows:
        raise ValueError(f"CSV exceeds the maximum of {max_rows} rows.")

    parsed: list[dict[str, str]] = []
    for row in data_rows:
        entry: dict[str, str] = {"email": "", "full_name": "", "note": ""}
        for idx, key in header_map.items():
            if idx >= len(row):
                continue
            entry[key] = str(row[idx] or "").strip()
        parsed.append(entry)

    return parsed


def classify_invitation_rows(
    rows: list[dict[str, str]],
    *,
    existing_invitations: dict[str, object],
    freeipa_lookup: Callable[[str], list[str]],
) -> tuple[list[dict[str, object]], dict[str, int]]:
    preview_rows: list[dict[str, object]] = []
    counts: dict[str, int] = {"new": 0, "resend": 0, "existing": 0, "invalid": 0, "duplicate": 0}
    seen: set[str] = set()
    freeipa_cache: dict[str, list[str]] = {}

    for row in rows:
        email_raw = str(row.get("email") or "")
        full_name = str(row.get("full_name") or "")
        note = str(row.get("note") or "")
        normalized = normalize_invitation_email(email_raw)

        status = ""
        reason = ""
        matches: list[str] = []

        if not normalized:
            status = "invalid"
            reason = "Missing email"
        elif normalized in seen:
            status = "duplicate"
            reason = "Duplicate email in upload"
        else:
            seen.add(normalized)
            try:
                validate_email(normalized)
            except ValidationError:
                status = "invalid"
                reason = "Invalid email"
            else:
                cached = freeipa_cache.get(normalized)
                if cached is None:
                    cached = sorted(
                        {str(u or "").strip().lower() for u in freeipa_lookup(normalized) if str(u or "").strip()}
                    )
                    freeipa_cache[normalized] = cached
                matches = cached

                if matches:
                    status = "existing"
                    reason = "Already has an account"
                else:
                    existing = existing_invitations.get(normalized)
                    if existing is not None and getattr(existing, "accepted_at", None):
                        status = "existing"
                        reason = "Accepted invitation"
                        existing_usernames = getattr(existing, "freeipa_matched_usernames", [])
                        matches = [str(u or "").strip().lower() for u in existing_usernames if str(u or "").strip()]
                    else:
                        status = "resend" if existing is not None else "new"

        counts[status] = counts.get(status, 0) + 1
        preview_rows.append(
            {
                "email": normalized or email_raw,
                "full_name": full_name,
                "note": note,
                "status": status,
                "reason": reason,
                "freeipa_usernames": matches,
                "has_multiple_matches": len(matches) > 1,
            }
        )

    return preview_rows, counts


def confirm_existing_usernames(usernames: list[str]) -> tuple[list[str], bool]:
    confirmed: list[str] = []
    seen: set[str] = set()

    for username in usernames:
        normalized = str(username or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        try:
            user = FreeIPAUser.get(normalized)
        except Exception:
            logger.exception("Account invitation FreeIPA user lookup failed")
            return [], False
        if user is None:
            continue
        confirmed.append(normalized)
        seen.add(normalized)

    return sorted(confirmed), True


def find_account_invitation_matches(email: str) -> list[str]:
    normalized = normalize_invitation_email(email)
    if not normalized:
        return []

    try:
        matches = FreeIPAUser.find_usernames_by_email(normalized)
    except Exception:
        logger.exception("Account invitation FreeIPA email lookup failed")
        return []

    confirmed, ok = confirm_existing_usernames(matches)
    if not ok:
        return []
    return confirmed

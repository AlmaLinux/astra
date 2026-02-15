from collections.abc import Collection

from core.backends import FreeIPAUser


def search_freeipa_users(
    *,
    query: str,
    limit: int,
    exclude_usernames: Collection[str] | None = None,
) -> list[FreeIPAUser]:
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return []

    excluded = {str(username).strip() for username in (exclude_usernames or []) if str(username).strip()}

    matches: list[FreeIPAUser] = []
    for user in FreeIPAUser.all():
        username = str(user.username or "").strip()
        if not username:
            continue

        if username in excluded:
            continue

        full_name = str(user.full_name or "")
        if normalized_query not in username.lower() and normalized_query not in full_name.lower():
            continue

        matches.append(user)

    matches.sort(key=lambda user: str(user.username).lower())
    return matches[:limit]

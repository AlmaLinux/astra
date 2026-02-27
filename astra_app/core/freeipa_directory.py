from collections.abc import Collection

from core.freeipa.user import FreeIPAUser


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
    fetch_limit = limit + len(excluded)
    if fetch_limit <= 0:
        return []

    try:
        client = FreeIPAUser.get_client()
        result = client.user_find(
            a_criteria=normalized_query,
            o_all=True,
            o_no_members=False,
            o_sizelimit=fetch_limit,
            o_timelimit=0,
        )
    except Exception:
        return []

    if not isinstance(result, dict):
        return []

    raw_matches = result.get("result")
    if not isinstance(raw_matches, list):
        return []

    matches: list[FreeIPAUser] = []
    for user_data in raw_matches:
        if not isinstance(user_data, dict):
            continue

        uid = user_data.get("uid")
        if isinstance(uid, list):
            username = str(uid[0] if uid else "").strip()
        else:
            username = str(uid or "").strip()
        if not username:
            continue

        if username in excluded:
            continue

        user = FreeIPAUser(username, user_data)
        matches.append(user)
        if len(matches) >= limit:
            break

    matches.sort(key=lambda user: str(user.username).lower())
    return matches[:limit]

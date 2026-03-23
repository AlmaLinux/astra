from collections.abc import Collection

from core.freeipa.user import FreeIPAUser


def normalize_user_search_query(query: str) -> str:
    return str(query or "").strip().lower()


def user_matches_search_query(
    *,
    normalized_query: str,
    username: str,
    full_name: str,
) -> bool:
    if not normalized_query:
        return False

    normalized_username = str(username or "").strip().lower()
    if normalized_query in normalized_username:
        return True

    normalized_full_name = str(full_name or "").strip().lower()
    return normalized_query in normalized_full_name


def search_freeipa_users(
    *,
    query: str,
    limit: int,
    exclude_usernames: Collection[str] | None = None,
) -> list[FreeIPAUser]:
    normalized_query = normalize_user_search_query(query)
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
        if not user_matches_search_query(
            normalized_query=normalized_query,
            username=str(user.username),
            full_name=str(user.full_name),
        ):
            continue

        matches.append(user)
        if len(matches) >= limit:
            break

    matches.sort(key=lambda user: str(user.username).lower())
    return matches[:limit]

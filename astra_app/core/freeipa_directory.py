import re
from collections.abc import Collection

from core.freeipa.user import FreeIPAUser

_NAME_SEARCH_SEPARATOR_RE = re.compile(r"[^0-9a-z]+")


def normalize_user_search_query(query: str) -> str:
    return " ".join(str(query or "").strip().lower().split())


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
    if normalized_query in normalized_full_name:
        return True

    normalized_query_for_name = " ".join(_NAME_SEARCH_SEPARATOR_RE.sub(" ", normalized_query).split())
    if not normalized_query_for_name:
        return False

    normalized_full_name_for_name = " ".join(_NAME_SEARCH_SEPARATOR_RE.sub(" ", normalized_full_name).split())
    if normalized_query_for_name in normalized_full_name_for_name:
        return True

    if " " not in normalized_query_for_name:
        return False

    query_tokens = [token for token in normalized_query_for_name.split(" ") if token]
    if len(query_tokens) < 2:
        return False

    name_tokens = [token for token in normalized_full_name_for_name.split(" ") if token]
    if len(name_tokens) < 2:
        return False

    # Support first-name + remaining-initials queries like "alex ir".
    if not name_tokens[0].startswith(query_tokens[0]):
        return False

    trailing_initials = "".join(token[0] for token in name_tokens[1:] if token)
    if not trailing_initials:
        return False

    trailing_initials_query = "".join(query_tokens[1:])
    if not trailing_initials_query:
        return False

    return trailing_initials.startswith(trailing_initials_query)


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

    candidate_rows: list[dict[str, object]] = list(raw_matches)
    query_tokens = [token for token in normalized_query.split(" ") if token]
    if not candidate_rows and len(query_tokens) > 1:
        fallback_criteria = [query_tokens[0]]
        if query_tokens[-1] != query_tokens[0]:
            fallback_criteria.append(query_tokens[-1])

        for criteria in fallback_criteria:
            try:
                fallback_result = client.user_find(
                    a_criteria=criteria,
                    o_all=True,
                    o_no_members=False,
                    o_sizelimit=fetch_limit,
                    o_timelimit=0,
                )
            except Exception:
                continue

            if not isinstance(fallback_result, dict):
                continue

            fallback_raw_matches = fallback_result.get("result")
            if not isinstance(fallback_raw_matches, list):
                continue

            for user_data in fallback_raw_matches:
                if isinstance(user_data, dict):
                    candidate_rows.append(user_data)

            # Keep fallback bounded: stop after first retrieval that yields candidates.
            if fallback_raw_matches:
                break

    matches: list[FreeIPAUser] = []
    matched_usernames: set[str] = set()
    for user_data in candidate_rows:

        uid = user_data.get("uid")
        if isinstance(uid, list):
            username = str(uid[0] if uid else "").strip()
        else:
            username = str(uid or "").strip()
        if not username:
            continue

        if username in matched_usernames:
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
        matched_usernames.add(username)
        if len(matches) >= limit:
            break

    matches.sort(key=lambda user: str(user.username).lower())
    return matches[:limit]

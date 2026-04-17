import re
from collections.abc import Collection

from django.conf import settings

from core.freeipa.user import FreeIPAUser

_NAME_SEARCH_SEPARATOR_RE = re.compile(r"[^0-9a-z]+")
_SNAPSHOT_USER_ATTRS = (
    "displayname",
    "gecos",
    "cn",
    "givenname",
    "sn",
    "mail",
    "fasIsPrivate",
)


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


def _snapshot_username_from_uid(uid: object) -> str:
    if isinstance(uid, list):
        return str(uid[0] if uid else "").strip()
    return str(uid or "").strip()


def _snapshot_user_data(*, username: str, user_data: dict[str, object]) -> dict[str, object]:
    snapshot_data: dict[str, object] = {"uid": [username]}
    for key in _SNAPSHOT_USER_ATTRS:
        if key in user_data:
            value = user_data[key]
        elif key == "fasIsPrivate" and "fasisprivate" in user_data:
            value = user_data["fasisprivate"]
        else:
            continue

        snapshot_data[key] = list(value) if isinstance(value, list) else value
    return snapshot_data


def snapshot_freeipa_users(*, respect_privacy: bool = True) -> list[FreeIPAUser]:
    try:
        client = FreeIPAUser.get_client()
        result = client.user_find(
            o_all=False,
            o_no_members=True,
            o_sizelimit=0,
            o_timelimit=0,
        )
    except Exception:
        return []

    if not isinstance(result, dict):
        return []

    raw_matches = result.get("result")
    if not isinstance(raw_matches, list):
        return []

    filtered_usernames = {
        str(username).strip().lower()
        for username in settings.FREEIPA_FILTERED_USERNAMES
        if str(username).strip()
    }

    users: list[FreeIPAUser] = []
    for user_data in raw_matches:
        if not isinstance(user_data, dict):
            continue

        username = _snapshot_username_from_uid(user_data.get("uid"))
        if not username:
            continue

        if username.lower() in filtered_usernames:
            continue

        users.append(
            FreeIPAUser(
                username,
                _snapshot_user_data(username=username, user_data=user_data),
                respect_privacy=respect_privacy,
            )
        )

    users.sort(key=lambda user: str(user.username).strip().lower())
    return users


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
            o_all=False,
            o_no_members=True,
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
        try:
            fallback_result = client.user_find(
                a_criteria=query_tokens[0],
                o_all=False,
                o_no_members=True,
                o_sizelimit=fetch_limit,
                o_timelimit=0,
            )
        except Exception:
            fallback_result = None

        if isinstance(fallback_result, dict):
            fallback_raw_matches = fallback_result.get("result")
            if isinstance(fallback_raw_matches, list):
                for user_data in fallback_raw_matches:
                    if isinstance(user_data, dict):
                        candidate_rows.append(user_data)

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

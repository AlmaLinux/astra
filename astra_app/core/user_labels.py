from collections.abc import Mapping

from core.freeipa.user import FreeIPAUser


def user_label(username: str, *, user: FreeIPAUser | None = None) -> str:
    """Render a human-friendly label for a FreeIPA username.

    Use `Full Name (username)` when a full name is available and distinct.
    Fall back to just the username if FreeIPA is unavailable or lacks a name.
    """

    if not username:
        return ""

    full_name = "" if user is None else user.full_name
    if full_name and full_name != username:
        return f"{full_name} ({username})"
    return username


def user_choice(username: str, *, user: FreeIPAUser | None = None) -> tuple[str, str]:
    if not username:
        return ("", "")
    return (username, user_label(username, user=user))


def user_choice_from_freeipa(username: str) -> tuple[str, str]:
    u = str(username or "").strip()
    if not u:
        return ("", "")

    try:
        user = FreeIPAUser.get(u)
    except Exception:
        user = None

    return user_choice(u, user=user)


def user_choice_with_fallback(username: str, users_by_username: Mapping[str, FreeIPAUser]) -> tuple[str, str]:
    """Build a user choice tuple, using a pre-fetched mapping with FreeIPA fallback.

    Avoids repeated inline conditionals across admin forms.
    """
    user = users_by_username.get(username)
    if user is not None:
        return user_choice(username, user=user)
    return user_choice_from_freeipa(username)


def user_choices_from_users(usernames: list[str], *, users_by_username: Mapping[str, FreeIPAUser]) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    for username in usernames:
        if not username:
            continue
        choices.append(user_choice(username, user=users_by_username.get(username)))
    return choices

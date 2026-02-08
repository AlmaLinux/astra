def try_get_full_name(user: object) -> str:
    """Extract full name from a user-like object (FreeIPAUser, Django User, test stubs).

    Uses feature detection because template tags may receive different user-like
    objects with varying attribute shapes.
    """
    full_name = getattr(user, "full_name", None)
    if isinstance(full_name, str):
        return full_name.strip()
    if full_name is not None:
        try:
            return str(full_name).strip()
        except Exception:
            return ""

    get_full_name = getattr(user, "get_full_name", None)
    if callable(get_full_name):
        try:
            return str(get_full_name()).strip()
        except Exception:
            return ""
    return ""

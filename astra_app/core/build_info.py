import os


def get_build_sha() -> str:
    return os.environ.get("ASTRA_BUILD_SHA", "").strip()

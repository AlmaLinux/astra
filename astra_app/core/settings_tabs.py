from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class SettingsTabSpec:
    tab_id: str
    label: str


SETTINGS_TAB_REGISTRY: Final[tuple[SettingsTabSpec, ...]] = (
    SettingsTabSpec("profile", "Profile"),
    SettingsTabSpec("emails", "Emails"),
    SettingsTabSpec("keys", "SSH & GPG Keys"),
    SettingsTabSpec("security", "Security"),
    SettingsTabSpec("privacy", "Privacy"),
    SettingsTabSpec("agreements", "Agreements"),
    SettingsTabSpec("membership", "Membership"),
)
SETTINGS_DEFAULT_TAB: Final[str] = SETTINGS_TAB_REGISTRY[0].tab_id
_ALL_SETTINGS_TAB_IDS: Final[frozenset[str]] = frozenset(tab.tab_id for tab in SETTINGS_TAB_REGISTRY)


def is_settings_tab(tab_id: str) -> bool:
    return tab_id in _ALL_SETTINGS_TAB_IDS


def get_settings_tabs(*, show_agreements_tab: bool) -> tuple[SettingsTabSpec, ...]:
    if show_agreements_tab:
        return SETTINGS_TAB_REGISTRY
    return tuple(tab for tab in SETTINGS_TAB_REGISTRY if tab.tab_id != "agreements")


def normalize_settings_tab(tab_id: str, *, show_agreements_tab: bool) -> str:
    normalized_tab_id = str(tab_id or "").strip()
    visible_tab_ids = {tab.tab_id for tab in get_settings_tabs(show_agreements_tab=show_agreements_tab)}
    if normalized_tab_id in visible_tab_ids:
        return normalized_tab_id
    return SETTINGS_DEFAULT_TAB
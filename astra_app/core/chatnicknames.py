import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

from django.conf import settings

from core.ipa_user_attrs import _split_list_field
from core.views_utils import _normalize_str


class ChatScheme(StrEnum):
    mattermost = "mattermost"
    irc = "irc"
    matrix = "matrix"


_IRC_NICK_RE = re.compile(r"^[a-z_\[\]\\^{}|`-][a-z0-9_\[\]\\^{}|`-]*$", re.IGNORECASE)
_IRC_CHANNEL_RE = re.compile(r"^#[^\s,]{1,63}$")
_MATRIX_LOCALPART_RE = re.compile(r"^[a-z0-9.=_/-]+$", re.IGNORECASE)
_MATTERMOST_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
_MATTERMOST_TEAM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
_MATTERMOST_CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
_SERVER_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*(:[0-9]+)?$", re.IGNORECASE)


def _get_default_server(scheme: ChatScheme) -> str | None:
    networks = settings.CHAT_NETWORKS
    if not isinstance(networks, dict):
        return None

    network = networks.get(str(scheme))
    if not isinstance(network, dict):
        return None

    default_server = network.get("default_server")
    return default_server if isinstance(default_server, str) and default_server else None


def _get_default_team() -> str | None:
    networks = settings.CHAT_NETWORKS
    if not isinstance(networks, dict):
        return None

    network = networks.get(str(ChatScheme.mattermost))
    if not isinstance(network, dict):
        return None

    default_team = network.get("default_team")
    return default_team if isinstance(default_team, str) and default_team else None


@dataclass(frozen=True, slots=True)
class ParsedChatIdentity:
    scheme: ChatScheme
    nick: str
    server: str
    team: str
    raw: str


def parse_chat_identity(value: str, *, scheme_override: str | None = None) -> ParsedChatIdentity | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    parsed = urlparse(raw)
    parsed_scheme = (parsed.scheme or "").lower()

    if scheme_override:
        try:
            scheme = ChatScheme(str(scheme_override).lower())
        except ValueError:
            scheme = ChatScheme.irc
    elif parsed_scheme:
        try:
            scheme = ChatScheme(parsed_scheme)
        except ValueError:
            scheme = ChatScheme.irc
    else:
        scheme = ChatScheme.irc

    default_server = _get_default_server(scheme) or ""
    default_team = _get_default_team() or ""

    nick: str
    server: str
    team: str

    if parsed_scheme:
        path = (parsed.path or "").lstrip("/")
        team = ""

        if scheme == ChatScheme.mattermost:
            # Stored Mattermost form is mattermost://server/team/nick.
            # Legacy values may be mattermost://server/nick.
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                team = parts[0]
                if len(parts) >= 3 and parts[1] == "messages" and parts[2].startswith("@"):
                    nick = parts[2].lstrip("@")
                else:
                    nick = parts[1]
            else:
                nick = parts[0] if parts else ""
        else:
            nick = path

        if not nick and parsed.fragment:
            nick = parsed.fragment.lstrip("#@")
        nick = nick.lstrip("@").strip()
        server = _normalize_str(parsed.netloc) or default_server
    else:
        cleaned = raw.lstrip("@").strip()
        team = ""
        if scheme == ChatScheme.matrix and ":" in cleaned:
            nick, _, server = cleaned.rpartition(":")
        elif scheme == ChatScheme.mattermost and ":" in cleaned:
            # Plain form for Mattermost is @nick:server:team (server may include :port).
            parts = [p for p in cleaned.split(":") if p]
            if len(parts) >= 3:
                nick = parts[0]
                team = parts[-1]
                server = ":".join(parts[1:-1])
            else:
                nick, _, server = cleaned.rpartition(":")
        elif ":" in cleaned:
            nick, _, server = cleaned.partition(":")
        elif "@" in cleaned:
            nick, _, server = cleaned.partition("@")
        else:
            nick, server = cleaned, ""
        server = server.strip() or default_server

        if scheme == ChatScheme.mattermost and not team:
            team = default_team

    nick = nick.strip()
    server = server.strip()
    team = team.strip() if scheme == ChatScheme.mattermost else ""

    if scheme == ChatScheme.mattermost:
        if not nick or not server:
            return ParsedChatIdentity(scheme=scheme, nick=nick, server=server, team=team, raw=raw)

        # Enforce: if a custom server is specified, the team must be explicit.
        if server != default_server and not team:
            return ParsedChatIdentity(scheme=scheme, nick=nick, server=server, team="", raw=raw)

        if not team and server == default_server:
            team = default_team

        return ParsedChatIdentity(scheme=scheme, nick=nick, server=server, team=team, raw=raw)

    if not nick or not server:
        return ParsedChatIdentity(scheme=scheme, nick=nick, server=server, team="", raw=raw)

    return ParsedChatIdentity(scheme=scheme, nick=nick, server=server, team="", raw=raw)


@dataclass(frozen=True, slots=True)
class ChatNicknameLink:
    href: str
    title: str
    display: str
    external: bool = False


def build_chat_nickname_link(value: str, *, scheme_override: str | None = None) -> ChatNicknameLink | None:
    parsed = parse_chat_identity(value, scheme_override=scheme_override)
    if parsed is None:
        return None

    scheme = parsed.scheme
    nick = parsed.nick
    server = parsed.server
    team = parsed.team

    if not nick or not server:
        return None

    default_server = _get_default_server(scheme) or ""
    default_team = _get_default_team() or ""

    if scheme == ChatScheme.irc:
        if nick.startswith("#"):
            href = f"irc://{server}/{nick}"
            title = f"IRC channel on {server}"
            display = nick
            if not default_server or server != default_server:
                display = f"{display}@{server}"
            return ChatNicknameLink(href=href, title=title, display=display)

        href = f"irc://{server}/{nick},isnick"
        title = f"IRC on {server}"
        display = nick
        if not default_server or server != default_server:
            display = f"{display}:{server}"
        return ChatNicknameLink(href=href, title=title, display=display)

    if scheme == ChatScheme.matrix:
        args = settings.CHAT_MATRIX_TO_ARGS
        args = args if isinstance(args, str) and args else ""

        href = f"https://matrix.to/#/@{nick}:{server}"
        if args:
            href = f"{href}?{args}"

        title = f"Matrix on {server}"

        display = f"@{nick}"
        if not default_server or server != default_server:
            display = f"{display}:{server}"

        return ChatNicknameLink(href=href, title=title, display=display, external=True)

    if scheme == ChatScheme.mattermost:
        if not team:
            # For default server we can fall back to the configured default team.
            if default_server and server == default_server and default_team:
                team = default_team
            else:
                return None

        href = f"mattermost://{server}/{team}/messages/@{nick}"
        title = f"Mattermost on {server} ({team})"

        display = f"@{nick}"
        if (default_server and server != default_server) or (default_team and team != default_team) or (not default_server) or (not default_team):
            display = f"@{nick}:{server}:{team}"

        return ChatNicknameLink(href=href, title=title, display=display)

    return None


@dataclass(frozen=True, slots=True)
class ParsedChatChannel:
    scheme: ChatScheme
    channel: str
    server: str
    team: str
    raw: str


def _validate_channel_name(*, scheme: ChatScheme, channel: str) -> None:
    if scheme == ChatScheme.mattermost:
        if not _MATTERMOST_CHANNEL_RE.match(channel):
            raise ValueError("This does not look like a valid Mattermost channel name.")
        return

    if not channel.startswith("#"):
        raise ValueError("Channel names must start with #.")

    if scheme == ChatScheme.irc:
        if not _IRC_CHANNEL_RE.match(channel):
            raise ValueError("This does not look like a valid IRC channel name.")
        return

    if scheme == ChatScheme.matrix:
        localpart = channel.lstrip("#")
        if not localpart or not _MATRIX_LOCALPART_RE.match(localpart):
            raise ValueError("This does not look like a valid Matrix room alias.")
        return

    raise ValueError(f"Unsupported chat protocol: '{scheme}'")


def parse_chat_channel(value: str, *, scheme_override: str | None = None) -> ParsedChatChannel | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    parsed = urlparse(raw)
    parsed_scheme = (parsed.scheme or "").lower()

    if scheme_override:
        try:
            scheme = ChatScheme(str(scheme_override).lower())
        except ValueError:
            scheme = ChatScheme.irc
    elif parsed_scheme in {"ircs", "irc"}:
        scheme = ChatScheme.irc
    elif parsed_scheme:
        try:
            scheme = ChatScheme(parsed_scheme)
        except ValueError:
            scheme = ChatScheme.irc
    else:
        scheme = ChatScheme.irc

    default_server = _get_default_server(scheme) or ""
    default_team = _get_default_team() or ""

    channel: str
    server: str
    team: str

    if parsed_scheme:
        server = _normalize_str(parsed.netloc) or default_server
        team = ""

        if scheme == ChatScheme.mattermost:
            path = (parsed.path or "").lstrip("/")
            parts = [p for p in path.split("/") if p]
            # Stored forms:
            # - mattermost:/channels/<channel>
            # - mattermost://server/team/channels/<channel>
            channel = ""
            if len(parts) >= 3 and parts[1] == "channels":
                team = parts[0]
                channel = parts[2]
            elif len(parts) >= 2 and parts[0] == "channels":
                channel = parts[1]
            else:
                channel = parts[-1] if parts else ""

            channel = channel.strip().lstrip("~")
            if not team and server == default_server:
                team = default_team

            return ParsedChatChannel(scheme=scheme, channel=channel, server=server, team=team, raw=raw)

        # IRC + Matrix: stored channels commonly use URL fragments ("#").
        if parsed.fragment:
            channel = f"#{parsed.fragment.strip()}"
        else:
            path = (parsed.path or "").lstrip("/")
            channel = path.strip()
            if channel and not channel.startswith("#"):
                channel = f"#{channel}"

        return ParsedChatChannel(scheme=scheme, channel=channel, server=server, team="", raw=raw)

    # Plain forms:
    compact = raw.replace(" ", "")
    if compact.startswith("~"):
        scheme = ChatScheme.mattermost
        cleaned = compact.lstrip("~").strip()
        parts = [p for p in cleaned.split(":") if p]
        channel = parts[0] if parts else ""
        team = ""
        server = ""
        if len(parts) >= 3:
            team = parts[-1]
            server = ":".join(parts[1:-1])
        elif len(parts) == 2:
            server = parts[1]
        server = server.strip() or default_server
        team = team.strip()
        if not team and server == default_server:
            team = default_team
        return ParsedChatChannel(scheme=scheme, channel=channel, server=server, team=team, raw=raw)

    if compact.startswith("#"):
        scheme = ChatScheme.irc
        cleaned = compact
        if ":" in cleaned:
            ch, _, srv = cleaned.rpartition(":")
            channel = ch
            server = srv.strip() or default_server
        else:
            channel = cleaned
            server = default_server
        return ParsedChatChannel(scheme=scheme, channel=channel, server=server, team="", raw=raw)

    return ParsedChatChannel(scheme=scheme, channel="", server=default_server, team="", raw=raw)


@dataclass(frozen=True, slots=True)
class ChatChannelLink:
    href: str
    title: str
    display: str
    external: bool = False


def build_chat_channel_link(value: str, *, scheme_override: str | None = None) -> ChatChannelLink | None:
    parsed = parse_chat_channel(value, scheme_override=scheme_override)
    if parsed is None:
        return None

    scheme = parsed.scheme
    channel = parsed.channel
    server = parsed.server
    team = parsed.team

    if scheme == ChatScheme.mattermost:
        if not channel or not server:
            return None

        default_server = _get_default_server(ChatScheme.mattermost) or ""
        default_team = _get_default_team() or ""

        if not team:
            if default_server and server == default_server and default_team:
                team = default_team
            else:
                return None

        href = f"mattermost://{server}/{team}/channels/{channel}"
        title = f"Mattermost channel on {server} ({team})"

        display = f"~{channel}"
        if (default_server and server != default_server) or (default_team and team != default_team) or (not default_server) or (not default_team):
            display = f"~{channel}:{server}:{team}"

        return ChatChannelLink(href=href, title=title, display=display)

    if not channel or not server:
        return None

    default_server = _get_default_server(scheme) or ""

    if scheme == ChatScheme.irc:
        href = f"ircs://{server}/{channel}"
        title = f"IRC channel on {server}"
        display = channel
        if not default_server or server != default_server:
            display = f"{display}:{server}"
        return ChatChannelLink(href=href, title=title, display=display)

    if scheme == ChatScheme.matrix:
        args = settings.CHAT_MATRIX_TO_ARGS
        args = args if isinstance(args, str) and args else ""

        href = f"https://matrix.to/#/{channel}:{server}"
        if args:
            href = f"{href}?{args}"

        title = f"Matrix room alias on {server}"
        display = channel
        if not default_server or server != default_server:
            display = f"{display}:{server}"

        return ChatChannelLink(href=href, title=title, display=display, external=True)

    return None


def normalize_chat_channels_text(value: str, *, max_item_len: int = 64) -> str:
    items = _split_list_field(value)

    normalized: list[str] = []
    for item in items:
        compact = str(item).strip().replace(" ", "")
        if not compact:
            continue
        if len(compact) > max_item_len:
            raise ValueError(f"Each value must be at most {max_item_len} characters")

        parsed = urlparse(compact)
        scheme_raw = (parsed.scheme or "").lower()

        if scheme_raw in {"irc", "ircs", "matrix", "mattermost"}:
            scheme = ChatScheme.irc if scheme_raw == "ircs" else ChatScheme(scheme_raw)
            default_server = _get_default_server(scheme) or ""
            default_team = _get_default_team() or ""

            server = _normalize_str(parsed.netloc) or ""
            team = ""

            if scheme == ChatScheme.mattermost:
                path = (parsed.path or "").lstrip("/")
                parts = [p for p in path.split("/") if p]
                channel = ""
                if len(parts) >= 3 and parts[1] == "channels":
                    team = parts[0]
                    channel = parts[2]
                elif len(parts) >= 2 and parts[0] == "channels":
                    channel = parts[1]
                else:
                    raise ValueError(
                        "Mattermost channels must be like mattermost:/channels/<channel> or mattermost://server/team/channels/<channel>."
                    )

                channel = channel.strip().lstrip("~")
                _validate_channel_name(scheme=scheme, channel=channel)

                if server and not _SERVER_RE.match(server):
                    raise ValueError("This does not look like a valid Mattermost server name.")

                if server:
                    if not team:
                        if default_server and server == default_server and default_team:
                            team = default_team
                        else:
                            raise ValueError(
                                "Mattermost custom servers require a team name (use ~channel:server:team or mattermost://server/team/channels/channel)."
                            )
                    if not _MATTERMOST_TEAM_RE.match(team):
                        raise ValueError("This does not look like a valid Mattermost team name.")

                if not server or (default_server and server == default_server and team == default_team):
                    normalized.append(f"mattermost:/channels/{channel}")
                else:
                    normalized.append(f"mattermost://{server}/{team}/channels/{channel}")
                continue

            # IRC + Matrix
            if server and not _SERVER_RE.match(server):
                raise ValueError("This does not look like a valid server name.")

            if parsed.fragment:
                channel = f"#{parsed.fragment.strip()}"
            else:
                path = (parsed.path or "").lstrip("/").strip()
                channel = path if path.startswith("#") else f"#{path}" if path else ""

            _validate_channel_name(scheme=scheme, channel=channel)

            if not server:
                normalized.append(f"{scheme.value}:/{channel}")
            elif default_server and server == default_server:
                normalized.append(f"{scheme.value}:/{channel}")
            else:
                normalized.append(f"{scheme.value}://{server}/{channel}")
            continue

        # Plain heuristics:
        if compact.startswith("~"):
            default_server = _get_default_server(ChatScheme.mattermost) or ""
            default_team = _get_default_team() or ""

            cleaned = compact.lstrip("~").strip()
            parts = [p for p in cleaned.split(":") if p]
            channel = parts[0] if parts else ""
            team = ""
            server = ""

            if len(parts) >= 3:
                team = parts[-1]
                server = ":".join(parts[1:-1])
            elif len(parts) == 2:
                server = parts[1]

            channel = channel.strip()
            _validate_channel_name(scheme=ChatScheme.mattermost, channel=channel)

            server = server.strip() or ""
            if server and not _SERVER_RE.match(server):
                raise ValueError("This does not look like a valid Mattermost server name.")

            if server:
                if not team:
                    if default_server and server == default_server and default_team:
                        team = default_team
                    else:
                        raise ValueError(
                            "Mattermost custom servers require a team name (use ~channel:server:team or mattermost://server/team/channels/channel)."
                        )
                if not _MATTERMOST_TEAM_RE.match(team):
                    raise ValueError("This does not look like a valid Mattermost team name.")

            if not server or (default_server and server == default_server and team == default_team):
                normalized.append(f"mattermost:/channels/{channel}")
            else:
                normalized.append(f"mattermost://{server}/{team}/channels/{channel}")
            continue

        if compact.startswith("#"):
            default_server = _get_default_server(ChatScheme.irc) or ""
            channel = compact
            server = ""
            if ":" in compact:
                channel, _, server = compact.rpartition(":")
            channel = channel.strip()
            _validate_channel_name(scheme=ChatScheme.irc, channel=channel)

            server = server.strip()
            if server and not _SERVER_RE.match(server):
                raise ValueError("This does not look like a valid IRC server name.")

            if not server or (default_server and server == default_server):
                normalized.append(f"irc:/{channel}")
            else:
                normalized.append(f"irc://{server}/{channel}")
            continue

        raise ValueError("This does not look like a valid chat channel.")

    return "\n".join(normalized)


def normalize_chat_nicknames_text(value: str, *, max_item_len: int = 64, allow_irc_channels: bool = False) -> str:
    items = _split_list_field(value)

    normalized: list[str] = []
    for item in items:
        compact = str(item).strip().replace(" ", "")
        if not compact:
            continue
        if len(compact) > max_item_len:
            raise ValueError(f"Each value must be at most {max_item_len} characters")

        parsed = urlparse(compact)
        scheme_raw = (parsed.scheme or "").lower()

        nick = ""
        server = ""
        team = ""

        if scheme_raw in {s.value for s in ChatScheme}:
            scheme = ChatScheme(scheme_raw)
            path = (parsed.path or "").lstrip("/")
            if scheme == ChatScheme.mattermost:
                parts = [p for p in path.split("/") if p]
                if len(parts) >= 2:
                    team = parts[0]
                    if len(parts) >= 3 and parts[1] == "messages" and parts[2].startswith("@"):
                        nick = parts[2].lstrip("@")
                    else:
                        nick = parts[1]
                else:
                    nick = parts[0] if parts else ""
            else:
                nick = path
            if not nick and parsed.fragment:
                nick = parsed.fragment.lstrip("#@")
            nick = nick.lstrip("@").strip()
            server = _normalize_str(parsed.netloc)
        else:
            # Heuristics for common inputs:
            # - Matrix: @nick:server
            # - Mattermost: @nick:server:team
            # - IRC legacy: nick or nick:server or nick@server
            if compact.startswith("@") and compact.count(":") >= 2:
                scheme = ChatScheme.mattermost
                value2 = compact.lstrip("@").strip()
                parts = [p for p in value2.split(":") if p]
                nick = parts[0] if parts else ""
                team = parts[-1] if len(parts) >= 2 else ""
                server = ":".join(parts[1:-1]) if len(parts) >= 3 else ""
            elif compact.startswith("@") and ":" in compact:
                scheme = ChatScheme.matrix
                value2 = compact.lstrip("@").strip()
                nick, _, server = value2.rpartition(":")
            else:
                scheme = ChatScheme.irc
                value2 = compact.lstrip("@").strip()
                if ":" in value2:
                    nick, _, server = value2.partition(":")
                elif "@" in value2:
                    nick, _, server = value2.partition("@")
                else:
                    nick, server = value2, ""

        if scheme == ChatScheme.irc:
            if not _IRC_NICK_RE.match(nick) and not (allow_irc_channels and _IRC_CHANNEL_RE.match(nick)):
                if allow_irc_channels:
                    raise ValueError("This does not look like a valid IRC nickname or channel.")
                raise ValueError("This does not look like a valid IRC nickname.")
            if server and not _SERVER_RE.match(server):
                raise ValueError("This does not look like a valid IRC server name.")
        elif scheme == ChatScheme.matrix:
            if not _MATRIX_LOCALPART_RE.match(nick):
                raise ValueError("This does not look like a valid Matrix username.")
            if server and not _SERVER_RE.match(server):
                raise ValueError("This does not look like a valid Matrix server name.")
        elif scheme == ChatScheme.mattermost:
            default_server = _get_default_server(ChatScheme.mattermost) or ""
            default_team = _get_default_team() or ""

            if not _MATTERMOST_USERNAME_RE.match(nick):
                raise ValueError("This does not look like a valid Mattermost username.")
            if server and not _SERVER_RE.match(server):
                raise ValueError("This does not look like a valid Mattermost server name.")

            # If the server is given, we require a team. For the default server we
            # can fill in the default team; for custom servers the team must be explicit.
            if server:
                if not team:
                    if default_server and server == default_server and default_team:
                        team = default_team
                    else:
                        raise ValueError(
                            "Mattermost custom servers require a team name (use @nick:server:team or mattermost://server/team/nick)."
                        )
                if not _MATTERMOST_TEAM_RE.match(team):
                    raise ValueError("This does not look like a valid Mattermost team name.")
        else:
            raise ValueError(f"Unsupported chat protocol: '{scheme}'")

        # Normalize to stored form:
        # - irc/matrix: scheme:/nick (no server) or scheme://server/nick
        # - mattermost: mattermost:/nick (defaults) or mattermost://server/team/nick
        if scheme == ChatScheme.mattermost:
            if server:
                normalized.append(f"{scheme.value}://{server}/{team}/{nick}")
            else:
                normalized.append(f"{scheme.value}:/{nick}")
        else:
            if server:
                normalized.append(f"{scheme.value}://{server}/{nick}")
            else:
                normalized.append(f"{scheme.value}:/{nick}")

    return "\n".join(normalized)

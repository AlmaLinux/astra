from django.template import Library
from django.utils.html import format_html

from core.chatnicknames import build_chat_channel_link, build_chat_nickname_link

register = Library()


@register.filter(name="nickname")
def nickname(value: str | None, scheme: str | None = None) -> str:
    """Render a chat nickname as a link (Noggin-style).

    Supported schemes:
    - Mattermost: produces a mattermost:// link
    - IRC: produces an irc:// link
    - Matrix: produces a https://matrix.to link

    Input formats supported (best-effort):
    - Stored URL forms: mattermost:/nick, mattermost://server/nick, irc:/nick, irc://server/nick, matrix:/nick, matrix://server/nick
    - Plain forms: nick, @nick, nick:server, nick@server
    """

    if not value:
        return ""

    raw = str(value).strip()
    if not raw:
        return ""

    link = build_chat_nickname_link(raw, scheme_override=scheme)
    if link is None:
        # Best-effort: if we can't confidently build a link, return plain text.
        return raw

    if link.external:
        return format_html(
            '<a href="{}" title="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            link.href,
            link.title,
            link.display,
        )

    return format_html('<a href="{}" title="{}">{}</a>', link.href, link.title, link.display)


@register.filter(name="channel")
def channel(value: str | None, scheme: str | None = None) -> str:
    """Render a chat channel as a link.

    Supported schemes:
    - Mattermost: produces a mattermost:// channel link
    - IRC: produces an ircs:// channel link
    - Matrix: produces a https://matrix.to room alias link
    """

    if not value:
        return ""

    raw = str(value).strip()
    if not raw:
        return ""

    link = build_chat_channel_link(raw, scheme_override=scheme)
    if link is None:
        return raw

    if link.external:
        return format_html(
            '<a href="{}" title="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            link.href,
            link.title,
            link.display,
        )

    return format_html('<a href="{}" title="{}">{}</a>', link.href, link.title, link.display)

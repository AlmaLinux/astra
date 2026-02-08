import re
from html.parser import HTMLParser
from typing import override

# NOTE: This module is imported by migrations. Treat its behavior as effectively
# part of migration history: if you change it, you may change the outcome of a
# fresh install that runs migrations from scratch.

_DJANGO_TEMPLATE_TAG_RE = re.compile(r"{%[\s\S]*?%}")

# Normalize the standard signature block into a conventional plain-text signature.
# Mirrors `htmlToPlainText()` in astra_app/core/static/core/js/templated_email.js.
_SIGNATURE_BLOCK_RE = re.compile(
    r"<p>\s*<em>\s*The AlmaLinux Team\s*</em>\s*</p>",
    re.IGNORECASE,
)


class _TextFromHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignore_depth = 0
        self._anchor_stack: list[tuple[str | None, list[str]]] = []
        self._blockquote_depth = 0
        self._verbatim_depth = 0
        self._heading_level: int | None = None

    def _append_text(self, text: str) -> None:
        if not text:
            return

        if self._verbatim_depth:
            cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
        else:
            cleaned = re.sub(r"\s+", " ", text)

            # Mirror JS behavior: don't let HTML formatting/indentation introduce
            # leading spaces at the beginning of a line/paragraph.
            if not self._parts or self._parts[-1].endswith("\n"):
                cleaned = cleaned.lstrip(" ")

        if not cleaned.strip() and not self._verbatim_depth:
            # Mirror JS behavior: whitespace-only text nodes in inline contexts
            # should become a single space so we don't glue formatting tokens.
            if not self._parts:
                return
            last = self._parts[-1]
            if last.endswith((" ", "\n")):
                return
            self._parts.append(" ")
            return

        if self._heading_level is not None and (not self._parts or self._parts[-1].endswith("\n")):
            self._parts.append("#" * self._heading_level + " ")
        self._heading_level = None

        # Mirror JS blockquote behavior: prefix only non-empty lines.
        if self._blockquote_depth and (not self._parts or self._parts[-1].endswith("\n")):
            self._parts.append("> ")

        self._parts.append(cleaned)

    def _endswith_newline(self) -> bool:
        return bool(self._parts) and self._parts[-1].endswith("\n")

    def _ensure_newline(self) -> None:
        if not self._parts:
            return
        if self._endswith_newline():
            return
        self._parts.append("\n")

    def _ensure_blank_line(self) -> None:
        if not self._parts:
            return

        # Ensure we end with exactly one blank line (two newlines).
        tail = "".join(self._parts[-3:])
        if tail.endswith("\n\n"):
            return
        self._ensure_newline()
        self._ensure_newline()

    @property
    def text(self) -> str:
        raw = "".join(self._parts)
        raw = raw.replace("\xa0", " ")
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        lines = [ln.rstrip() for ln in raw.split("\n")]
        return "\n".join(lines).strip()

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Keep a conservative ignore list to avoid including non-content.
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._ignore_depth += 1
            return
        if self._ignore_depth:
            return

        # Match JS conversion: images are dropped entirely.
        if tag == "img":
            return

        if tag == "br":
            self._ensure_newline()
            return
        if tag == "hr":
            self._ensure_newline()
            self._parts.append("\n---")
            self._ensure_blank_line()
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._ensure_newline()
            try:
                self._heading_level = max(1, min(6, int(tag[1:])))
            except ValueError:
                self._heading_level = 3
            return

        if tag == "blockquote":
            self._ensure_newline()
            self._blockquote_depth += 1
            return

        if tag == "li":
            self._ensure_newline()
            self._parts.append("- ")
            return

        if tag in {"ul", "ol"}:
            self._ensure_newline()
            return

        if tag in {"p", "div"}:
            self._ensure_newline()
            self._parts.append("\n")
            return

        if tag == "pre":
            self._ensure_newline()
            self._verbatim_depth += 1
            return

        if tag in {"b", "strong"}:
            self._append_text("**")
            return
        if tag in {"i", "em"}:
            self._append_text("*")
            return
        if tag == "u":
            self._append_text("_")
            return

        if tag == "a":
            href: str | None = None
            for k, v in attrs:
                if k == "href" and v:
                    href = v
                    break
            self._anchor_stack.append((href, []))

    @override
    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            if self._ignore_depth:
                self._ignore_depth -= 1
            return
        if self._ignore_depth:
            return

        if tag in {"b", "strong"}:
            self._append_text("**")
            return
        if tag in {"i", "em"}:
            self._append_text("*")
            return
        if tag == "u":
            self._append_text("_")
            return

        if tag in {"p", "div"}:
            self._ensure_blank_line()
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._ensure_blank_line()
            return

        if tag == "blockquote":
            if self._blockquote_depth:
                self._blockquote_depth -= 1
            self._ensure_blank_line()
            return

        if tag == "li":
            self._ensure_newline()
            return

        if tag in {"ul", "ol"}:
            self._ensure_blank_line()
            return

        if tag == "pre":
            if self._verbatim_depth:
                self._verbatim_depth -= 1
            self._ensure_blank_line()
            return

        if tag == "a" and self._anchor_stack:
            href, chunks = self._anchor_stack.pop()
            link_text = re.sub(r"\s+", " ", "".join(chunks)).strip()

            if not href:
                self._append_text(link_text)
                return

            if not link_text or link_text == href:
                self._append_text(href)
                return

            self._append_text(f"[{link_text}]({href})")

    @override
    def handle_data(self, data: str) -> None:
        if self._ignore_depth:
            return

        if self._anchor_stack:
            _href, chunks = self._anchor_stack[-1]
            chunks.append(data)
            return

        self._append_text(data)


def text_from_html(html_content: str) -> str:
    """Convert HTML template content to a plain-text alternative.

    Mirrors the client-side conversion used by the template editor.

    - Drops Django template tags `{% ... %}` to avoid leaking directives.
    - Drops `<img>` elements.
    - Keeps `{{ ... }}` variables intact.
    """

    raw = str(html_content or "")

    raw = _SIGNATURE_BLOCK_RE.sub("\n<p>-- The AlmaLinux Team</p>", raw)
    raw = _DJANGO_TEMPLATE_TAG_RE.sub("", raw)

    parser = _TextFromHTMLParser()
    parser.feed(raw)
    parser.close()
    return parser.text

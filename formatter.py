"""Translate the Plugin's Markdown dialect into TrueConf-compatible HTML.

Rendering model
---------------
Mistune renders bottom-up: a token's children are rendered to a string and
passed to the token-type method. TrueConf commands must stay bare and must be
pulled out of any surrounding decoration (spec 0002 §5), so the Plugin cannot
use that string model: it needs to know where each command is while a tag is
being wrapped around it. Hyphenated command tokens display with hyphens
replaced by underscores — TrueConf's client highlights only tokens of
letters, digits, and underscores (spec 0002 §5). Block tokens therefore
render to visible lines that
``render_tokens`` joins with ``<br>``, and inline tokens render to ``Segment``
lists in which a command is a ``(True, ...)`` entry that ``_wrap_command_segments``
leaves outside the tags. That is why ``render_tokens`` is overridden instead
of the per-token-type string methods of ``HTMLRenderer``.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Sequence
from typing import Any, cast
from urllib.parse import unquote, urlsplit

import mistune

# A Mistune syntax-tree token.
Token = dict[str, Any]

# One rendered inline fragment: (is_command, escaped_text). A command is kept
# bare so TrueConf renders it as a command; non-command text is escaped and may
# already contain inline HTML.
Segment = tuple[bool, str]

_COMMAND_RE = re.compile(
    r"(?<![A-Za-z0-9_/<])/[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*(?:@[A-Za-z0-9_]+)?"
)
_ALLOWED_LINK_SCHEMES = frozenset({"http", "https", "mailto", "trueconf"})
_CELL_SEPARATOR = " | "
_THEMATIC_BREAK = "—" * 10
_INDENT_STEP = "&nbsp;" * 4
_BULLET_MARKERS = ("•", "-", "▪", "▸")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _underscore_command_token(token: str) -> str:
    """Render a matched command token with hyphens replaced by underscores.

    TrueConf's client highlights command tokens made only of letters, digits,
    and underscores, so a hyphenated command (``/claude-code``) is displayed
    as ``/claude_code``. Display-only: Hermes Core resolves inbound commands
    with ``-`` and ``_`` interchangeably (agent/skill_commands.py), so the
    underscored form still reaches the same skill. The ``@handle`` suffix is
    preserved — it may be needed for multi-bot disambiguation. Only matched
    tokens are touched: prose ``--`` and trailing hyphens are unaffected
    (spec 0002 §5).
    """

    return token.replace("-", "_")


def _split_commands(raw: str, *, replace_dashes: bool = True) -> list[Segment]:
    """Split raw text into (is_command, escaped_text) segments.

    Prose ``--`` becomes an em dash; ``replace_dashes=False`` keeps literal
    code (backtick spans) untouched.
    """

    segments: list[Segment] = []
    position = 0
    for match in _COMMAND_RE.finditer(raw):
        if match.start() > position:
            text = raw[position : match.start()]
            if replace_dashes:
                text = text.replace("--", "—")
            segments.append((False, html.escape(text, quote=True)))
        segments.append((True, _underscore_command_token(match.group(0))))
        position = match.end()
    if position < len(raw):
        text = raw[position:]
        if replace_dashes:
            text = text.replace("--", "—")
        segments.append((False, html.escape(text, quote=True)))
    return segments


def _preserve_code_spaces(escaped: str) -> str:
    """Replace spaces that TrueConf's HTML would collapse with ``&nbsp;``.

    ``escaped`` is already HTML-escaped code text. A space that is leading or
    part of a run of two or more spaces becomes ``&nbsp;`` so indentation and
    alignment survive; a single space between tokens stays breakable. A tab is
    expanded to four spaces.
    """

    normalized = escaped.replace("\t", "    ")
    return re.sub(r"^ |(?<= ) ", "&nbsp;", normalized)


def _wrap_run(
    run: Sequence[Segment],
    *,
    open_tag: str,
    close_tag: str,
    prev_command: bool,
    next_command: bool,
) -> list[Segment]:
    """Wrap one non-command run, keeping its boundary spaces outside the tags.

    TrueConf trims a space that ends up inside an inline tag and adjacent to a
    highlighted command, so the single space directly touching a bare command
    is emitted as a plain segment outside the tags.
    """

    text = "".join(part for _, part in run)
    start, end = 0, len(text)
    leading = trailing = ""
    if prev_command and end > start and text[start] == " ":
        leading = " "
        start += 1
    if next_command and end > start and text[end - 1] == " ":
        end -= 1
        trailing = " "
    wrapped: list[Segment] = []
    if leading:
        wrapped.append((False, leading))
    if start < end:
        wrapped.append((False, f"{open_tag}{text[start:end]}{close_tag}"))
    if trailing:
        wrapped.append((False, trailing))
    return wrapped


def _wrap_command_segments(
    segments: Iterable[Segment],
    open_tag: str,
    close_tag: str,
) -> list[Segment]:
    """Wrap non-command content in ``open_tag``/``close_tag``, leaving commands bare."""

    wrapped: list[Segment] = []
    pending: list[Segment] = []
    run_start = -1
    segment_list = list(segments)
    for index, (is_command, text) in enumerate(segment_list):
        if is_command:
            if pending:
                wrapped.extend(
                    _wrap_run(
                        pending,
                        open_tag=open_tag,
                        close_tag=close_tag,
                        prev_command=run_start > 0 and segment_list[run_start - 1][0],
                        next_command=True,
                    )
                )
                pending = []
            wrapped.append((True, text))
        else:
            if not pending:
                run_start = index
            pending.append((False, text))
    if pending:
        wrapped.extend(
            _wrap_run(
                pending,
                open_tag=open_tag,
                close_tag=close_tag,
                prev_command=run_start > 0 and segment_list[run_start - 1][0],
                next_command=False,
            )
        )
    return wrapped


def _parse_delimited(
    inline: mistune.InlineParser,
    match: re.Match[str],
    state: mistune.InlineState,
    *,
    marker: str,
    token_type: str,
) -> int | None:
    """Parse one non-empty custom inline span, including nested markup."""

    start = match.end()
    end = state.src.find(marker, start)
    while end >= 0:
        if end > start and not state.src[end - 1].isspace():
            nested = state.copy()
            nested.src = state.src[start:end]
            state.append_token({"type": token_type, "children": inline.render(nested)})
            return end + len(marker)
        end = state.src.find(marker, end + 1)
    return None


def _parse_underline(
    inline: mistune.InlineParser,
    match: re.Match[str],
    state: mistune.InlineState,
) -> int | None:
    return _parse_delimited(
        inline,
        match,
        state,
        marker="__",
        token_type="underline",
    )


def _parse_spoiler(
    inline: mistune.InlineParser,
    match: re.Match[str],
    state: mistune.InlineState,
) -> int | None:
    return _parse_delimited(
        inline,
        match,
        state,
        marker="||",
        token_type="spoiler",
    )


def _trueconf_inline_plugin(markdown: mistune.Markdown) -> None:
    markdown.inline.register(
        "underline",
        r"__(?=[^\s_])",
        _parse_underline,
        before="emphasis",
    )
    markdown.inline.register(
        "spoiler",
        r"\|\|(?=[^\s|])",
        _parse_spoiler,
        before="emphasis",
    )


_TABLE_ROW_RE = r"^ {0,3}\|[^\n]*\|[ \t]*(?:\n|$)"
_TABLE_ALIGN_NONE = re.compile(r"^ *-+ *$")
_TABLE_ALIGN_LEFT = re.compile(r"^ *:-+ *$")
_TABLE_ALIGN_RIGHT = re.compile(r"^ *-+: *$")
_TABLE_ALIGN_CENTER = re.compile(r"^ *:-+: *$")


def _table_is_escaped_pipe(text: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _table_cells(text: str) -> list[str]:
    cells: list[str] = []
    start = 0
    position = 0
    while position < len(text):
        if text[position] == "|" and not _table_is_escaped_pipe(text, position):
            cells.append(text[start:position].strip())
            start = position + 1
        position += 1
    cells.append(text[start:].strip())
    return cells


def _table_strip_pipes(line: str) -> str | None:
    text = line.rstrip("\n").rstrip(" \t")
    if not text.startswith("|") and text.startswith((" ", "\t")):
        text = text.lstrip(" ")
    if not text.startswith("|") or not text.endswith("|"):
        return None
    return text[1:-1]


def _table_alignments(delimiter: str) -> list[str | None] | None:
    alignments: list[str | None] = []
    for value in _table_cells(delimiter):
        if _TABLE_ALIGN_CENTER.match(value):
            alignments.append("center")
        elif _TABLE_ALIGN_LEFT.match(value):
            alignments.append("left")
        elif _TABLE_ALIGN_RIGHT.match(value):
            alignments.append("right")
        elif _TABLE_ALIGN_NONE.match(value) or not value.strip():
            alignments.append(None)
        else:
            return None
    return alignments


def _table_cell_tokens(
    cells: Sequence[str],
    alignments: Sequence[str | None],
    *,
    head: bool,
) -> list[dict[str, Any]]:
    padded = list(cells)
    if len(padded) < len(alignments):
        padded.extend("" for _ in range(len(alignments) - len(padded)))
    return [
        {
            "type": "table_cell",
            "text": cell,
            "attrs": {
                "align": alignments[index] if index < len(alignments) else None,
                "head": head,
            },
        }
        for index, cell in enumerate(padded)
    ]


def _parse_table_block(
    block: mistune.BlockParser,
    match: re.Match[str],
    state: mistune.BlockState,
) -> int | None:
    position = match.end()
    header = _table_strip_pipes(match.group(0))
    if header is None:
        return None

    delimiter_line = state.get_line(position)
    delimiter = _table_strip_pipes(delimiter_line)
    if delimiter is None:
        return None
    alignments = _table_alignments(delimiter)
    if alignments is None:
        return None
    header_cells = _table_cells(header)
    if not header_cells or len(header_cells) != len(alignments):
        return None

    position += len(delimiter_line)
    rows: list[dict[str, Any]] = []
    while position < state.cursor_max:
        line = state.get_line(position)
        text = _table_strip_pipes(line)
        if text is None:
            break
        rows.append(
            {
                "type": "table_row",
                "children": _table_cell_tokens(
                    _table_cells(text), alignments, head=False
                ),
            }
        )
        position += len(line)

    state.append_token(
        {
            "type": "table",
            "children": [
                {
                    "type": "table_head",
                    "children": _table_cell_tokens(header_cells, alignments, head=True),
                },
                {"type": "table_body", "children": rows},
            ],
        }
    )
    return position


def _trueconf_table_plugin(markdown: mistune.Markdown) -> None:
    markdown.block.register(
        "trueconf_table",
        _TABLE_ROW_RE,
        _parse_table_block,
        before="paragraph",
    )


class TrueConfHTMLRenderer(mistune.BaseRenderer):
    """Render a Mistune syntax tree using only TrueConf's supported tags.

    ``HTMLRenderer``'s per-token-type string methods cannot keep a bare
    command outside a surrounding decoration, so this renderer walks the
    syntax tree directly: block tokens become visible lines, inline tokens
    become ``Segment`` lists (see the module docstring).
    """

    NAME = "trueconf"

    def __init__(self) -> None:
        super().__init__()
        self._escape_anchor_close = False

    def render_tokens(
        self,
        tokens: Iterable[Token],
        state: mistune.BlockState | None = None,
    ) -> str:
        """Render one parsed message; anchor state never crosses messages.

        ``_escape_anchor_close`` pairs an unsafe raw ``<a>`` with its
        ``</a>``. It is deliberately reset here, the single per-message entry
        point reached through ``BaseRenderer.__call__``, so no ``</a>`` is
        escaped because of an earlier message (spec 0003 §5).
        """

        self._escape_anchor_close = False
        return self.render_document(tokens)

    def render_document(self, tokens: Iterable[Token]) -> str:
        lines: list[str] = []
        for token in tokens:
            if token["type"] == "blank_line":
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            lines.extend(self._render_block(token))

        while lines and lines[-1] == "":
            lines.pop()
        return "<br>".join(lines)

    def _render_block(self, token: Token) -> list[str]:
        token_type = token["type"]
        children = token.get("children", ())

        if token_type in {"paragraph", "block_text"}:
            return [self._render_inline(children)]
        if token_type == "heading":
            segments = _wrap_command_segments(
                self._render_inline_segments(children),
                "<b>",
                "</b>",
            )
            return ["".join(text for _, text in segments)]
        if token_type == "thematic_break":
            return [_THEMATIC_BREAK]
        if token_type == "block_code":
            return self._render_code_block(token)
        if token_type == "block_quote":
            quoted = self.render_document(children)
            return [f"<i>«{quoted}»</i>"]
        if token_type == "list":
            return self._render_list(token)
        if token_type == "table":
            return self._render_table(token)
        if token_type in {"block_html", "block_error"}:
            return [self._render_html_fragment(token.get("raw", ""))]
        if children:
            return [self._render_inline(children)]
        raw = token.get("raw", "")
        return [html.escape(str(raw), quote=True)] if raw else []

    def _render_inline(self, tokens: Iterable[Token]) -> str:
        return "".join(text for _, text in self._render_inline_segments(tokens))

    def _render_inline_segments(self, tokens: Iterable[Token]) -> list[Segment]:
        segments: list[Segment] = []
        for token in tokens:
            segments.extend(self._render_token_segments(token))
        return segments

    def _render_token_segments(self, token: Token) -> list[Segment]:
        token_type = token["type"]
        children = token.get("children", ())

        if token_type == "text":
            return _split_commands(str(token.get("raw", "")))
        if token_type in {"linebreak", "softbreak"}:
            return [(False, "<br>")]
        if token_type == "codespan":
            return _wrap_command_segments(
                _split_commands(str(token.get("raw", "")), replace_dashes=False),
                "<b><i>",
                "</i></b>",
            )
        if token_type in {"strong", "emphasis", "underline", "strikethrough"}:
            tag = {
                "strong": "b",
                "emphasis": "i",
                "underline": "u",
                "strikethrough": "s",
            }[token_type]
            return _wrap_command_segments(
                self._render_inline_segments(children),
                f"<{tag}>",
                f"</{tag}>",
            )
        if token_type == "spoiler":
            return [(False, f"[{self._render_inline(children)}]")]
        if token_type == "link":
            return [(False, self._render_link(token))]
        if token_type == "image":
            label = self._render_inline(children)
            url = str(token.get("attrs", {}).get("url", ""))
            return [(False, f"{label} ({html.escape(url, quote=True)})")]
        if token_type == "inline_html":
            return [(False, self._passthrough_html_tag(token.get("raw", "")))]
        if children:
            return self._render_inline_segments(children)
        return [(False, html.escape(str(token.get("raw", "")), quote=True))]

    def _render_link(self, token: Token) -> str:
        label = self._render_inline(token.get("children", ()))
        url = str(token.get("attrs", {}).get("url", ""))
        if self._is_allowed_url(url):
            return f'<a href="{html.escape(url, quote=True)}">{label}</a>'
        return f"{label} ({html.escape(unquote(url), quote=True)})"

    @staticmethod
    def _is_allowed_url(url: str) -> bool:
        if not url or url != url.strip() or url.startswith("//"):
            return False
        try:
            parsed = urlsplit(url)
            raw = urlsplit(unquote(url))
        except ValueError:
            return False
        for candidate in (parsed, raw):
            scheme = candidate.scheme.lower()
            if scheme not in _ALLOWED_LINK_SCHEMES:
                return False
            if scheme in {"http", "https"}:
                if not candidate.netloc:
                    return False
            elif not candidate.path:
                return False
        return True

    def _passthrough_html_tag(self, raw: Any) -> str:
        """Pass a raw inline HTML tag through, escaping an unsafe ``<a>`` href.

        TrueConf's server strips every tag outside its supported subset, so
        model-written HTML is forwarded as-is; only a supported ``<a>`` whose
        href is not on the scheme allowlist is degraded to escaped text (its
        matching ``</a>`` is escaped too). ``_escape_anchor_close`` carries
        that pairing only within the current message and is reset in
        ``render_tokens`` (spec 0003 §5).
        """

        tag = str(raw).strip()
        if tag == "</a>" and self._escape_anchor_close:
            self._escape_anchor_close = False
            return html.escape(tag, quote=True)
        match = re.fullmatch(r"<a\b([^>]*)>", tag)
        if match is not None:
            href = re.search(r'href\s*=\s*"([^"]*)"', match.group(1))
            if href is None or not self._is_allowed_url(href.group(1)):
                self._escape_anchor_close = True
                return html.escape(tag, quote=True)
        return tag

    def _render_html_fragment(self, raw: Any) -> str:
        """Pass a raw HTML block through, escaping text and unsafe ``<a>`` hrefs."""

        text = str(raw).strip()
        parts: list[str] = []
        position = 0
        for match in _HTML_TAG_RE.finditer(text):
            parts.append(
                html.escape(text[position : match.start()], quote=True).replace(
                    "\n", "<br>"
                )
            )
            parts.append(self._passthrough_html_tag(match.group(0)))
            position = match.end()
        parts.append(html.escape(text[position:], quote=True).replace("\n", "<br>"))
        return "".join(parts)

    def _render_code_block(self, token: Token) -> list[str]:
        raw = str(token.get("raw", ""))
        if raw.endswith("\n"):
            raw = raw[:-1]
        escaped_lines = [
            _preserve_code_spaces(html.escape(line, quote=True))
            for line in raw.split("\n")
        ]
        content = "<br>".join(escaped_lines)
        rendered = f"<i>{content}</i>"
        info = str(token.get("attrs", {}).get("info", "")).strip()
        if not info:
            return [rendered]
        language = info.split(None, 1)[0]
        return [f"{html.escape(language, quote=True)}:", rendered]

    def _render_list(self, token: Token, depth: int = 1) -> list[str]:
        """Render a list, keeping nesting visible through indentation and markers.

        Each deeper level is indented with four ``&nbsp;`` (one tab-width, the
        standard nesting step, surviving TrueConf's whitespace collapsing),
        bullet items switch markers per level (``•``, ``-``, ``▪``, ``▸``), and
        each numbered sub-list restarts at its own start number.
        """

        attrs = token.get("attrs", {})
        ordered = bool(attrs.get("ordered"))
        number = int(attrs.get("start") or 1)
        indent = _INDENT_STEP * (depth - 1)
        bullet = _BULLET_MARKERS[min(depth - 1, len(_BULLET_MARKERS) - 1)]
        lines: list[str] = []
        for item in token.get("children", ()):
            if item.get("type") != "list_item":
                continue
            content_parts: list[str] = []
            nested_lines: list[str] = []
            for child in item.get("children", ()):
                if child.get("type") == "list":
                    nested_lines.extend(self._render_list(child, depth + 1))
                else:
                    rendered = self._render_block(child)
                    content_parts.extend(rendered)
            marker = f"{number}. " if ordered else f"{bullet} "
            lines.append(indent + marker + "<br>".join(content_parts))
            lines.extend(nested_lines)
            number += 1
        return lines

    def _render_table(self, token: Token) -> list[str]:
        if self._table_uses_block_layout(token):
            return self._render_table_blocks(token)
        return self._render_table_compact(token)

    def _table_uses_block_layout(self, token: Token) -> bool:
        """A table renders as labeled blocks when it cannot stay readable in rows.

        TrueConf never aligns columns (no monospace rendering), so a compact
        row layout only survives narrow tables with short cells: three or
        fewer columns and every rendered cell at most 24 visible characters
        with no internal ``<br>``. Everything else becomes labeled blocks.
        """

        header_count = 0
        cells: list[str] = []
        for section in token.get("children", ()):
            section_type = section.get("type")
            if section_type == "table_head":
                header_count = len(section.get("children", ()))
                cells.extend(self._render_table_cells(section.get("children", ())))
            elif section_type == "table_body":
                for row in section.get("children", ()):
                    cells.extend(self._render_table_cells(row.get("children", ())))
        if header_count > 3:
            return True
        return any(
            "<br>" in rendered
            or len(html.unescape(_HTML_TAG_RE.sub("", rendered))) > 24
            for rendered in cells
        )

    def _render_table_compact(self, token: Token) -> list[str]:
        lines: list[str] = []
        for section in token.get("children", ()):
            section_type = section.get("type")
            if section_type == "table_head":
                cells = self._render_table_cells(section.get("children", ()))
                lines.append(f"<b>{_CELL_SEPARATOR.join(cells)}</b>")
                lines.append(_THEMATIC_BREAK)
            elif section_type == "table_body":
                for row in section.get("children", ()):
                    cells = self._render_table_cells(row.get("children", ()))
                    lines.append(_CELL_SEPARATOR.join(cells))
        return lines

    def _render_table_blocks(self, token: Token) -> list[str]:
        header_cells: list[str] = []
        rows: list[Token] = []
        for section in token.get("children", ()):
            section_type = section.get("type")
            if section_type == "table_head":
                header_cells = self._render_table_cells(section.get("children", ()))
            elif section_type == "table_body":
                rows = list(section.get("children", ()))
        if not rows:
            return [f"<b>{_CELL_SEPARATOR.join(header_cells)}</b>"]
        labels = [
            rendered if rendered else str(index + 1)
            for index, rendered in enumerate(header_cells)
        ]
        lines: list[str] = []
        for row in rows:
            if lines:
                lines.append("")
            cells = self._render_table_cells(row.get("children", ()))
            for index, value in enumerate(cells[: len(labels)]):
                rendered = f"<b>{labels[index]}:</b>"
                if value:
                    rendered += f" {value}"
                lines.append(rendered)
            if len(cells) > len(labels):
                lines.append(_CELL_SEPARATOR.join(cells[len(labels) :]))
        return lines

    def _render_table_cells(
        self,
        cells: Iterable[Token],
    ) -> list[str]:
        return [self._render_inline(cell.get("children", ())) for cell in cells]


_RENDERER = TrueConfHTMLRenderer()
_MARKDOWN = mistune.create_markdown(
    renderer=_RENDERER,
    plugins=[
        cast(mistune.Plugin, _trueconf_inline_plugin),
        "strikethrough",
        cast(mistune.Plugin, _trueconf_table_plugin),
    ],
)


def render_trueconf_html(markdown: str) -> str:
    """Translate TrueConf Markdown into the server-supported HTML subset."""

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    return cast(str, _MARKDOWN(normalized))

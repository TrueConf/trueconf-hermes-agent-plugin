# TrueConf Markdown Output Formatter — Design Specification

## 1. Background

Hermes delivers the raw agent text — typically Markdown — into
`adapter.send(content)` without any formatting conversion; the public
platform contract documents the content as "may be markdown". The TrueConf
Chatbot Connector renders only a narrow formatting set:

- `html` parse mode: `<b>`, `<i>`, `<s>`, `<u>`, `<a href>`; all other tags
  and attributes are stripped; `<br>` is the line-break form.
- `markdown` parse mode: bold, italic, strikethrough, and links only; every
  other syntax (code, monospace, headings, lists) passes through as literal
  text.

The Plugin currently (ADR 0004) tells the agent through `platform_hint` to
write the TrueConf HTML subset directly, and `format_message()` only converts
raw line endings to `<br>`. In practice agents write Markdown — inline code,
fenced blocks, bold commands like `**/start**`, headings — which TrueConf
mangles: unsupported tags are stripped in HTML mode, and Markdown-only syntax
is shown literally.

This specification supersedes the "no Markdown-to-HTML renderer" consequence
of ADR 0004: the Plugin now owns a deterministic, mistune-based
Markdown → TrueConf HTML translator. The wire format remains native HTML.

## 2. Goals and non-goals

Goals:

- Deterministically translate the agent's Markdown output into the supported
  TrueConf HTML subset.
- Keep the wire format at `parse_mode=html`.
- Define one unambiguous input dialect: each syntax maps to exactly one style.
- Preserve content: unsupported constructs degrade to readable text, never
  drop data (tables are linearized).
- Keep command tokens (`/start`, `/help@bot`) bare so TrueConf renders them as
  commands.
- Pass raw HTML tags through for the server to sanitize against its supported
  subset; unsafe raw link hrefs degrade to escaped text. Literal HTML the agent
  wants shown is written in backticks or fenced blocks.
- Emit links only for explicitly allowed URI schemes; unsafe or unknown links
  degrade to readable text.

Non-goals (separate follow-ups, see §9):

- Media captions are not chunked; an over-long caption follows the
  deterministic `too_long`, non-retryable result path.
- Auto-linking bare URLs.

## 3. TrueConf formatting contract

Advertised HTML subset — what the Plugin emits and the server renders:

- `<b>bold</b>`, `<i>italic</i>`, `<s>strikethrough</s>`, `<u>underline</u>`
- `<a href="URL">label</a>`, including mentions
  `<a href="trueconf:user@server">Label</a>`
- `<br>` line breaks

Nothing else is relied upon; the server strips unknown tags and attributes.
A blockquote has no server tag: the previously advertised `<quote>` renders an
empty block in TrueConf clients unless the message is an actual reply carrying
`replyMessageId`, so blockquotes degrade to italic text wrapped in
guillemets (`<i>«quote»</i>`).

## 4. Input dialect — TrueConf Markdown

The agent is told, through `platform_hint`, to write this dialect. It is
unambiguous: every input syntax maps to exactly one output style.

### 4.1 Inline

| Source | Rendered |
|---|---|
| `**text**` | `<b>text</b>` |
| `__text__` | `<u>text</u>` |
| `*text*` or `_text_` | `<i>text</i>` |
| `~~text~~` | `<s>text</s>` |
| `[label](url)` | `<a href="url">label</a>` for an allowed absolute URI; otherwise `label (url)` |
| `` `code` `` | `<b><i>code</i></b>` (bold-italic) |
| `||text||` | `[text]` (square brackets) |
| `--` | `—` (em dash); literal `--` stays unchanged inside code spans and fenced blocks |

The `**`/`__` split follows Telegram MarkdownV2 for underline while keeping
standard Markdown for bold: `**` = bold, `__` = underline. This removes the
ambiguity where standard Markdown maps `__` to bold.

### 4.2 Block

| Source | Rendered |
|---|---|
| single line ending | `<br>` |
| blank line | `<br><br>` |
| `# text` … `###### text` | `<b>text</b>` (all levels) |
| `- item` / `* item` | `• item` + `<br>` (level 2 `-`, level 3 `▪`, level 4 `▸`) |
| `1. item` | `1. item` + `<br>` |
| `> quote` | `<i>«quote»</i>` |
| fenced block ```` ```lang … ``` ```` | (`lang:` + `<br>` when a language is present) + `<i>lines joined by <br></i>`; leading and repeated spaces in code become `&nbsp;` so TrueConf keeps the indentation |
| table | narrow tables render as pipe-separated rows (bold header, divider, `<br>` between rows); wide, long, or many-column tables render as labeled blocks; no alignment |
| `---` | `————————————` (10 em-dashes) |
| raw HTML | escaped literal text |

Nested lists keep their hierarchy visible: each deeper level is indented with
four `&nbsp;` (one tab-width, the standard nesting step, surviving TrueConf's
whitespace collapsing), bullet items switch markers per level (`•`, `-`,
`▪`, `▸`), and each numbered sub-list restarts at its own start number.

Block rendering is line-oriented. Each block renderer returns visible lines
without leading or trailing separator markup, and the document renderer joins
adjacent lines with one `<br>`. A source blank line between blocks adds one
additional empty visible line, producing `<br><br>`. Runs of two or more source
blank lines are normalized to that same single empty line; exact blank-line
counts are not preserved. Empty input renders as an empty string, and a fenced
block at the start of a message does not acquire a leading `<br>`.

Table linearization:

Tables render in one of two deterministic forms. TrueConf never aligns columns
(no monospace rendering), so a row layout is used only when it stays readable:
three or fewer columns and every rendered cell at most 24 visible characters
with no internal `<br>`. Otherwise the table renders as labeled blocks.

- Compact rows: the GFM header row is one bold line with cells joined by a
  ` | ` separator, followed by a `—`×10 divider line; each body row becomes
  one line (`v1 | v2 | v3`). Rows are joined with `<br>`.
- Labeled blocks: header cells become labels; each body row is a block of
  `<b>label:</b> value` lines (the label rendered inline), and blocks are
  separated by one empty line (`<br><br>`). An empty header cell uses its
  1-based column number as the label. A body row with more cells than header
  labels keeps the extra cells as one appended ` | `-joined line; a row with
  fewer cells renders the missing trailing values as empty.

## 5. Command protection

A command token matches
`(?<![A-Za-z0-9_/<])/[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*(?:@[A-Za-z0-9_]+)?` in
plain text: a slash-prefixed identifier whose start is not glued to an
alphanumeric, another slash, or a `<`, so URL paths (`https://site.com/start`),
`foo/bar`, and HTML closing tags (`</b>`, `</a>`) are never misread as commands.
A hyphen joins command word segments, so compound commands (`/claude-code`,
`/document-to-action-items`) stay one token; a trailing or doubled hyphen is
not part of the command (`/help-` keeps `-` as prose, and `--` after a command
still becomes an em dash). Commands render
bare everywhere:
TrueConf highlights a command only when it carries no decoration, so any
decoration is split around the command rather than applied to it. Within
`**bold**`/`*italic*`/`~~strike~~`/`__underline__`, inline code
(`` ` ``…`` ` ``), and headings, each command is pulled out of the decoration
and left bare while the surrounding text keeps the style.

TrueConf's client highlights command tokens made only of letters, digits, and
underscores, so a hyphen in a matched command token is displayed as an
underscore: `/claude-code` renders as `/claude_code` and `/claude-code@bot`
as `/claude_code@bot`. The substitution is display-only and confined to the
matched token (prose `--` and trailing hyphens are untouched): Hermes Core
resolves inbound commands with `-` and `_` interchangeably, so the underscored
form still reaches the same skill. The `@handle` suffix is preserved even
though TrueConf does not highlight suffixed commands — it may be needed for
multi-bot disambiguation.

- `**/start**` and `# /start` → `/start`
- `` `/start` `` and `**_/start_**` → `/start`
- `**/claude-code**` and `` `/claude-code` `` → `/claude_code`
- `**/claude-code@bot**` → `/claude_code@bot`
- `**run /start now**` → `<b>run</b> /start <b>now</b>`
- `` `run /start now` `` → `<b><i>run</i></b> /start <b><i>now</i></b>`
- `**open https://site.com/start**` → `<b>open https://site.com/start</b>`

TrueConf trims a space that is the last character inside an inline tag when a
highlighted bare command follows, so a decoration never absorbs the space
directly touching a command: that single space is kept as plain text outside
the tag. It stays a regular space, not `&nbsp;`:

- `**Confirm /new**` → `<b>Confirm</b> /new`
- `reply /approve, /always` → `reply /approve, /always`
- `` _Text fallback: reply `/approve`, `/always`_ `` →
  `<i>Text fallback: reply</i> /approve<i>,</i> /always<i>.</i>`

`||spoiler||` wraps its whole content in `[...]` and is not command-split;
`[label](url)` keeps the label as-is (a command label stays a command).
`platform_hint` additionally asks the agent to write commands as plain text.

## 6. Architecture

- New module `formatter.py` exposing a pure function:

  ```python
  def render_trueconf_html(markdown: str) -> str
  ```

  No I/O, no platform state; fully hermetic-testable.

- Built on `mistune>=3,<4`:

  - a custom `BaseRenderer` subclass emitting only the §3 subset, passed to
    `create_markdown(renderer=...)` so `render_trueconf_html` goes through the
    documented renderer entry point; it renders tokens directly instead of
    using `HTMLRenderer`'s per-token-type string methods, because those cannot
    keep a bare command outside a surrounding decoration (§5);
  - a custom inline rule for `__` → underline, registered so `**` remains
    bold;
  - a custom inline rule for `||…||` → `[…]`;
  - the `strikethrough` plugin;
  - a Plugin-owned `trueconf_table` block rule replacing the GFM `table` plugin:
    it emits the same `table`/`table_head`/`table_body`/`table_row`/
    `table_cell` token shape, pads a body row with fewer cells than the header
    to empty cells, keeps a body row with more cells, and never falls back to
    literal pipe text;
  - `escape=True`, with raw HTML blocks and inline HTML passed through so the
    server can apply its own tag subset sanitization.

  The renderer is stateless per message: the degraded-raw-`<a>` flag is reset
  at the per-message render entry, so rendering is deterministic and no
  `</a>` escapes because of an earlier message.

- `mistune>=3,<4` is a Plugin runtime dependency. The same requirement is
  declared in `pyproject.toml` and `plugin.yaml`. Because manifest
  `python_dependencies` is declarative and does not install packages by
  itself, the existing passive availability check and `ensure_deps_fn` are
  generalized from an SDK-only check to a Plugin-runtime dependency check.
  The latter passes both the SDK and Mistune requirements to Hermes'
  `tools.lazy_deps.install_specs()` pipeline when either is unavailable.
  Contract tests keep the project, manifest, availability check, and installer
  requirements synchronized.

- `adapter.format_message()` becomes `return render_trueconf_html(content)`,
  replacing the raw `\n` → `<br>` normalization. All outbound text paths
  already route through `format_message`: `send`, `edit_message`, media
  captions, and the standalone sender. `parse_mode` stays `ParseMode.HTML`.

## 7. Escaping and security

- Plain text is HTML-escaped (`<`, `>`, `&`, `"`).
- Raw HTML tags the agent writes are passed through, and TrueConf's server
  strips every tag and attribute outside its supported subset. The agent
  escapes literal HTML in backticks or fenced blocks; a raw `<a>` whose href is
  not on the link allowlist (and its matching `</a>`) is degraded to escaped
  text instead of being forwarded.
- Link URLs are escaped for the `href` attribute; only `href` is emitted, no
  other attributes.
- Links are emitted only for absolute `http`, `https`, `mailto`, and `trueconf`
  URIs. Scheme matching is case-insensitive. Relative URLs, protocol-relative
  URLs, malformed URLs, and every other scheme degrade to the readable
  `label (url)` form, with both parts escaped as text. The `trueconf` scheme is
  retained for native mentions such as `trueconf:user@server`.

## 8. platform_hint

Rewritten to describe the dialect: supported styles (`**bold**`, `*italic*`,
`~~strikethrough~~`, `__underline__`, `[label](url)`), nested lists
keeping indentation and per-level markers, the hybrid table forms (compact
pipe-separated rows; labeled blocks for wide, long, or many-column tables),
what degrades (backtick code → bold-italic text, fenced blocks → italic text
with indentation kept, headings → bold, blockquotes → italic with guillemets,
`||spoiler||` → `[…]`), commands as bare text, and the 4096 visible-character
limit.

## 9. Chunking interaction and follow-ups

The SDK does not auto-split: `Bot.send_message`/`edit_message` raise
`TextMessageTooLongError` above 4096 visible characters and document
`safe_split_text` for the caller. Rendering can increase visible length by
adding bullets, table separators, repeated block labels, spoiler brackets, a
code-language label, or the thematic-break replacement.

The Plugin owns outbound chunking: `TrueConfAdapter.send` and
`edit_message` render each content value to HTML and split it with the SDK's
`safe_split_text` at the 4096 visible-character limit. Overflow is delivered
as chained continuation messages and reported through Hermes' `SendResult`
`message_id`/`continuation_message_ids` contract, so Hermes re-targets
subsequent edits at the last delivered continuation. The SDK splitter
recognizes `<br>` as an atomic, zero-width break point (fixed in
`python-trueconf-bot`), so a chunk boundary never splits a `<br>` or any
other tag, and it carries open `<b>/<i>/<u>/<s>/<a>` tags across chunks.
Media captions are the remaining out-of-scope case; an over-long caption
still follows the deterministic `too_long`, non-retryable result path.

## 10. Testing

Hermetic `tests/test_formatter.py`:

- each inline rule, including the `**` vs `__` disambiguation and nesting
  (`**a _b_ c**`);
- `--` → em dash in prose, literal inside code spans and fenced blocks;
- each block rule: headings, ordered/unordered lists (including nested levels
  with `&nbsp;` indentation and per-level bullet markers), blockquotes (as
  italic), fenced code with and without a language tag and with preserved
  indentation, thematic breaks, spoilers;
- GFM table linearization, including the compact-row form (pipe-separated
  cells, bold header, divider line), the labeled-block form for wide and long
  tables, empty cells, empty header labels, ragged rows in both forms, the
  24-character boundary, and inline formatting in header and body cells;
- command unwrapping for decorated, nested, heading, inline-code, and embedded
  commands; command-like URL paths and `foo/bar` stay untouched; linked commands
  stay linked; boundary spaces stay as regular plain text outside the tags;
- raw HTML passthrough: supported tags render, text and `&` are escaped, an
  unsafe raw `<a>` href (and its `</a>`) degrades to escaped text, and the
  matching safe anchor still renders;
- link policy for every allowed scheme plus relative, protocol-relative,
  malformed, and unsafe URLs;
- block joining: adjacent blocks, nested and empty list items, multiline and
  nested blockquotes, a leading fenced block, and normalization of repeated
  blank lines;
- malformed Markdown: unclosed decorations, code spans, links, and fences;
- boundaries: empty input, newline-only input, CRLF and CR normalization, and a
  single paragraph whose rendered visible length crosses 4096.

Adapter wiring: `format_message` returns the expected HTML and is used by
`send`, `edit_message`, and media captions; `parse_mode` stays HTML.

## 11. Acceptance criteria

- All §10 tests pass; the full existing hermetic suite and Ruff/ty checks stay
  green.
- `render_trueconf_html` is deterministic and dependency-isolated.
- `mistune>=3,<4` is declared consistently in the project and Plugin manifest;
  the passive dependency check detects its absence without installing it, and
  the lazy dependency hook can install and import it in a clean environment.
- `platform_hint` advertises the §4 dialect.
- Links obey the §7 scheme allowlist and never emit an unsafe `href`.
- Content that exceeds 4096 visible characters after rendering is delivered
  as multiple chained messages via the SDK's `safe_split_text`, each chunk
  within the limit; media captions remain non-chunked and an over-long caption
  follows the existing non-retryable `too_long` path.
- ADR 0004 is amended and CONTEXT.md decision 10 is updated to match.
- No SDK or Hermes monkeypatching.

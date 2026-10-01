# TrueConf Markdown Output Formatter — Corrections

Status: **implemented; re-verify at the next code review**

Amends: [docs/specs/0002-trueconf-markdown-formatter.md](0002-trueconf-markdown-formatter.md)

A code review of the formatter implementation (commits `b0d08f9`..`0391a2b`)
found places where the implementation did not meet the §4/§7 requirements of
spec 0002, plus one unasked-for behavior that this document ratifies into the
dialect. Each finding below is a normative requirement on the formatter
implementation. This document is re-checked against the raw-HTML passthrough
design committed in `9b93251` (spec 0002 §2/§6/§7, ADR 0004, CONTEXT.md
decision 10). The §1, §2, §4, and §5 findings are resolved by the committed
formatter changes; §3 is a regression guard.

## 1. Ragged tables must linearize

Spec 0002 §4.2:

> | table | header row bold; cells joined by ` · `; rows joined by `<br>`; no alignment |

and the table-linearization rules in §4.2.

Finding (resolved): the mistune `table` plugin rejects a body row whose cell
count differs from the header (`_process_row` returns no row, the fallback
turns the whole source into literal pipe text), so ragged tables are never
linearized. The Plugin now registers its own `trueconf_table` block rule (spec
0002 §6) that pads fewer cells to empty and keeps extra cells.

Requirement: a table with ragged body rows MUST still be recognized and
linearized. In the compact-row form a body row with fewer cells than the
header renders its missing trailing cells as empty, and a row with more cells
renders every cell. In the labeled-block form (spec 0002 §4.2) missing
trailing cells render as empty values and extra cells are appended as one
` | `-joined line. Content is never dropped in either form. No raw `|` pipe
text reaches the wire. This is satisfied by the `trueconf_table` block rule;
the Plugin MUST NOT patch or fork mistune.

## 2. Malformed link URLs must degrade

Spec 0002 §7:

> Relative URLs, protocol-relative URLs, malformed URLs, and every other
> scheme degrade to the readable `label (url)` form

Finding (resolved): the link allowlist is evaluated against the URL after
mistune percent-encodes it, so a malformed source URL that is valid after
encoding passes the allowlist and the `urlsplit` `ValueError` degradation path
is dead for that class. `_is_allowed_url` now also validates the `unquote`d
form, so `[x](http://[)` degrades to `x (http://[)` instead of emitting
`<a href="http://%5B">`.

Requirement: the link allowlist MUST be evaluated against the URL exactly as
written in the Markdown source, before percent-encoding. A source URL that
does not parse as an allowed absolute URI (for example an unclosed IPv6
bracket) degrades to `label (url)`, with both parts escaped as text. This is
satisfied by validating the unquoted URL; a URL that is valid in both its
written and its encoded form, such as `https://example.com/a%20b`, is
unchanged.

## 3. Raw-HTML blocks use `<br>` for text line breaks

Spec 0002 §1 and §4.2, as amended by `9b93251`:

> `<br>` is the line-break form
> raw HTML | passed through for server sanitization; text between tags is
> escaped

Finding (resolved): pre-passthrough, a multi-line raw-HTML block was escaped
as a single block whose internal line endings survived as literal `\n`.
Verified against `9b93251`: `<div>\nhi\n</div>` renders as
`<div><br>hi<br></div>`, tags pass through and the escaped text between them
uses `<br>` for line endings.

Requirement: within a raw-HTML block, the text between passed-through tags is
escaped and its line endings MUST become `<br>`, exactly like every other
block's line endings. This is satisfied by `9b93251`; it is recorded here as a
regression guard.

## 4. Literal HTML in backticks must not be command-split

Spec 0002 §7, as amended by `9b93251`:

> The agent escapes literal HTML in backticks or fenced blocks

Finding (resolved): fenced blocks escape literal HTML correctly, but a backtick
span was command-split before escaping, and every closing tag's slash was read
as a command token (`</b>` matched the command rule as `/b`). The command rule
now excludes a slash directly after `<` (spec 0002 §5), so `` `<b>bold</b>` ``
renders as escaped literal text.

Requirement: literal HTML inside a backtick span MUST render as escaped literal
text, consistent with the fenced-block promise. The command rule MUST NOT
treat a slash that is part of an HTML closing tag as a command token within a
backtick span; the intended behavior for `` `/start` `` → `/start` is
unchanged.

## 5. Rendering must be deterministic across calls

Spec 0002 §2 requires deterministic translation; CONTEXT.md decision 10 keeps
the formatter dependency-isolated.

Finding (resolved): `TrueConfHTMLRenderer._escape_anchor_close` was mutable
instance state on a module-global renderer. An unsafe raw `<a>` without a
matching `</a>` in one rendered message left the flag set; the next render call
then escaped an unrelated `</a>`. The flag is now reset at the per-message
render entry (spec 0002 §6).

Requirement: rendering MUST be deterministic per call and free of
cross-call instance state. The unsafe-anchor degradation MUST be decided from
the token stream of a single render, not from a flag mutated across calls.

## 6. Ratified behavior — image degradation

Finding (scope creep in the review): `![alt](url)` renders as `alt (url)` in
the implementation, although spec 0002's dialect tables define no image
mapping.

Decision: the behavior is ratified into the dialect. TrueConf messages do not
render text-inline images, and `alt (url)` is content-preserving,
deterministic, and consistent with the goal that unsupported constructs
degrade to readable text without dropping data. The §4.1 dialect table gains:

| Source | Rendered |
|---|---|
| `![alt](url)` | `alt (url)`, both parts escaped as text |

Image URLs are always shown as text and never emitted as an `href`; the link
allowlist does not apply to them.

## 7. Out of scope

- The `[tool.uv.sources]` local wheel pin in `pyproject.toml` (commit
  `ec4b559`). It is a local dev-tooling pin, not formatter behavior; whether it
  may remain in the standalone repository is decided separately.
- The chunking follow-ups of spec 0002 §9 remain out of scope.

## 8. Testing

The hermetic `tests/test_formatter.py` covers every requirement above and
passes together with the full Plugin suite, Ruff, and type checks:

- ragged tables: in the compact-row form a body row with fewer cells (trailing
  cells render empty) and a body row with more cells (no cell dropped); in the
  labeled-block form missing trailing cells render as empty values and extra
  cells are appended as one ` | `-joined line; assert no raw `|` appears in
  the output;
- malformed source URLs: `[x](http://[)` degrades to `x (http://[)` while every
  existing §7 allowlist case still renders as before;
- raw HTML line breaks: single-line, multi-line, and CRLF raw-HTML blocks
  render passed-through tags with `<br>` between escaped text and no literal
  `\n` (regression guard for `9b93251`);
- literal HTML in backticks: `` `<b>bold</b>` `` and `` `<a href="x">y</a>` ``
  render as escaped literal text with no bare command token, while `` `/start` ``
  stays `/start`;
- determinism: rendering an unsafe raw `<a>` without its closing tag, then
  rendering a message containing a stray `</a>`, must not escape the stray
  close tag;
- image: `![alt](https://example.com/i.png)` renders as
  `alt (https://example.com/i.png)`.

## 9. Acceptance criteria

- Every requirement above has a passing hermetic test; the full formatter and
  Plugin suites, Ruff, and type checks stay green.
- No mistune patch or fork; changes stay inside `formatter.py` and its tests.
- The exact §4.1, §4.2, and §5 examples of spec 0002 that already render
  correctly, and the raw-HTML passthrough examples added in `9b93251`, are
  unchanged.

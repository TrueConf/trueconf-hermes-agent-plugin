# AGENTS.md

Notes for AI agents working in this repository. Read this before changing
`formatter.py` or the message-delivery paths in `adapter.py`.

## Accepted trade-offs (do not "fix" without asking)

### Formatter degrades unsafe raw `<a>` tags to escaped text

Server behavior (verified against a live TrueConf Server, October 2026):

- unsupported tags are stripped entirely (`<img src=...>` disappears);
- unknown attributes on supported tags are stripped, the tags survive
  (`<b onclick=...>bold</b>` → `<b>bold</b>`, same for `<a onclick=...>`);
- an unsupported href scheme is stripped by the server, leaving an empty
  `href=""`, and the empty href then fails the whole send with error
  `399 wrong payload`.

Therefore `TrueConfHTMLRenderer._passthrough_html_tag` (`formatter.py`)
checks `<a href>` against the scheme allowlist
(`http`/`https`/`mailto`/`trueconf`) *before* sending: an unsafe or missing
href degrades the tag to escaped text (together with its matching `</a>`) so
the message still delivers instead of failing with 399. `</a>` alone passes
through unchanged.

Everything else is forwarded verbatim — the server strips unknown attributes
and unsupported tags itself. Do not add a broader attribute allowlist (the
server already strips attributes); do not remove the href check (the server
empties unsupported hrefs and then rejects the empty href with 399, which
would fail the whole send).

Relevant decision context: `docs/specs/0002-trueconf-markdown-formatter.md`,
`docs/specs/0003-trueconf-markdown-formatter-corrections.md`.

### Oversized-edit shrink leaves stale continuation chunks

When a send exceeds the 4096-character limit it is split into chained
continuation messages (`_deliver_chunk_chain`, `adapter.py`); Hermes later
edits only the most recent message id (`continuation_message_ids` contract).
If a subsequent edit is short, earlier chunks keep their old text on screen.

This mirrors the reference Hermes Telegram adapter
(`plugins/platforms/telegram/adapter.py`, `_edit_overflow_split`), which has
the same behavior. The TrueConf SDK has no message-delete API, so tails
cannot be removed. Do not treat the mixed old/new output after a shrinking
edit as a bug report-worthy defect; it is an accepted Hermes streaming
contract trade-off.

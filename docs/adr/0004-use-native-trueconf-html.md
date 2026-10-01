---
status: accepted
---

# Use native TrueConf HTML for agent output

TrueConf HTML is the Plugin's output format because it maps directly to the
server's native formatting contract. The agent writes the TrueConf Markdown
dialect, and the Plugin deterministically translates it to the
server-supported HTML subset with the mistune parser before sending with
`parse_mode=html`. Line breaks are emitted as `<br>`. Raw HTML tags the agent
writes are passed through and sanitized by the server, which strips every tag
outside its supported subset; the agent escapes literal HTML with backticks or
fenced blocks, so model-written formatting still reaches the server as
formatting, never as a mix of escaped and live markup.

This decision preserves the original wire-format choice of this ADR and
supersedes its "no Markdown-to-HTML renderer" consequence: the Plugin now owns
a deterministic Markdown-to-HTML translator, because agents do not reliably
write a hand-constrained HTML subset, while they do write Markdown. ADR 0002's
vertical-slice strategy remains in force. The concrete dialect and mapping are
specified in `docs/specs/0002-trueconf-markdown-formatter.md`.

## Consequences

- The advertised HTML subset is `<b>`, `<i>`, `<s>`, `<u>`, `<a href>`, and
  `<br>`. Blockquotes have no server tag: `<quote>` renders an empty block
  unless the message is an actual reply carrying `replyMessageId`, so `> …`
  degrades to italic text wrapped in guillemets.
- The Plugin owns and tests a mistune-based Markdown-to-HTML translator; model
  output is parsed and re-rendered, never passed through as HTML.
- Unsupported Markdown (code, monospace, headings, spoilers) degrades to
  plain text or the bold-italic form; tables are linearized without dropping
  content; command tokens are kept bare.
- Only the explicitly supported `http`, `https`, `mailto`, and `trueconf` URI
  schemes are emitted as links; other link targets degrade to escaped readable
  text.
- The input dialect is unambiguous and documented in `platform_hint`.
- Mistune is a declared Plugin runtime dependency covered by the same passive
  availability check and opt-in lazy dependency pipeline as the TrueConf SDK.
- Markdown and literal-text parse modes remain unsupported; the wire format is
  always native HTML.

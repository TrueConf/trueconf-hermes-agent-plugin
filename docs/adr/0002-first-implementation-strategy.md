---
status: accepted
---

# Build version 0.1.0 as verified vertical slices

The first release will include text messaging, core TrueConf chat types,
media, home-channel delivery, and detached cron delivery, but it will be built
as a sequence of independently verified vertical slices. A text-only live
round trip is the first functional checkpoint; richer behavior is added only
after lifecycle and SDK contracts are proven. This limits the influence of the
historical AI-generated adapters and exposes SDK deficiencies before the
plugin accumulates workarounds.

Hermes owns authorization policy, session behavior, message orchestration, and
long-message routing. The TrueConf SDK owns protocol behavior, authentication,
transport, file operations, and low-level reconnection. The plugin translates
between those contracts and may invoke Hermes' authorization seam early to
avoid downloading content from rejected senders, but it does not define a
second access policy.

The default output mode is the SDK's native Markdown mode. Plain text is
supported, while HTML requires a separately reviewed and tested renderer; the
historical regular-expression Markdown-to-HTML conversion is rejected. The
initial implementation passively reported a missing `python-trueconf-bot`
dependency. This part of the decision is superseded: the platform now uses
Hermes' public `ensure_deps_fn` hook and lazy dependency pipeline during adapter
creation, while its diagnostic probe remains passive. Behavioral settings use
`config.yaml`; historical
environment settings for parse mode and SSL are not part of the primary
interface.

## Consequences

- Media and cron work cannot hide lifecycle or basic message-mapping failures.
- Missing public SDK behavior blocks the corresponding plugin milestone until
  it is implemented and tested in the SDK repository.
- Automatic installation is isolated behind Hermes' public lazy-dependency
  policy and can be disabled centrally by the operator.
- Compatibility aliases for old environment variables require a deliberate
  migration decision rather than becoming accidental permanent API.

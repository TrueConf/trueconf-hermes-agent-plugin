# TrueConf Plugin for Hermes Agent

This repository contains the standalone TrueConf messaging-platform plugin for
Hermes Agent. It is the durable source of truth for the project's language,
boundaries, and accepted decisions.

## Language

**Plugin**:
The standalone extension that connects Hermes Agent to TrueConf exclusively
through Hermes' public plugin API.
_Avoid_: built-in integration, core patch, adapter installer

**Hermes Core**:
The upstream Hermes Agent runtime consumed by the Plugin but never modified by
this repository.
_Avoid_: our Hermes fork, patched core

**TrueConf SDK**:
The unmodified `python-trueconf-bot` package providing the TrueConf protocol,
transport, and typed API used by the Plugin.
_Avoid_: patched SDK, vendored SDK, `lib_patches`

**Platform Adapter**:
The Plugin component that translates between the public Hermes platform
contract and the public TrueConf SDK contract.
_Avoid_: TrueConf client, SDK wrapper

**Output Formatter**:
The Plugin component that deterministically translates the agent's TrueConf
Markdown dialect into the server-supported TrueConf HTML subset using the
mistune parser.
_Avoid_: passthrough formatter, HTML sanitizer, agent-written HTML

**Reference Implementation**:
Historical code used to discover requirements and test scenarios; it is not a
source to copy from without explicit review and approval.
_Avoid_: baseline implementation, canonical adapter

**Standalone Repository**:
This repository root, which is the distributable source of the Plugin and does
not include local reference checkouts.
_Avoid_: Hermes fork, bundled plugin directory

**Native Media Message**:
A TrueConf message rendered with a media-specific client experience supplied
by a dedicated server API capability.
_Avoid_: uploaded file, generic attachment

**File Attachment**:
A file transferred through TrueConf's generic file API, including video or
audio content when no native send capability exists.
_Avoid_: native video message, native voice message

**Advertised Attachment Size**:
The authoritative file size reported by TrueConf Server metadata before an
attachment download begins.
_Avoid_: untrusted client size, observed stream size

## Context boundaries

- Hermes Core owns agent sessions, authorization orchestration, plugin
  discovery, cron routing, message delivery orchestration, and the public
  platform-plugin contract.
- The TrueConf SDK owns authentication, protocol details, transport, TrueConf
  data models, and low-level TrueConf operations.
- The Plugin owns translation and lifecycle coordination at the boundary
  between Hermes Core and the TrueConf SDK.
- Reference implementations provide evidence about desired behavior only.

## Accepted decisions

1. This directory is the root of the standalone Plugin repository.
2. The Plugin targets the latest agreed Hermes baseline, currently v0.21.0,
   and uses only its public plugin API.
3. The Plugin never changes Hermes Core and uses no core monkeypatches.
4. The Plugin uses the clean TrueConf SDK and never vendors or loads patched
   copies from `lib_patches/`.
5. Missing TrueConf behavior is implemented and tested in
   `python-trueconf-bot`, not worked around with private patches in the Plugin.
6. Historical adapters are reviewed as requirements; code is transferred only
   after explicit architectural review and user approval.
7. Version 1.0.0 is being prepared for publication at
   https://github.com/TrueConf/trueconf-hermes-agent-plugin. PyPI packaging and
   a possible upstream Hermes PR remain deferred.
8. Local Hermes forks, old adapters, handoffs, and copied documentation are
   reference material and must not be committed to this repository.
9. Version 1.0.0 includes media and cron delivery. The original 0.1.0
   implementation was built through a separately verified text-only vertical
   slice; its plans and verification reports remain historical records.
 10. Native TrueConf HTML is the only wire format. The agent writes the
     TrueConf Markdown dialect, and the Plugin deterministically translates it
     to the server-supported HTML subset with the mistune parser (the Output
     Formatter). The advertised subset is `<b>`, `<i>`, `<s>`, `<u>`,
     `<a href>`, and `<br>`; blockquotes have no server tag — `<quote>`
     renders an empty block unless the message is an actual reply carrying
     `replyMessageId`, so `> …` degrades to italic wrapped in guillemets.
     Unsupported Markdown
     degrades without dropping content, tables are linearized as compact rows
     or, for wide, long, or many-column tables, as labeled blocks, and command
     tokens stay bare.
     Raw HTML tags the agent writes pass through for the server to sanitize
     against its subset; literal HTML is written in backticks or fenced blocks.
     Markdown and literal-text parse modes remain rejected; the wire format is
     always HTML.
11. When an enabled TrueConf adapter is created during gateway startup, the
    Plugin lazily installs its supported SDK through Hermes' public
    `ensure_deps_fn` and `tools.lazy_deps.install_specs()` contracts. The
    passive dependency probe and plugin diagnostics never install packages.
12. Behavioral configuration belongs in the canonical
    `platforms.trueconf` block in `config.yaml`. The top-level `trueconf` and
    `gateway.platforms.trueconf` forms are not supported. Historical environment
    variables for parse mode and SSL verification are migration-only aliases,
    if migration support is deliberately added later. The TrueConf Server
    address is a connection setting, not a behavioral one: it is a prompted
    `requires_env` value, following the bundled self-hosted platform pattern
    (`MATTERMOST_URL`, `MATRIX_HOMESERVER`, `IRC_SERVER`). YAML `server`
    remains an advanced alternative and is overridden by the environment value.
    `TRUECONF_VERIFY_SSL` is the narrow installation-time bridge for the TLS
    choice prompted beside those connection values; YAML remains the canonical
    interface for later behavioral configuration.
13. The supported TrueConf SDK range is `>=1.5.0,<2`. Release-facing Plugin
    diagnostics and installation instructions use this version contract
    rather than a source revision or an unbounded compatibility promise.
14. The public SDK `Message.timestamp` preserves the TrueConf protocol's Unix
    timestamp in milliseconds. The Plugin converts it to a timezone-aware UTC
    `datetime` when building the Hermes event.
15. Hermes' public `SessionSource.chat_type` vocabulary represents a direct
    TrueConf P2P chat as `dm`; group and channel values remain `group` and
    `channel`.
16. Hermes' inbound-media limit value `0` means unlimited, while the TrueConf
    SDK requires a positive in-memory download limit. The Plugin therefore
    keeps `0` for Hermes preflight and configures the SDK with `sys.maxsize`,
    preserving the same practical unlimited behavior without bypassing either
    public API.
17. Native outbound image presentation requires `Bot.send_photo()` with a
    separate public `InputFile.clone()` as preview. A live beta-server run
    proved that `preview=None` renders as a generic file even though the SDK
    accepts it.
18. Outbound edits target the exact TrueConf message ID supplied through
    Hermes' public adapter contract. The Plugin renders each content value to
    native HTML with the Output Formatter (the same one sends and captions
    use, including `<br>` line breaks) without resolving a chat, accumulating,
    or interpreting `finalize`. When one SDK call would exceed the 4096
    visible-character limit, the Plugin splits the rendered HTML with the
    SDK's `safe_split_text` and delivers the overflow as chained continuation
    messages, reporting every id through Hermes' `SendResult` /
    `continuation_message_ids` contract so subsequent edits re-target the last
    delivered continuation. Sends do the same split-and-chain delivery, with
    the last visible message id returned as `message_id`.
19. Standalone delivery validates the complete local configuration before it
    constructs an adapter. Invalid cron configuration returns a deterministic,
    non-retryable error result and never exposes constructor exceptions.
20. The Plugin registers a public interactive `setup_fn` that stores the
    required connection values, authorization choice, and optional cron home
    chat in the active Hermes profile. Password entry is masked.
21. Group and channel configuration separates four concerns: chat scope
    (`allowed_chats`), whole-chat trust (`group_allowed_chats`), individual
    sender access (`group_allow_from`), and message triggering
    (`require_mention`, `free_response_chats`, and observation). Triggering and
    observation never grant access or widen chat scope.
22. Observed group context is a stateless, trigger-anchored view of recent
    TrueConf history since the previous authorized mention. It is reference
    context only, never a request or a separately persisted Plugin transcript.
23. Plugin installation prompts for `TRUECONF_VERIFY_SSL` alongside the server
    and credentials so the first gateway start has an explicit TLS policy.
    Accepted values are `true`, `false`, or a readable CA bundle path; `true`
    remains the recommended production choice.
24. TrueConf's client highlights command tokens made only of letters, digits,
    and underscores. The Output Formatter therefore renders hyphenated command
    tokens with hyphens replaced by underscores (`/claude-code` displays as
    `/claude_code`) so they stay highlightable; the substitution is
    display-only and confined to matched tokens, and Hermes Core resolves
    inbound commands with `-` and `_` interchangeably, so the underscored form
    still reaches the same skill. The `@handle` suffix is preserved even
    though TrueConf does not highlight suffixed commands — it may be needed
    for multi-bot disambiguation.

## Delivery state

- Stage 1 observable behavior is approved.
- Stage 2 SDK audit and blocker remediation are complete.
- Stage 3 Plugin boundary is complete.
- Stage 4 is complete. Its hermetic unit/contract suite passes, and the opt-in
  live connect → receive → send → disconnect smoke test passed on 2026-09-06
  against both approved test servers. Their self-signed TLS certificates were
  handled through an explicit test-only `verify_ssl: false` choice.
- Stage 5 is complete. P2P, group, and channel text map through the public
  Hermes adapter contract; native SDK mentions gate group/channel traffic by
  default; exact TrueConf chat IDs stay case-preserving and are passed directly
  to the SDK without a Plugin-owned user-resolution layer. Incoming chat ID,
  title, and type come from the SDK message's public `Chat` entity. The hermetic
  Plugin suite covers reply identity in both directions, SDK self-message
  suppression, and ignored Favorites/unknown chat types.
- Stage 6 is complete: documents,
  images, video, native voice, and generic audio files map through public
  Hermes and TrueConf APIs; authorization and advertised-size validation run
  before download; outbound video/voice use generic file delivery; caller
  files remain caller-owned on success, failure, and cancellation. The
  hermetic suite passes 44 tests. Authorized live runs on 2026-09-07 against
  beta server `10.140.1.255` passed document, native image, video, and native
  voice receive/send round-trips; video and voice returned through the
  intentionally generic file API. The first image run exposed the missing
  preview and the repeated run confirmed native photo presentation after the
  public `InputFile.clone()` fix.
- The Stage 6 live run exposed a clean-SDK logging defect: file download URLs
  containing bearer-like query tokens were emitted at INFO level. The fix is
  present in the 1.5.0 release candidate; the Plugin does not intercept or
  monkeypatch SDK logging.
- Stage 7 is complete. Hermes `send` results provide the exact message ID used
  by `edit_message`; native HTML is forwarded with SDK-provided line breaks.
  Edit failures
  use the existing typed send-result categories, cancellation propagates, and
  oversized content was rejected through a single SDK call with Hermes as the
  sole chunking owner (superseded by Plugin-side chunking, see decision 18).
  The hermetic Plugin suite passes 48 tests.
- Stage 8 is complete. Public Hermes registration exposes environment/YAML
  configuration bridges, `TRUECONF_HOME_CHANNEL` cron discovery, and an
  out-of-process standalone sender. Each detached delivery creates one
  ephemeral adapter, waits for the SDK authorization event, sends text before
  ordered media, reports partial-delivery message IDs on a later failure, and
  disconnects on success, failure, or cancellation without leaving
  `trueconf-*` tasks running. The standalone hook accepts Hermes' standard
  thread and media arguments; TrueConf has no thread/topic target, so the
  thread value has no wire effect. Hermes v0.21.0 routes text-plus-media to
  third-party standalone hooks but rejects media-only input before invoking
  them; the hook supports media-only calls directly and the Plugin adds no
  core workaround. The hermetic Plugin suite passes 58 tests.
- Standalone cron now performs the same deterministic configuration preflight
  before adapter construction, including in a detached cron process.
- Stage 9 is complete. `README.md` now takes a new operator through explicit
  Plugin and clean-SDK installation, masked credential setup, YAML behavior,
  authorization, exact home chat IDs, verification, migration without patched
  files, troubleshooting, logs, and removal. The installer manifest now uses
  Hermes' public `secret` marker for masked password entry, and the Stage 9
  documentation contract is covered by the hermetic suite. The Plugin suite
  passes 60 tests and Hermes Plugin Doctor passes without warnings.
- The registered interactive setup flow now configures TrueConf directly from
  `hermes setup` and `hermes gateway setup` instead of using the generic
  environment-variable fallback.
- Stage 10 release verification is complete. An isolated Hermes v0.21.0
  plus the clean SDK 1.5.0 release candidate passes all 64 Plugin tests and the
  complete SDK suite, Plugin Doctor, Ruff, production type checking, and real
  missing/outdated/supported-SDK availability probes. The SDK URL-log
  redaction fix and its SDK regression tests are present. The user accepted the
  recorded 2026-09-06/07 live text and media results as the release-gate
  evidence; repeating them against the published SDK 1.5.0 remains an operator
  release check rather than a blocker. Detailed evidence is recorded in
  `docs/release/0001-stage-10-verification.md`.
- Trigger-anchored observed context is implemented through the public TrueConf
  history API and Hermes `channel_context` field. It is disabled by default and
  covered by hermetic configuration, filtering, ordering, boundary, and
  failure tests.
- The Output Formatter is specified in
  `docs/specs/0002-trueconf-markdown-formatter.md` and amends ADR 0004: the
  agent writes the TrueConf Markdown dialect and the Plugin translates it to
  native HTML with mistune. Outbound chunking is implemented in the Plugin:
  `send` and `edit_message` split rendered HTML with the SDK's `safe_split_text`
  (which tracks `<br>` as an atomic zero-width break point) so every SDK call
  stays within the 4096 visible-character limit, chain the overflow through
  `reply_message_id`, and report every delivered id through Hermes'
  `SendResult.continuation_message_ids` so later edits re-target the last
  continuation. If a tail chunk fails after the head is delivered, the Plugin
  returns partial success with the delivered ids (a hard failure would make
  Hermes re-send and duplicate the delivered head) and surfaces the gap through
  `SendResult.error`; repeated edits re-target the latest continuation and do
  not remove earlier chunks, so a shrinking edit leaves the earlier chunk text
  visible (Telegram-like continuation artifact). Long slash-command output
  (e.g. `/help`) and agent answers now arrive as multiple messages instead of
  failing with `TextMessageTooLongError` and falling back to Hermes' truncated
  3500-char plain-text path. Button prompts are never split. Media captions
  remain out of scope.

The rationale and consequences are recorded in:

- [ADR 0001](docs/adr/0001-standalone-plugin-boundaries.md)
- [ADR 0002](docs/adr/0002-first-implementation-strategy.md)
- [ADR 0003](docs/adr/0003-trueconf-media-contract.md)
- [ADR 0004](docs/adr/0004-use-native-trueconf-html.md)
- [ADR 0005](docs/adr/0005-address-deliveries-by-chat-id.md)
- [ADR 0006](docs/adr/0006-group-chat-configuration-model.md)
- [ADR 0007](docs/adr/0007-recover-observed-context-from-trueconf-history.md)

The executable implementation sequence and acceptance gates are recorded in
[the version 0.1.0 implementation plan](docs/plans/0001-first-implementation.md).

The Output Formatter dialect and mapping are specified in
[docs/specs/0002-trueconf-markdown-formatter.md](docs/specs/0002-trueconf-markdown-formatter.md).

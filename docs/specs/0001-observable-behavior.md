# TrueConf Platform Plugin 0.1.0 — Observable Behavior Specification

Status: **approved; Stage 1 complete**

Drafted: 2026-09-04

Approved: 2026-09-05

Output-format contract amended: 2026-09-07 by
[ADR 0004](../adr/0004-use-native-trueconf-html.md).

This document defines the approved behavior to be implemented. Approval closes
the Stage 1 gate and authorizes the Stage 2 SDK audit; it does not authorize
Plugin implementation before the later stage gates.

## 1. Baseline and sources

The contract is based on:

- Hermes Agent v0.21.0 at commit `29112bef`;
- the public platform-plugin guide, `PluginContext.register_platform()`,
  `PlatformEntry`, and `BasePlatformAdapter` supplied by that release;
- the clean `python-trueconf-bot` checkout at commit `afb4bde4` on its local
  `v1.5.0` branch (SDK fixes through 2026-09-05);
- historical TrueConf adapters only as evidence of scenarios and failure
  modes.

The detailed evidence ledger is
[`docs/research/0001-stage-1-public-contracts-and-history.md`](../research/0001-stage-1-public-contracts-and-history.md).

Historical implementation details are not requirements. No historical code
has been approved for transfer.

Normative words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** describe the
proposed 0.1.0 contract.

## 2. Scope and success criteria

Version 0.1.0 connects one Hermes platform instance to one TrueConf bot
identity and provides:

- inbound and outbound text in P2P, group, and channel chats;
- replies and outbound edits;
- inbound documents, images, videos, and native voice messages;
- outbound documents and images, with video/audio preserved as generic file
  attachments because TrueConf has no native video/voice send API;
- explicit user and chat delivery targets;
- Hermes-owned access control and mention-gated group/channel behavior;
- live gateway delivery, home-channel routing, and detached cron delivery;
- deterministic startup, health reporting, reconnect handoff, and shutdown;
- structured, non-secret diagnostics.

The release is successful only when these behaviors work against unmodified
Hermes v0.21.0 and TrueConf SDK `>=1.5.0,<2`.

## 3. Architecture and ownership

The design has three modules separated by two translation seams:

```text
Hermes Core                  Plugin                         TrueConf SDK
------------                 ------                         ------------
sessions                     platform registration          authentication
authorization policy   <-->  event/result translation  <--> protocol models
message orchestration        lifecycle coordination         WebSocket/HTTP
chunking and send retry      target resolution              low-level reconnect
cron invocation              configuration validation       TrueConf operations
media cache                  SDK error classification       file transfer
```

### 3.1 Hermes Core owns

- plugin discovery and the public platform registration contract;
- agent sessions, command handling, pairing, and the final authorization
  decision;
- allowlists, allow-all behavior, DM/group policy, and their configuration;
- response chunking and retry orchestration after a retryable `SendResult`;
- invocation of live sends, detached sends, and cron delivery;
- the durable inbound media cache and its age-based cleanup;
- gateway-level adapter replacement after an adapter has terminated.

The Plugin MUST NOT reproduce these policies or modify Hermes Core.

### 3.2 The Plugin owns

- registration of the dynamic `trueconf` platform through
  `PluginContext.register_platform()`;
- validation and normalization of TrueConf-specific configuration;
- translation between public SDK models and public Hermes events/results;
- deterministic coordination of SDK startup, readiness, task termination,
  and shutdown;
- classification of typed SDK failures into Hermes failure categories;
- exact `chat_id` target parsing and delivery;
- mention gating using an SDK-native mention signal;
- the pre-download authorization safety check, without becoming the source of
  authorization policy;
- cleanup of tasks and temporary resources created by the Plugin.

The Plugin is an adapter, not a second transport client, policy engine, or
message orchestrator.

### 3.3 The TrueConf SDK owns

- username/password authentication and token acquisition;
- TLS and protocol handling;
- WebSocket connection maintenance and low-level reconnect attempts while its
  run loop remains alive;
- parsing TrueConf updates into typed public models;
- all TrueConf API requests, including chat lookup, P2P creation, send, edit,
  upload, and download;
- TrueConf file constraints and server error models;
- self-message suppression through its supported public behavior.

Missing protocol behavior MUST be added to the SDK itself. The Plugin MUST
NOT inspect SDK-private state, patch SDK classes, or vendor replacement files.

### 3.4 Permitted Hermes seam

The Plugin may use only documented plugin registration fields and the
documented adapter surface. Hermes' platform guide explicitly demonstrates
the lifecycle methods `_mark_connected()` and `_mark_disconnected()` despite
their leading underscores; for this project they are treated only as the
documented adapter extension seam, not as permission to use other underscored
Hermes symbols.

The public `set_authorization_check(callback)` hook may be retained by the
adapter for the media preflight described in section 9. The Plugin MUST NOT
call the private `_is_sender_authorized()` helper.

## 4. User scenarios

### S1 — Start a configured gateway

Given valid configuration and reachable TrueConf credentials, the adapter
authenticates, receives an explicit authorized health signal, reports itself
connected, and begins accepting updates. There is no fixed readiness sleep.

### S2 — Configuration or authentication failure

An invalid local configuration fails before network activity with a field-
specific diagnostic. Invalid credentials or a certificate-verification
failure leave no background task or open connection and are reported as
non-retryable until configuration changes.

### S3 — Receive a direct text message

The Plugin preserves the TrueConf sender, chat, message, text, timestamp, and
reply identity and submits one Hermes `MessageEvent`. Hermes decides whether
the sender is authorized and whether pairing or an unauthorized notice is
appropriate.

TrueConf protocol timestamps have millisecond precision. The public SDK
preserves that integer value; the Plugin converts milliseconds to a
timezone-aware UTC `datetime` for the Hermes event.

### S4 — Receive text in a group or channel

When `require_mention` is true, only a message carrying the SDK's native
positive mention signal is submitted. When it is false, every otherwise
supported message is submitted. P2P messages never require a mention.

### S4.1 — Recover unmentioned group context

When observation is enabled for an explicitly allowed group or channel, an
authorized message that mentions the agent triggers one history request
anchored at that message. The Plugin provides eligible unmentioned text since
the previous authorized mention through `MessageEvent.channel_context`; those
background messages do not independently invoke the agent. History failure
does not block the triggering message.

### S5 — Send and reply with text

Hermes supplies a real chat target and optional reply message ID. The Plugin
renders the content to native HTML and sends one SDK request per chunk at the
4096 visible-character limit, chaining overflow through reply message IDs. It
returns the last delivered TrueConf message ID as `message_id` and the earlier
delivered ids as `continuation_message_ids`. It never truncates content.

### S6 — Address a TrueConf chat

`trueconf:<chat-id>` sends directly to that exact P2P, group, or channel chat.
The Plugin does not classify the identifier, resolve users, create P2P chats,
or maintain a target cache.

### S7 — Validate an external destination

The public target parser accepts one non-empty, case-preserving `chat_id` with
no surrounding whitespace. TrueConf Server remains authoritative for whether
that chat exists and whether the bot may send to it.

### S8 — Receive media safely

The Plugin resolves trusted chat metadata needed for policy, evaluates the
Hermes-provided authorization callback, and downloads attachment bytes only
when that callback returns the literal value `True`. Unauthorized media is
not downloaded. Hermes still receives enough message identity to apply its
own rejection/pairing behavior.

### S9 — Send media

Hermes passes a local file path and optional caption. The Plugin uses the
photo-specific SDK operation for images and the generic file operation for
documents, video, and audio/voice. It preserves filename and MIME type where
the SDK supports them and does not delete the caller's file. Version 0.1.0 does
not promise a native outbound video player or voice-message bubble.

### S10 — Edit a sent message

Hermes supplies the TrueConf message ID returned by a previous send. The
Plugin edits that exact message and returns success or a structured failure.
It does not infer an edit target from conversation state, message text, or
emoji. The Hermes `chat_id` remains interface context but is not used to
resolve an edit target because the public TrueConf operation addresses a
message directly. Hermes' `finalize` hint has no TrueConf wire meaning and
does not alter content or request count.

### S11 — Deliver a cron result

Hermes resolves a configured home `chat_id` and invokes the standalone
sender. The Plugin opens an ephemeral SDK session, waits for explicit
authorization, sends, and shuts the session down on success, failure, or
cancellation. It does not depend on a live gateway adapter.

The sender accepts Hermes' standard `thread_id`, `media_files`, and
`force_document` arguments. TrueConf has no thread/topic delivery primitive,
so `thread_id` is accepted but has no wire effect. Non-empty text is sent
first, followed by media in caller order. Images use native photo delivery;
video, voice/audio, and other documents use the generic file API described in
the media contract. `force_document` routes every media item through that
generic file API.

A successful call returns the last delivered TrueConf message ID. If a later
item fails after earlier items were accepted by the server, the structured
failure also reports the last delivered ID and the ordered list of all IDs
already delivered. The Plugin does not attempt rollback or replay.

Hermes v0.21.0 invokes third-party standalone senders for text and text-plus-
media delivery, but its public send-message parser rejects a media-only
request before a third-party hook is called. The TrueConf hook itself supports
media-only delivery; the Plugin does not work around the host limitation with
a core patch or private entry point.

### S12 — Recover from transport loss

While the SDK run loop is alive, the SDK owns low-level reconnect. If that
loop exits unexpectedly, the Plugin reports one typed fatal condition and
releases its resources; Hermes then owns any adapter-level retry/replacement.
There is never a second Plugin reconnect loop.

### S13 — Stop or restart the gateway

Shutdown stops accepting new updates, asks the SDK to shut down, awaits all
Plugin-owned tasks, releases its scoped credential lock, and is idempotent.
No task or Plugin-owned temporary file remains.

## 5. Supported events and operations

### 5.1 Inbound

| TrueConf input | Hermes representation | 0.1.0 behavior |
| --- | --- | --- |
| Text | `MessageType.TEXT` | Supported; text preserved as exposed by the SDK |
| Image attachment | `MessageType.PHOTO` plus cached path/MIME | Required |
| Document attachment | `MessageType.DOCUMENT` plus cached path/MIME | Required |
| Video attachment | `MessageType.VIDEO` plus cached path/MIME | Required |
| Native voice message | `MessageType.VOICE` plus cached path/MIME | Required after SDK gap is fixed |
| Forwarded text | `MessageType.TEXT` | Supported with an explicit `[Forwarded message]` marker |
| Location | `MessageType.LOCATION` | Supported with coordinates, optional venue title, and a map URL |
| Reply | `reply_to_message_id`, embedded reply text, author ID, and own-message flag | Required; embedded reply must belong to the current chat |
| Generic audio file | `MessageType.DOCUMENT` | Preserved as a document; native audio UX is not promised |
| Sticker | none | Excluded unless separately approved after the SDK audit |
| Survey, service/system update | none | Ignored with debug-level reason |
| Inbound edit/delete/reaction | none | Excluded from 0.1.0 |
| Favorites or unknown chat type | none | Ignored; never guessed to be a P2P chat |

An attachment event has one cached path in `media_urls`, the corresponding
MIME type in `media_types`, and a short non-secret description in `text` when
the SDK provides no caption. Inbound attachment-caption preservation is not
promised until the SDK exposes and tests such a field. Malformed metadata or a
failed download produces an operational diagnostic and no fabricated local
path.

### 5.2 Outbound

| Hermes operation | TrueConf result | 0.1.0 behavior |
| --- | --- | --- |
| `send` | native text message ID | Required |
| `send(..., reply_to=...)` | native reply | Required |
| `edit_message` | edited message ID | Required |
| `send_document` | native file message ID | Required |
| `send_image_file` | native photo/file message ID | Required |
| `send_sticker` | native WebP sticker message ID | Uses native `choosing_sticker` activity while sending |
| `send_video` | generic file attachment carrying the video MIME type | Required; native video send is unavailable in TrueConf |
| `send_voice` | generic file attachment carrying the audio MIME type | Required; native voice send is unavailable in TrueConf |
| typing indicator | native `typing` chat activity | Refreshed for the duration of Hermes response generation |
| media upload indicator | native `uploading_file` chat activity | Kept active while an image, document, video, or voice file is uploaded |

`force_document=true` always selects document delivery. Without that flag,
media kind is derived from the explicit Hermes operation first and MIME/file
extension second; ambiguous input is sent as a document.

For detached delivery with text and multiple media files, text is sent first
when non-empty, followed by media in input order. There is no rollback after a
partial delivery. The result identifies the last successful message and
reports earlier delivered IDs as structured metadata when a later item fails.

## 6. Identity and target model

Identifiers are opaque, case-preserving strings. The Plugin MUST NOT infer
meaning from UUID shape, split identifiers on `@`, or case-fold them.

| Concept | Canonical value |
| --- | --- |
| Platform | dynamic `Platform("trueconf")` |
| User ID | SDK `Message.author.id` |
| User display name | public SDK lookup when authorized and available; otherwise user ID |
| Chat ID | SDK `Message.chat.chat_id`, unchanged |
| Chat name | SDK `Message.chat.chat_title`; fallback is chat ID |
| Chat type | SDK `Message.chat.chat_type`: `P2P -> dm`, `GROUP -> group`, `CHANNEL -> channel` |
| Message ID | SDK message/send response ID, unchanged |
| Reply ID | SDK `reply_message_id`, unchanged |
| Reply text | SDK `reply_message` text after same-chat validation |
| Session key inputs | Hermes derives them from the normalized source; Plugin does not own session keys |

Incoming `SessionSource.chat_id` and outbound delivery targets are real
TrueConf chat IDs. Incoming chat ID, title, and type come from the public SDK
`Message.chat` entity without an additional chat lookup. The full external
form is `trueconf:<chat-id>`. The target parser accepts no thread component,
because TrueConf replies are message references rather than Hermes threads.
An empty value or surrounding whitespace fails local validation.

## 7. Connection lifecycle

### 7.1 States

```text
disabled
   |
   v
validating -> authenticating -> starting -> authorized
   |              |               |             |
   |              +----fatal------+             +-- SDK reconnecting --+
   |                                                               |    |
   +------------------fatal/non-retryable                           +----+
                                                   |
                                                   +-- loop exited -> fatal

authorized/fatal -> stopping -> stopped
```

`authorized` is the only ready state. A TCP/WebSocket connection alone is not
readiness.

### 7.2 Startup contract

1. Validate local configuration without importing or installing missing
   dependencies as a side effect.
2. Acquire the Hermes-documented scoped credential lock for the normalized
   `(server, username)` identity.
3. Obtain the SDK bot using its public credential constructor off the event
   loop, because the current public constructor performs synchronous network
   I/O.
4. Start one SDK run task without SDK signal handlers.
5. Race the public authorized health/event signal against SDK task exit,
   cancellation, and the adapter startup timeout supplied by Hermes.
6. Report connected only after authorization succeeds.
7. On every unsuccessful path, shut down the SDK, settle the task, release the
   lock, and report a typed fatal condition.

The implementation MUST NOT read the SDK's private connection task.

### 7.3 Runtime contract

- Transient health changes emitted while the SDK task remains alive are
  status signals, not permission for the Plugin to start another connection.
- An unexpected SDK task exit is retryable unless a typed authentication,
  configuration, or TLS-verification cause proves otherwise.
- A fatal notification is emitted once per lifecycle generation.
- Updates from an obsolete generation are ignored.

### 7.4 Shutdown contract

`disconnect()` is safe before, during, or after startup and on repeated calls.
It cancels/settles Plugin watchers, calls public SDK shutdown, releases the
credential lock exactly once, and reports disconnected only when shutdown is
intentional. Cancellation is re-raised after cleanup.

The standalone sender uses the same lifecycle rules but does not register a
long-lived inbound listener. Its authorization callback denies all inbound
events during the short outbound-only session. Connection readiness comes
only from the SDK authorization event; elapsed time or a fixed sleep is never
treated as readiness.

## 8. Configuration contract

### 8.1 Canonical configuration

Behavioral configuration lives in `config.yaml` under the canonical
`platforms.trueconf` block. The top-level `trueconf` and
`gateway.platforms.trueconf` forms are not supported. Secrets live in the
environment/secret store.

```yaml
platforms:
  trueconf:
    enabled: true
    server: video.example.com
    port: 443
    https: true
    verify_ssl: true
    parse_mode: html
    require_mention: true
    allowed_chats:
      - "<trueconf-group-chat-id>"
    group_allowed_chats:
      - "<trueconf-group-chat-id>"
    free_response_chats: []
    observe_unmentioned_group_messages: false
    observe_context_limit: 20
    home_channel:
      platform: trueconf
      chat_id: "<trueconf-chat-id>"
      name: "TrueConf home"
    allow_from:
      - "alice@video.example.com"
    group_policy: allowlist
    group_allow_from:
      - "alice@video.example.com"
```

Primary environment variables:

| Variable | Meaning |
| --- | --- |
| `TRUECONF_USERNAME` | bot account name |
| `TRUECONF_PASSWORD` | bot account password; secret |
| `TRUECONF_SERVER` | optional override/seed for server host |
| `TRUECONF_HOME_CHANNEL` | optional existing TrueConf `chat_id` for cron delivery |
| `TRUECONF_ALLOWED_USERS` | Hermes comma-separated user allowlist |
| `TRUECONF_ALLOW_ALL_USERS` | explicit Hermes allow-all switch |

`hermes setup` and `hermes gateway setup` dispatch to the Plugin's interactive
setup callback. It prompts for the required connection values with masked
password input, TLS certificate verification, authorization, and the optional
cron home chat, and persists them in the active Hermes profile. Verification
defaults to enabled; disabling it presents an explicit interception warning.

Username/password is the sole credential mode in 0.1.0. A static token mode is
outside scope unless separately approved.

### 8.2 Defaults

| Setting | Default |
| --- | --- |
| `port` | `443` |
| `https` | `true` |
| `verify_ssl` | `true` |
| `parse_mode` | `html` |
| `require_mention` | `true` for group/channel; irrelevant for P2P |
| `observe_unmentioned_group_messages` | `false` |
| `observe_context_limit` | `20`; `0` disables; maximum `100` |
| receive unread history | `false` |
| receive system messages | `false` |
| skip self messages | `true` |

HTML output uses the server's native subset and is the only supported output
mode. `parse_mode: markdown` and `parse_mode: text` are rejected. No
regular-expression Markdown-to-HTML conversion exists.

### 8.3 Precedence

From highest to lowest:

1. explicit `enabled: false` disables the platform even when credentials are
   present;
2. secret-store/environment credentials;
3. supported operational environment overrides (`TRUECONF_SERVER`,
   `TRUECONF_HOME_CHANNEL`, authorization variables);
4. `config.yaml` behavioral values;
5. defaults in section 8.2.

Historical environment variables for parse mode or SSL verification are not
accepted in 0.1.0. They remain candidates for a later, explicit migration
feature and MUST NOT silently override YAML.

### 8.4 Validation

Validation is deterministic and field-specific:

- `server` is a non-empty hostname/address without a URL scheme or path;
- `port` is an integer from 1 through 65535;
- username and password are both non-empty;
- a `password` key in `config.yaml` is rejected with guidance to use the
  secret environment/store;
- `https`, `verify_ssl`, and `require_mention` have valid boolean values;
- `observe_unmentioned_group_messages` is a boolean;
- `observe_context_limit` is an integer from `0` through `100` and is not a boolean;
- `verify_ssl` MAY instead be an existing readable CA bundle path if the SDK
  audit confirms the public contract end to end;
- `parse_mode` is exactly `html`, case-insensitively
  normalized;
- home and explicit delivery targets follow the typed grammar in section 6;
- authorization lists contain non-empty string IDs.

Standalone cron delivery runs this validation before adapter construction and
returns a non-retryable error result for invalid configuration.

`verify_ssl: false` is allowed only as an explicit operator choice and MUST
produce a prominent warning without logging credentials. Dependency probes
are passive. When adapter creation finds a formatter or SDK runtime dependency
missing, the registered `ensure_deps_fn` installs `mistune>=3,<4` and
`python-trueconf-bot>=1.5.0,<2` through Hermes'
`tools.lazy_deps.install_specs()` pipeline. The pipeline obeys Hermes'
lazy-install security policy.

## 9. Authorization and mention timing

Hermes remains the only owner of access policy. The Plugin does not parse
allowlists, issue pairing codes, or decide which users are permanently
trusted.

For each inbound update, processing order is:

1. reject self-originated, malformed, unsupported, or stale-generation input;
2. obtain/cache public chat metadata sufficient to know P2P/group/channel;
3. apply the configured mention gate for group/channel input;
4. for an attachment, call the callback installed through public
   `set_authorization_check(user_id, chat_type, chat_id)`;
5. download only when the result is the literal `True`;
6. construct the normalized event and call Hermes `handle_message()`;
7. let Hermes perform the final authorization, pairing, command, and session
   behavior.

If the callback returns `False`, the Plugin sends Hermes the message identity
and a non-content attachment descriptor but no bytes or local path. This lets
Hermes preserve its unauthorized-DM behavior without exposing attachment
content. If no callback is installed, attachment download fails closed; a
long-lived adapter MUST NOT enter ready state without that public hook being
available.

Reply-text lookup, user display-name lookup, and media download occur only
after a positive preflight when they would otherwise fetch additional
content. The minimal chat metadata lookup may precede it because chat type is
an input to Hermes' policy callback.

Mention gating is traffic selection, not authorization. In group/channel
chats `require_mention: true` accepts only the SDK's native positive mention
signal, including a native `@all` signal if the SDK defines it. The Plugin
does not detect mentions with a second regex. A reply to the bot is not an
implicit mention unless the SDK exposes it as one.

## 10. Media size, files, and cleanup

### 10.1 Inbound

- The Plugin checks advertised attachment size against Hermes'
  `gateway.max_inbound_media_bytes` before download.
- TrueConf Server's advertised attachment size is authoritative. The SDK's
  configured in-memory limit MUST match the effective Hermes inbound-media
  limit and reject an advertised oversized file before reading its body.
- A second limit based on observed streamed bytes is not required because the
  authenticated TrueConf Server is trusted to report correct metadata.
- Download occurs once, as bytes, after authorization.
- The Plugin passes those bytes to Hermes' public media-cache helper and does
  not create a second copy.
- After a successful cache handoff, Hermes owns the cache path and its TTL
  cleanup. The Plugin MUST NOT unlink it at end of turn.
- A cache/write failure produces no `media_urls` entry.

### 10.2 Outbound

- A path received from Hermes/caller remains caller-owned on success, failure,
  timeout, and cancellation.
- The Plugin never modifies or deletes caller-owned media.
- Any Plugin-created preview, conversion, or staging file is Plugin-owned and
  removed in `finally`; however, 0.1.0 SHOULD avoid such files and use SDK
  byte/file inputs directly.
- SDK-created temporary resources are SDK-owned.

The same ownership rules apply to the detached sender. Cleanup errors are
logged without replacing the primary send/lifecycle result.

## 11. Error behavior

No failure is classified by matching human-readable substrings. The Plugin
uses typed SDK exceptions and numeric SDK API codes; unknown shapes remain
`unknown`.

### 11.1 Startup/lifecycle

| Cause | Adapter outcome | Retryable |
| --- | --- | --- |
| missing SDK, lazy install succeeds | adapter creation continues | no |
| missing SDK, lazy install blocked/fails | unavailable with install hint | no |
| invalid local config | `config_invalid` | no |
| invalid/revoked credentials, API 200–204 | `authentication_failed` | no |
| TLS certificate verification failure | `tls_verification_failed` | no |
| connect timeout/DNS/network loss | `transport_unavailable` | yes |
| SDK run loop exits unexpectedly | `transport_stopped` | yes unless typed otherwise |
| cancellation/operator shutdown | clean stop | no fatal event |

Error messages include the platform, operation, safe server identity, and SDK
error code when present. They MUST NOT include password, token, full raw
payloads that may contain secrets, or attachment content.

### 11.2 Send/edit

| SDK condition | `SendResult.error_kind` | `retryable` |
| --- | --- | --- |
| `TextMessageTooLongError` / caption too long | `too_long` | false |
| API 302–303 | `forbidden` | false |
| API 304, 306–308, 404 | `not_found` | false |
| API 100–101, 300–301 | `transient` | true |
| invalid extension, file too large, malformed payload | `unknown` | false |
| any unrecognized exception/code | `unknown` | false |

The Plugin performs no send retry. Hermes may retry only results explicitly
marked retryable. Cancellation propagates after resource cleanup. A failed
send or edit never returns a fabricated message ID. Each `send` or
`edit_message` invocation produces one SDK request per chunk: content above
the 4096 visible-character limit is split with the SDK's `safe_split_text`
and delivered as chained continuation messages, reported through
`SendResult.message_id` (last) and `SendResult.continuation_message_ids`
(earlier ids, send order) so Hermes re-targets later edits at the most recent
continuation. The Plugin forwards the complete content and configured native
parse mode without truncation, accumulation, or content-based classification.
In HTML mode only, raw line endings become the SDK `LineBreak` representation.

## 12. Explicit exclusions

Version 0.1.0 excludes:

- changes to or private APIs from Hermes Core;
- monkeypatches, `lib_patches`, vendored SDK files, or private SDK state;
- Plugin-owned authorization, anti-spam, rate limiting, deduplication, or
  emergency-stop policy;
- a second reconnect loop;
- regex Markdown-to-HTML conversion;
- heuristic merging/editing based on emoji, text, timing, or a global
  "last message";
- Plugin-owned user-ID resolution or P2P-chat creation;
- TrueConf Favorites, surveys, service events, reactions, inbound
  edits/deletes, forwarding semantics, and threads;
- generic native audio UX beyond preserving the file as a document;
- native outbound video and voice-message presentation that the TrueConf API
  does not provide;
- stickers unless the SDK audit proves the scenario and the user separately
  approves it;
- multiple TrueConf identities in one platform instance;
- compatibility promises outside Hermes v0.21.0 and the supported SDK versions
  after Stage 2.

## 13. SDK and host gap status before implementation

These are findings, not permission for Plugin workarounds. Status was
rechecked at clean SDK commit `afb4bde4`; its suite passes `89` tests.

1. **Inbound voice parsing — resolved:** the public update content factory
   constructs the voice model and the clean SDK regression suite covers a
   representative voice update. Stage 6 additionally verifies the Plugin's
   public `Message.voice` to Hermes `MessageType.VOICE` mapping.
2. **Outbound native video — resolved by scope decision:** TrueConf exposes no
   native send capability, so 0.1.0 sends video as a generic file attachment.
3. **Outbound native voice — resolved by scope decision:** TrueConf exposes no
   native send capability, so 0.1.0 sends audio/voice as a generic file
   attachment.
4. **Bounded downloads — resolved for the accepted trust boundary:**
   `max_in_memory_download_size` rejects an advertised oversized file before
   download and is tested. TrueConf Server metadata is authoritative, so a
   second observed-stream byte counter is not required.
5. **Upload failures — resolved in the audited source:** upload HTTP failures
   and missing temporary file IDs now raise typed `FileUploadError`; regression
   tests cover both paths.
6. **Reconnect exhaustion — resolved in the audited source:** common network
   and invalid-URI failures now exhaust `ws_max_retries`, raise typed
   `WSConnectionError`, and have regression tests.
7. **Mention semantics:** the SDK-native mention predicate must be verified for
   bot mentions and `@all`; any defect is fixed in the SDK, not duplicated in
   the Plugin.
8. **Handler shutdown — resolved in the audited source:** inbound update tasks
   are tracked, router handlers remain within those tasks, shutdown stops new
   updates and waits for active handlers, and regression tests cover normal,
   self-initiated, and externally timed-out shutdown. The WebSocket session
   transport is also closed by a subsequent tested fix.
9. **Inbound media captions:** the public attachment model contains file
   metadata but no caption field. Caption preservation remains excluded unless
   a captured protocol case and public SDK model establish it.
10. **Hermes cache limit consistency:** Hermes v0.21.0's document cache helper
   does not visibly apply the same byte cap as image/audio/video helpers. The
   Plugin will enforce the public limit before cache handoff; no Hermes patch
   is authorized.
11. **SDK download-URL logging — newly exposed by Stage 6 live testing:** the
   clean SDK currently logs a signed attachment download URL, including its
   query token, at INFO level. The Plugin must not monkeypatch or filter SDK
   internals; URL/token redaction and its regression test belong in the SDK
   before release.

Stage 2 must turn each required SDK item into a minimal public-contract test,
then either fix it in the SDK with approval or return to this scope for an
explicit reduction.

## 14. Rejected historical patterns

Historical code revealed useful scenarios but also demonstrated patterns this
specification rejects:

- fixed sleeps for readiness;
- adapter and SDK reconnect loops running simultaneously;
- media download before authorization;
- adapter-owned allowlists, anti-spam, rate limits, and deduplication;
- regex Markdown-to-HTML conversion;
- favorites/self-echo heuristics instead of SDK self filtering;
- duplicate downloads and unclear temporary-directory cleanup;
- substring-based retry/error classification;
- assumptions about chat fields absent from the clean SDK;
- stateful response merging and emoji-based message classification.

No fragment implementing these patterns is transferable.

## 15. Stage 1 acceptance checklist

Stage 1 is complete only after the user explicitly approves:

- the three ownership boundaries in section 3;
- the exact `chat_id` target contract in section 6;
- username/password as the only 0.1.0 credential mode;
- `require_mention: true` as the group/channel default and the native-only
  mention semantics;
- the authorization/media ordering in section 9;
- media ownership and cleanup in section 10;
- the exclusions in section 12;
- verification of the remaining required SDK contracts during Stage 2,
  including the dedicated inbound voice regression test.

After approval, accepted decisions that change project architecture will be
recorded in `CONTEXT.md` and, where a durable trade-off warrants it, an ADR.
Only then may Stage 2 begin. Plugin and SDK implementation remains prohibited
at this checkpoint.

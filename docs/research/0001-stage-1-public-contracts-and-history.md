# Stage 1 research: public contracts and historical evidence

Status: research note; not an approved behavior specification.

This note records the evidence needed to write the Stage 1 observable-behavior
specification. It contains no Plugin or SDK implementation. Historical code is
used only to identify scenarios and failure modes; no fragment is approved for
transfer.

## Evidence legend and baselines

- **H-CONTRACT** — confirmed by the Hermes v0.21.0 public platform/plugin
  documentation or its documented extension types.
- **SDK-CONTRACT** — confirmed by a public, documented `python-trueconf-bot`
  symbol in the clean checkout.
- **HISTORY** — evidence of a scenario or defect from a historical
  implementation; never an implementation prescription.
- **INFERENCE** — a proposed consequence of confirmed contracts that still
  belongs in the Stage 1 review.
- **SDK-GAP** — public SDK behavior that is absent, internally inconsistent, or
  insufficiently specified; it must be proved in Stage 2 and fixed in the SDK
  if the agreed behavior depends on it.

Local source snapshots examined:

Hermes and historical paths below are relative to this repository root. SDK
paths are relative to the read-only local checkout
`/Users/baadzianton/TrueConf/python-trueconf-bot`.

| Source | Revision | Notes |
|---|---|---|
| Hermes Agent | `29112bef099274229cadff79cdff7bf7b99c4b77` (`v2026.8.31`) | `hermes-agent/pyproject.toml:5` declares `0.21.0`; this is the agreed target. |
| `python-trueconf-bot` | `5cb6258f05bdfba4d96df1af641fcc0bbef6fbec` (`v1.5.0` branch, `v1.4.4-5-g5cb6258`) | Tracked files are clean. Latest release tag in the checkout is `v1.4.4`; the five later commits are not a release tag. |
| historical standalone adapter | `8302fd41c40c6136cb85ee1a28ba81e0a437717b` (`v1.0.0-54-g8302fd4`) | Scenario/problem evidence only. |
| historical Hermes fork | `435801b87d4e73ebda35a4ca62471c1356343ced` | Scenario/problem evidence only. |

The SDK checkout identifies itself as the TrueConf project and links its
repository and documentation at `python-trueconf-bot/pyproject.toml:5-10,79-83`.
Its README documents installation and TrueConf Server compatibility at
`python-trueconf-bot/README.md:49-69`.

## Confirmed Hermes public boundary

### Discovery and registration

- **H-CONTRACT:** native directory plugins are discovered from bundled, user,
  project, and pip sources. A directory plugin has `plugin.yaml` and an
  `__init__.py` exposing `register(ctx)`; pip distributions use the
  `hermes_agent.plugins` entry-point group
  (`hermes-agent/hermes_cli/plugins.py:5-20,457-458`). Hermes explicitly says
  third-party product integrations ship as standalone repositories
  (`hermes-agent/website/docs/developer-guide/plugins/index.md:41-42`).
- **H-CONTRACT:** `PluginContext.register_platform()` receives a platform name,
  label, adapter factory, passive dependency probe, optional validation and
  required-env metadata, installation hint, and documented `PlatformEntry`
  fields. Its factory receives `PlatformConfig` and must return a
  `BasePlatformAdapter` subclass
  (`hermes-agent/hermes_cli/plugins.py:2927-2981`).
- **H-CONTRACT:** `check_fn` is a side-effect-free availability probe. The only
  active installation hook is `ensure_deps_fn`, invoked by adapter creation
  when the passive check fails. Omitting it makes a missing SDK a hard block
  with `install_hint`, which matches the accepted no-auto-install decision
  (`hermes-agent/gateway/platform_registry.py:77-104,618-694`).
- **H-CONTRACT:** a registered name is accepted as dynamic
  `Platform("trueconf")`; arbitrary unregistered names are rejected, and the
  dynamic member is identity-stable
  (`hermes-agent/gateway/config.py:325-400`). No TrueConf enum member or core
  branch is needed.
- **H-CONTRACT:** registration metadata already covers authorization env names,
  message length, environment enablement, YAML bridging, cron home-channel
  discovery, target parsing/validation, and standalone sending
  (`hermes-agent/gateway/platform_registry.py:131-229`). The official guide
  lists the corresponding automatic integration points
  (`hermes-agent/website/docs/developer-guide/adding-platform-adapters.md:210-235`).

### Adapter and message contract

- **H-CONTRACT:** an adapter extends `BasePlatformAdapter` and implements
  `connect(*, is_reconnect=False)`, `disconnect()`, `send(chat_id, content,
  reply_to=None, metadata=None)`, and `get_chat_info(chat_id)`
  (`hermes-agent/gateway/platforms/base.py:3031-3040,4158-4203,7443-7452`). The
  public guide demonstrates `build_source()`, `MessageEvent`, and
  `handle_message()` for inbound delivery
  (`hermes-agent/website/docs/developer-guide/adding-platform-adapters.md:609-625`).
- **H-CONTRACT:** `MessageEvent` carries normalized text and message type,
  sender identity, `SessionSource`, original message ID, local media paths and
  MIME types, reply ID/text/author information, timestamp, and control metadata
  (`hermes-agent/gateway/platforms/base.py:2424-2517`). Hermes message types are
  text, location, photo, video, audio, voice, document, sticker, and command
  (`hermes-agent/gateway/platforms/base.py:2403-2413`).
- **H-CONTRACT:** `SessionSource` supplies the stable routing identity:
  platform, chat ID/name/type, user ID/name, optional thread, scope, parent, and
  triggering message ID (`hermes-agent/gateway/session.py:148-220`).
- **H-CONTRACT:** `SendResult` separates success, message ID, human-readable
  error, retryability/delay, continuation IDs, and machine-readable error kind
  (`hermes-agent/gateway/platforms/base.py:2593-2624`). Hermes defines the
  platform-neutral kinds `too_long`, `bad_format`, `forbidden`, `not_found`,
  `rate_limited`, `transient`, and `unknown`, plus the public
  `classify_send_error()` helper
  (`hermes-agent/gateway/platforms/base.py:2627-2654,2693-2741`).
- **H-CONTRACT:** `edit_message()` is optional; returning unsuccessful
  `SendResult` makes the caller fall back to a new message
  (`hermes-agent/gateway/platforms/base.py:4240-4267`). Media-specific outbound
  methods have safe failure fallbacks; a capable adapter must override them for
  actual native video/document delivery
  (`hermes-agent/gateway/platforms/base.py:4896-4955`).
- **H-CONTRACT:** adapters default to `splits_long_messages = False`; Hermes then
  owns the long-message path. An adapter may claim native splitting only when
  its `send()` actually preserves the complete output
  (`hermes-agent/gateway/platforms/base.py:3094-3100`). The registry's
  `max_message_length` is the platform cap used by Hermes
  (`hermes-agent/gateway/platform_registry.py:137-140`).

### Configuration and detached delivery

- **H-CONTRACT:** `PlatformConfig` contains enablement, optional generic token
  and API key, `HomeChannel`, reply mode, lifecycle/typing behavior,
  per-channel overrides, and platform-specific `extra`
  (`hermes-agent/gateway/config.py:646-711`). A home destination has platform,
  chat ID, display name, and optional thread/user/scope provenance
  (`hermes-agent/gateway/config.py:473-516`).
- **H-CONTRACT:** Hermes bridges shared YAML keys, including
  `require_mention`, `allow_from`, and `group_allow_from`, into
  `PlatformConfig.extra` for plugin platforms
  (`hermes-agent/gateway/config.py:1656-1687,1698-1810`). The plugin-specific
  `apply_yaml_config_fn` then runs, and its returned mapping is merged into
  `extra`; hook failures do not abort config loading
  (`hermes-agent/gateway/config.py:1811-1846`). Environment overrides are
  applied after that bridge, so explicit environment values have higher
  precedence (`hermes-agent/gateway/platform_registry.py:167-181` and
  `hermes-agent/website/docs/developer-guide/adding-platform-adapters.md:338-374`).
- **H-CONTRACT:** environment-only enablement is explicit. Hermes respects an
  explicit `enabled: false`, calls `env_enablement_fn` before construction,
  validates configured credentials through `is_connected`, and only then runs
  the passive dependency check
  (`hermes-agent/gateway/config.py:2682-2816`).
- **H-CONTRACT:** `standalone_sender_fn` has the signature
  `(pconfig, chat_id, message, *, thread_id=None, media_files=None,
  force_document=False)` and returns either success with a message ID or a
  structured error. This is the supported path when cron runs without a live
  gateway adapter (`hermes-agent/gateway/platform_registry.py:214-229` and
  `hermes-agent/website/docs/developer-guide/adding-platform-adapters.md:391-420`).
- **H-CONTRACT:** a persistent credential should use the documented
  machine-local scoped lock; its public operations are
  `acquire_scoped_lock(scope, identity, metadata)` and
  `release_scoped_lock(scope, identity)`
  (`hermes-agent/website/docs/developer-guide/adding-platform-adapters.md:762-778`;
  `hermes-agent/gateway/status.py:1610-1615,1763-1777`).

### Authorization and lifecycle ownership

- **H-CONTRACT:** Hermes owns the final access decision. Its order is platform
  allow-all, configured allowlists, pairing approval, global allow-all, then
  default deny (`hermes-agent/gateway/authz_mixin.py:488-503`). Plugin platform
  env names are taken from `PlatformEntry.allowed_users_env` and
  `allow_all_env` (`hermes-agent/gateway/authz_mixin.py:665-682`); absent an
  explicit grant, a network adapter remains fail-closed
  (`hermes-agent/gateway/authz_mixin.py:695-823`).
- **H-CONTRACT:** before connecting an adapter, the runner installs its final
  message handler, fatal handler, session store, and the Hermes-owned
  authorization callback (`hermes-agent/gateway/run.py:13882-13897`). The
  callback delegates back to the complete Hermes authorization chain
  (`hermes-agent/gateway/run.py:17481-17517`).
- **H-CONTRACT:** the adapter-side early seam is tri-state. It returns only
  literal `True`/`False`, and returns `None` when missing, failing, or malformed;
  a credentialed media fetch must require `is True`
  (`hermes-agent/gateway/platforms/base.py:3900-3951`). The bundled Buzz plugin
  demonstrates the intended fail-closed media gate while still passing the
  metadata-only event to the Gateway so pairing/denial remains Hermes-owned
  (`hermes-agent/plugins/platforms/buzz/adapter.py:2483-2512,2827-2858`).
- **H-CONTRACT:** `connect()` is bounded by Hermes' timeout and receives the
  cold-start/reconnect flag (`hermes-agent/gateway/run.py:8292-8361`). After a
  terminal retryable adapter failure, Hermes reconstructs and reconnects the
  adapter with 30/60/120-second exponential backoff capped at five minutes;
  transient retries continue indefinitely while long-running failure becomes
  visible as `needs_attention`
  (`hermes-agent/gateway/run.py:4638-4658,15715-15851`). An adapter that never
  became owned is still disconnected on all failure paths
  (`hermes-agent/gateway/run.py:4584-4635`).
- **H-CONTRACT:** adapters expose state via `_mark_connected()`,
  `_mark_disconnected()`, and `_set_fatal_error(code, message, retryable=...)`,
  which are the documented subclass extension pattern despite protected Python
  naming (`hermes-agent/gateway/platforms/base.py:3580-3601` and the public
  example at `hermes-agent/website/docs/developer-guide/adding-platform-adapters.md:102-121`).

## Confirmed clean TrueConf SDK public surface

### Construction, authentication, readiness, and shutdown

- **SDK-CONTRACT:** `Bot(server, token, ...)` publicly supports a Dispatcher,
  unread/system-message toggles, self-message suppression (default true), TLS
  verification as bool/CA path/`SSLContext`, protocol and port, connection retry
  settings, an async health callback, and operation timeout
  (`python-trueconf-bot/trueconf/client/bot.py:120-174`).
- **SDK-CONTRACT:** `Bot.from_credentials(server, username, password, ...)`
  exposes the same configuration and obtains a token before constructing the
  bot (`python-trueconf-bot/trueconf/client/bot.py:370-447`). Invalid credentials
  can raise `InvalidGrantError`; local token validation uses
  `TokenValidationError`; server API failures carry numeric `code`, `detail`,
  and payload in `ApiErrorException`
  (`python-trueconf-bot/trueconf/exceptions.py:1-27,194-210`). Server error codes
  distinguish network, authorization, chat, and file classes
  (`python-trueconf-bot/trueconf/types/responses/api_error.py:7-60`).
- **SDK-CONTRACT:** `connected_event`, `authorized_event`, and `stopped_event`
  are exposed on the Bot; the async health callback reports connected,
  authorized, and disconnected transitions
  (`python-trueconf-bot/trueconf/client/bot.py:189-210,652-677,688-759`).
  `health_check()` gives a current public snapshot
  (`python-trueconf-bot/trueconf/client/bot.py:880-906`).
- **SDK-CONTRACT:** `run(handle_signals=False)` starts the Bot, then awaits its
  internal connection loop and therefore propagates terminal exceptions other
  than cancellation. `start()` only starts the internal task and returns;
  `shutdown()` cancels/awaits that task, closes the session, clears readiness,
  and sets `stopped_event`
  (`python-trueconf-bot/trueconf/client/bot.py:1659-1688,1951-2006`).
- **INFERENCE:** the Plugin can create and own a task for the public
  `bot.run(handle_signals=False)` coroutine, wait on the public
  `authorized_event`, and always call public `shutdown()`. It does not need to
  inspect SDK `_connect_task`, and a fixed readiness sleep is unnecessary.
- **SDK-CONTRACT:** after authorization, `bot.me_id` is the bot's TrueConf user
  ID. `bot.me` is deprecated and means the Favorites chat ID, not the user ID
  (`python-trueconf-bot/trueconf/client/bot.py:337-368,776-788`). The default
  `SkipSelfMessages` middleware drops only messages whose author equals
  `me_id` (`python-trueconf-bot/trueconf/middleware.py:42-78`).

### Messages, identity, chat metadata, and targets

- **SDK-CONTRACT:** the root package publicly exports `Bot`, `Dispatcher`,
  `Router`, `Message`, `F`, and `ParseMode`; input-file classes and `Message`
  are exported from `trueconf.types`, and `ChatType`/`MessageType` from
  `trueconf.enums` (`python-trueconf-bot/trueconf/__init__.py:1-20`,
  `python-trueconf-bot/trueconf/types/__init__.py:1-40`,
  `python-trueconf-bot/trueconf/enums/__init__.py:1-23`).
- **SDK-CONTRACT:** an inbound `Message` supplies timestamp, message type,
  author, box, content, stable `message_id`, stable `chat_id`, edit status, and
  optional `reply_message_id`. Public helpers expose text, document, photo,
  video, voice (current branch only), sticker, and mention
  (`python-trueconf-bot/trueconf/types/message.py:37-105,127-265`). The author
  envelope contains only ID and author type; the box contains only ID and
  position (`python-trueconf-bot/trueconf/types/author_box.py:8-17`).
- **SDK-CONTRACT:** `get_chat_by_id(chat_id)` returns `title`, `chat_id`, and
  numeric `chat_type`; `get_user_display_name(user_id)` returns the display
  name (`python-trueconf-bot/trueconf/client/bot.py:1350-1366,1517-1537`;
  response shapes at
  `python-trueconf-bot/trueconf/types/responses/get_chat_by_id_response.py:7-14`
  and
  `python-trueconf-bot/trueconf/types/responses/get_user_display_name_response.py:6-9`).
  Public `ChatType` values distinguish P2P, group, system, Favorites, and
  channel (`python-trueconf-bot/trueconf/enums/chat_type.py:4-17`).
- **INFERENCE:** stable mapping is available without raw payload access:
  `Message.author.id` → Hermes user ID, `Message.chat_id` → Hermes chat ID,
  `Message.message_id` → Hermes message ID, and `Message.reply_message_id` →
  Hermes reply ID. Chat name/type and sender display name require documented
  SDK lookups and should be cached by the Plugin rather than inferred from
  absent `box.title`, `box.type`, or `author.display_name` fields.
- **SDK-CONTRACT:** `create_personal_chat(user_id)` accepts identifiers with or
  without a domain, adds the server domain for a bare ID, and returns the
  existing or newly created P2P chat ID
  (`python-trueconf-bot/trueconf/client/bot.py:1088-1113` and
  `python-trueconf-bot/trueconf/types/responses/create_p2p_chat_response.py:6-8`).
  Therefore user-like targets can be resolved through public API while UUID-like
  chat targets can remain chat IDs; target syntax/disambiguation is a Plugin
  specification decision.
- **SDK-CONTRACT:** `Message.mention` recognizes an exact stripped `@all` or a
  TrueConf mention link whose ID equals `bot.me_id`; otherwise it returns
  `None`, not `False` (`python-trueconf-bot/trueconf/types/message.py:239-265`).
  It does not remove mention markup from the delivered text.

### Text, replies, edits, formatting, media, and errors

- **SDK-CONTRACT:** `send_message()` and `edit_message()` accept the native
  parse mode and enforce 4096 visible characters. `send_message()` and
  `send_document()` accept `reply_message_id`
  (`python-trueconf-bot/trueconf/client/bot.py:1244-1270,1690-1784`). Their
  response types expose the resulting message ID
  (`python-trueconf-bot/trueconf/types/responses/send_message_response.py:6-10`,
  `python-trueconf-bot/trueconf/types/responses/send_file_response.py:6-11`).
- **SDK-CONTRACT:** native parse modes are `text`, `markdown`, and `html`
  (`python-trueconf-bot/trueconf/enums/parse_mode.py:4-14`). This supports the
  accepted Markdown default and literal plain-text mode without a Plugin
  Markdown-to-HTML converter.
- **SDK-CONTRACT:** `download_file_by_id(file_id, dest_path=None)` obtains file
  metadata, waits for readiness, and returns bytes when no destination is
  supplied or a `Path` when one is supplied
  (`python-trueconf-bot/trueconf/client/bot.py:1132-1176`). The public file-info
  result includes name, byte size, MIME type, readiness, file ID, previews, and
  download URL
  (`python-trueconf-bot/trueconf/types/responses/get_file_info_response.py:8-24`).
- **SDK-CONTRACT:** public attachment/photo/video/document types preserve file
  ID, filename, size, and MIME type (`python-trueconf-bot/trueconf/types/content/attachment.py:8-14`,
  `photo.py:7-23`, `video.py:7-23`, `document.py:7-24`). The unreleased voice
  type preserves file ID, size, MIME type, and duration but has no filename
  (`python-trueconf-bot/trueconf/types/content/voice.py:6-23`).
- **SDK-CONTRACT:** `BufferedInputFile` and `FSInputFile` are public upload
  inputs. `send_photo()` is the photo-specific path; `send_document()` accepts
  arbitrary file types but describes them as files/documents
  (`python-trueconf-bot/trueconf/types/input_file.py:60-91,119-179` and
  `python-trueconf-bot/trueconf/client/bot.py:1690-1748,1786-1808`).
- **INFERENCE:** for inbound media, the safest no-temp path is authorize first,
  size-check public metadata, receive bytes from the SDK, then place them in a
  Hermes-owned media cache. For outbound existing local files, the Plugin is a
  borrower and must never delete the caller's path. Any transient file created
  by Plugin or standalone delivery is Plugin-owned and must be removed in a
  `finally` path; SDK-created files remain SDK-owned.

## Preliminary SDK gaps and Stage 2 proofs

These findings are blockers/questions, not permission to add workarounds to the
Plugin.

1. **SDK-GAP — voice updates do not reach public handlers.** Release `v1.4.4`
   has no voice enum/model. The current unreleased revision adds
   `MessageType.VOICE_MESSAGE = 205` and a `Voice` model
   (`python-trueconf-bot/trueconf/enums/message_type.py:5-24`, commit
   `5cb6258`), but `_content_factory()` still has no `VOICE_MESSAGE` branch and
   returns `None` for unknown content. `parse_update()` consequently discards
   the update before Dispatcher delivery
   (`python-trueconf-bot/trueconf/types/parser.py:32-45,102-118`). Stage 2 needs
   a minimal SDK test and an SDK fix before inbound voice can be in Plugin
   0.1.0.

2. **SDK-GAP — retry exhaustion contradicts the constructor contract.** The
   constructor says `ws_max_retries` limits network/IP attempts
   (`python-trueconf-bot/trueconf/client/bot.py:161-162`), but the common
   `ConnectionClosed`, `InvalidStatus`, and `OSError` branch never increments or
   checks `retry_count` and retries forever. Only `InvalidURI` increments it and
   can raise exhaustion
   (`python-trueconf-bot/trueconf/client/bot.py:621-743`). Stage 2 must define
   whether the SDK's low-level reconnect is intentionally unbounded (with a
   health signal) or has deterministic exhaustion. The Plugin must not add a
   second reconnect loop either way; Hermes resumes ownership only after the
   SDK-run coroutine terminates.

3. **SDK-GAP — no confirmed native outbound video or voice operation.** The
   public Bot has `send_photo()` and `send_document()`, and the latter accepts
   arbitrary files, but there is no video- or voice-specific send method.
   Sending a video/audio file as a document may satisfy attachment transport;
   it does not prove inline video or voice-message semantics. The required
   observable behavior must be chosen and verified against the SDK/server. If
   native semantics are required, the capability belongs in the SDK.

4. **SDK-GAP — chat type/name are absent from the inbound `Message`.** Public
   lookup APIs can enrich an authorized event, but the adapter's early Hermes
   authorization callback accepts chat type before any credentialed fetch. The
   Stage 1 specification must state whether one bounded `get_chat_by_id()`
   metadata lookup is permitted before the media gate, how failed lookup is
   represented, and how an unauthorized DM still reaches Hermes pairing without
   being misclassified. If zero pre-authorization server reads is required,
   inbound chat type must be exposed by the SDK event itself.

5. **SDK-GAP — deterministic handler shutdown is not exposed.** Router handler
   matches are launched with bare `asyncio.create_task()` and are not retained
   (`python-trueconf-bot/trueconf/dispatcher/router.py:229-240`), while
   `Bot.shutdown()` only awaits its connection/session tasks. Stage 2 must prove
   whether Plugin callbacks can still be running after shutdown. If the Plugin
   cannot reliably account for them through its own public callback boundary,
   task drain/cancellation belongs in the SDK.

6. **SDK-GAP — credential exchange is synchronous.** `Bot.from_credentials()`
   calls the synchronous HTTP token helper before returning
   (`python-trueconf-bot/trueconf/client/bot.py:426-432`;
   `python-trueconf-bot/trueconf/utils/_token.py:12-42`). Calling it directly in
   async `connect()` can block the gateway event loop. Stage 2 should decide on
   an async public credential constructor or explicitly prove the supported
   non-blocking invocation pattern; the Plugin must not import the private token
   helper.

7. **SDK-GAP — inbound media caption is not represented by the public attachment
   model.** `AttachmentContent` exposes file metadata only and `Message.text`
   returns text only for `TextContent`
   (`python-trueconf-bot/trueconf/types/content/attachment.py:8-14`;
   `python-trueconf-bot/trueconf/types/message.py:127-159`). If TrueConf sends a
   caption with an attachment and 0.1.0 must preserve it, Stage 2 needs a
   captured payload test and an SDK model change.

8. **SDK-GAP — malformed token errors are not uniformly typed.** Public Bot
   construction promises token validation, but `_validate_token()` performs an
   unguarded three-part split, base64 decode, JSON parse, and `exp` lookup
   (`python-trueconf-bot/trueconf/utils/_token.py:45-77`). Several malformed
   inputs can therefore escape as built-in exceptions rather than
   `TokenValidationError`. Stage 2 should establish one public validation error
   contract so Plugin configuration failures are deterministic.

9. **SDK audit debt — no contract tests cover these integration surfaces.** The
   clean checkout's tracked `tests/` contains only `test_fsm.py`; no tracked test
   covers readiness, retry exhaustion, voice parsing, media transfer, auth
   failure, or shutdown. Every gap above requires a minimal SDK-level test
   before any SDK change, as required by the implementation plan.

## Historical scenario and problem evidence

The following observations explain what must be specified or explicitly
excluded. They do not legitimize copying the referenced code.

### Scenarios worth preserving as requirements

- **HISTORY:** the old fork needed both UUID chat targets and user-like P2P
  targets; it cached user→chat resolution and called public
  `create_personal_chat()` when necessary
  (`trueconf-hermes-agent/gateway/platforms/trueconf.py:98-137`). The historical
  standalone repository records follow-up fixes `c36c51d` and `d66fc45` for
  bare user-ID resolution. This supports an explicit, tested target grammar and
  public SDK resolution, not the old implementation.
- **HISTORY:** both implementations attempted P2P/group/channel identity,
  replies, document/photo/video/audio, editing/streaming, home destinations,
  and reconnect recovery. These are scenario evidence for the current plan,
  while surveys, system-event narration, emergency anti-spam policy, and
  stickers have no approved 0.1.0 behavior.
- **HISTORY:** commit `ec8ccf8` changed the old monitor to the SDK's private
  `_connect_task`, demonstrating why the new design needs an explicit public
  run/readiness/termination contract rather than private task inspection.

### Patterns explicitly rejected by the public boundary

- **HISTORY:** the standalone adapter had its own reconnect counters and loop,
  then disabled its monitor after observing duplicate SDK tasks and a
  connect/disconnect “blinking” failure
  (`hermes-trueconf-adapter/gateway/platforms/trueconf.py:320-325,443-446,496-567`).
  The fork likewise wrapped `bot.run()` in another perpetual reconnect loop
  (`trueconf-hermes-agent/gateway/platforms/trueconf.py:246-317`). The Plugin
  must coordinate SDK termination with Hermes but own no third retry policy.
- **HISTORY:** one implementation scheduled `bot.start()` even though it already
  creates the SDK's internal task, then inspected private `_connect_task`
  (`hermes-trueconf-adapter/gateway/platforms/trueconf.py:410-425,496-512`). The
  supported public composition is an owned task around `bot.run()` plus public
  events.
- **HISTORY:** the fork used a fixed five-second startup sleep
  (`trueconf-hermes-agent/gateway/platforms/trueconf.py:229-240`). Readiness must
  be event/termination/timeout driven.
- **HISTORY:** media was downloaded before the Hermes authorization chain; the
  local `_validate_incoming()` checked self/dedup/rate limiting, not the Hermes
  allowlist (`hermes-trueconf-adapter/gateway/platforms/trueconf.py:704-924,962-1005`).
  A new adapter must send the minimal identity to the Hermes seam before any
  credentialed media download and must leave the final access decision to
  Hermes.
- **HISTORY:** fallback document handling allocated `mkdtemp()` paths without a
  complete ownership/cleanup path
  (`hermes-trueconf-adapter/gateway/platforms/trueconf.py:758-787`). The fork
  also implemented a separate 15-round file retry loop over an SDK operation
  that already waits for file readiness
  (`trueconf-hermes-agent/gateway/platforms/trueconf.py:539-605`). Neither
  behavior belongs in the Plugin.
- **HISTORY:** the standalone adapter wrote `~/.hermes/.env` on the first inbound
  message and reached into `_gateway_runner_ref`
  (`hermes-trueconf-adapter/gateway/platforms/trueconf.py:650-685`). Home-channel
  changes must go through public configuration/operator flows, never hidden
  core mutation.
- **HISTORY:** old code preferred `config.extra` over environment credentials
  (`hermes-trueconf-adapter/gateway/platforms/trueconf.py:275-298`), inferred
  chat metadata from SDK fields that do not exist in the clean public model
  (`hermes-trueconf-adapter/gateway/platforms/trueconf.py:1358-1372`), and used
  `await bot.me` as a bot user ID even though it is a Favorites chat ID
  (`hermes-trueconf-adapter/gateway/platforms/trueconf.py:429-435`). The new
  specification must use the confirmed precedence and public identity fields.
- **HISTORY:** the old sender duplicated long-message splitting, forced HTML,
  converted Markdown with regular expressions, and classified retryability by
  error-string substrings
  (`hermes-trueconf-adapter/gateway/platforms/trueconf.py:1022-1071,1314-1352,1374-1385`).
  Hermes owns chunk orchestration and shared error categories; SDK native
  Markdown/plain modes are sufficient for the approved scope.

## Boundary consequences for the Stage 1 specification

The evidence supports this ownership map, subject to user approval of the
observable details:

| Concern | Owner | Required boundary behavior |
|---|---|---|
| plugin discovery, enablement, sessions, final authorization, pairing, response chunking, retrying terminal adapter failures, cron routing | Hermes Core | Plugin declares metadata and invokes documented adapter hooks; no core branches or mutations. |
| TrueConf token exchange, TLS, WebSocket protocol, typed TrueConf models, API calls, file readiness/transfer, low-level socket reconnect | TrueConf SDK | Missing/defective public behavior blocks its Plugin capability until fixed in SDK. |
| config translation/validation, TrueConf↔Hermes identity and event translation, readiness coordination, SDK task ownership, early auth media gate, public error mapping, target parsing, transient resource cleanup | Plugin | Thin coordination only; no protocol reimplementation, access policy, or retry/chunking subsystem. |

The specification should therefore make the following observable choices
explicit before Stage 2:

1. accepted credential combinations and environment-over-YAML precedence;
2. direct/group/channel mention behavior, including the SDK's exact `@all`
   semantics;
3. target grammar that unambiguously separates chat IDs from user IDs;
4. cold startup states, readiness timeout, permanent authentication failure,
   transient disconnect, terminal SDK-loop exit, Hermes reconnect, and
   idempotent shutdown;
5. metadata-only behavior for unauthorized or malformed media events and the
   permitted timing of chat-metadata lookup;
6. exact text/reply/edit behavior and mapping of SDK error codes to
   `SendResult` fields;
7. whether outbound video/voice must be native media or may be generic file
   attachments;
8. file-size limits, cache ownership, cancellation, and cleanup on every path;
9. standalone sender readiness, error result, media scope, and guaranteed
   shutdown;
10. explicit exclusions: HTML rendering, surveys/system-event narration,
    automatic dependency installation, legacy compatibility aliases, and
    stickers unless separately approved.

Until those choices are approved and the SDK gaps needed by the chosen behavior
are resolved, Plugin/SDK implementation must not start.

## 2026-09-05 SDK fix verification addendum

The clean SDK was rechecked after seven commits, from `5cb6258f` through
`afb4bde4`. The working tree has no tracked modifications. Running the complete
suite at `afb4bde4` produced `89 passed`.

| Commit | Observed change | Verification status |
| --- | --- | --- |
| `a2d4d9f` | Parse `VOICE_MESSAGE` into public `Voice` content | Source path is present; no dedicated voice regression test exists yet |
| `30fe2d2` | Standardize public media attribute on `mime_type`, retaining deprecated compatibility aliases | Source confirmed; not one of the original blocking tests |
| `a254384` | Preserve upload failures as `FileUploadError` | Covered by upload success, HTTP failure, and missing-ID tests |
| `41d8fff` | Add `max_in_memory_download_size` and reject oversized advertised metadata | Covered by tests, but the streaming reader does not count actual bytes and still trusts metadata |
| `bf99267` | Enforce WebSocket retry exhaustion with typed `WSConnectionError` | Covered for common network and invalid-URI failure paths |
| `b391130` | Track inbound update tasks and await handlers during shutdown | Covered for active, self-initiated, and externally timed-out shutdown |
| `afb4bde` | Close the attached WebSocket when closing `WebSocketSession` | Covered by a transport-close regression test |

Consequences for the specification:

- voice parsing, typed upload failure, retry exhaustion, and handler/transport
  shutdown are now implemented in the audited SDK source;
- inbound voice parsing still needs the minimal representative payload test
  required by the approved implementation plan;
- bounded download remains only partially solved because actual streamed bytes
  are not capped when server metadata is wrong;
- no public `send_video()` or `send_voice()` operation was added, so native
  outbound video and voice remain open;
- mention semantics and inbound attachment-caption representation remain
  unchanged.

This addendum updates evidence only. It does not approve the behavior
specification, authorize a Plugin workaround, or open Stage 2.

### Accepted media clarification

On 2026-09-05 the project accepted that TrueConf currently has no native
outbound video or voice-message send API. Version 0.1.0 will use the SDK's
generic file operation for those media types and promise file delivery, not a
native player/bubble. The absence of `send_video()` and `send_voice()` is
therefore no longer an SDK gap for the accepted scope.

The project also accepted TrueConf Server's advertised attachment size as
authoritative. Commit `41d8fff` satisfies the agreed limit by rejecting
oversized metadata before download; counting streamed bytes against false
server metadata is outside the current threat model. These decisions are
recorded in ADR 0003.

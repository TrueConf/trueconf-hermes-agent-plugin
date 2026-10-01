# Stage 2 — TrueConf SDK capability matrix

Status: **Stage 2 complete**

Audited: 2026-09-05

SDK checkout: `/Users/baadzianton/TrueConf/python-trueconf-bot`

Audited development revision: `26e42a464f281f86b2f455084bf2eebbb972fd82`

Release contract: `python-trueconf-bot>=1.5.0,<2`

## Scope and method

This began as a read-only audit of the clean `python-trueconf-bot` checkout
at `afb4bde4`. Four blockers were then reproduced through public-contract
tests and fixed in the SDK at `26e42a4`. The audit is measured against the
approved observable-behavior specification and the Stage 2 checklist in
`docs/plans/0001-first-implementation.md`. Evidence is limited to tracked SDK
source, tracked SDK documentation, tracked SDK tests, and Git history in that
checkout. Historical TrueConf adapters and untracked SDK files are not used as
evidence.

The tracked suite passes at the audited revision:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider
94 passed in 1.75s
```

Status meanings:

- **Ready** — the required public behavior exists and has adequate tracked
  public-contract evidence for this audit.
- **Needs test** — the public surface is sufficient by source
  audit, but the Plugin implementation stage should add focused integration
  evidence for the behavior it consumes.
- **Blocker** — the audited SDK cannot satisfy the approved contract through
  the public SDK API alone. It must be fixed in the SDK or explicitly removed
  from Plugin 0.1.0 scope before Stage 3.

“Minimum revision” means the earliest revision that contains the specifically
identified fix when that is known. For older capabilities not re-audited at
historical commits, `afb4bde4` is deliberately recorded as the minimum
**verified** revision rather than guessing compatibility.

Post-audit live evidence on 2026-09-06 confirmed that the protocol timestamp
is a 13-digit Unix value with millisecond precision, as specified by the
official TrueConf Chatbot Connector documentation. The SDK preserves that raw
integer; conversion to Hermes' UTC `datetime` belongs to the Plugin.

## Capability matrix

| Capability | Public symbol | Source evidence | Tracked test evidence | Status | Required action | Minimum revision |
| --- | --- | --- | --- | --- | --- | --- |
| Username/password authentication on default HTTPS endpoint | `Bot.from_credentials()` | Constructor and documented credential factory are public (`trueconf/__init__.py:1-20`; `trueconf/client/bot.py:387-469`); OAuth failures may raise `InvalidGrantError` (`trueconf/utils/_token.py:12-42`). | None. | **Needs test** | Add a black-box credential-exchange test using a local HTTP(S) endpoint and the public factory. Assert successful token acquisition and typed invalid-credential behavior without reading the token or SDK internals. | `afb4bde4` verified |
| Authentication with configured `https`, `web_port`, and `timeout` | `Bot.from_credentials(..., https=, web_port=, timeout=)` | The factory now forwards the selected protocol, effective web port, and timeout to credential exchange (`trueconf/client/bot.py`). | `test_from_credentials_honors_http_and_custom_web_port` enters through the public factory and a local HTTP server. | **Ready** | No Plugin workaround. | `26e42a4` |
| Deterministic startup | `Bot.start()`; `Bot.run(handle_signals=False)` | `start()` creates one connection task after version check and is documented as repeat-safe (`trueconf/client/bot.py:2035-2055`); `run()` awaits it (`trueconf/client/bot.py:1743-1772`). | No test enters through `start()` or `run()`. | **Needs test** | Add public lifecycle tests for one start, repeated start, startup failure, and `run(handle_signals=False)`. Do not inspect `_connect_task`. | `afb4bde4` verified |
| Explicit authenticated readiness | `Bot.authorized_event`; `on_health_check`; `Bot.health_check()` | The event is public state (`trueconf/client/bot.py:203-207`); authorization sets it and emits `status="authorized"` only after the auth response (`trueconf/client/bot.py:700-715`); the pull API distinguishes connected from authorized (`trueconf/client/bot.py:951-977`). Public docs describe callback and pull models (`docs/en/learn/getting-started.md:157-230`). | None. | **Needs test** | Add a public `run()` test that observes `connected` then `authorized`, and races readiness against a failing run task. No fixed sleep and no `_connect_task` access. | `afb4bde4` verified |
| Run-loop error propagation | `Bot.run(handle_signals=False)`; `WSConnectionError` | `run()` awaits the connection task and suppresses only `CancelledError`, so other terminal exceptions propagate (`trueconf/client/bot.py:1766-1772`). Retry exhaustion raises `WSConnectionError` with the transport exception as cause (`trueconf/client/bot.py:726-758,769-791`; `trueconf/exceptions.py:1-12`). | Retry tests call private `_Bot__connect_and_listen()` directly (`tests/test_ws_retries.py:47-81`); public `run()` is untested. | **Needs test** | Verify exhaustion and auth failure at the public `run()` boundary, including the exact exception/cause contract. | `afb4bde4` verified |
| External cancellation of `run()` | `Bot.run(handle_signals=False)` | `run()` distinguishes cancellation of its caller from the connection-task cancellation used by intentional shutdown (`trueconf/client/bot.py`). | `test_external_run_cancellation_propagates` exercises public `run()` against an external WebSocket stub. | **Ready** | The Plugin may use normal task cancellation and `shutdown()`; no task-state workaround is required. | `26e42a4` |
| Graceful shutdown and handler drain | `Bot.shutdown()`; `Bot.stopped_event` | Shutdown rejects new updates, waits active update handlers, closes session/transport, clears state, and sets the public stopped event (`trueconf/client/bot.py:2057-2106`); handler draining was introduced at `b391130`, transport close at `afb4bde`. | Public `shutdown()` is invoked, but fixtures fabricate `_update_tasks`, `_session`, and `_connect_task` (`tests/test_shutdown.py:36-48,122-190`). The transport test targets internal `WebSocketSession` directly (`tests/test_shutdown.py:51-59`). | **Needs test** | Add end-to-end public tests: deliver an update through a public router, call `shutdown()`, prove the handler drains, transport closes, repeat shutdown safely, and observe `stopped_event` without manipulating private state. | `afb4bde4` (full fix set) |
| Shutdown accounts for protocol ACK work | `Bot.shutdown()` | Protocol ACK work is lifecycle-owned, removed when complete, and cancelled/joined during shutdown (`trueconf/client/bot.py`). | `test_shutdown_settles_protocol_ack_work` injects a server request through the external WebSocket boundary and observes public shutdown. | **Ready** | No Plugin task tracking for SDK protocol work. | `26e42a4` |
| Low-level reconnect and configured exhaustion | `Bot(..., ws_max_retries=, ws_max_delay=)`; `Bot.run()`; `WSConnectionError` | The loop retries connection-closed/status/network and invalid-URI failures, bounds delay, exhausts at the configured count, and resets the counter after authorization (`trueconf/client/bot.py:659-670,700-704,726-795`). Fix originated at `bf99267`. | Constructor validation uses public `Bot(...)` (`tests/test_ws_retries.py:84-91`), but exhaustion calls the private loop and fills private fields (`tests/test_ws_retries.py:21-33,47-81`). | **Needs test** | Retain the implementation test if useful, but add a public `run()` contract test for exact attempt count, terminal `WSConnectionError`, cause preservation, and reset after a successful authorization. | `bf99267` implementation; `afb4bde4` verified |
| Self-message suppression | `Bot(..., skip_self_messages=True)`; `SkipSelfMessages` | Default construction registers public middleware (`trueconf/client/bot.py:123-142,229-231`); it drops only `Message.author.id == bot.me_id` (`trueconf/middleware.py:42-78`); `me_id` is populated after authorization (`trueconf/client/bot.py:829-841`). | No dedicated tracked test. | **Needs test** | Through public `Dispatcher`/`Router` registration, prove own messages are dropped, other users pass, and `skip_self_messages=False` disables the behavior. | `afb4bde4` verified |
| Inbound text and identity/reply fields | `Router.message()`; `Message` | The public router accepts message handlers (`trueconf/dispatcher/router.py:302-305`); parser constructs `Message` with sender, chat, timestamp, message ID, edit flag, reply ID, and typed text content (`trueconf/types/parser.py:105-121`; `trueconf/types/message.py:37-135`). | No tracked parser-to-public-handler test. FSM tests manually construct `Message` and call private dispatcher methods, so they are not evidence for transport parsing (`tests/test_fsm.py:22-32` and private-seam list below). | **Needs test** | Feed a representative protocol update through a running public bot/router boundary and assert every identity field and text. Include a reply ID. | `afb4bde4` verified |
| Outbound text and native reply | `Bot.send_message(..., reply_message_id=)`; `SendMessageResponse` | Public send validates 4096 visible characters and carries parse mode/reply ID (`trueconf/client/bot.py:1834-1868`; `trueconf/methods/send_message.py:9-29`); response exposes chat/message/timestamp (`trueconf/types/responses/send_message_response.py:6-10`). | None. | **Needs test** | Add public method contract tests for plain send, reply payload, returned ID, API error, timeout, and cancellation. | `afb4bde4` verified |
| Outbound edit by exact message ID | `Bot.edit_message()`; `EditMessageResponse` | The public method validates length and sends the exact message ID (`trueconf/client/bot.py:1328-1354`; `trueconf/methods/edit_message.py:7-24`); response exposes the edited ID (`trueconf/types/responses/edit_message_response.py:6-9`). | Stage 7 Plugin contract tests enter through public Hermes `send`/`edit_message`, preserve the returned ID at the public SDK boundary, and classify not-found, forbidden, transient, unknown, oversize, and cancellation outcomes. | **Ready** | No Plugin workaround; edit by exact message ID once. A direct SDK transport-conformance test remains desirable but is not a Plugin blocker. | `26e42a4` verified at Plugin boundary |
| P2P user resolution | `Bot.create_personal_chat()`; `CreateP2PChatResponse.chat_id` | Public API creates or returns the P2P chat and exposes the real chat ID (`trueconf/client/bot.py:1159-1184`; `trueconf/types/responses/create_p2p_chat_response.py:6-8`); official SDK docs describe idempotent resolution semantics (`docs/en/learn/send_message.md:13-26`). | None. | **Needs test** | Add public tests for fully qualified opaque user IDs, returned chat ID, server error, and repeated existing-chat response. Plugin tests must separately own process-local caching. | `afb4bde4` verified |
| Chat type/title metadata for P2P, group, channel | `Bot.get_chat_by_id()`; `GetChatByIdResponse`; `ChatType` | Public lookup returns title, chat ID, and integer chat type (`trueconf/client/bot.py:1434-1450`; `trueconf/types/responses/get_chat_by_id_response.py:7-14`); public enum defines P2P/GROUP/CHANNEL and excluded types (`trueconf/enums/chat_type.py:4-17`). | None. | **Needs test** | Add response-deserialization tests for P2P, group, channel, favorites, and unknown values through `get_chat_by_id()`. Decide in SDK whether unknown future chat-type integers remain parseable instead of failing enum conversion in callers. | `afb4bde4` verified |
| Public user display-name lookup | `Bot.get_user_display_name()` | The public method returns the SDK response and preserves fully qualified IDs without rewriting them (`trueconf/client/bot.py:1601-1621`). | None. | **Needs test** | Add public success/not-found tests; Plugin fallback remains the opaque user ID. | `afb4bde4` verified |
| Inbound document | `Message.content`; `Message.document`; `Bot.download_file_by_id()` | `AttachmentContent` exposes file ID/name/size/MIME (`trueconf/types/content/attachment.py:9-24`); non-image/video/audio attachments become `Message.document` (`trueconf/types/message.py:137-159`); public download returns bytes when no destination is supplied (`trueconf/client/bot.py:1203-1260`). | Download limit is tested, but attachment parsing/classification/download success is not (`tests/test_download_limit.py:30-90`). | **Needs test** | Add one public update-to-handler test plus one public bytes-download test with metadata, MIME, filename, ready state, failure, and cancellation assertions. | `41d8fff` for bounded bytes; `afb4bde4` verified |
| Inbound image | `Message.photo`; `Bot.download_file_by_id()` | An attachment whose MIME begins `image/` maps to a bound public photo model retaining ID/name/size/MIME (`trueconf/types/message.py:161-179`; `trueconf/types/content/photo.py:8-65`). | None. | **Needs test** | Add public parser/handler and bytes-download tests using a representative image update. | `30fe2d2` for canonical `mime_type`; `afb4bde4` verified |
| Inbound video | `Message.video`; `Bot.download_file_by_id()` | An attachment whose MIME begins `video/` maps to a bound video model retaining ID/name/size/MIME (`trueconf/types/message.py:181-199`; `trueconf/types/content/video.py:8-65`). | None. | **Needs test** | Add public parser/handler and bytes-download tests using a representative video update. | `30fe2d2` for canonical `mime_type`; `afb4bde4` verified |
| Inbound native voice | `Message.voice`; `MessageType.VOICE_MESSAGE` | Voice is a protocol message type (`trueconf/enums/message_type.py:19-24`); the content factory constructs `Voice` (`trueconf/types/parser.py:33-48`); `Message.voice` exposes ID/size/MIME/duration (`trueconf/types/message.py:201-219`). | `test_native_voice_reaches_public_message_handler` proves parsing, reply/identity fields, metadata, bot binding, and delivery through public `Router.message()`. | **Ready** | Use `Message.voice`; a direct `Voice` convenience export is not required by the Plugin. | `26e42a4` public-contract evidence (`a2d4d9f` implementation) |
| Inbound generic audio file | `Message.content` (`AttachmentContent`); `Bot.download_file_by_id()` | `Message.document` intentionally excludes `audio/` (`trueconf/types/message.py:137-159`), but the public `Message.content` remains an `AttachmentContent` carrying ID/name/size/MIME (`trueconf/types/message.py:85-105`; `trueconf/types/content/attachment.py:9-24`). This is sufficient for the Plugin's accepted “generic audio becomes Hermes document” mapping without an SDK patch. | None. | **Needs test** | Add a public handler test proving an `audio/*` attachment remains accessible through `Message.content` and downloadable even though `Message.document` is `None`. | `30fe2d2` for canonical `mime_type`; `afb4bde4` verified |
| Outbound document | `FSInputFile`; `Bot.send_document()`; `SendFileResponse` | `FSInputFile` preserves/derives filename, size, and MIME (`trueconf/types/input_file.py:266-360`); document send validates, uploads, sends optional caption/reply, and returns message/file IDs (`trueconf/client/bot.py:1774-1832`; `trueconf/types/responses/send_file_response.py:6-11`). | Upload tests invoke private `_Bot__upload_file_to_server()` only (`tests/test_upload_errors.py:61-104`). | **Needs test** | Add public `send_document()` tests for payload, MIME/filename, returned IDs, validation, upload failure propagation, API failure, and cancellation. | `a254384` for typed upload failure; `30fe2d2` for `mime_type`; `afb4bde4` verified |
| Outbound image | `FSInputFile`; `InputFile.clone()`; `Bot.send_photo()` | Photo send accepts a preview, preserves caption/parse mode/reply, and uses the common typed upload path (`trueconf/client/bot.py:1901-1968`). The public file model provides an ownership-safe clone. | Stage 6 Plugin coverage proves a distinct clone with identical bytes/name/MIME is passed. Live beta-server evidence on 2026-09-07 showed `preview=None` as a generic file and the clone form as a native photo. | **Ready** | Use `file.clone()` as preview; no Plugin-owned temporary file or SDK patch. | `26e42a4` verified live |
| Outbound video as generic file | `FSInputFile(..., mime_type="video/…")`; `Bot.send_document()` | The public document method explicitly supports arbitrary file types and sends the input MIME in multipart upload (`trueconf/client/bot.py:548-646,1774-1832`); this matches the approved generic-file semantics, not native video UI. | None through the public method. | **Needs test** | Add a public `send_document()` test with video MIME and an opt-in live check that the server preserves it as a generic attachment. | `a254384`/`30fe2d2`; `afb4bde4` verified |
| Outbound voice/audio as generic file | `FSInputFile(..., mime_type="audio/…")`; `Bot.send_document()` | The same arbitrary-file operation and MIME-preserving upload support audio/voice as a generic attachment (`trueconf/client/bot.py:548-646,1774-1832`). No native voice bubble is promised. | None through the public method. | **Needs test** | Add a public `send_document()` test with audio MIME and an opt-in live generic-attachment check. | `a254384`/`30fe2d2`; `afb4bde4` verified |
| Advertised-size in-memory download limit | `Bot(..., max_in_memory_download_size=)`; `Bot.download_file_by_id()`; `InMemoryDownloadLimitExceededError` | Constructor validates a positive limit (`trueconf/client/bot.py:175-190`); download checks the latest authoritative server metadata before reading bytes (`trueconf/client/bot.py:1203-1260`); typed error exposes actual size and limit (`trueconf/exceptions.py:158-170`). Fix originated at `41d8fff`. | Public signature/default and public oversize call are covered (`tests/test_download_limit.py:30-48`); post-upload metadata is checked (`tests/test_download_limit.py:71-90`). Tests use fabricated Bot state/private wait helper, but the required pre-read rejection itself enters through the public method. | **Ready** | Keep the existing regression. A second observed-stream counter is intentionally not required because approved scope trusts authenticated TrueConf Server metadata. | `41d8fff` |
| Secret-safe attachment download logging | `Bot.download_file_by_id()` | The 1.5.0 release candidate sanitizes download URLs before all INFO/ERROR logging while retaining the complete URL for transport. | `test_download_url_logging.py` covers the sanitizer, public download path, transfer start, and failure logging; Stage 10 log capture also verifies that query and fragment secrets are absent while transport retains the complete URL. | **Ready** | Keep redaction in the SDK; do not filter or monkeypatch SDK logs in the Plugin. | 1.5.0 |
| TLS verification modes | `Bot(..., verify_ssl=)`; `Bot.from_credentials(..., verify_ssl=)` | Public API accepts `True`, `False`, CA path, or `SSLContext` (`trueconf/client/bot.py:123-177,387-438`). Context construction enables truststore verification, explicit disable, or a readable CA bundle and rejects other values (`trueconf/utils/_ssl.py:6-33`). The context is used for OAuth, WebSocket, upload, and download (`trueconf/client/bot.py:447-468,664-680,521-538,598-629`). | None. | **Needs test** | Add public tests for system trust, explicit disable, valid/missing CA path, custom context, and certificate failure during Plugin integration. | `26e42a4` (configured OAuth endpoint); TLS modes verified at `afb4bde4` |
| Parse modes and 4096 visible-character limits | `ParseMode.TEXT`; `ParseMode.MARKDOWN`; `Bot.send_message()`; `Bot.edit_message()`; `Bot.send_document()` | Public enum contains text/Markdown/HTML (`trueconf/enums/parse_mode.py:4-14`). Send/edit/caption paths call `visible_len` and raise typed 4096-limit errors (`trueconf/client/bot.py:1328-1354,1813-1816,1834-1868,1919-1922`; `trueconf/exceptions.py:49-87`). Plugin scope selects only text/Markdown. | Stage 7 Plugin tests prove exact Markdown and literal plain text reach the public SDK input unchanged; an oversize SDK rejection yields one `too_long` result without Plugin splitting or retry. Stage 6 covers caption-limit classification. | **Ready** | Keep Hermes as the only chunk owner and pass one complete value to the SDK. Direct SDK boundary-value conformance remains desirable but is not a Plugin blocker. | `26e42a4` verified at Plugin boundary |
| Numeric API error typing | `ApiErrorException`; public `Bot` request methods | All method responses with nonzero `errorCode` become `ApiErrorException` carrying numeric `code`, `detail`, and `payload` (`trueconf/methods/base.py:51-71`; `trueconf/types/responses/api_error.py:7-61`; `trueconf/exceptions.py:213-229`). This supports Plugin classification without substring matching. | None. | **Needs test** | Through public send/edit/chat methods, test representative auth, transient, forbidden, not-found, and unknown numeric codes. Verify no secret payload is logged by Plugin; it need not expose SDK payloads. | `afb4bde4` verified |
| Typed upload failures through public media sends | `Bot.send_document()`; `Bot.send_photo()`; `FileUploadError` | HTTP failures and missing `temporalFileId` are wrapped as `FileUploadError` with causes preserved (`trueconf/client/bot.py:548-646`; `trueconf/exceptions.py:232-233`). Fix originated at `a254384`. | All upload tests call private `_Bot__upload_file_to_server()` (`tests/test_upload_errors.py:77-104`), so propagation through public sends is unproven. | **Needs test** | Add public `send_document()` and `send_photo()` regression tests for HTTP failure and missing temporary ID. | `a254384` implementation; `afb4bde4` verified |
| Transport-send failure typing | Public request methods, including `Bot.send_message()` | WebSocket send failure now raises public `WSConnectionError` with the transport exception preserved as its cause; request futures are discarded on every terminal path (`trueconf/client/bot.py`; `trueconf/methods/base.py`). | `test_public_send_preserves_transport_failure` enters through public `send_message()`. | **Ready** | Classify `WSConnectionError`; no timeout or string heuristic. | `26e42a4` |

## Blocker resolution

All four public-contract blockers found at `afb4bde4` are fixed in SDK revision
`26e42a4`. The Plugin can now use configured credential endpoints, ordinary
async cancellation, lifecycle-owned SDK shutdown, and typed transport-send
failures without private access or error-string heuristics.

## Tracked-test private-seam audit

The test count is real, but the recent fixes are mostly implementation tests,
not public-contract tests. They may remain useful as low-level tests; they do
not by themselves close the Stage 2 gate.

### `tests/test_download_limit.py`

- bypasses construction with `object.__new__(Bot)` at lines 36, 52, and 72;
- patches name-mangled `_Bot__download_file_from_server` at line 65;
- patches name-mangled `_Bot__wait_upload_complete` at line 87;
- patching public `get_file_info()` at lines 42, 64, and 86 is an acceptable
  boundary, but the fabricated object still skips constructor invariants.

### `tests/test_upload_errors.py`

- bypasses construction and manually fills `_protocol` at lines 61-68;
- patches the internal module's `ClientSession` and `Bot.__call__` at lines
  73, 79, 89, and 99;
- calls `_Bot__upload_file_to_server()` directly at lines 81, 92, and 102;
- consequently does not prove error propagation through public
  `send_document()` or `send_photo()`.

### `tests/test_ws_retries.py`

- bypasses construction and fills `_ws_max_retries`, `_ws_max_delay`, `_stop`,
  `_session`, and `_ws` at lines 21-33;
- patches implementation details `websockets.connect`, `asyncio.sleep`, and
  module-local `random.uniform` at lines 70-72;
- calls `_Bot__connect_and_listen()` directly at line 75;
- patches module-private `_validate_token` at line 85;
- consequently does not prove exhaustion at public `run()`.

### `tests/test_shutdown.py`

- imports internal `trueconf.client.session.WebSocketSession` at line 8;
- bypasses construction and fills private lifecycle fields at lines 36-48;
- patches/calls `_Bot__process_message` and `_Bot__on_raw_message` at lines
  62-100;
- reads or writes `_update_tasks`, `_accept_updates`, `_session`, and
  `_connect_task` throughout lines 76-179;
- calls private router `_register()`/`_feed()` at lines 108 and 113;
- public `shutdown()` assertions at lines 122-190 are behaviorally useful, but
  they do not prove the full public ingress-to-shutdown contract.

### `tests/test_fsm.py`

FSM is outside this Plugin audit, but the tracked suite also relies on private
FSM/dispatcher seams: `State._group`, `Dispatcher._outer_middlewares`, repeated
`Dispatcher._feed_update()` calls, `StateFilter._states`, and the custom
`__states__` attribute (notably lines 64-87, 378-489, 626-684, and 822-893).
These tests must not be cited as proof of the Plugin's public inbound-message
contract.

## Stage 2 gate result

- The capability matrix is stored in the Plugin repository.
- Every SDK blocker identified by the audit is fixed and verified through a
  public-contract regression test.
- The Plugin's supported SDK range is `>=1.5.0,<2`.
- Remaining rows marked **Needs test** identify Stage 4–7 Plugin
  integration coverage, not missing SDK behavior and not permission to use
  private SDK APIs.

Stage 2 is complete. Current delivery progress is tracked in `CONTEXT.md`.

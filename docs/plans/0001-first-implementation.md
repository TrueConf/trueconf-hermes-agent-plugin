# TrueConf Plugin 0.1.0 — First Implementation Plan

Status: approved on 2026-09-04.

The Stage 7 output-format decision was superseded on 2026-09-07 by
[ADR 0004](../adr/0004-use-native-trueconf-html.md).

## Objective

Deliver a standalone TrueConf platform plugin for Hermes Agent v0.21.0 using
only the public Hermes plugin API and the clean public `python-trueconf-bot`
API. Version 0.1.0 includes text messaging, the principal TrueConf chat types,
media, home-channel routing, and detached cron delivery.

Historical adapters are requirements evidence, not an implementation
baseline. Do not copy old code without reviewing the specific fragment and
obtaining explicit user approval.

## Non-negotiable constraints

- Do not modify files under the Hermes Core reference checkout.
- Do not add TrueConf-specific branches, enum members, or imports to Hermes
  Core.
- Do not monkeypatch Hermes or the TrueConf SDK.
- Do not vendor `python-trueconf-bot` or anything from historical
  `lib_patches/` directories.
- Implement missing protocol behavior in the `python-trueconf-bot` repository
  with SDK-level tests before consuming it from the Plugin.
- Use no private Hermes APIs.
- Do not implement a second reconnect loop, authorization policy, or
  long-message orchestration layer.
- Do not use fixed sleeps to infer connection readiness.
- Do not use the historical regular-expression Markdown-to-HTML converter.

## Target capabilities

Version 0.1.0 must provide:

- standalone platform-plugin discovery and registration;
- explicit SDK dependency diagnostics;
- configuration of server, username, password, home channel, authorization,
  SSL verification, parse mode, and mention behavior;
- inbound and outbound text in P2P, group, and channel conversations;
- stable sender, chat, message, and reply identity mapping;
- P2P target resolution using the public SDK;
- images, documents, video, and voice in both supported directions;
- message editing through the Hermes adapter contract;
- home-channel and out-of-process cron delivery;
- deterministic connection startup, failure reporting, reconnection ownership,
  and shutdown;
- automated tests that do not require a TrueConf server;
- opt-in smoke tests against a real TrueConf Server.

Stickers are included only if the SDK audit confirms a real supported scenario
and the user approves adding them.

## Stage 1 — Specify observable behavior

Create an architecture/specification document before implementation. Define:

- user scenarios and expected outcomes;
- Hermes Core, Plugin, and TrueConf SDK ownership boundaries;
- supported incoming and outgoing event types;
- identity mapping for users, chats, messages, and replies;
- connection state transitions and failure behavior;
- configuration precedence and validation;
- authorization timing, especially before media downloads;
- temporary-file ownership and cleanup;
- explicit exclusions for version 0.1.0.

### Gate

- The user has approved the behavior specification.
- Every capability has one named owner: Hermes, Plugin, or SDK.
- No required behavior depends on historical implementation details.

## Stage 2 — Audit the clean TrueConf SDK

Build a capability matrix against the public API of a clean
`python-trueconf-bot` checkout:

- authentication and connection startup;
- an explicit ready/authenticated signal;
- event-loop termination and error propagation;
- graceful shutdown;
- transient reconnect behavior and retry exhaustion;
- self-message identification;
- text send/receive, replies, and edits;
- P2P chat creation or resolution;
- group and channel metadata;
- upload, send, download, and metadata for each media type;
- SSL verification behavior;
- parse modes and message-length limits.

For each missing or defective capability:

1. Reproduce it in a minimal SDK test.
2. Agree on the public SDK behavior.
3. Implement and verify the change in the SDK repository.
4. Record the minimum SDK version or revision required by the Plugin.

### Gate

- The capability matrix is committed to project documentation.
- All blockers are either fixed in the clean SDK or explicitly removed from
  the 0.1.0 scope with user approval.
- The Plugin requires no patched SDK files or private SDK access.

## Stage 3 — Establish the plugin boundary

Create only the minimum plugin surface needed for Hermes discovery:

- manifest;
- registration entry point;
- dependency probe and installation guidance;
- platform registration metadata;
- configuration schema/bridges supported by the public API;
- dynamic `Platform("trueconf")` usage;
- explicit target parser for TrueConf identifiers.

The module must remain importable when `python-trueconf-bot` is absent. The
passive dependency probe must not install anything or require credentials.

### Contract tests

- clean Hermes v0.21.0 discovers the Plugin;
- `register(ctx)` registers exactly one `trueconf` platform;
- all registration arguments use documented public hooks;
- missing SDK produces a clear unavailable state and installation hint;
- imports cause no filesystem, network, logging-handler, or package-install
  side effects;
- no tracked file under the Hermes reference checkout changes.

### Gate

Hermes can list and validate the Plugin without connecting to TrueConf.

## Stage 4 — Implement the text-only vertical slice

Implement:

- configuration validation;
- one explicit connection lifecycle;
- deterministic readiness and shutdown;
- incoming P2P text to `MessageEvent`;
- outgoing text to `SendResult`;
- sender/chat/message identity mapping;
- early use of the Hermes-owned authorization seam;
- cleanup of every background task.

Do not implement media, editing, standalone delivery, HTML conversion, or
plugin-owned retry policy in this stage.

### Tests

- valid and invalid configuration;
- successful startup and readiness failure;
- authentication failure;
- inbound text mapping;
- outbound success, API failure, and cancellation;
- unauthorized inbound messages cause no downstream side effects;
- shutdown is idempotent and leaves no tasks running;
- SDK loop exit changes adapter state correctly.

### Gate

- Unit and contract tests pass.
- Opt-in live test completes connect → receive text → send text → disconnect.
- Hermes Core remains unchanged.

## Stage 5 — Add chat types, replies, and target resolution

Add P2P, group, and channel mapping; display names; reply IDs; self-message
suppression; explicit TrueConf target parsing; and P2P creation/resolution.
Add mention behavior only through a boundary consistent with Hermes' public
contract.

### Tests

- each supported chat type maps to the correct Hermes chat type;
- opaque TrueConf chat IDs remain case-preserving;
- reply metadata survives both directions;
- self-originated events are ignored without hiding other bot-session events;
- exact TrueConf chat IDs are passed without Plugin-owned user resolution, as
  superseded by ADR 0005;
- mention behavior covers direct, group, and channel cases.

### Gate

Text messaging works in every target chat type without core special cases.

## Stage 6 — Add media one type at a time

Implement in this order:

1. Documents.
2. Images.
3. Video.
4. Voice messages.

For each type, complete inbound mapping and download, outbound upload and send,
size/error handling, cancellation, and temporary-file cleanup before starting
the next type. Authorization must run before downloading untrusted content.

### Tests for every type

- valid inbound attachment;
- missing or malformed metadata;
- download failure and size rejection;
- valid outbound local file;
- upload/send failure;
- cancellation;
- temporary files removed on success and every failure path;
- MIME type and filename preserved when available.

### Gate

All four media types pass isolated tests and an opt-in live send/receive smoke
test supported by the available server.

## Stage 7 — Add editing and formatting

Implement the public Hermes `edit_message` contract and explicit message-ID
mapping. The original Markdown-default decision is retained here as historical
plan context and superseded by ADR 0004; native HTML now requires no Plugin
renderer because Hermes emits the documented subset and SDK `LineBreak`
provides `<br>`.

Hermes remains the primary owner of response chunking. The adapter must not
silently truncate, duplicate, or recursively re-split already routed chunks.

### Tests

- send then edit using the returned message ID;
- edit failure and deleted-message behavior;
- Markdown reaches the SDK without regex conversion;
- plain text preserves literal markup characters;
- oversized content follows one documented orchestration path;
- no emoji-based classification or unbounded message accumulation exists.

### Gate

Editing and formatting behavior is deterministic and independent of message
content heuristics.

## Stage 8 — Add home-channel and cron delivery

Register and implement the public hooks needed for:

- environment-driven enablement;
- YAML configuration bridging;
- `TRUECONF_HOME_CHANNEL`;
- cron target discovery;
- out-of-process standalone delivery.

Standalone delivery must wait for an explicit SDK readiness signal and close
the SDK connection in every outcome. It must accept the standard Hermes media
arguments even if a particular media behavior is explicitly unsupported.

### Tests

- env-only credentials enable the platform;
- YAML behavior settings preserve documented precedence;
- home-channel routing produces the correct target;
- standalone text delivery succeeds and disconnects;
- startup/auth/send failures disconnect and return structured errors;
- cancellation disconnects cleanly;
- no fixed readiness sleeps are used;
- supported standalone media behavior is explicit and tested.

### Gate

Detached cron delivery works without a live gateway adapter and leaks no
connections or tasks.

## Stage 9 — Add setup and operator documentation

Document and, where appropriate, implement:

- explicit SDK installation;
- server, username, and masked password setup;
- allowlist and allow-all behavior;
- home-channel selection;
- `config.yaml` settings for SSL, parse mode, and mention behavior;
- a strong warning for disabled TLS certificate verification;
- migration from historical integrations without patched files;
- supported features and known limitations;
- troubleshooting and log locations.

Do not recommend storing the password in `config.yaml`. Do not present
historical parse-mode or SSL environment variables as the primary interface.

### Gate

A new operator can install, configure, verify, and remove the Plugin using only
the repository documentation.

## Stage 10 — Release verification

Run and record:

- unit tests;
- plugin-registration contract tests;
- integration tests against a clean Hermes v0.21.0 checkout;
- import and status behavior without the SDK installed;
- tests against a clean supported SDK installation;
- linting and type checking;
- background-task and temporary-file cleanup tests;
- network-loss/retry-exhaustion tests;
- opt-in live TrueConf smoke tests;
- a repository check proving reference directories and Hermes Core changes are
  absent from the commit.

Review imports for private Hermes or SDK symbols and review every fragment
derived from historical code with the user before release.

### Definition of done

- All 0.1.0 target capabilities pass their automated acceptance tests.
- Live text and supported media smoke tests pass on a real TrueConf Server.
- The Plugin works with an explicitly recorded clean SDK version/revision.
- Hermes Agent v0.21.0 requires no modifications.
- No monkeypatches, vendored SDK files, fixed readiness sleeps, duplicate
  lifecycle loops, duplicate access policy, or regex Markdown-to-HTML
  conversion remain.
- Documentation accurately describes installation, configuration, security,
  capabilities, and limitations.

## Deferred work

- PyPI publication;
- publication under the TrueConf organization or a plugin catalog;
- an upstream Hermes Agent pull request;
- automatic SDK installation;
- broad compatibility with historical environment-variable contracts;
- stickers without an approved use case;
- support for Hermes versions outside the tested compatibility range.

## Instructions for the next session

1. Read `CONTEXT.md`, ADR 0001, ADR 0002, and this plan before acting.
2. Do not start implementation until the Stage 1 behavior specification is
   written and approved by the user.
3. Treat the reference directories as read-only evidence.
4. Stop at every stage gate and report evidence before advancing.
5. Record newly accepted architectural decisions in an ADR and link them from
   `CONTEXT.md` immediately.

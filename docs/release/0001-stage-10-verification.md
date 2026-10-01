# Stage 10 release verification

Date: 2026-09-07

Release candidate: TrueConf Plugin 0.1.0

Status: **complete**

This record verifies the Plugin from an isolated temporary Python environment.
Hermes Agent was installed from the local v0.21.0 release checkout, and the
clean tracked `python-trueconf-bot` release candidate was built with version
metadata `1.5.0`. No dependency environment was created inside either
reference checkout.

## Automated verification

| Gate | Result | Evidence |
|---|---|---|
| Plugin unit, registration-contract, integration, lifecycle, media, cleanup, and guard tests | Pass | `pytest tests -q -p no:cacheprovider`: 64 passed |
| Hermes compatibility | Pass | Installed distribution reports `hermes-agent 0.21.0`; registration tests exercise the clean checkout |
| Plugin Doctor | Pass | `hermes plugins doctor . --ci`: runtime discovery, manifest parsing, import, and registration passed |
| Real environment without the SDK | Pass | Discovery keeps `trueconf` out of `sys.modules`, reports unavailable, and returns the 1.5.0 installation hint |
| Outdated SDK behavior | Pass | A simulated installed 1.4.9 is rejected without importing it; the diagnostic requires `>=1.5.0,<2` |
| Supported SDK behavior | Pass | Installed 1.5.0 is accepted without importing `trueconf` during the passive probe |
| Next-major SDK behavior | Pass | A simulated installed 2.0.0 is rejected until its compatibility is verified |
| Clean supported SDK installation | Pass | Installed candidate distribution reports `python-trueconf-bot 1.5.0` |
| Complete SDK test suite | Pass | 103 SDK tests passed, including lifecycle, retry exhaustion, handler drain, download limits, and URL-log redaction |
| Secret-safe SDK download logging | Pass | Committed SDK regression tests cover sanitizer, public download, transfer-start, and error logs; an independent Stage 10 capture contains the URL origin/path but no query, fragment, parameter name, or test secret, while transport receives the complete URL |
| Lint | Pass | Ruff 0.15.10 reports all checks passed for Plugin source, scripts, and tests |
| Production type check | Pass | ty 0.0.21 reports no diagnostics for `__init__.py` and `adapter.py` with the Hermes checkout on its search path; unresolved-import reporting is disabled only for the Plugin's directory-loaded relative module |
| Live smoke guard | Pass | Both live scripts import successfully and refuse network activity without their explicit opt-in variables |

The 64-test Plugin suite includes cancellation and cleanup coverage for
connection creation, readiness failure, disconnect, standalone delivery,
message editing, document/image/video/voice transfer, cached media, and SDK
loop exit. The complete SDK suite covers bounded websocket retry behavior,
retry exhaustion, and graceful handler drain. Neither suite requires a
TrueConf Server.

## Source and repository audit

- Production code imports documented Hermes platform, status, configuration,
  authorization, and media contracts. It imports only public `trueconf`
  package exports, enums, exceptions, and types; no leading-underscore Hermes
  or SDK module is imported.
- Production code contains no fixed readiness sleep, monkeypatch, vendored SDK
  path, `lib_patches` reference, regex Markdown-to-HTML converter, or
  Plugin-owned retry loop. Hermes remains the authorization and chunking owner;
  the SDK remains the reconnect owner.
- `git ls-files` contains no Hermes reference checkout, historical adapter,
  patched SDK, or `lib_patches` directory. The ignored Hermes checkout had one
  pre-existing contributor-email file modification before verification; its
  state did not change during this stage and it is not part of the Plugin
  repository.
- No source fragment was identified as copied from the historical adapters.
  Those trees remain ignored requirements evidence and are absent from the
  release diff.

## Live verification acceptance

Live text passed on 2026-09-06, and document, image, video, and voice round
trips passed on 2026-09-07. No `TRUECONF_*` credentials were present for a
second Stage 10 run. On 2026-09-07, the user explicitly accepted the recorded
live results as sufficient release-gate evidence and directed that Stage 10 be
treated as complete. Repeating both smoke scripts against the published SDK
1.5.0 remains a recommended operator release check.

## Release decision

The Plugin's automated release matrix passes against the SDK 1.5.0 candidate,
including the SDK-owned regressions and an independent verification of the
corrected URL logging behavior. Together with the accepted live evidence, this
closes Stage 10. Release artifacts must declare the supported SDK range
`>=1.5.0,<2` rather than a source revision.

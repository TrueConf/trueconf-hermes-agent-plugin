---
status: accepted
---

# Keep the TrueConf integration outside Hermes Core

The TrueConf integration will be developed in this standalone repository
against Hermes Agent v0.21.0 and its public plugin API. The plugin must not
modify or monkeypatch Hermes Core, because coupling to internal implementation
details would make ordinary Hermes upgrades unsafe and would recreate the
maintenance burden of the previous installer-based integration.

The plugin will depend on the clean `python-trueconf-bot` SDK. Patched or
vendored SDK files, including the historical `lib_patches/`, are prohibited.
When the public SDK lacks required behavior, that behavior will be designed,
tested, and implemented in the SDK repository itself before the plugin relies
on it. This keeps TrueConf protocol responsibilities in their owning project
and prevents the plugin from accumulating transport-level workarounds.

Historical Hermes forks and adapters are evidence for user scenarios, not an
approved implementation baseline. No old code is transferred until it has
been reviewed and explicitly approved. Publication under the TrueConf
organization, PyPI distribution, and a possible upstream Hermes contribution
remain future work and do not shape the first implementation beyond preserving
a clean standalone boundary.

## Consequences

- Compatibility is protected through the public Hermes API and contract tests,
  rather than by editing the core for TrueConf-specific cases.
- SDK gaps may block plugin features until corresponding SDK changes are
  accepted and available.
- Local reference trees and research handoffs remain untracked development
  inputs and are excluded from this repository.

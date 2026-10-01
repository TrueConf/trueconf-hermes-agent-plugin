# Inline-button integration

## Goal

Use the public TrueConf SDK 1.5 inline-keyboard contract to give the existing
Hermes interactive flows a native TrueConf UI without adding a new Plugin
interface or changing Hermes Core.

## Reference behavior

The Telegram Platform Adapter provides three relevant Hermes interfaces:

1. `send_clarify` renders choices and an `Other` action, then resolves through
   `tools.clarify_gateway`.
2. `send_exec_approval` renders the allowed approval choices, then resolves
   through `tools.approval`.
3. `send_slash_confirm` renders once, always, and cancel, then resolves through
   `tools.slash_confirm`.

Telegram's callback payload syntax and platform limits are implementation
details and are not copied. TrueConf uses a single ASCII command plus
`custom_data`, and the SDK's callback-query router.

## Design

The Platform Adapter remains the seam. Its public surface is the three Hermes
methods above; keyboard construction, compact callback routing, state lookup,
recipient binding, and TrueConf acknowledgement stay behind that interface.

- The callback state is bounded and scoped by prompt kind and request ID.
- Every callback must originate from the chat that received the prompt.
- Buttons carry no TrueConf `to` recipient: `to` is the command's delivery
  target, not a clicker restriction, and setting it to the prompt author
  redirects the click away from the bot. Same-chat binding and authorization
  stay in the callback handler.
- Clarify buttons carry indexes, not answer text. This keeps callback data
  compact and makes long or non-ASCII answers safe.
- Open-ended clarifications and prompts above the TrueConf 64-button limit use
  Hermes' existing text fallback.
- A failed native send returns a failed `SendResult`, allowing Hermes to use
  its established text fallback for approval and confirmation flows.
- Unknown, malformed, cross-chat, duplicate, and expired callbacks do not
  resolve a Hermes wait.

The general `send` interface deliberately does not accept arbitrary keyboard
configuration. Hermes owns the interaction lifecycle; exposing raw buttons
there would create a shallow pass-through interface and leave callback state,
authorization, expiry, and resolution to every caller.

## Verification

- SDK keyboard serialization is asserted through the adapter's public
  `send_clarify` interface.
- A selected clarify option resolves to the original answer text.
- An approval callback from a different chat is rejected.
- The complete Plugin test suite and lint checks pass with SDK 1.5.0.

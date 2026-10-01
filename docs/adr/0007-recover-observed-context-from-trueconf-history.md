---
status: accepted
---

# Recover observed context from anchored TrueConf history

The Plugin recovers background group and channel context only when an
authorized message explicitly invokes the agent. It makes one public
`get_chat_history` request anchored at that trigger and supplies the resulting
read-only block through Hermes' public `MessageEvent.channel_context` field.
This stateless design survives gateway restarts and avoids both a Plugin-owned
buffer and the Telegram-specific observed-transcript marker in Hermes Core.

The history window defaults to 20 records and may be configured from 0 through
100, where 0 disables recovery. Within that window, the most recent authorized
message that mentions the agent is the lower boundary: that message and all
older records are excluded. The Plugin also excludes the trigger, its own
messages, other mentioned messages, non-text messages, empty messages, and
messages from unauthorized senders. Remaining HTML is preserved, each message
is limited to 1,000 characters, and the complete context block is limited to
12,000 characters by discarding the oldest excess records first.

## Consequences

- Background messages never start an agent turn and are not persisted by the
  Plugin; they are recovered afresh for each addressed turn.
- History recovery requires `require_mention: true`, an explicit matching
  `allowed_chats` entry, and a chat outside `free_response_chats`. Those rules
  ensure that only messages the normal intake path skipped are recovered.
- A failed history request never drops the authorized trigger; the event is
  delivered without `channel_context`. Cancellation still propagates.
- TrueConf's `(box.id, box.position)` order is normalized to chronological
  order before boundaries and limits are applied.

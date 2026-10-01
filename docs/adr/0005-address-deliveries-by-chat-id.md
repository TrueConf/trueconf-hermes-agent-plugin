---
status: accepted
---

# Address TrueConf deliveries by chat ID

TrueConf sends to an existing `chat_id` regardless of whether the server
classifies that chat as P2P, group, or channel. The Plugin therefore accepts
and preserves one concrete chat ID across inbound sources, direct sends, home
channels, `/sethome`, and cron delivery; it does not add `user:` or `chat:`
prefixes, resolve user IDs, create P2P chats, or cache those resolutions. The
public SDK `Message.chat` entity supplies the inbound chat ID, title, and type.

## Consequences

- Hermes' external target form is `trueconf:<chat-id>`.
- Operators must provide an existing chat ID for explicit and scheduled sends.
- A home channel captured by Hermes can round-trip without Plugin-specific
  rewriting.
- This supersedes the typed-target and user-resolution decisions in the
  Stage 1 specification and implementation records.

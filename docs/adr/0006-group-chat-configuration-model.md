---
status: accepted
---

# Separate chat scope, chat trust, sender access, and message triggering

The TrueConf Plugin will use the established Hermes configuration names while
giving each one a single responsibility. `allowed_chats` limits the group and
channel chats in which the agent operates; `group_allowed_chats` grants access
to an entire group or channel; and `group_allow_from` grants access to
individual senders in groups and channels. Direct-message access remains under
`allow_from`. A group or channel message must be inside the configured chat
scope and must be authorized either by trusted-chat membership or by its
sender.

Message triggering is a separate decision. `require_mention` controls the
default group/channel mention requirement, `free_response_chats` removes only
that requirement for listed chats, and
`observe_unmentioned_group_messages` may retain otherwise untriggered messages
as context without treating them as requests. Neither free-response nor
observation bypasses chat scope or authorization. Direct P2P messages are not
subject to group/channel chat scope or mention rules.

This model deliberately follows shared Hermes names where Hermes already owns
authorization, but it does not treat Telegram's implementation as the Plugin's
architecture. The Plugin owns TrueConf-specific intake and trigger behavior,
and must use only public Hermes extension points.

## Consequences

- `allowed_chats` and `group_allowed_chats` may contain the same chat ID, but
  they are not synonyms: the former selects where the agent operates and the
  latter authorizes every sender in that chat.
- A chat admitted by `allowed_chats` still needs either a matching
  `group_allowed_chats` entry or an authorized sender in `group_allow_from`.
- A chat in `free_response_chats` accepts authorized messages without a native
  mention; it does not become trusted merely by appearing in that list.
- Observed messages are context only. They do not start an agent turn and must
  remain subject to the same chat-scope and authorization boundaries.
- The approved configuration model is recorded before implementation; support
  must not be advertised as complete until adapter behavior and contract tests
  exist.

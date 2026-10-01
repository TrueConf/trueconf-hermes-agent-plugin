<p align="center">
  <a href="https://trueconf.ru" target="_blank" rel="noopener noreferrer">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/TrueConf/.github/refs/heads/main/logos/logo-dark.svg">
      <img width="150" alt="trueconf" src="https://raw.githubusercontent.com/TrueConf/.github/refs/heads/main/logos/logo.svg">
    </picture>
  </a>
</p>

<h1 align="center">trueconf-hermes-agent-plugin</h1>

<p align="center">
    <a href="https://t.me/trueconf_chat" target="_blank">
        <img src="https://img.shields.io/badge/Telegram-2CA5E0?logo=telegram&logoColor=white" />
    </a>
    <a href="https://discord.gg/2gJ4VUqATZ">
        <img src="https://img.shields.io/badge/Discord-%235865F2.svg?&logo=discord&logoColor=white" />
    </a>
    <a href="#">
        <img src="https://img.shields.io/github/stars/trueconf/trueconf-hermes-agent-plugin?style=social" />
    </a>
</p>

<p align="center">
  <img src="assets/poster.png" alt="" width="600" height="auto">
</p>

<p align="center">
  <a href="./README.md">English</a> /
  <a href="./README-ru.md">Русский</a>
</p>

> [!CAUTION]
> This plugin provides a gateway interface for interacting with Hermes Agent. TrueConf does not encourage the use of Hermes Agent, recommend deploying it in corporate infrastructure, or accept responsibility for the operation of Hermes Agent, associated AI agents, external services, or third-party components.
>
> Users install the plugin and connect to Hermes Agent independently and at their own risk. Before deploying it on a corporate network, review the information security risks, including data leaks, unauthorized access, prompt injection, unwanted command execution, compromised API keys, and unexpected AI agent behavior:
>
> - [AI Agent or Threat? A Chronicle of OpenClaw (In)security](https://xakep.ru/2026/05/05/openclaw-exploits/)
> - [OpenClaw Threats: Risk Assessment and Responding to “Shadow AI”](https://www.kaspersky.ru/blog/moltbot-enterprise-risk-management/41329/)
>
> Use this plugin only after assessing the risks, testing in an isolated environment, restricting access, configuring monitoring, and taking measures to protect corporate infrastructure.

The TrueConf plugin lets you interact with Hermes Agent directly from TrueConf:
ask questions in a direct chat, add the agent to team groups, and receive
scheduled task results. It supports text, images, file attachments, and voice
messages. When speech recognition is enabled, Hermes transcribes voice
messages using local `faster-whisper`.[^voice]

This guide covers installing and configuring **plugin 1.0.0**. The plugin
targets **Hermes Agent v0.21.0**; compatibility with other Hermes versions
has not yet been tested.

## Contents

- [Before you begin](#before-you-begin)
- [Installation and first run](#installation-and-first-run)
- [Talking to the agent](#talking-to-the-agent)
- [Who can access the agent](#who-can-access-the-agent)
- [Groups and channels](#groups-and-channels)
- [Notifications and scheduled tasks](#notifications-and-scheduled-tasks)
- [Connection settings](#connection-settings)
- [Features and limitations](#features-and-limitations)
- [Troubleshooting](#troubleshooting)
- [Migrate from a historical TrueConf integration](#migrate-from-a-historical-trueconf-integration)
- [Disabling and removing the plugin](#disabling-and-removing-the-plugin)
- [For developers](#for-developers)

## Before you begin

You will need:

- Hermes Agent v0.21.0, installed and configured. Make sure it already responds
  to requests in the terminal: the plugin connects TrueConf to your existing agent.
- A TrueConf Server reachable from the computer or server running Hermes.
- A dedicated TrueConf bot account and its password. If you cannot create
  accounts, ask your TrueConf administrator to prepare one.
- The TrueConf Server address, such as `video.example.com`.
- Your exact TrueConf user ID, which you will need to grant access.
  This is the account identifier, not the display name.
- Git on the computer running Hermes, to install the plugin from its repository.

> [!NOTE]
> Run the commands in this guide in a terminal on the computer running Hermes.
> Do not send them to the bot in TrueConf.

The connection to TrueConf uses `python-trueconf-bot` version `>=1.5.0,<2`.
Hermes normally installs the required dependencies automatically when the
gateway starts.[^dependencies]

## Installation and first run

### 1. Install the plugin

```bash
hermes plugins install https://github.com/TrueConf/trueconf-hermes-agent-plugin.git --enable
```

The installer asks for the connection settings:

| Setting | What to enter |
|---|---|
| `TRUECONF_SERVER` | The server hostname or IP address, such as `video.example.com`, without `https://` or a path. |
| `TRUECONF_USERNAME` | The bot account name. |
| `TRUECONF_PASSWORD` | The bot password. Input is masked. |
| `TRUECONF_VERIFY_SSL` | `true` to verify the server certificate; this is the recommended option.[^tls] |

> [!CAUTION]
> The password is saved in your Hermes profile's secret environment file,
> normally `~/.hermes/.env`. Do not put it in `config.yaml`, terminal commands,
> or repository files.

### 2. Configure access with the setup wizard

```bash
hermes gateway setup
```

Select **TrueConf**. The wizard shows the saved connection settings and lets
you configure certificate verification, user access, and the home chat for
notifications. If it asks whether to reconfigure TrueConf, choose yes.

For your first run:

1. Check the server address and bot account.
2. Keep certificate verification enabled.
3. Answer **no** when asked whether to allow all users.
4. Enter your exact TrueConf user ID in the allowed users list.
   Separate multiple IDs with commas and preserve their case.
5. You can leave the home chat empty and configure it later.

> [!IMPORTANT]
> The wizard saves settings in the active Hermes profile. If you use a named
> profile, run all commands with that profile.[^profiles]

### 3. Start the gateway

The gateway is the Hermes process that receives TrueConf messages and delivers
the agent's replies. It must keep running while you use the bot.

```bash
hermes gateway restart
hermes gateway status
```

After starting it, check the plugin:

```bash
hermes plugins doctor trueconf-platform --ci
```

If the check reports missing dependencies, see [Troubleshooting](#troubleshooting).

### 4. Send your first message

Open a direct chat with the bot account in TrueConf and send a message such as:
“Hi! What can you help me with?”

If the bot replies, the connection is working. You can now add it to a group
or configure delivery of scheduled task results.

## Talking to the agent

**In a direct chat**, send the bot ordinary messages. No mention is required.

**In a group or channel**, you must mention the bot using TrueConf's mention
feature by default. Select it in the mention interface: simply typing its name
does not count as a mention. Access to the agent must also be enabled in the settings.

When Hermes asks a follow-up question or requests confirmation:

- In a direct chat, choose a button below the message.
- In a group or channel, reply with the number of the proposed option.
- If Hermes asks for a text answer, follow the instructions in the message.

Groups use text replies so that the sender who confirmed the action can be
identified and checked.[^buttons]

Long replies arrive as multiple messages. By default, the first reply is
anchored to your request, and continuations form a chain. You can change this
with `reply_to_mode`.[^replies]

## Who can access the agent

The bot has access to your Hermes capabilities, so grant access only to the
people who need it. Unknown senders are denied access by default. If the user
list is empty, you must approve a pairing request in Hermes or configure
access explicitly.

For a simple setup, use the user list in the setup wizard. It is saved as
`TRUECONF_ALLOWED_USERS` and applies to direct chats, groups, and channels.

For separate access rules, use `~/.hermes/config.yaml`:

```yaml
platforms:
  trueconf:
    # Users who may contact the agent in direct chats.
    allow_from:
      - alice@example.com

    # Users who may invoke the agent in groups and channels.
    group_allow_from:
      - moderator@example.com
```

Replace the example values with exact TrueConf user IDs.

> [!TIP]
> If the file already has `platforms:` and `trueconf:` blocks, add the settings
> to the existing block. Do not create another block with the same name;
> use spaces for indentation.

Choose one source for user lists: the wizard with `TRUECONF_ALLOWED_USERS`,
or YAML. When switching to YAML, remove `TRUECONF_ALLOWED_USERS` from the
active profile's `.env` file so that it does not override your chosen rules.

> [!WARNING]
> `TRUECONF_ALLOW_ALL_USERS=true` grants access to every TrueConf user who can
> contact the bot and takes precedence over user lists. Leave this option
> disabled for a personal or work agent.

After changing access settings, restart the gateway:

```bash
hermes gateway restart
```

## Groups and channels

These settings use TrueConf chat IDs (`chat_id`), not group names. Obtain the
ID from TrueConf data or ask your administrator for help. The values
`agent-room-id` and `team-chat-id` below are placeholders; replace them with
real chat IDs.

The examples assume access is configured through YAML. If you previously
configured access in the wizard, first align the configuration sources as
described in [Who can access the agent](#who-can-access-the-agent).

### A dedicated chat for the agent

In a chat created specifically for the agent, you can let every participant
contact it without a mention:

```yaml
platforms:
  trueconf:
    allowed_chats:
      - agent-room-id
    group_allowed_chats:
      - agent-room-id
    require_mention: true
    free_response_chats:
      - agent-room-id
```

Every supported participant message in this chat invokes the agent.
Other groups and channels still require a mention.

### A team group: reply when mentioned and use conversation context

To have the agent reply only when addressed while still considering recent
group messages, use:

```yaml
platforms:
  trueconf:
    allowed_chats:
      - team-chat-id
    group_allowed_chats:
      - team-chat-id
    require_mention: true
    observe_unmentioned_group_messages: true
    observe_context_limit: 20
```

Ordinary messages do not trigger a reply. When a participant mentions the
agent, it receives up to 20 eligible preceding records from after the previous
request. History is retrieved when the agent is addressed; the plugin does
not maintain a separate archive of the background conversation.[^context]

In this example, every participant in `team-chat-id` has access. To allow
only selected people, remove `group_allowed_chats` and add their IDs to
`group_allow_from`.

### What the settings mean

| Setting | Purpose |
|---|---|
| `allowed_chats` | Limits the agent to the listed groups and channels. It does not grant their participants access by itself. |
| `group_allowed_chats` | Grants access to every participant in the listed groups and channels. |
| `group_allow_from` | Grants individual users access in groups and channels. |
| `require_mention` | When `true`, the agent replies only when mentioned. Defaults to `true`; direct chats are unaffected. |
| `free_response_chats` | Removes the mention requirement in specific chats. It does not grant access by itself. |
| `observe_unmentioned_group_messages` | Allows messages that do not address the agent to provide context for the next request. |
| `observe_context_limit` | Number of preceding records to inspect: defaults to `20`, accepts `0` through `100`. `0` disables context retrieval. |

A non-empty `allowed_chats` excludes all unlisted groups and channels.
In a listed chat, access must also be granted through `group_allowed_chats`
or `group_allow_from`.

A global `require_mention: false` removes the mention requirement in every
group and channel with authorized access. If only one chat needs this behavior,
use `free_response_chats`.

## Notifications and scheduled tasks

The home chat (`home_channel`) is where Hermes can deliver notifications and
scheduled task results (cron). It can be an existing direct chat, group, or channel.

You can set its ID in the setup wizard or in the active profile's `.env` file:

```dotenv
TRUECONF_HOME_CHANNEL=Exact-Case-Sensitive-Chat-ID
```

Replace the placeholder with a real `chat_id`, preserving case and removing
leading or trailing spaces. A chat ID is different from a user ID.

You can also configure the home chat in YAML:

```yaml
platforms:
  trueconf:
    home_channel:
      platform: trueconf
      chat_id: Exact-Case-Sensitive-Chat-ID
      name: Hermes notifications
```

`TRUECONF_HOME_CHANNEL` takes precedence over YAML and is used by Hermes for
standalone cron delivery when the active gateway connection is not used.
Set the environment variable for this scenario.

After configuring it, test delivery with a normal Hermes scheduled task.
Text with attachments is supported; attachment-only delivery has a Hermes
v0.21.0 limitation described below.

## Connection settings

The wizard is enough for your first run. YAML is useful if you want to change
the port, use a custom certificate, or configure behavior in more detail.
Credentials remain in the profile's `.env` file.

```yaml
plugins:
  enabled:
    - trueconf-platform

platforms:
  trueconf:
    enabled: true
    server: video.example.com
    port: 443
    https: true
    verify_ssl: true
    parse_mode: html
    require_mention: true
```

Keep other enabled plugins in `plugins.enabled` and preserve the rest of your
configuration. If the installer or wizard has already set the server name,
`TRUECONF_SERVER` takes precedence over the YAML `server` setting.

| Setting | Default | Description |
|---|---|---|
| `port` | `443` | TrueConf Server port. |
| `https` | `true` | Use a secure connection. |
| `verify_ssl` | `true` | Verify the certificate; also accepts a path to a CA bundle.[^tls] |
| `parse_mode` | `html` | The only supported format for sending formatted text. |
| `reply_to_mode` | `first` | Anchor replies to the original message: `first`, `all`, or `off`.[^replies] |

With non-empty `TRUECONF_SERVER`, `TRUECONF_USERNAME`, and `TRUECONF_PASSWORD`,
the platform can be enabled without a YAML `trueconf:` block. The block is
needed for additional behavior settings. An explicit `enabled: false`
disables the platform.

### If the server uses your organization's certificate

Ask your administrator for a certificate authority bundle (CA bundle) and
specify a readable path on the computer running Hermes:

```yaml
platforms:
  trueconf:
    verify_ssl: /etc/company-pki/trueconf-ca.pem
```

The `TRUECONF_VERIFY_SSL` value entered during installation sets the initial
verification setting; subsequent changes can be made explicitly in YAML.

> [!CAUTION]
> Do not use `verify_ssl: false` as a permanent workaround for certificate errors.
> It disables server identity verification and allows passwords and messages
> to be intercepted. This option is only for short-term testing in a controlled
> environment; the plugin warns whenever it is used.

## Features and limitations

Plugin 1.0.0 supports:

- TrueConf direct chats, groups, and channels.
- Text messages and replies anchored to the original message.
- Incoming and outgoing documents, images, video, voice messages, and audio
  files; images are shown with previews.
- Transcription of incoming voice messages using local `faster-whisper`,
  when speech recognition is enabled and configured in Hermes.[^voice]
- Formatted replies: bold, italic, underline, strikethrough, links, lists,
  quotes, and tables.[^formatting]
- Clarification and confirmation buttons in direct chats; numbered choices
  in groups.
- Scheduled task delivery, including text with attachments.
- Updating progress messages instead of repeatedly sending new messages
  with the same status.

Known limitations to keep in mind:

| Limitation | What it means |
|---|---|
| Only Hermes v0.21.0 has been tested | Compatibility with other Hermes versions is not guaranteed. |
| SDK `>=1.5.0,<2` | Earlier `python-trueconf-bot` versions and the 2.x series are unsupported. |
| Outgoing video and voice messages | Delivered as file attachments rather than dedicated TrueConf video or voice messages. |
| Maximum 4096 visible characters per message | Long replies are automatically split into a chain. Attachment captions are not split. |
| Media-only delivery without text | Hermes v0.21.0 rejects it before calling a third-party standalone sender. The plugin's own handler supports it. |
| Incoming attachments: up to 128 MiB by default | Controlled by `gateway.max_inbound_media_bytes`; `0` disables the limit. Increasing it raises memory usage. |
| Stickers | Outgoing WebP uses the sticker API; incoming stickers arrive as ordinary attachments. |
| Favorites, threads, and topics | Unsupported as delivery targets. |
| Editing a long reply | If a previously split reply becomes shorter, older parts may remain in the chat.[^edits] |

## Troubleshooting

First, check the gateway status and recent log entries:

```bash
hermes gateway status
hermes logs gateway -n 100
```

| Problem | What to do |
|---|---|
| The bot does not reply in a direct chat | Make sure the gateway is running, the plugin is enabled, and your exact ID is allowed. Check the active Hermes profile. |
| The platform is disabled | Check that `trueconf-platform` is in `plugins.enabled`, `enabled: false` is not set, and the server, bot username, and password are filled in. |
| TrueConf is unavailable or dependencies are missing | After starting the gateway, run `hermes plugins doctor trueconf-platform --ci`. If automatic installation fails, use the commands below. |
| Login fails | Check the bot account and password. Make sure another gateway is not using the same account on the same server. |
| Certificate error | Specify the correct CA bundle; contact your server administrator. |
| Direct chats work, but groups do not | Check access for the user or the entire group, `allowed_chats`, and the native TrueConf mention of the bot. |
| YAML rules have no effect | Check whether `TRUECONF_ALLOWED_USERS` or `TRUECONF_ALLOW_ALL_USERS` is set in the profile's `.env` file. |
| The agent does not use group conversation context | The chat must be explicitly listed in `allowed_chats`, absent from `free_response_chats`, and both `require_mention` and `observe_unmentioned_group_messages` must be `true`. |
| Notifications do not arrive | Check that `TRUECONF_HOME_CHANNEL` contains an existing `chat_id`, preserves case, and has no leading or trailing spaces. |
| A large attachment is rejected | Check `gateway.max_inbound_media_bytes`. Assess available memory before increasing the limit. |
| The connection keeps dropping | Check server reachability and SDK retry-exhaustion errors in the gateway log. |

### Installing dependencies manually

Run this command **in the same Python environment that runs Hermes**:

```bash
python -m pip install "mistune>=3,<4" "python-trueconf-bot>=1.5.0,<2"
```

If Hermes was installed through `uv tool`, inject the dependencies into its environment:

```bash
uv tool inject hermes-agent "mistune>=3,<4" "python-trueconf-bot>=1.5.0,<2"
```

After installation, restart the gateway and repeat the plugin check.
Installing packages into another system Python will not fix the Hermes environment.

### Where to find more details

```bash
hermes plugins list
hermes status
hermes logs errors
hermes logs -f
```

`hermes logs -f` shows live output; stop watching with `Ctrl+C`.
Logs are normally stored in `~/.hermes/logs/`.

> [!WARNING]
> Before sharing logs, remove passwords, server names, user IDs,
> message contents, and URL query parameters.

Report problems through the
[repository Issues](https://github.com/TrueConf/trueconf-hermes-agent-plugin/issues).
Include the Hermes and plugin versions, the expected behavior, and the error message.

## Migrate from a historical TrueConf integration

If you previously used another TrueConf adapter for Hermes:

1. Stop the gateway and back up the active profile.
2. Remove the old integration following its documentation. If it modified
   Hermes Core, use a clean Hermes v0.21.0 installation.
3. Remove old `lib_patches`, SDK overrides, post-update patch hooks,
   and Python path changes for the old adapter.
4. Install this plugin and migrate the server, credentials, access list,
   and home chat ID.
5. Move the port, transport, parse mode, and mention requirement to
   `platforms.trueconf`. `TRUECONF_USE_SSL` and other historical variables
   are unsupported; `TRUECONF_VERIFY_SSL` is supported during installation.
6. Review access permissions and certificate verification before starting.
7. Complete the first-run checks, leaving the old integration stopped.

Historical HTML conversion, automatic home chat detection, the old plugin's
own rate limits and reconnect loops, and Hermes Core patches are not migrated.

## Disabling and removing the plugin

To disable the plugin while keeping its files and settings:

```bash
hermes plugins disable trueconf-platform
hermes gateway restart
```

To remove the installed plugin:

```bash
hermes gateway stop
hermes plugins remove trueconf-platform
```

Removing the plugin preserves profile settings and secrets. If other processes
no longer need them, remove the `trueconf` block from `config.yaml` and the
`TRUECONF_*` entries from the selected profile's `.env` file. Then start the
gateway again if you need it for other connections.

## For developers

The plugin uses the public Hermes and `python-trueconf-bot` APIs. Installation
does not require modifying Hermes Core or bundling a copy of the SDK.

The tests do not require a TrueConf Server connection:

```bash
pytest tests -q
ruff check .
ty check
```

Architecture and accepted decisions are documented in [CONTEXT.md](CONTEXT.md).
The original [behavior specification](docs/specs/0001-observable-behavior.md)
and [implementation plan](docs/plans/0001-first-implementation.md) describe
the 0.1.0 stage; subsequent changes are recorded in the
[specification amendments](docs/specs/0004-observable-behavior-amendments.md).
For user configuration of release 1.0.0, follow this guide.

The project is licensed under [BSD-3-Clause-Clear](LICENSE).

[^dependencies]: Automatic installation depends on the Hermes `security.allow_lazy_installs` setting. The plugin checks the SDK and Mistune versions when the platform starts and, if needed, installs them into the gateway environment. If installation is disallowed or fails, install the dependencies manually. The manifest's `python_dependencies` field does not install packages by itself.

[^tls]: TLS protects the connection; certificate verification confirms that you are connected to the intended server. During installation, `TRUECONF_VERIFY_SSL` accepts `true`, `false`, or a path to a readable CA bundle. For a private PKI, use its CA bundle and keep verification enabled.

[^profiles]: A profile is a separate set of Hermes settings. For the `work` profile, add `-p work` before the subcommand, for example `hermes -p work gateway setup` and `hermes -p work gateway restart`. Install the plugin and perform subsequent actions with the same profile. The `~/.hermes/config.yaml` and `~/.hermes/.env` paths in this guide refer to the default profile.

[^buttons]: The TrueConf `CallbackQuery` event does not identify who pressed a button. A text reply lets Hermes check the sender through its normal authorization mechanism. Text is also used for open-ended questions, too many options, or failed button delivery.

[^replies]: `first` anchors the first delivered message to the request and continuations to the previous part; `all` anchors each part to the request; `off` disables reply anchors. Only these three values are allowed.

[^context]: Background context retrieval works only with `require_mention: true`, the chat explicitly included in `allowed_chats`, and the chat absent from `free_response_chats`. Agent messages, previous mentions of this agent (including `@all`), non-text and empty messages, and unauthorized messages are excluded. The previous authorized mention limits the history window. Mentions of other users and supported HTML are preserved. Limits are 1,000 characters per message and 12,000 per block; the oldest messages are removed first if the block is too large. A history retrieval failure does not block the response to the request. This mechanism does not apply to direct chats.

[^formatting]: The agent writes Markdown, which the plugin converts into TrueConf HTML using `<b>`, `<i>`, `<s>`, `<u>`, `<a>`, and `<br>`. Code is shown as emphasized text, headings as bold, and quotes as italic text in quotation marks; wide tables become “field: value” blocks. Spoilers are replaced with `[spoiler]`. Unsupported markup is preserved as readable text; links with unsafe schemes do not become clickable links. Raw HTML is passed to the server for sanitization, while unsafe or incomplete link tags are first converted to text so that the server does not reject the entire message.

[^edits]: Hermes edits the last message ID in the chain, and the TrueConf SDK does not provide a message deletion API. As a result, older parts can remain visible when a previously split reply becomes shorter. This is an accepted limitation of the editing mechanism.

[^voice]: Speech recognition is performed by Hermes, not the TrueConf plugin itself. To use local `faster-whisper`, set `enabled: true` and `provider: local` in the root `stt` block of `config.yaml`. For Russian speech, you can set `language: ru`. The `faster-whisper` library must be available in the Hermes environment. Restart the gateway after changing the settings.

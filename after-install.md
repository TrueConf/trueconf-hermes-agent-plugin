# Finish TrueConf setup

1. Run `hermes gateway setup` and select TrueConf to review the connection
   settings and configure who may use the bot. Keep TLS certificate
   verification enabled; for a private PKI, use a readable CA bundle.
2. Restrict access to specific TrueConf user IDs. Leave allow-all disabled
   unless every user who can contact the bot should have access to Hermes.
3. Run `hermes gateway restart`, then `hermes gateway status`.
4. Run `hermes plugins doctor trueconf-platform --ci` and send the bot a
   direct message from an allowed user.

Hermes normally installs `mistune>=3,<4` and `python-trueconf-bot>=1.5.0,<2`
when the enabled platform starts. If lazy installs are disabled or fail,
install them manually into the same Python environment that runs Hermes.
The bot password belongs in the Hermes profile secret environment, never in YAML.

For complete instructions, see [English](README.md) or [Русский](README-ru.md).

"""Hermes registration surface for the TrueConf platform plugin."""

from __future__ import annotations

import logging
import os
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version


MINIMUM_SDK_VERSION = "1.5.3"
MAXIMUM_SDK_VERSION = "2"
SDK_REQUIREMENT = "python-trueconf-bot>=1.5.3,<2"
MINIMUM_MISTUNE_VERSION = "3"
MAXIMUM_MISTUNE_VERSION = "4"
MISTUNE_REQUIREMENT = "mistune>=3,<4"
PLUGIN_REQUIREMENTS = (MISTUNE_REQUIREMENT, SDK_REQUIREMENT)
SDK_INSTALL_HINT = f"Install {' and '.join(PLUGIN_REQUIREMENTS)}"
_CONFIG_ERROR_KEY = "_trueconf_config_error"
_LOGGER = logging.getLogger(__name__)
_IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
_VIDEO_EXTENSIONS = {".3gp", ".avi", ".mkv", ".mov", ".mp4", ".webm"}
_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".weba",
}


def _sdk_available() -> bool:
    """Return whether a supported TrueConf SDK is installed, without importing it."""

    if find_spec("trueconf") is None:
        return False
    try:
        installed = Version(version("python-trueconf-bot"))
        return Version(MINIMUM_SDK_VERSION) <= installed < Version(MAXIMUM_SDK_VERSION)
    except (InvalidVersion, PackageNotFoundError):
        return False


def _mistune_available() -> bool:
    """Return whether a supported Mistune parser is installed."""

    if find_spec("mistune") is None:
        return False
    try:
        installed = Version(version("mistune"))
        return (
            Version(MINIMUM_MISTUNE_VERSION)
            <= installed
            < Version(MAXIMUM_MISTUNE_VERSION)
        )
    except (InvalidVersion, PackageNotFoundError):
        return False


def _runtime_dependencies_available() -> bool:
    """Probe Plugin runtime dependencies without installing or importing them."""

    return _sdk_available() and _mistune_available()


def _ensure_runtime_dependencies() -> bool:
    """Install Plugin dependencies through Hermes' lazy dependency pipeline."""

    if _runtime_dependencies_available():
        return True

    try:
        from tools.lazy_deps import install_specs

        outcome = install_specs(list(PLUGIN_REQUIREMENTS), timeout=300)
    except Exception:
        _LOGGER.exception("TrueConf Plugin dependency installation failed")
        return False

    if not outcome.ok:
        _LOGGER.warning(
            "TrueConf Plugin dependency installation was not completed: %s",
            outcome.reason or "dependency installer failed",
        )
        return False

    if not _runtime_dependencies_available():
        _LOGGER.warning(
            "TrueConf Plugin dependency installation completed, but %s "
            "is still unavailable",
            ", ".join(PLUGIN_REQUIREMENTS),
        )
        return False
    return True


def _create_adapter(_config: Any) -> Any:
    from .adapter import TrueConfAdapter

    return TrueConfAdapter(_config)


def _parse_target_ref(target_ref: object) -> tuple[str, None] | None:
    """Accept one concrete, case-preserving TrueConf chat ID."""

    if not isinstance(target_ref, str) or target_ref != target_ref.strip():
        return None
    if target_ref:
        return target_ref, None
    return None


def _env_enablement() -> dict[str, Any] | None:
    """Seed public Hermes configuration from supported environment values."""

    username = (_get_secret("TRUECONF_USERNAME", "") or "").strip()
    password = (_get_secret("TRUECONF_PASSWORD", "") or "").strip()
    server = (_get_secret("TRUECONF_SERVER", "") or "").strip()
    if not (username and password and server):
        return None

    seed: dict[str, Any] = {"server": server}
    verify_ssl = (_get_secret("TRUECONF_VERIFY_SSL", "") or "").strip()
    if verify_ssl:
        lowered = verify_ssl.lower()
        if lowered == "true":
            seed["verify_ssl"] = True
        elif lowered == "false":
            seed["verify_ssl"] = False
        else:
            seed["verify_ssl"] = verify_ssl
    home_target = (_get_secret("TRUECONF_HOME_CHANNEL", "") or "").strip()
    if _parse_target_ref(home_target) is not None:
        seed["home_channel"] = {
            "chat_id": home_target,
            "name": _get_secret("TRUECONF_HOME_CHANNEL_NAME", "TrueConf home")
            or "TrueConf home",
        }
    return seed


def _apply_yaml_config(
    yaml_config: dict[str, Any],
    platform_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Expose TrueConf-owned YAML fields as ``PlatformConfig.extra`` values."""

    if isinstance(yaml_config.get("trueconf"), dict):
        return {
            _CONFIG_ERROR_KEY: (
                "move the top-level trueconf block to platforms.trueconf"
            )
        }

    platforms = yaml_config.get("platforms")
    canonical_config = (
        platforms.get("trueconf") if isinstance(platforms, dict) else None
    )
    if not isinstance(canonical_config, dict):
        return {
            _CONFIG_ERROR_KEY: (
                "TrueConf settings must be configured under platforms.trueconf"
            )
        }
    platform_config = canonical_config

    supported = (
        "server",
        "port",
        "https",
        "verify_ssl",
        "parse_mode",
        "require_mention",
        "reply_to_mode",
        "allow_from",
        "group_allow_from",
        "allowed_chats",
        "group_allowed_chats",
        "free_response_chats",
        "observe_unmentioned_group_messages",
        "observe_context_limit",
    )
    seeded = {key: platform_config[key] for key in supported if key in platform_config}

    home_channel = platform_config.get("home_channel")
    if home_channel is not None:
        if not isinstance(home_channel, dict):
            seeded[_CONFIG_ERROR_KEY] = "home_channel must be a mapping"
        else:
            home_target = home_channel.get("chat_id")
            if _parse_target_ref(home_target) is None:
                seeded[_CONFIG_ERROR_KEY] = (
                    "home_channel.chat_id must be a non-empty TrueConf chat ID"
                )
            elif not os.getenv("TRUECONF_HOME_CHANNEL"):
                os.environ["TRUECONF_HOME_CHANNEL"] = home_target
                home_name = home_channel.get("name")
                if isinstance(home_name, str) and home_name.strip():
                    os.environ.setdefault("TRUECONF_HOME_CHANNEL_NAME", home_name)

    if "password" in platform_config:
        seeded[_CONFIG_ERROR_KEY] = (
            "password must be provided through the secret environment/store, "
            "not config.yaml"
        )
    if isinstance(seeded.get("parse_mode"), str):
        seeded["parse_mode"] = seeded["parse_mode"].lower()
    return seeded or None


def _validate_config(config: Any) -> bool:
    """Validate local configuration and report a field-specific diagnostic."""

    error = _config_error(config)
    if error is not None:
        _LOGGER.warning("TrueConf configuration invalid: %s", error)
        return False
    return True


def _config_error(config: Any) -> str | None:
    """Return the first deterministic local configuration error, if any."""

    extra = getattr(config, "extra", {}) or {}
    bridged_error = extra.get(_CONFIG_ERROR_KEY)
    if isinstance(bridged_error, str):
        return bridged_error

    server_value = _get_secret("TRUECONF_SERVER", "") or extra.get("server", "")
    if not isinstance(server_value, str):
        return "server must be a string"
    server = server_value.strip()
    if (
        not server
        or server != server_value
        or "://" in server
        or "/" in server
        or "?" in server
        or "#" in server
    ):
        return "server must be a non-empty hostname/address without a scheme or path"

    username = _get_secret("TRUECONF_USERNAME", "")
    if not isinstance(username, str) or not username.strip():
        return "TRUECONF_USERNAME must be provided through the secret environment/store"
    password = _get_secret("TRUECONF_PASSWORD", "")
    if not isinstance(password, str) or not password.strip():
        return "TRUECONF_PASSWORD must be provided through the secret environment/store"

    port = extra.get("port", 443)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return "port must be an integer from 1 through 65535"
    if not isinstance(extra.get("https", True), bool):
        return "https must be a boolean"

    verify_ssl = extra.get("verify_ssl", True)
    if not isinstance(verify_ssl, bool):
        if not isinstance(verify_ssl, str) or not (
            os.path.isfile(verify_ssl) and os.access(verify_ssl, os.R_OK)
        ):
            return "verify_ssl must be a boolean or a readable CA bundle path"

    parse_mode = extra.get("parse_mode", "html")
    if not isinstance(parse_mode, str) or parse_mode.lower() != "html":
        return "parse_mode must be html; markdown and text are not supported"

    reply_to_mode = extra.get("reply_to_mode", "first")
    if reply_to_mode not in ("first", "all", "off"):
        return "reply_to_mode must be one of: first, all, off"

    if "require_mention" in extra and not isinstance(extra["require_mention"], bool):
        return "require_mention must be a boolean"

    if "observe_unmentioned_group_messages" in extra and not isinstance(
        extra["observe_unmentioned_group_messages"], bool
    ):
        return "observe_unmentioned_group_messages must be a boolean"

    observe_context_limit = extra.get("observe_context_limit", 20)
    if (
        isinstance(observe_context_limit, bool)
        or not isinstance(observe_context_limit, int)
        or not 0 <= observe_context_limit <= 100
    ):
        return "observe_context_limit must be an integer from 0 through 100"

    for field in (
        "allow_from",
        "group_allow_from",
        "allowed_chats",
        "group_allowed_chats",
        "free_response_chats",
    ):
        if field not in extra:
            continue
        values = extra[field]
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            return f"{field} must be a list of non-empty string IDs"

    home_channel = getattr(config, "home_channel", None)
    if home_channel is not None:
        chat_id = getattr(home_channel, "chat_id", None)
        if _parse_target_ref(chat_id) is None:
            return "home_channel.chat_id must be a non-empty TrueConf chat ID"
    return None


def _get_secret(name: str, default: str) -> str | None:
    """Use Hermes' scope-aware public accessor when Hermes is available."""

    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return os.getenv(name, default)
    return get_secret(name, default)


def _is_connected(config: Any) -> bool:
    """Report local readiness without importing the SDK or opening a connection."""

    return bool(getattr(config, "enabled", False)) and _config_error(config) is None


def _interactive_setup() -> None:
    """Prompt for and persist TrueConf connection and delivery settings."""

    from hermes_cli.cli_output import (
        print_header,
        print_info,
        print_success,
        print_warning,
        prompt,
        prompt_yes_no,
    )
    from hermes_cli.config import (
        get_env_value,
        remove_env_value,
        save_env_value,
        write_platform_config_field,
    )

    print_header("TrueConf")
    existing_server = get_env_value("TRUECONF_SERVER") or ""
    existing_username = get_env_value("TRUECONF_USERNAME") or ""
    existing_password = get_env_value("TRUECONF_PASSWORD") or ""
    if existing_server and existing_username and existing_password:
        print_info(
            f"TrueConf is already configured for {existing_username} on "
            f"{existing_server}."
        )
        if not prompt_yes_no("Reconfigure TrueConf?", False):
            return

    print_info("Connect Hermes to a TrueConf Server bot account.")
    server = prompt(
        "TrueConf Server hostname/address (without https://)",
        default=existing_server,
    ).strip()
    if not server:
        print_warning("TrueConf Server is required — setup cancelled")
        return

    username = prompt(
        "Bot account name",
        default=existing_username,
    ).strip()
    if not username:
        print_warning("Bot account name is required — setup cancelled")
        return

    password_label = (
        "Bot password (leave blank to keep the saved password)"
        if existing_password
        else "Bot password"
    )
    entered_password = prompt(password_label, default="", password=True)
    password = entered_password or existing_password
    if not password:
        print_warning("Bot password is required — setup cancelled")
        return

    save_env_value("TRUECONF_SERVER", server)
    save_env_value("TRUECONF_USERNAME", username)
    save_env_value("TRUECONF_PASSWORD", password)

    print_info(
        "TLS certificate verification confirms that Hermes is connecting to "
        "the intended TrueConf Server and protects the bot password and messages."
    )
    verify_ssl = prompt_yes_no(
        "Verify the TrueConf Server TLS certificate? (recommended)",
        True,
    )
    if not verify_ssl:
        print_warning(
            "TLS certificate verification is disabled. Use this only for a "
            "controlled test server with a self-signed certificate: an attacker "
            "on the network could otherwise intercept credentials and messages."
        )
    write_platform_config_field("trueconf", "verify_ssl", verify_ssl, raw=True)

    print_info("Restrict which TrueConf users may use the bot.")
    allow_all = prompt_yes_no("Allow all TrueConf users?", False)
    if allow_all:
        save_env_value("TRUECONF_ALLOW_ALL_USERS", "true")
        remove_env_value("TRUECONF_ALLOWED_USERS")
    else:
        remove_env_value("TRUECONF_ALLOW_ALL_USERS")
        allowed_users = prompt(
            "Allowed user IDs (comma-separated, leave empty for pairing)",
            default=get_env_value("TRUECONF_ALLOWED_USERS") or "",
        ).strip()
        if allowed_users:
            normalized_users = ",".join(
                value.strip() for value in allowed_users.split(",") if value.strip()
            )
            save_env_value("TRUECONF_ALLOWED_USERS", normalized_users)
        else:
            remove_env_value("TRUECONF_ALLOWED_USERS")
            print_warning(
                "No users are allowed. The bot will ignore all incoming messages "
                "until you approve a pairing request, add user IDs, or enable "
                "allow-all access."
            )

    print_info("Set the chat used for cron results and notifications.")
    home_chat = prompt(
        "Home chat ID (leave empty to set later with /set-home)",
        default=get_env_value("TRUECONF_HOME_CHANNEL") or "",
    ).strip()
    if home_chat:
        save_env_value("TRUECONF_HOME_CHANNEL", home_chat)
    else:
        remove_env_value("TRUECONF_HOME_CHANNEL")

    print_success("TrueConf configured")


async def _standalone_send(
    platform_config: Any,
    chat_id: str,
    message: str,
    *,
    thread_id: str | None = None,
    media_files: list[Any] | None = None,
    force_document: bool = False,
) -> dict[str, Any]:
    """Deliver through one ephemeral adapter when no gateway adapter exists."""

    # TrueConf has no thread/topic target; accepting this standard Hermes
    # argument keeps the public standalone-sender interface uniform.
    _ = thread_id
    config_error = _config_error(platform_config)
    if config_error is not None:
        return {
            "error": f"TrueConf configuration invalid: {config_error}",
            "error_kind": "unknown",
            "retryable": False,
        }
    adapter = _create_adapter(platform_config)
    adapter.set_authorization_check(lambda _user, _kind, _chat: False)
    delivered_message_ids: list[str] = []

    def with_partial_delivery(payload: dict[str, Any]) -> dict[str, Any]:
        if delivered_message_ids:
            payload["message_id"] = delivered_message_ids[-1]
            payload["message_ids"] = list(delivered_message_ids)
        return payload

    def failure(result: Any) -> dict[str, Any]:
        return with_partial_delivery(
            {
                "error": result.error
                or f"TrueConf standalone send failed on {adapter.server}",
                "error_kind": result.error_kind or "unknown",
                "retryable": result.retryable,
            }
        )

    try:
        if not await adapter.connect():
            return {
                "error": adapter.fatal_error_message
                or f"TrueConf standalone connection failed on {adapter.server}",
                "error_kind": adapter.fatal_error_code or "transient",
                "retryable": adapter.fatal_error_retryable,
            }
        last_result = None
        if message:
            last_result = await adapter.send(chat_id, message)
            if not last_result.success:
                return failure(last_result)
            for mid in list(last_result.continuation_message_ids) + [
                last_result.message_id
            ]:
                if mid is not None:
                    delivered_message_ids.append(str(mid))

        for index, descriptor in enumerate(media_files or []):
            if not isinstance(descriptor, (list, tuple)) or not descriptor:
                return with_partial_delivery(
                    {
                        "error": (
                            f"TrueConf standalone media descriptor {index + 1} "
                            "is invalid"
                        ),
                        "error_kind": "unknown",
                        "retryable": False,
                    }
                )
            file_path = descriptor[0]
            if not isinstance(file_path, str) or not file_path:
                return with_partial_delivery(
                    {
                        "error": (
                            f"TrueConf standalone media descriptor {index + 1} "
                            "is invalid"
                        ),
                        "error_kind": "unknown",
                        "retryable": False,
                    }
                )
            is_voice = bool(descriptor[1]) if len(descriptor) > 1 else False
            extension = Path(file_path).suffix.lower()
            if force_document:
                last_result = await adapter.send_document(chat_id, file_path)
            elif extension in _IMAGE_EXTENSIONS:
                last_result = await adapter.send_image_file(chat_id, file_path)
            elif extension in _VIDEO_EXTENSIONS:
                last_result = await adapter.send_video(chat_id, file_path)
            elif is_voice or extension in _AUDIO_EXTENSIONS:
                last_result = await adapter.send_voice(chat_id, file_path)
            else:
                last_result = await adapter.send_document(chat_id, file_path)
            if not last_result.success:
                return failure(last_result)
            if last_result.message_id is not None:
                delivered_message_ids.append(str(last_result.message_id))

        if last_result is None:
            return {
                "error": "TrueConf standalone send has no text or media to deliver",
                "error_kind": "unknown",
                "retryable": False,
            }
        response: dict[str, Any] = {
            "success": True,
            "message_id": last_result.message_id,
        }
        if media_files:
            response["media_delivered"] = True
        return response
    finally:
        await adapter.disconnect()


def register(ctx: Any) -> None:
    """Register the TrueConf platform through Hermes' public plugin context."""

    ctx.register_platform(
        name="trueconf",
        label="TrueConf",
        adapter_factory=_create_adapter,
        check_fn=_runtime_dependencies_available,
        ensure_deps_fn=_ensure_runtime_dependencies,
        validate_config=_validate_config,
        is_connected=_is_connected,
        required_env=["TRUECONF_SERVER", "TRUECONF_USERNAME", "TRUECONF_PASSWORD"],
        install_hint=SDK_INSTALL_HINT,
        setup_fn=_interactive_setup,
        allowed_users_env="TRUECONF_ALLOWED_USERS",
        allow_all_env="TRUECONF_ALLOW_ALL_USERS",
        max_message_length=4096,
        emoji="📹",
        platform_hint=(
            "You are chatting through TrueConf. Write TrueConf Markdown: "
            "**bold**, *italic*, __underline__, ~~strikethrough~~, "
            "[label](https://example.com), headings, lists, and "
            "tables. Nested lists keep indentation and per-level markers; "
            "narrow tables render as pipe-separated rows, wide or long tables "
            "as labeled blocks. Backtick code becomes bold-italic text, "
            "fenced blocks become italic text with indentation kept, headings "
            "become bold, and ||spoilers|| become [spoilers]. Write commands "
            "such as /start as bare text without decoration. Bare HTML tags "
            "render as TrueConf formatting; write literal HTML inside "
            "backticks or a fenced block. "
            "Messages are limited to 4096 visible characters and are "
            "automatically split into multiple chained messages when longer."
        ),
        env_enablement_fn=_env_enablement,
        apply_yaml_config_fn=_apply_yaml_config,
        cron_deliver_env_var="TRUECONF_HOME_CHANNEL",
        parse_target_ref_fn=_parse_target_ref,
        standalone_sender_fn=_standalone_send,
    )

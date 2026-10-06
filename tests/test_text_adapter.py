from __future__ import annotations

import asyncio
import base64
import json
import threading
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def registered_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    hermes_home = tmp_path / "hermes-home"
    plugin_dir = hermes_home / "plugins" / "trueconf"
    plugin_dir.parent.mkdir(parents=True)
    plugin_dir.symlink_to(PROJECT_ROOT, target_is_directory=True)
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - trueconf-platform\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", str(tmp_path / "locks"))

    from gateway.platform_registry import platform_registry
    from hermes_cli.plugins import discover_plugins

    discover_plugins(force=True)
    return platform_registry


def test_registry_creates_a_text_adapter_without_connecting(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.config import PlatformConfig
    from gateway.platforms.base import BasePlatformAdapter

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    config = PlatformConfig(
        enabled=True,
        extra={
            "server": "video.example.com",
            "port": 8443,
            "https": True,
            "verify_ssl": True,
            "parse_mode": "html",
        },
    )

    adapter = registered_plugin.create_adapter("trueconf", config)

    assert isinstance(adapter, BasePlatformAdapter)
    assert adapter.platform.value == "trueconf"
    assert adapter.is_connected is False


def test_registry_accepts_raw_home_chat_id_written_by_hermes_sethome(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.config import HomeChannel, Platform, PlatformConfig
    from gateway.platforms.base import BasePlatformAdapter

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    config = PlatformConfig(
        enabled=True,
        home_channel=HomeChannel(
            platform=Platform("trueconf"),
            chat_id="5559db793777b228a585e293fc64ae8991cec437",
            name="TrueConf home",
        ),
        extra={"server": "video.example.com"},
    )

    adapter = registered_plugin.create_adapter("trueconf", config)

    assert isinstance(adapter, BasePlatformAdapter)


class ControlledBot:
    def __init__(self, *, authorize: bool = True):
        self.me_id = "hermes-bot@video.example.com"
        self.authorized_event = asyncio.Event()
        self.stopped_event = asyncio.Event()
        self.run_started = asyncio.Event()
        self.run_exited = asyncio.Event()
        self.shutdown_calls = 0
        self.activity_calls: list[tuple[str, Any]] = []
        self.activity_event = asyncio.Event()
        self.authorize = authorize
        self._exit = asyncio.Event()
        self._terminal_error: BaseException | None = None

    async def run(self, handle_signals: bool = True) -> None:
        self.run_started.set()
        if self.authorize:
            self.authorized_event.set()
        try:
            await self._exit.wait()
            if self._terminal_error is not None:
                raise self._terminal_error
        finally:
            self.run_exited.set()

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.authorized_event.clear()
        self._exit.set()
        self.stopped_event.set()

    async def send_chat_activity(self, chat_id: str, activity_type: Any):
        self.activity_calls.append((chat_id, activity_type))
        self.activity_event.set()
        return SimpleNamespace(retry_after=60_000)

    def stop_with(self, error: BaseException | None = None) -> None:
        self._terminal_error = error
        self._exit.set()


def create_adapter(registered_plugin, **extra_overrides):
    from gateway.config import PlatformConfig

    return registered_plugin.create_adapter(
        "trueconf",
        PlatformConfig(
            enabled=True,
            extra={
                "server": "video.example.com",
                "port": 8443,
                "https": True,
                "verify_ssl": True,
                "parse_mode": "html",
                **extra_overrides,
            },
        ),
    )


def test_group_allowed_chats_authorizes_the_whole_trueconf_chat(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.config import Platform
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    adapter = create_adapter(
        registered_plugin,
        group_allowed_chats=["Trusted-Group-ID"],
    )
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform("trueconf"): adapter}

    source = SessionSource(
        platform=Platform("trueconf"),
        chat_id="Trusted-Group-ID",
        chat_type="group",
        user_id=None,
        user_name=None,
    )

    assert runner._is_user_authorized(source) is True


def install_bot(monkeypatch: pytest.MonkeyPatch, bot: ControlledBot) -> None:
    from trueconf import Bot

    def from_credentials(cls, server, username, password, **kwargs):
        bot.dispatcher = kwargs["dispatcher"]
        return bot

    monkeypatch.setattr(Bot, "from_credentials", classmethod(from_credentials))


def test_connect_waits_for_authorization_and_disconnects_cleanly(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_connect_waits_for_authorization(registered_plugin, monkeypatch))


def test_connect_fails_closed_without_the_public_authorization_hook(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_missing_authorization_hook(registered_plugin, monkeypatch))


def test_long_running_turn_does_not_reset_delayed_typing_indicator(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_long_running_turn_keeps_typing(registered_plugin, monkeypatch))


async def _long_running_turn_keeps_typing(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    visible = asyncio.Event()
    pending_visibility: asyncio.TimerHandle | None = None

    async def delayed_activity(chat_id: str, activity_type: Any):
        nonlocal pending_visibility
        bot.activity_calls.append((chat_id, activity_type))
        if pending_visibility is not None:
            pending_visibility.cancel()
        pending_visibility = asyncio.get_running_loop().call_later(0.05, visible.set)
        return SimpleNamespace(retry_after=100)

    bot.send_chat_activity = delayed_activity
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    stop = asyncio.Event()
    typing_task = asyncio.create_task(adapter._keep_typing("Slow-Chat", interval=0.02, stop_event=stop))
    await asyncio.wait_for(visible.wait(), timeout=0.2)

    assert typing_task.done() is False
    assert len(bot.activity_calls) == 1

    stop.set()
    await typing_task
    assert adapter._typing_senders == {}
    await adapter.disconnect()


async def _missing_authorization_hook(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)

    assert await adapter.connect() is False
    assert adapter.is_connected is False
    assert adapter.fatal_error_code == "authorization_hook_missing"
    assert adapter.fatal_error_retryable is False
    assert bot.run_started.is_set() is False


async def _connect_waits_for_authorization(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf import Bot, ParseMode
    from trueconf.enums import ChatActivity

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    captured: dict[str, Any] = {}

    def from_credentials(cls, server, username, password, **kwargs):
        captured.update(
            server=server,
            username=username,
            password=password,
            kwargs=kwargs,
        )
        return bot

    monkeypatch.setattr(Bot, "from_credentials", classmethod(from_credentials))
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)

    assert await adapter.connect() is True
    assert adapter.is_connected is True
    assert captured == {
        "server": "video.example.com",
        "username": "bot",
        "password": "secret",
        "kwargs": {
            "dispatcher": captured["kwargs"]["dispatcher"],
            "receive_unread_messages": False,
            "receive_system_messages": False,
            "skip_self_messages": True,
            "max_in_memory_download_size": 128 * 1024 * 1024,
            "verify_ssl": True,
            "web_port": 8443,
            "https": True,
        },
    }
    assert captured["kwargs"]["dispatcher"] is not None
    assert adapter.parse_mode is ParseMode.HTML

    await adapter.send_typing("Typing-Chat")
    await asyncio.wait_for(bot.activity_event.wait(), timeout=1)
    await adapter.send_typing("Typing-Chat")
    assert bot.activity_calls == [("Typing-Chat", ChatActivity.TYPING)]

    await adapter.disconnect()
    assert adapter.is_connected is False
    assert bot.shutdown_calls == 1
    assert bot.run_exited.is_set()
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and task.get_name().startswith("trueconf-") and not task.done()
    ]


def test_inbound_p2p_text_preserves_identity_and_timestamp(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_inbound_p2p_text(registered_plugin, monkeypatch))


async def _inbound_p2p_text(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf import Router
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.text import TextContent

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler: dict[str, Any] = {}
    real_message = Router.message

    def record_message_handler(router, *filters):
        register = real_message(router, *filters)

        def capture(handler):
            sdk_handler["handler"] = handler
            return register(handler)

        return capture

    monkeypatch.setattr(Router, "message", record_message_handler)
    bot = ControlledBot()
    metadata_calls: list[tuple[str, str]] = []

    async def get_chat_by_id(chat_id):
        metadata_calls.append(("chat", chat_id))
        return type(
            "ChatInfo",
            (),
            {"chat_id": chat_id, "title": "Private chat", "chat_type": 1},
        )()

    async def get_user_display_name(user_id):
        metadata_calls.append(("user", user_id))
        return type("UserInfo", (), {"display_name": "Alice"})()

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = get_user_display_name
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    received = asyncio.Event()
    events = []

    async def on_message(event):
        events.append(event)
        received.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True

    replied = Message(
        chat=_sdk_chat("Chat-ID-AbC", title="Private chat"),
        timestamp=1_787_000_000_000,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id="hermes-bot@video.example.com", type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=6, position="inbox"),
        content=TextContent(
            text="Поехали — запускаю задачу на ~30 секунд, наблюдайте за чатом:",
            parse_mode="text",
        ),
        message_id="message-9",
        is_edited=False,
    ).bind(bot)
    message = Message(
        chat=_sdk_chat("Chat-ID-AbC", title="Private chat"),
        timestamp=1_788_000_000_123,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id="Alice@Video.Example", type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=7, position="inbox"),
        content=TextContent(text="Hello", parse_mode="text"),
        message_id="message-17",
        is_edited=False,
        reply_message_id="message-9",
        reply_message=replied,
    ).bind(bot)
    await sdk_handler["handler"](message)
    await asyncio.wait_for(received.wait(), timeout=1)

    assert len(events) == 1
    event = events[0]
    assert event.text == "Hello"
    assert event.user_id == "Alice@Video.Example"
    assert event.user_name == "Alice"
    assert event.message_id == "message-17"
    assert event.reply_to_message_id == "message-9"
    assert event.reply_to_text == ("Поехали — запускаю задачу на ~30 секунд, наблюдайте за чатом:")
    assert event.reply_to_author_id == "hermes-bot@video.example.com"
    assert event.reply_to_is_own_message is True
    assert event.source.platform.value == "trueconf"
    assert event.source.chat_id == "Chat-ID-AbC"
    assert event.source.chat_name == "Private chat"
    assert event.source.chat_type == "dm"
    assert event.source.user_id == "Alice@Video.Example"
    assert event.source.message_id == "message-17"
    assert event.timestamp.timestamp() == pytest.approx(1_788_000_000.123)
    assert event.timestamp.tzinfo is timezone.utc
    assert metadata_calls == [("user", "Alice@Video.Example")]
    await adapter.disconnect()


def test_inbound_reply_id_without_embedded_message_keeps_id_only(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_inbound_reply_id_without_embedded_message(registered_plugin, monkeypatch))


def test_inbound_partial_quote_uses_selected_fragment(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_inbound_partial_quote_uses_selected_fragment(registered_plugin, monkeypatch))


async def _inbound_partial_quote_uses_selected_fragment(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.text import TextContent

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()
    bot.get_user_display_name = lambda _user: None
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    received = asyncio.Event()
    events = []

    async def on_message(event):
        events.append(event)
        received.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True

    replied = Message(
        chat=_sdk_chat("Chat-ID-AbC", title="Private chat"),
        timestamp=1_787_000_000_000,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id="bob@video.example.com", type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=6, position="inbox"),
        content=TextContent(
            text="Пункт A: релиз. Пункт B: ротация ключей.",
            parse_mode="html",
        ),
        message_id="message-9",
        is_edited=False,
    ).bind(bot)
    message = Message(
        chat=_sdk_chat("Chat-ID-AbC", title="Private chat"),
        timestamp=1_788_000_000_123,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id="alice@video.example.com", type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=7, position="inbox"),
        content=TextContent(
            text=('<quote class="reply">Пункт B: ротация ключей.</quote><br><br>Сделай это сегодня'),
            parse_mode="html",
        ),
        message_id="message-17",
        is_edited=False,
        reply_message_id="message-9",
        reply_message=replied,
    ).bind(bot)

    await sdk_handler["handler"](message)
    await asyncio.wait_for(received.wait(), timeout=1)

    assert len(events) == 1
    event = events[0]
    assert event.text == "Сделай это сегодня"
    assert event.reply_to_message_id == "message-9"
    assert event.reply_to_text == "Пункт B: ротация ключей."
    assert event.reply_to_author_id == "bob@video.example.com"
    await adapter.disconnect()


async def _inbound_reply_id_without_embedded_message(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.text import TextContent

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()
    fetch_calls: list[str] = []
    bot.get_user_display_name = lambda _user: None

    async def get_message_by_id(message_id):
        fetch_calls.append(message_id)
        return SimpleNamespace(
            chat_id="Chat-ID-AbC",
            content=SimpleNamespace(text="unexpected fetch"),
            author=SimpleNamespace(id="hermes-bot@video.example.com"),
        )

    bot.get_message_by_id = get_message_by_id
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    received = asyncio.Event()
    events = []

    async def on_message(event):
        events.append(event)
        received.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True

    message = Message(
        chat=_sdk_chat("Chat-ID-AbC", title="Private chat"),
        timestamp=1_788_000_000_123,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id="Alice@Video.Example", type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=7, position="inbox"),
        content=TextContent(text="Hello", parse_mode="text"),
        message_id="message-17",
        is_edited=False,
        reply_message_id="message-9",
    ).bind(bot)
    await sdk_handler["handler"](message)
    await asyncio.wait_for(received.wait(), timeout=1)

    assert len(events) == 1
    event = events[0]
    assert event.reply_to_message_id == "message-9"
    assert event.reply_to_text is None
    assert event.reply_to_author_id is None
    assert event.reply_to_is_own_message is False
    assert fetch_calls == []
    await adapter.disconnect()


def test_inbound_reply_from_different_chat_is_rejected(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_inbound_reply_from_different_chat(registered_plugin, monkeypatch))


async def _inbound_reply_from_different_chat(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.text import TextContent

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()
    bot.get_user_display_name = lambda _user: None
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    received = asyncio.Event()
    events = []

    async def on_message(event):
        events.append(event)
        received.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True

    replied = Message(
        chat=_sdk_chat("Other-Chat", title="Other chat"),
        timestamp=1_787_000_000_000,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id="hermes-bot@video.example.com", type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=6, position="inbox"),
        content=TextContent(text="different chat text", parse_mode="text"),
        message_id="message-9",
        is_edited=False,
    ).bind(bot)
    message = Message(
        chat=_sdk_chat("Chat-ID-AbC", title="Private chat"),
        timestamp=1_788_000_000_123,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id="Alice@Video.Example", type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=7, position="inbox"),
        content=TextContent(text="Hello", parse_mode="text"),
        message_id="message-17",
        is_edited=False,
        reply_message_id="message-9",
        reply_message=replied,
    ).bind(bot)
    await sdk_handler["handler"](message)
    await asyncio.wait_for(received.wait(), timeout=1)

    assert len(events) == 1
    event = events[0]
    assert event.reply_to_message_id == "message-9"
    assert event.reply_to_text is None
    assert event.reply_to_author_id is None
    assert event.reply_to_is_own_message is False
    await adapter.disconnect()


def test_unauthorized_inbound_avoids_optional_sdk_lookups_but_reaches_hermes(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_unauthorized_inbound(registered_plugin, monkeypatch))


async def _unauthorized_inbound(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.text import TextContent

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    metadata_calls: list[tuple[str, str]] = []

    async def get_chat_by_id(chat_id):
        metadata_calls.append(("chat", chat_id))
        return type(
            "ChatInfo",
            (),
            {"chat_id": chat_id, "title": "Private chat", "chat_type": 1},
        )()

    async def get_user_display_name(user_id):
        metadata_calls.append(("user", user_id))
        raise AssertionError("unauthorized input must not fetch a display name")

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = get_user_display_name
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    authorization_calls = []
    adapter.set_authorization_check(lambda user, kind, chat: authorization_calls.append((user, kind, chat)) or False)
    events = []
    delivered = asyncio.Event()

    async def on_message(event):
        events.append(event)
        delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True
    message = Message(
        chat=_sdk_chat("private-chat", title="Private chat"),
        timestamp=1_788_000_000_123,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id="unknown-user", type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=7, position="inbox"),
        content=TextContent(text="pair me", parse_mode="text"),
        message_id="message-18",
        is_edited=False,
        reply_message_id=None,
    ).bind(bot)

    await adapter._handle_trueconf_message(message, adapter._generation)
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert authorization_calls == [("unknown-user", "dm", "private-chat")]
    assert metadata_calls == []
    assert len(events) == 1
    assert events[0].user_name == "unknown-user"
    assert events[0].text == "pair me"
    await adapter.disconnect()


def test_authentication_failure_is_fatal_and_leaves_no_running_bot(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_authentication_failure(registered_plugin, monkeypatch))


async def _authentication_failure(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf.exceptions import InvalidGrantError

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot(authorize=False)
    bot.stop_with(InvalidGrantError("invalid credentials"))
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)

    assert await adapter.connect() is False
    assert adapter.is_connected is False
    assert adapter.fatal_error_code == "authentication_failed"
    assert adapter.fatal_error_retryable is False
    assert "secret" not in (adapter.fatal_error_message or "")
    assert bot.shutdown_calls == 1
    assert bot.run_exited.is_set()


def test_readiness_failure_is_retryable_and_releases_the_bot(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_readiness_failure(registered_plugin, monkeypatch))


async def _readiness_failure(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot(authorize=False)
    bot.stop_with()
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)

    assert await adapter.connect() is False
    assert adapter.is_connected is False
    assert adapter.fatal_error_code == "transport_stopped"
    assert adapter.fatal_error_retryable is True
    assert bot.shutdown_calls == 1
    assert bot.run_exited.is_set()


def test_cancelled_sdk_run_during_connect_fails_cleanly(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_cancelled_sdk_run_during_connect(registered_plugin, monkeypatch))


async def _cancelled_sdk_run_during_connect(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot(authorize=False)
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)

    connect_task = asyncio.create_task(adapter.connect())
    await bot.run_started.wait()
    assert adapter._run_task is not None
    adapter._run_task.cancel()

    assert await connect_task is False
    assert adapter.is_connected is False
    assert adapter.fatal_error_code == "transport_stopped"
    assert bot.shutdown_calls == 1
    assert bot.run_exited.is_set()
    assert adapter._run_task is None


def test_connect_cancellation_waits_for_creation_then_cleans_up(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_connect_cancellation(registered_plugin, monkeypatch))


def test_repeated_connect_cancellation_still_cleans_up(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_repeated_connect_cancellation(registered_plugin, monkeypatch))


def test_disconnect_during_credential_creation_settles_the_created_bot(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_disconnect_during_creation(registered_plugin, monkeypatch))


async def _disconnect_during_creation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf import Bot

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    factory_entered = threading.Event()
    release_factory = threading.Event()
    bot = ControlledBot()

    def from_credentials(cls, server, username, password, **kwargs):
        factory_entered.set()
        assert release_factory.wait(timeout=2)
        return bot

    monkeypatch.setattr(Bot, "from_credentials", classmethod(from_credentials))
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    connect_task = asyncio.create_task(adapter.connect())
    assert await asyncio.to_thread(factory_entered.wait, 1)

    disconnect_task = asyncio.create_task(adapter.disconnect())
    await asyncio.sleep(0)
    assert disconnect_task.done() is False
    release_factory.set()

    await disconnect_task
    assert await connect_task is False
    assert adapter.is_connected is False
    assert bot.shutdown_calls == 1
    assert bot.run_started.is_set() is False
    assert adapter._bot_creation_task is None
    assert adapter._run_task is None


async def _connect_cancellation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf import Bot

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    factory_entered = threading.Event()
    release_factory = threading.Event()
    bot = ControlledBot()

    def from_credentials(cls, server, username, password, **kwargs):
        factory_entered.set()
        assert release_factory.wait(timeout=2)
        return bot

    monkeypatch.setattr(Bot, "from_credentials", classmethod(from_credentials))
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    connect_task = asyncio.create_task(adapter.connect())
    assert await asyncio.to_thread(factory_entered.wait, 1)

    connect_task.cancel()
    await asyncio.sleep(0)
    assert connect_task.done() is False
    release_factory.set()
    with pytest.raises(asyncio.CancelledError):
        await connect_task

    assert adapter.is_connected is False
    assert bot.shutdown_calls == 1
    assert adapter._bot_creation_task is None
    assert adapter._run_task is None


async def _repeated_connect_cancellation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf import Bot

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    factory_entered = threading.Event()
    release_factory = threading.Event()
    bot = ControlledBot()

    def from_credentials(cls, server, username, password, **kwargs):
        factory_entered.set()
        assert release_factory.wait(timeout=2)
        return bot

    monkeypatch.setattr(Bot, "from_credentials", classmethod(from_credentials))
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    connect_task = asyncio.create_task(adapter.connect())
    assert await asyncio.to_thread(factory_entered.wait, 1)

    connect_task.cancel()
    await asyncio.sleep(0)
    connect_task.cancel()
    await asyncio.sleep(0)
    assert connect_task.done() is False
    release_factory.set()

    with pytest.raises(asyncio.CancelledError):
        await connect_task
    assert adapter.is_connected is False
    assert adapter._lock_acquired is False
    assert bot.shutdown_calls == 1
    assert adapter._bot_creation_task is None
    assert adapter._bot is None
    assert adapter._run_task is None


def test_send_returns_the_trueconf_message_identity(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_send_returns_message_identity(registered_plugin, monkeypatch))


def test_standalone_text_delivery_waits_for_readiness_and_disconnects(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_standalone_text_delivery(registered_plugin, monkeypatch))


def test_interactive_setup_saves_trueconf_connection_access_and_home(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hermes_cli.cli_output as cli_output
    import hermes_cli.config as cli_config

    saved: dict[str, str] = {}
    removed: list[str] = []
    config_writes: list[tuple[str, str, bool, bool]] = []
    password_flags: list[bool] = []
    answers = iter(
        [
            "video.example.com",
            "hermes-bot",
            "<REDACTED>",
            "alice@example.com, bob@example.com",
            "Exact-Home-Chat-ID",
        ]
    )

    monkeypatch.setattr(cli_config, "get_env_value", lambda _key: "")
    monkeypatch.setattr(cli_config, "save_env_value", saved.__setitem__)
    monkeypatch.setattr(
        cli_config,
        "write_platform_config_field",
        lambda platform, field, value, *, raw=False: config_writes.append((platform, field, value, raw)),
    )
    monkeypatch.setattr(
        cli_config,
        "remove_env_value",
        lambda key: removed.append(key) or False,
    )

    def prompt(_question, *, default="", password=False):
        password_flags.append(password)
        return next(answers)

    monkeypatch.setattr(cli_output, "prompt", prompt)
    monkeypatch.setattr(cli_output, "prompt_yes_no", lambda *_a, **_kw: False)
    for name in ("print_header", "print_info", "print_success", "print_warning"):
        monkeypatch.setattr(cli_output, name, lambda *_a, **_kw: None)

    registered_plugin.get("trueconf").setup_fn()

    assert saved == {
        "TRUECONF_SERVER": "video.example.com",
        "TRUECONF_USERNAME": "hermes-bot",
        "TRUECONF_PASSWORD": "<REDACTED>",
        "TRUECONF_ALLOWED_USERS": "alice@example.com,bob@example.com",
        "TRUECONF_HOME_CHANNEL": "Exact-Home-Chat-ID",
    }
    assert "TRUECONF_ALLOW_ALL_USERS" in removed
    assert config_writes == [("trueconf", "verify_ssl", False, True)]
    assert password_flags == [False, False, True, False, False]


def test_interactive_setup_keeps_existing_configuration_when_declined(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hermes_cli.cli_output as cli_output
    import hermes_cli.config as cli_config

    existing = {
        "TRUECONF_SERVER": "video.example.com",
        "TRUECONF_USERNAME": "hermes-bot",
        "TRUECONF_PASSWORD": "<REDACTED>",
    }
    monkeypatch.setattr(cli_config, "get_env_value", existing.get)
    monkeypatch.setattr(
        cli_config,
        "save_env_value",
        lambda *_a: pytest.fail("declined setup must not overwrite configuration"),
    )
    monkeypatch.setattr(
        cli_output,
        "prompt",
        lambda *_a, **_kw: pytest.fail("declined setup must not prompt for values"),
    )
    monkeypatch.setattr(cli_output, "prompt_yes_no", lambda *_a, **_kw: False)
    for name in ("print_header", "print_info", "print_success", "print_warning"):
        monkeypatch.setattr(cli_output, name, lambda *_a, **_kw: None)

    registered_plugin.get("trueconf").setup_fn()


def test_interactive_setup_warns_when_no_users_are_allowed(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hermes_cli.cli_output as cli_output
    import hermes_cli.config as cli_config

    answers = iter(["video.example.com", "hermes-bot", "<REDACTED>", "", ""])
    confirmations = iter([True, False])
    warnings: list[str] = []

    monkeypatch.setattr(cli_config, "get_env_value", lambda _key: "")
    monkeypatch.setattr(cli_config, "save_env_value", lambda *_a: None)
    monkeypatch.setattr(cli_config, "remove_env_value", lambda *_a: False)
    monkeypatch.setattr(
        cli_config,
        "write_platform_config_field",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        cli_output,
        "prompt",
        lambda *_a, **_kw: next(answers),
    )
    monkeypatch.setattr(
        cli_output,
        "prompt_yes_no",
        lambda *_a, **_kw: next(confirmations),
    )
    monkeypatch.setattr(cli_output, "print_warning", warnings.append)
    for name in ("print_header", "print_info", "print_success"):
        monkeypatch.setattr(cli_output, name, lambda *_a, **_kw: None)

    registered_plugin.get("trueconf").setup_fn()

    assert warnings == [
        "No users are allowed. The bot will ignore all incoming messages until "
        "you approve a pairing request, add user IDs, or enable allow-all access."
    ]


def test_standalone_rejects_incomplete_config_before_adapter_creation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(
        _standalone_rejects_incomplete_config_before_adapter_creation(
            registered_plugin,
            monkeypatch,
        )
    )


async def _standalone_rejects_incomplete_config_before_adapter_creation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.config import PlatformConfig

    for key in ("TRUECONF_SERVER", "TRUECONF_USERNAME", "TRUECONF_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRUECONF_HOME_CHANNEL", "Exact-Home-Chat-ID")
    sender = registered_plugin.get("trueconf").standalone_sender_fn
    monkeypatch.setitem(
        sender.__globals__,
        "_create_adapter",
        lambda _config: pytest.fail("invalid config must not create an adapter"),
    )

    result = await sender(
        PlatformConfig(enabled=True, extra={}),
        "Exact-Home-Chat-ID",
        "hello",
    )

    assert result == {
        "error": (
            "TrueConf configuration invalid: server must be a non-empty hostname/address without a scheme or path"
        ),
        "error_kind": "unknown",
        "retryable": False,
    }


async def _standalone_text_delivery(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.config import PlatformConfig
    from trueconf import ParseMode
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sent: list[dict[str, Any]] = []

    async def send_message(**kwargs):
        assert bot.authorized_event.is_set()
        sent.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="cron-message-42",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    sender = registered_plugin.get("trueconf").standalone_sender_fn

    assert sender is not None
    result = await sender(
        PlatformConfig(enabled=True, extra={"server": "video.example.com"}),
        "Exact-Cron-Chat",
        "Scheduled **HTML**",
        thread_id=None,
        media_files=None,
        force_document=False,
    )

    assert result == {"success": True, "message_id": "cron-message-42"}
    assert sent == [
        {
            "chat_id": "Exact-Cron-Chat",
            "text": "Scheduled <b>HTML</b>",
            "parse_mode": ParseMode.HTML,
            "reply_message_id": None,
        }
    ]
    assert bot.shutdown_calls == 1
    assert bot.run_exited.is_set()
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and task.get_name().startswith("trueconf-") and not task.done()
    ]


def test_standalone_delivery_sends_text_then_every_media_file(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(_standalone_text_then_media(registered_plugin, monkeypatch, tmp_path))


async def _standalone_text_then_media(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from gateway.config import PlatformConfig
    from trueconf.types.responses.send_file_response import SendFileResponse
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    paths = [
        tmp_path / "image.png",
        tmp_path / "video.mp4",
        tmp_path / "voice.ogg",
        tmp_path / "report.pdf",
    ]
    payloads = [b"image", b"video", b"voice", b"document"]
    for path, payload in zip(paths, payloads, strict=True):
        path.write_bytes(payload)

    bot = ControlledBot()
    operations: list[tuple[str, str]] = []

    async def send_message(**kwargs):
        operations.append(("text", kwargs["text"]))
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="text-message",
            timestamp=1_788_000_000_123,
        )

    async def send_photo(**kwargs):
        operations.append(("image", kwargs["file"].file_name))
        return SendFileResponse(
            chat_id=kwargs["chat_id"],
            message_id="image-message",
            file_id="image-file",
            timestamp=1_788_000_000_124,
        )

    async def send_document(**kwargs):
        operations.append(("file", kwargs["file"].file_name))
        return SendFileResponse(
            chat_id=kwargs["chat_id"],
            message_id=f"file-message-{len(operations)}",
            file_id=f"file-{len(operations)}",
            timestamp=1_788_000_000_125,
        )

    bot.send_message = send_message
    bot.send_photo = send_photo
    bot.send_document = send_document
    install_bot(monkeypatch, bot)
    sender = registered_plugin.get("trueconf").standalone_sender_fn

    result = await sender(
        PlatformConfig(enabled=True, extra={"server": "video.example.com"}),
        "Media-Cron-Chat",
        "Scheduled report",
        media_files=[
            (str(paths[0]), False),
            (str(paths[1]), False),
            (str(paths[2]), True),
            (str(paths[3]), False),
        ],
        force_document=False,
    )

    assert result == {
        "success": True,
        "message_id": "file-message-5",
        "media_delivered": True,
    }
    assert operations == [
        ("text", "Scheduled report"),
        ("image", "image.png"),
        ("file", "video.mp4"),
        ("file", "voice.ogg"),
        ("file", "report.pdf"),
    ]
    assert [path.read_bytes() for path in paths] == payloads
    assert bot.shutdown_calls == 1


def test_standalone_partial_failure_reports_already_delivered_message_ids(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(_standalone_partial_failure(registered_plugin, monkeypatch, tmp_path))


async def _standalone_partial_failure(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from gateway.config import PlatformConfig
    from trueconf.exceptions import ApiErrorException
    from trueconf.types.responses.send_file_response import SendFileResponse
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    image = tmp_path / "first.png"
    document = tmp_path / "second.pdf"
    image.write_bytes(b"image")
    document.write_bytes(b"document")
    bot = ControlledBot()

    async def send_message(**kwargs):
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="delivered-text",
            timestamp=1_788_000_000_123,
        )

    async def send_photo(**kwargs):
        return SendFileResponse(
            chat_id=kwargs["chat_id"],
            message_id="delivered-image",
            file_id="image-file",
            timestamp=1_788_000_000_124,
        )

    async def send_document(**_kwargs):
        raise ApiErrorException(302, "private failure detail")

    bot.send_message = send_message
    bot.send_photo = send_photo
    bot.send_document = send_document
    install_bot(monkeypatch, bot)
    sender = registered_plugin.get("trueconf").standalone_sender_fn
    result = await sender(
        PlatformConfig(enabled=True, extra={"server": "video.example.com"}),
        "Cron-Chat-ID",
        "text",
        media_files=[(str(image), False), (str(document), False)],
    )

    assert result["error_kind"] == "forbidden"
    assert result["retryable"] is False
    assert result["message_id"] == "delivered-image"
    assert result["message_ids"] == ["delivered-text", "delivered-image"]
    assert "private failure detail" not in result["error"]
    assert bot.shutdown_calls == 1


def test_standalone_delivery_classifies_startup_auth_and_send_failures(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_standalone_failures(registered_plugin, monkeypatch))


async def _standalone_failures(registered_plugin, monkeypatch) -> None:
    from gateway.config import PlatformConfig
    from trueconf import Bot
    from trueconf.exceptions import ApiErrorException, InvalidGrantError

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sender = registered_plugin.get("trueconf").standalone_sender_fn
    config = PlatformConfig(enabled=True, extra={"server": "video.example.com"})

    def failing_factory(cls, server, username, password, **kwargs):
        raise OSError("credential-shaped transport detail")

    monkeypatch.setattr(Bot, "from_credentials", classmethod(failing_factory))
    startup = await sender(config, "Cron-Chat-ID", "text")
    assert startup["error_kind"] == "transport_unavailable"
    assert startup["retryable"] is True
    assert "credential-shaped transport detail" not in startup["error"]

    auth_bot = ControlledBot(authorize=False)
    auth_bot.stop_with(InvalidGrantError("credential-shaped auth detail"))
    install_bot(monkeypatch, auth_bot)
    auth = await sender(config, "Cron-Chat-ID", "text")
    assert auth["error_kind"] == "authentication_failed"
    assert auth["retryable"] is False
    assert "credential-shaped auth detail" not in auth["error"]
    assert auth_bot.shutdown_calls == 1

    send_bot = ControlledBot()

    async def failing_send(**_kwargs):
        raise ApiErrorException(100, "credential-shaped send detail")

    send_bot.send_message = failing_send
    install_bot(monkeypatch, send_bot)
    send = await sender(config, "Cron-Chat-ID", "text")
    assert send["error_kind"] == "transient"
    assert send["retryable"] is True
    assert "credential-shaped send detail" not in send["error"]
    assert send_bot.shutdown_calls == 1
    assert send_bot.run_exited.is_set()


def test_standalone_delivery_cancellation_disconnects_and_propagates(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_standalone_cancellation(registered_plugin, monkeypatch))


async def _standalone_cancellation(registered_plugin, monkeypatch) -> None:
    from gateway.config import PlatformConfig

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    send_started = asyncio.Event()

    async def cancelled_send(**_kwargs):
        send_started.set()
        await asyncio.Event().wait()

    bot.send_message = cancelled_send
    install_bot(monkeypatch, bot)
    sender = registered_plugin.get("trueconf").standalone_sender_fn
    task = asyncio.create_task(
        sender(
            PlatformConfig(enabled=True, extra={"server": "video.example.com"}),
            "Cron-Chat-ID",
            "text",
        )
    )
    await send_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert bot.shutdown_calls == 1
    assert bot.run_exited.is_set()
    assert not [
        pending
        for pending in asyncio.all_tasks()
        if pending is not asyncio.current_task() and pending.get_name().startswith("trueconf-") and not pending.done()
    ]


def test_standalone_media_only_force_document_is_explicit(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(
        _standalone_media_only_force_document(
            registered_plugin,
            monkeypatch,
            tmp_path,
        )
    )


async def _standalone_media_only_force_document(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from gateway.config import PlatformConfig
    from trueconf.types.responses.send_file_response import SendFileResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    image = tmp_path / "diagram.png"
    image.write_bytes(b"caller-owned image")
    bot = ControlledBot()
    methods = []

    async def send_document(**kwargs):
        methods.append(("document", kwargs["file"].file_name))
        return SendFileResponse(
            chat_id=kwargs["chat_id"],
            message_id="document-message",
            file_id="document-file",
            timestamp=1_788_000_000_125,
        )

    async def unexpected_photo(**_kwargs):
        raise AssertionError("force_document must bypass photo delivery")

    bot.send_document = send_document
    bot.send_photo = unexpected_photo
    install_bot(monkeypatch, bot)
    sender = registered_plugin.get("trueconf").standalone_sender_fn
    result = await sender(
        PlatformConfig(enabled=True, extra={"server": "video.example.com"}),
        "Cron-Chat-ID",
        "",
        thread_id="accepted-but-not-used",
        media_files=[(str(image), False)],
        force_document=True,
    )

    assert result == {
        "success": True,
        "message_id": "document-message",
        "media_delivered": True,
    }
    assert methods == [("document", "diagram.png")]
    assert image.read_bytes() == b"caller-owned image"
    assert bot.shutdown_calls == 1


def test_public_hermes_send_uses_standalone_trueconf_without_gateway_adapter(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.send_message_tool import send_message_tool
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    monkeypatch.setenv("TRUECONF_SERVER", "video.example.com")
    monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: None)
    bot = ControlledBot()

    async def send_message(**kwargs):
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="detached-message",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)

    result = json.loads(
        send_message_tool(
            {
                "target": "trueconf:Exact-Cron-Chat",
                "message": "Detached cron result",
            }
        )
    )

    assert result["success"] is True
    assert result["message_id"] == "detached-message"
    assert bot.shutdown_calls == 1
    assert bot.run_exited.is_set()


def test_public_hermes_send_delivers_media_through_standalone_trueconf(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tools.send_message_tool import send_message_tool
    from trueconf.types.responses.send_file_response import SendFileResponse
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    monkeypatch.setenv("TRUECONF_SERVER", "video.example.com")
    monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: None)
    image = tmp_path / "cron-image.png"
    image.write_bytes(b"caller-owned image")
    bot = ControlledBot()
    operations = []

    async def send_message(**kwargs):
        operations.append(("text", kwargs["text"]))
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="detached-text",
            timestamp=1_788_000_000_123,
        )

    async def send_photo(**kwargs):
        operations.append(("image", kwargs["file"].file_name))
        return SendFileResponse(
            chat_id=kwargs["chat_id"],
            message_id="detached-image",
            file_id="detached-file",
            timestamp=1_788_000_000_124,
        )

    bot.send_message = send_message
    bot.send_photo = send_photo
    install_bot(monkeypatch, bot)
    result = json.loads(
        send_message_tool(
            {
                "target": "trueconf:Exact-Cron-Chat",
                "message": f"Detached report\nMEDIA:{image}",
            }
        )
    )

    assert result["success"] is True
    assert result["message_id"] == "detached-image"
    assert result["media_delivered"] is True
    assert "warnings" not in result
    assert operations == [
        ("text", "Detached report"),
        ("image", "cron-image.png"),
    ]
    assert image.read_bytes() == b"caller-owned image"
    assert bot.shutdown_calls == 1


async def _send_returns_message_identity(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sent: list[dict[str, Any]] = []

    async def send_message(**kwargs):
        sent.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="message-42",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.send(
        "real-chat-id",
        "Hello, **TrueConf**!",
        reply_to="message-17",
    )

    assert result.success is True
    assert result.message_id == "message-42"
    assert result.error is None
    assert sent == [
        {
            "chat_id": "real-chat-id",
            "text": "Hello, <b>TrueConf</b>!",
            "parse_mode": adapter.parse_mode,
            "reply_message_id": "message-17",
        }
    ]
    await adapter.disconnect()


def test_html_send_uses_sdk_line_break_and_native_markup(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(
        _html_send_uses_sdk_line_break_and_native_markup(
            registered_plugin,
            monkeypatch,
        )
    )


async def _html_send_uses_sdk_line_break_and_native_markup(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf import ParseMode
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sent: list[dict[str, Any]] = []

    async def send_message(**kwargs):
        sent.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="normalized-message",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.send(
        "real-chat-id",
        "Install **mcp** in the runtime.\nRestart the agent.",
    )

    assert result.success is True
    assert sent[0]["text"] == ("Install <b>mcp</b> in the runtime.<br>Restart the agent.")
    assert sent[0]["parse_mode"] is ParseMode.HTML
    assert adapter.format_message("first\nsecond") == "first<br>second"
    await adapter.disconnect()


def test_adapter_defensively_uses_html_when_validation_is_bypassed(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.config import PlatformConfig
    from trueconf import ParseMode

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    entry = registered_plugin.get("trueconf")
    adapter = entry.adapter_factory(
        PlatformConfig(
            enabled=True,
            extra={"server": "video.example.com", "parse_mode": "markdown"},
        )
    )

    assert adapter.parse_mode is ParseMode.HTML
    assert adapter.format_message("one\ntwo") == "one<br>two"


def test_edit_classifies_sdk_failures_without_retrying(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_edit_failure_classification(registered_plugin, monkeypatch))


async def _edit_failure_classification(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf.exceptions import ApiErrorException, TextMessageTooLongError

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    failures = iter(
        [
            ApiErrorException(304, "deleted"),
            ApiErrorException(302, "forbidden"),
            ApiErrorException(100, "busy"),
            ApiErrorException(999, "private remote detail"),
            TextMessageTooLongError(4097),
        ]
    )
    calls: list[dict[str, Any]] = []

    async def failing_edit(**kwargs):
        calls.append(kwargs)
        raise next(failures)

    bot.edit_message = failing_edit
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    contents = ["deleted", "forbidden", "transient", "unknown", "x" * 4097]
    results = [await adapter.edit_message("chat", "message-42", content) for content in contents]

    assert [(result.error_kind, result.retryable) for result in results] == [
        ("not_found", False),
        ("forbidden", False),
        ("transient", True),
        ("unknown", False),
        ("too_long", False),
    ]
    assert all(result.message_id is None for result in results)
    assert all("TrueConf edit failed" in (result.error or "") for result in results)
    assert all("private remote detail" not in (result.error or "") for result in results)
    # The oversized edit is split before the SDK call, so the head chunk (4096
    # visible chars) is what reaches the SDK; the SDK rejection is still
    # classified as too_long without retrying.
    assert [call["text"] for call in calls] == [*contents[:-1], "x" * 4096]
    assert len(calls) == len(contents)
    await adapter.disconnect()


def test_edit_reports_disconnection_and_propagates_cancellation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_edit_disconnection_and_cancellation(registered_plugin, monkeypatch))


async def _edit_disconnection_and_cancellation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    adapter = create_adapter(registered_plugin)

    disconnected = await adapter.edit_message("chat", "message-42", "text")
    assert (disconnected.success, disconnected.error_kind, disconnected.retryable) == (
        False,
        "transient",
        True,
    )

    bot = ControlledBot()
    edit_started = asyncio.Event()

    async def cancelled_edit(**_kwargs):
        edit_started.set()
        await asyncio.Event().wait()

    bot.edit_message = cancelled_edit
    install_bot(monkeypatch, bot)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    edit_task = asyncio.create_task(adapter.edit_message("chat", "message-42", "text"))
    await edit_started.wait()
    edit_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await edit_task
    assert adapter.is_connected is True
    await adapter.disconnect()


def test_send_classifies_public_sdk_failures_and_propagates_cancellation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_send_failures_and_cancellation(registered_plugin, monkeypatch))


async def _send_failures_and_cancellation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf.exceptions import (
        ApiErrorException,
        TextMessageTooLongError,
        WSConnectionError,
    )

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    failures = iter(
        [
            TextMessageTooLongError(4097),
            ApiErrorException(302, "forbidden"),
            ApiErrorException(304, "missing"),
            ApiErrorException(100, "busy"),
            WSConnectionError("offline"),
            RuntimeError("unexpected secret detail"),
        ]
    )

    async def failing_send(**_kwargs):
        raise next(failures)

    bot.send_message = failing_send
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    results = [await adapter.send("chat", "text") for _ in range(6)]
    assert [(result.error_kind, result.retryable, result.message_id) for result in results] == [
        ("too_long", False, None),
        ("forbidden", False, None),
        ("not_found", False, None),
        ("transient", True, None),
        ("transient", True, None),
        ("unknown", False, None),
    ]
    assert all("unexpected secret detail" not in (result.error or "") for result in results)

    send_started = asyncio.Event()

    async def cancelled_send(**_kwargs):
        send_started.set()
        await asyncio.Event().wait()

    bot.send_message = cancelled_send
    send_task = asyncio.create_task(adapter.send("chat", "text"))
    await send_started.wait()
    send_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await send_task
    assert adapter.is_connected is True
    await adapter.disconnect()


def test_sdk_loop_exit_notifies_one_retryable_fatal_error(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_sdk_loop_exit(registered_plugin, monkeypatch))


def test_disconnect_cancellation_is_re_raised_after_cleanup(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_disconnect_cancellation(registered_plugin, monkeypatch))


async def _disconnect_cancellation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    shutdown_entered = asyncio.Event()
    release_shutdown = asyncio.Event()
    regular_shutdown = bot.shutdown

    async def blocked_shutdown():
        shutdown_entered.set()
        await release_shutdown.wait()
        await regular_shutdown()

    bot.shutdown = blocked_shutdown
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    disconnect_task = asyncio.create_task(adapter.disconnect())
    await shutdown_entered.wait()
    disconnect_task.cancel()
    await asyncio.sleep(0)
    assert disconnect_task.done() is False
    release_shutdown.set()
    with pytest.raises(asyncio.CancelledError):
        await disconnect_task

    assert adapter.is_connected is False
    assert adapter._run_task is None
    assert bot.shutdown_calls == 1
    assert bot.run_exited.is_set()


async def _sdk_loop_exit(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trueconf.exceptions import WSConnectionError

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    notified = asyncio.Event()
    fatal_states = []

    async def on_fatal(failed_adapter):
        fatal_states.append((failed_adapter.fatal_error_code, failed_adapter.fatal_error_retryable))
        notified.set()

    adapter.set_fatal_error_handler(on_fatal)
    assert await adapter.connect() is True

    bot.stop_with(WSConnectionError("network down"))
    await asyncio.wait_for(notified.wait(), timeout=1)

    assert fatal_states == [("transport_unavailable", True)]
    assert adapter.is_connected is False
    assert bot.shutdown_calls == 1
    assert bot.run_exited.is_set()
    await adapter.disconnect()
    await adapter.disconnect()
    assert bot.shutdown_calls == 1


def _capture_public_sdk_message_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    from trueconf import Router

    captured: dict[str, Any] = {}
    real_message = Router.message

    def record_message_handler(router, *filters):
        register = real_message(router, *filters)

        def capture(handler):
            captured["handler"] = handler
            return register(handler)

        return capture

    monkeypatch.setattr(Router, "message", record_message_handler)
    return captured


def _sdk_chat(chat_id: str, *, title: str | None = None, chat_type: int = 1):
    from trueconf.enums import ChatType
    from trueconf.types import Chat

    try:
        sdk_chat_type = ChatType(chat_type)
    except ValueError:
        sdk_chat_type = chat_type
    return Chat(
        chat_id=chat_id,
        chat_title=title or chat_id,
        chat_type=sdk_chat_type,
    )


def _sdk_text_message(
    bot,
    *,
    chat_id: str,
    author_id: str,
    text: str,
    message_id: str,
    chat_title: str | None = None,
    chat_type: int = 1,
    box_id: int = 7,
    box_position: str = "inbox",
):
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.text import TextContent

    return Message(
        chat=_sdk_chat(chat_id, title=chat_title, chat_type=chat_type),
        timestamp=1_788_000_000_123,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id=author_id, type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=box_id, position=box_position),
        content=TextContent(text=text, parse_mode="text"),
        message_id=message_id,
        is_edited=False,
        reply_message_id="parent-message",
    ).bind(bot)


def _sdk_forwarded_message(
    bot,
    *,
    chat_id: str,
    author_id: str,
    text: str,
    message_id: str,
):
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.text import TextContent

    forwarded = Message(
        chat=_sdk_chat("Original-Chat", title="Original chat"),
        timestamp=1_787_000_000_123,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id="original@video.example", type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=6, position="inbox"),
        content=TextContent(text=text, parse_mode="text"),
        message_id="original-message",
        is_edited=False,
    )
    return Message(
        chat=_sdk_chat(chat_id),
        timestamp=1_788_000_000_123,
        type=MessageType.FORWARDED_MESSAGE,
        author=EnvelopeAuthor(id=author_id, type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=7, position="inbox"),
        content=forwarded,
        message_id=message_id,
        is_edited=False,
    ).bind(bot)


def _sdk_location_message(
    bot,
    *,
    chat_id: str,
    author_id: str,
    message_id: str,
    latitude: float,
    longitude: float,
    title: str,
):
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.location import Location

    return Message(
        chat=_sdk_chat(chat_id),
        timestamp=1_788_000_000_123,
        type=MessageType.LOCATION,
        author=EnvelopeAuthor(id=author_id, type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=7, position="inbox"),
        content=Location(latitude=latitude, longitude=longitude, title=title),
        message_id=message_id,
        is_edited=False,
    ).bind(bot)


def _sdk_attachment_message(
    bot,
    *,
    chat_id: str,
    author_id: str,
    message_id: str,
    file_id: str,
    file_name: str,
    file_size: int,
    mime_type: str,
):
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.attachment import AttachmentContent

    return Message(
        chat=_sdk_chat(chat_id),
        timestamp=1_788_000_000_123,
        type=MessageType.ATTACHMENT,
        author=EnvelopeAuthor(id=author_id, type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=7, position="inbox"),
        content=AttachmentContent(
            file_id=file_id,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
        ),
        message_id=message_id,
        is_edited=False,
        reply_message_id="document-parent",
    ).bind(bot)


def _sdk_voice_message(
    bot,
    *,
    chat_id: str,
    author_id: str,
    message_id: str,
    file_id: str,
    file_size: int,
    mime_type: str,
    duration: int = 1,
):
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.voice import Voice

    return Message(
        chat=_sdk_chat(chat_id),
        timestamp=1_788_000_000_123,
        type=MessageType.VOICE_MESSAGE,
        author=EnvelopeAuthor(id=author_id, type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=7, position="inbox"),
        content=Voice(
            file_id=file_id,
            file_size=file_size,
            mime_type=mime_type,
            duration=duration,
        ),
        message_id=message_id,
        is_edited=False,
        reply_message_id="voice-parent",
    ).bind(bot)


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.mark.parametrize(
    ("message_factory", "expected_type", "expected_text"),
    [
        (
            lambda bot: _sdk_forwarded_message(
                bot,
                chat_id="Forwarded-Chat",
                author_id="Alice@Video.Example",
                text="Original text",
                message_id="forwarded-message",
            ),
            "TEXT",
            "[Forwarded message]\nOriginal text",
        ),
        (
            lambda bot: _sdk_location_message(
                bot,
                chat_id="Location-Chat",
                author_id="Alice@Video.Example",
                message_id="location-message",
                latitude=55.7558,
                longitude=37.6173,
                title="Red Square",
            ),
            "LOCATION",
            (
                "[The user shared a location pin.]\n"
                "Venue: Red Square\n"
                "latitude: 55.7558\n"
                "longitude: 37.6173\n"
                "Map: https://www.google.com/maps/search/"
                "?api=1&query=55.7558,37.6173\n"
                "Ask what they'd like to find nearby (restaurants, cafes, etc.) "
                "and any preferences."
            ),
        ),
    ],
)
def test_forwarded_text_and_location_reach_hermes(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    message_factory,
    expected_type: str,
    expected_text: str,
) -> None:
    asyncio.run(
        _forwarded_text_or_location_reaches_hermes(
            registered_plugin,
            monkeypatch,
            message_factory,
            expected_type,
            expected_text,
        )
    )


async def _forwarded_text_or_location_reaches_hermes(
    registered_plugin,
    monkeypatch,
    message_factory,
    expected_type: str,
    expected_text: str,
) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()

    async def get_user_display_name(_user_id):
        return SimpleNamespace(display_name="Alice")

    bot.get_user_display_name = get_user_display_name
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    events = []
    delivered = asyncio.Event()

    async def on_message(event):
        events.append(event)
        delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True

    await sdk_handler["handler"](message_factory(bot))
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert len(events) == 1
    assert events[0].message_type.name == expected_type
    assert events[0].text == expected_text
    await adapter.disconnect()


def test_authorized_inbound_document_is_downloaded_once_into_hermes_cache(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_authorized_inbound_document(registered_plugin, monkeypatch))


async def _authorized_inbound_document(registered_plugin, monkeypatch) -> None:
    from gateway.platforms.base import MessageType

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()
    download_calls = []

    async def get_chat_by_id(chat_id):
        return type(
            "ChatInfo",
            (),
            {"chat_id": chat_id, "title": "Files", "chat_type": 1},
        )()

    async def get_user_display_name(_user_id):
        return type("UserInfo", (), {"display_name": "Alice"})()

    async def download_file_by_id(file_id, dest_path=None):
        download_calls.append((file_id, dest_path))
        return b"document payload"

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = get_user_display_name
    bot.download_file_by_id = download_file_by_id
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    delivered = asyncio.Event()
    events = []

    async def on_message(event):
        events.append(event)
        delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True

    await sdk_handler["handler"](
        _sdk_attachment_message(
            bot,
            chat_id="Document-Chat",
            author_id="Alice@Video.Example",
            message_id="document-message",
            file_id="file-17",
            file_name="Report.PDF",
            file_size=16,
            mime_type="application/pdf",
        )
    )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert download_calls == [("file-17", None)]
    assert len(events) == 1
    event = events[0]
    assert event.message_type is MessageType.DOCUMENT
    assert event.text == "Document: Report.PDF"
    assert event.media_types == ["application/pdf"]
    assert len(event.media_urls) == 1
    assert Path(event.media_urls[0]).read_bytes() == b"document payload"
    assert event.reply_to_message_id == "document-parent"
    await adapter.disconnect()


def test_outbound_document_preserves_caller_file_name_mime_and_reply(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(_outbound_document(registered_plugin, monkeypatch, tmp_path))


async def _outbound_document(registered_plugin, monkeypatch, tmp_path: Path) -> None:
    from trueconf.types.responses.send_file_response import SendFileResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    source = tmp_path / "caller-owned.bin"
    source.write_bytes(b"outbound document")
    bot = ControlledBot()
    sent = []

    async def send_document(**kwargs):
        sent.append(
            {
                "chat_id": kwargs["chat_id"],
                "file_name": kwargs["file"].file_name,
                "mime_type": kwargs["file"].mime_type,
                "data": await kwargs["file"].read(),
                "caption": kwargs["caption"],
                "parse_mode": kwargs["parse_mode"],
                "reply_message_id": kwargs["reply_message_id"],
            }
        )
        return SendFileResponse(
            chat_id=kwargs["chat_id"],
            message_id="document-sent",
            file_id="uploaded-file",
            timestamp=1_788_000_000_123,
        )

    bot.send_document = send_document
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.send_document(
        "Exact-Group-ID",
        str(source),
        caption="Quarterly report",
        file_name="Visible.PDF",
        reply_to="reply-message",
    )

    assert result.success is True
    assert result.message_id == "document-sent"
    assert sent == [
        {
            "chat_id": "Exact-Group-ID",
            "file_name": "Visible.PDF",
            "mime_type": "application/pdf",
            "data": b"outbound document",
            "caption": "Quarterly report",
            "parse_mode": adapter.parse_mode,
            "reply_message_id": "reply-message",
        }
    ]
    assert source.read_bytes() == b"outbound document"
    await adapter.disconnect()


def test_inbound_document_policy_and_failures_never_expose_untrusted_bytes(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(_inbound_document_failures(registered_plugin, monkeypatch, tmp_path))


async def _inbound_document_failures(registered_plugin, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    (tmp_path / "hermes-home" / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - trueconf-platform\ngateway:\n  max_inbound_media_bytes: 8\n",
        encoding="utf-8",
    )
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()
    download_calls = []

    async def get_chat_by_id(chat_id):
        return type(
            "ChatInfo",
            (),
            {"chat_id": chat_id, "title": "Files", "chat_type": 1},
        )()

    async def download_file_by_id(file_id, dest_path=None):
        download_calls.append((file_id, dest_path))
        if file_id == "download-fails":
            raise OSError("sensitive transport detail")
        return b"payload"

    bot.get_chat_by_id = get_chat_by_id
    bot.download_file_by_id = download_file_by_id
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda user, _kind, _chat: user != "unauthorized@video.example.com")
    delivered = asyncio.Event()
    events = []

    async def on_message(event):
        events.append(event)
        if len(events) == 4:
            delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True

    cases = [
        ("unauthorized@video.example.com", "secret", "secret.txt", 7, "text/plain"),
        (
            "oversize@video.example.com",
            "oversize",
            "large.bin",
            9,
            "application/octet-stream",
        ),
        (
            "malformed@video.example.com",
            "",
            "broken.bin",
            7,
            "application/octet-stream",
        ),
        (
            "failure@video.example.com",
            "download-fails",
            "missing.bin",
            7,
            "application/octet-stream",
        ),
    ]
    for index, (author_id, file_id, file_name, file_size, mime_type) in enumerate(cases):
        await sdk_handler["handler"](
            _sdk_attachment_message(
                bot,
                chat_id=f"Document-Chat-{index}",
                author_id=author_id,
                message_id=f"failure-{index}",
                file_id=file_id,
                file_name=file_name,
                file_size=file_size,
                mime_type=mime_type,
            )
        )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert download_calls == [("download-fails", None)]
    assert [event.media_urls for event in events] == [[], [], [], []]
    assert [event.media_types for event in events] == [[], [], [], []]
    assert events[0].text == "Document: secret.txt"
    assert [event.text for event in events[1:]] == [
        "Document unavailable: large.bin",
        "Document unavailable: broken.bin",
        "Document unavailable: missing.bin",
    ]
    await adapter.disconnect()


def test_outbound_document_failures_and_cancellation_preserve_caller_file(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(_outbound_document_failures(registered_plugin, monkeypatch, tmp_path))


async def _outbound_document_failures(registered_plugin, monkeypatch, tmp_path: Path) -> None:
    from trueconf.exceptions import ApiErrorException, FileCaptionTooLongError

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    source = tmp_path / "caller-owned.pdf"
    source.write_bytes(b"caller bytes")
    bot = ControlledBot()
    failures = iter(
        [
            FileCaptionTooLongError(4097),
            ApiErrorException(100, "temporary upload failure"),
        ]
    )

    async def failing_send_document(**_kwargs):
        raise next(failures)

    bot.send_document = failing_send_document
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True
    caller_owned_files = set(tmp_path.iterdir())

    too_long = await adapter.send_document("chat", str(source), caption="caption")
    transient = await adapter.send_document("chat", str(source))
    assert (too_long.error_kind, too_long.retryable) == ("too_long", False)
    assert (transient.error_kind, transient.retryable) == ("transient", True)
    assert set(tmp_path.iterdir()) == caller_owned_files

    send_started = asyncio.Event()

    async def cancelled_send_document(**_kwargs):
        send_started.set()
        await asyncio.Event().wait()

    bot.send_document = cancelled_send_document
    task = asyncio.create_task(adapter.send_document("chat", str(source)))
    await send_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert source.read_bytes() == b"caller bytes"
    assert set(tmp_path.iterdir()) == caller_owned_files
    await adapter.disconnect()


def test_inbound_document_download_cancellation_propagates_without_a_cached_path(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_inbound_document_download_cancellation(registered_plugin, monkeypatch))


async def _inbound_document_download_cancellation(registered_plugin, monkeypatch) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()
    download_started = asyncio.Event()

    async def get_chat_by_id(chat_id):
        return type("ChatInfo", (), {"title": chat_id, "chat_type": 1})()

    async def download_file_by_id(_file_id, dest_path=None):
        assert dest_path is None
        download_started.set()
        await asyncio.Event().wait()

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = lambda _user: None
    bot.download_file_by_id = download_file_by_id
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    adapter.set_message_handler(lambda _event: None)
    assert await adapter.connect() is True

    task = asyncio.create_task(
        sdk_handler["handler"](
            _sdk_attachment_message(
                bot,
                chat_id="Document-Cancel",
                author_id="alice@video.example.com",
                message_id="document-cancel",
                file_id="file-cancel",
                file_name="cancel.pdf",
                file_size=7,
                mime_type="application/pdf",
            )
        )
    )
    await download_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await adapter.disconnect()


def test_image_media_contract_covers_inbound_and_outbound_success(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(_image_media_success(registered_plugin, monkeypatch, tmp_path))


async def _image_media_success(registered_plugin, monkeypatch, tmp_path: Path) -> None:
    from gateway.platforms.base import MessageType
    from trueconf.enums import ChatActivity
    from trueconf.types.responses.send_file_response import SendFileResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    source = tmp_path / "caller-owned.png"
    source.write_bytes(_ONE_PIXEL_PNG)
    sticker = tmp_path / "caller-owned.webp"
    sticker.write_bytes(b"webp-sticker")
    bot = ControlledBot()
    downloads = []
    sent = []

    async def get_chat_by_id(chat_id):
        return type("ChatInfo", (), {"title": "Pictures", "chat_type": 1})()

    async def get_user_display_name(_user_id):
        return type("UserInfo", (), {"display_name": "Alice"})()

    async def download_file_by_id(file_id, dest_path=None):
        downloads.append((file_id, dest_path))
        return _ONE_PIXEL_PNG

    async def send_photo(**kwargs):
        await asyncio.sleep(0)
        preview = kwargs["preview"]
        sent.append(
            {
                "chat_id": kwargs["chat_id"],
                "file_name": kwargs["file"].file_name,
                "mime_type": kwargs["file"].mime_type,
                "data": await kwargs["file"].read(),
                "preview_is_distinct": preview is not kwargs["file"],
                "preview_file_name": preview.file_name,
                "preview_mime_type": preview.mime_type,
                "preview_data": await preview.read(),
                "caption": kwargs["caption"],
                "reply_message_id": kwargs["reply_message_id"],
            }
        )
        return SendFileResponse(
            chat_id=kwargs["chat_id"],
            message_id="photo-sent",
            file_id="photo-upload",
            timestamp=1_788_000_000_123,
        )

    async def send_sticker(**kwargs):
        await bot.activity_event.wait()
        return SendFileResponse(
            chat_id=kwargs["chat_id"],
            message_id="sticker-sent",
            file_id="sticker-upload",
            timestamp=1_788_000_000_124,
        )

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = get_user_display_name
    bot.download_file_by_id = download_file_by_id
    bot.send_photo = send_photo
    bot.send_sticker = send_sticker
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    delivered = asyncio.Event()
    events = []

    async def on_message(event):
        events.append(event)
        delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True
    await sdk_handler["handler"](
        _sdk_attachment_message(
            bot,
            chat_id="Image-Chat",
            author_id="alice@video.example.com",
            message_id="image-message",
            file_id="image-file",
            file_name="Picture.PNG",
            file_size=len(_ONE_PIXEL_PNG),
            mime_type="image/png",
        )
    )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    event = events[0]
    assert event.message_type is MessageType.PHOTO
    assert event.text == "Image: Picture.PNG"
    assert event.media_types == ["image/png"]
    assert Path(event.media_urls[0]).read_bytes() == _ONE_PIXEL_PNG
    assert downloads == [("image-file", None)]

    result = await adapter.send_image_file(
        "Exact-Image-Chat",
        str(source),
        caption="A picture",
        reply_to="image-parent",
    )
    assert result.success is True
    assert result.message_id == "photo-sent"
    bot.activity_event.clear()
    sticker_result = await adapter.send_sticker(
        "Exact-Sticker-Chat",
        str(sticker),
        reply_to="sticker-parent",
    )
    assert sticker_result.success is True
    assert sticker_result.message_id == "sticker-sent"
    assert bot.activity_calls == [
        ("Exact-Image-Chat", ChatActivity.UPLOADING_FILE),
        ("Exact-Sticker-Chat", ChatActivity.CHOOSING_STICKER),
    ]
    assert sent == [
        {
            "chat_id": "Exact-Image-Chat",
            "file_name": "caller-owned.png",
            "mime_type": "image/png",
            "data": _ONE_PIXEL_PNG,
            "preview_is_distinct": True,
            "preview_file_name": "caller-owned.png",
            "preview_mime_type": "image/png",
            "preview_data": _ONE_PIXEL_PNG,
            "caption": "A picture",
            "reply_message_id": "image-parent",
        }
    ]
    assert source.read_bytes() == _ONE_PIXEL_PNG
    await adapter.disconnect()


def test_image_media_contract_rejects_untrusted_input_and_preserves_files_on_failure(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(_image_media_failures(registered_plugin, monkeypatch, tmp_path))


async def _image_media_failures(registered_plugin, monkeypatch, tmp_path: Path) -> None:
    from trueconf.exceptions import ApiErrorException, FileCaptionTooLongError

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    (tmp_path / "hermes-home" / "config.yaml").write_text(
        "gateway:\n  max_inbound_media_bytes: 8\n",
        encoding="utf-8",
    )
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    source = tmp_path / "caller-image.png"
    source.write_bytes(_ONE_PIXEL_PNG)
    bot = ControlledBot()
    download_calls = []

    async def get_chat_by_id(chat_id):
        return type("ChatInfo", (), {"title": chat_id, "chat_type": 1})()

    async def download_file_by_id(file_id, dest_path=None):
        download_calls.append((file_id, dest_path))
        if file_id == "image-cancel":
            await asyncio.Event().wait()
        raise OSError("download failed")

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = lambda _user: None
    bot.download_file_by_id = download_file_by_id
    failures = iter([FileCaptionTooLongError(4097), ApiErrorException(100, "upload")])

    async def failing_send_photo(**_kwargs):
        raise next(failures)

    bot.send_photo = failing_send_photo
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda user, _kind, _chat: user != "unauthorized@video.example.com")
    events = []
    delivered = asyncio.Event()

    async def on_message(event):
        events.append(event)
        if len(events) == 4:
            delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True
    baseline = set(tmp_path.iterdir())

    cases = [
        ("unauthorized@video.example.com", "secret", "secret.png", 7),
        ("oversize@video.example.com", "oversize", "large.png", 9),
        ("malformed@video.example.com", "", "broken.png", 7),
        ("failure@video.example.com", "download-fails", "missing.png", 7),
    ]
    for index, (author_id, file_id, file_name, file_size) in enumerate(cases):
        await sdk_handler["handler"](
            _sdk_attachment_message(
                bot,
                chat_id=f"Image-Failure-{index}",
                author_id=author_id,
                message_id=f"image-failure-{index}",
                file_id=file_id,
                file_name=file_name,
                file_size=file_size,
                mime_type="image/png",
            )
        )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert download_calls == [("download-fails", None)]
    assert [event.media_urls for event in events] == [[], [], [], []]
    assert events[0].text == "Image: secret.png"
    assert [event.text for event in events[1:]] == [
        "Image unavailable: large.png",
        "Image unavailable: broken.png",
        "Image unavailable: missing.png",
    ]

    too_long = await adapter.send_image_file("chat", str(source), caption="caption")
    transient = await adapter.send_image_file("chat", str(source))
    assert (too_long.error_kind, too_long.retryable) == ("too_long", False)
    assert (transient.error_kind, transient.retryable) == ("transient", True)
    assert set(tmp_path.iterdir()) == baseline

    download_task = asyncio.create_task(
        sdk_handler["handler"](
            _sdk_attachment_message(
                bot,
                chat_id="Image-Cancel",
                author_id="cancel@video.example.com",
                message_id="image-cancel",
                file_id="image-cancel",
                file_name="cancel.png",
                file_size=7,
                mime_type="image/png",
            )
        )
    )
    await asyncio.sleep(0)
    download_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await download_task

    async def cancelled_send_photo(**_kwargs):
        await asyncio.Event().wait()

    bot.send_photo = cancelled_send_photo
    send_task = asyncio.create_task(adapter.send_image_file("chat", str(source)))
    await asyncio.sleep(0)
    send_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await send_task
    assert source.read_bytes() == _ONE_PIXEL_PNG
    assert set(tmp_path.iterdir()) == baseline
    await adapter.disconnect()


def test_video_media_contract_covers_inbound_and_generic_outbound_success(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(_video_media_success(registered_plugin, monkeypatch, tmp_path))


async def _video_media_success(registered_plugin, monkeypatch, tmp_path: Path) -> None:
    from gateway.platforms.base import MessageType
    from trueconf.types.responses.send_file_response import SendFileResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    payload = b"video payload"
    source = tmp_path / "caller-video.mp4"
    source.write_bytes(payload)
    bot = ControlledBot()
    downloads = []
    sent = []

    async def get_chat_by_id(chat_id):
        return type("ChatInfo", (), {"title": "Videos", "chat_type": 1})()

    async def get_user_display_name(_user_id):
        return type("UserInfo", (), {"display_name": "Alice"})()

    async def download_file_by_id(file_id, dest_path=None):
        downloads.append((file_id, dest_path))
        return payload

    async def send_document(**kwargs):
        sent.append(
            {
                "chat_id": kwargs["chat_id"],
                "file_name": kwargs["file"].file_name,
                "mime_type": kwargs["file"].mime_type,
                "data": await kwargs["file"].read(),
                "caption": kwargs["caption"],
                "reply_message_id": kwargs["reply_message_id"],
            }
        )
        return SendFileResponse(
            chat_id=kwargs["chat_id"],
            message_id="video-sent",
            file_id="video-upload",
            timestamp=1_788_000_000_123,
        )

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = get_user_display_name
    bot.download_file_by_id = download_file_by_id
    bot.send_document = send_document
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    delivered = asyncio.Event()
    events = []

    async def on_message(event):
        events.append(event)
        delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True
    await sdk_handler["handler"](
        _sdk_attachment_message(
            bot,
            chat_id="Video-Chat",
            author_id="alice@video.example.com",
            message_id="video-message",
            file_id="video-file",
            file_name="Clip.MP4",
            file_size=len(payload),
            mime_type="video/mp4",
        )
    )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    event = events[0]
    assert event.message_type is MessageType.VIDEO
    assert event.text == "Video: Clip.MP4"
    assert event.media_types == ["video/mp4"]
    assert Path(event.media_urls[0]).read_bytes() == payload
    assert downloads == [("video-file", None)]

    result = await adapter.send_video(
        "Exact-Video-Chat",
        str(source),
        caption="A video",
        reply_to="video-parent",
    )
    assert result.success is True
    assert result.message_id == "video-sent"
    assert sent == [
        {
            "chat_id": "Exact-Video-Chat",
            "file_name": "caller-video.mp4",
            "mime_type": "video/mp4",
            "data": payload,
            "caption": "A video",
            "reply_message_id": "video-parent",
        }
    ]
    assert source.read_bytes() == payload
    await adapter.disconnect()


def test_video_media_contract_handles_policy_failures_and_cancellation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(_video_media_failures(registered_plugin, monkeypatch, tmp_path))


async def _video_media_failures(registered_plugin, monkeypatch, tmp_path: Path) -> None:
    from trueconf.exceptions import ApiErrorException

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    (tmp_path / "hermes-home" / "config.yaml").write_text(
        "gateway:\n  max_inbound_media_bytes: 8\n",
        encoding="utf-8",
    )
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    source = tmp_path / "caller-video.mp4"
    source.write_bytes(b"caller video")
    bot = ControlledBot()
    downloads = []
    download_started = asyncio.Event()

    async def get_chat_by_id(chat_id):
        return type("ChatInfo", (), {"title": chat_id, "chat_type": 1})()

    async def download_file_by_id(file_id, dest_path=None):
        downloads.append((file_id, dest_path))
        if file_id == "video-cancel":
            download_started.set()
            await asyncio.Event().wait()
        raise OSError("download failed")

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = lambda _user: None
    bot.download_file_by_id = download_file_by_id

    async def failing_send_document(**_kwargs):
        raise ApiErrorException(100, "upload")

    bot.send_document = failing_send_document
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda user, _kind, _chat: user != "unauthorized@video.example.com")
    events = []
    delivered = asyncio.Event()

    async def on_message(event):
        events.append(event)
        if len(events) == 4:
            delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True
    baseline = set(tmp_path.iterdir())
    cases = [
        ("unauthorized@video.example.com", "secret", "secret.mp4", 7),
        ("oversize@video.example.com", "oversize", "large.mp4", 9),
        ("malformed@video.example.com", "", "broken.mp4", 7),
        ("failure@video.example.com", "download-fails", "missing.mp4", 7),
    ]
    for index, (author_id, file_id, file_name, file_size) in enumerate(cases):
        await sdk_handler["handler"](
            _sdk_attachment_message(
                bot,
                chat_id=f"Video-Failure-{index}",
                author_id=author_id,
                message_id=f"video-failure-{index}",
                file_id=file_id,
                file_name=file_name,
                file_size=file_size,
                mime_type="video/mp4",
            )
        )
    await asyncio.wait_for(delivered.wait(), timeout=1)
    assert downloads == [("download-fails", None)]
    assert [event.media_urls for event in events] == [[], [], [], []]
    assert events[0].text == "Video: secret.mp4"
    assert [event.text for event in events[1:]] == [
        "Video unavailable: large.mp4",
        "Video unavailable: broken.mp4",
        "Video unavailable: missing.mp4",
    ]

    failure = await adapter.send_video("chat", str(source))
    assert (failure.error_kind, failure.retryable) == ("transient", True)
    assert set(tmp_path.iterdir()) == baseline

    download_task = asyncio.create_task(
        sdk_handler["handler"](
            _sdk_attachment_message(
                bot,
                chat_id="Video-Cancel",
                author_id="cancel@video.example.com",
                message_id="video-cancel",
                file_id="video-cancel",
                file_name="cancel.mp4",
                file_size=7,
                mime_type="video/mp4",
            )
        )
    )
    await download_started.wait()
    download_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await download_task

    send_started = asyncio.Event()

    async def cancelled_send_document(**_kwargs):
        send_started.set()
        await asyncio.Event().wait()

    bot.send_document = cancelled_send_document
    send_task = asyncio.create_task(adapter.send_video("chat", str(source)))
    await send_started.wait()
    send_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await send_task
    assert source.read_bytes() == b"caller video"
    assert set(tmp_path.iterdir()) == baseline
    await adapter.disconnect()


def test_voice_media_contract_covers_native_inbound_and_generic_outbound_success(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(_voice_media_success(registered_plugin, monkeypatch, tmp_path))


async def _voice_media_success(registered_plugin, monkeypatch, tmp_path: Path) -> None:
    from gateway.platforms.base import MessageType
    from trueconf.types.responses.send_file_response import SendFileResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    payload = b"voice payload"
    source = tmp_path / "caller-voice.ogg"
    source.write_bytes(payload)
    bot = ControlledBot()
    downloads = []
    sent = []

    async def get_chat_by_id(chat_id):
        return type("ChatInfo", (), {"title": "Voice", "chat_type": 1})()

    async def get_user_display_name(_user_id):
        return type("UserInfo", (), {"display_name": "Alice"})()

    async def download_file_by_id(file_id, dest_path=None):
        downloads.append((file_id, dest_path))
        return payload

    async def send_document(**kwargs):
        sent.append(
            {
                "chat_id": kwargs["chat_id"],
                "file_name": kwargs["file"].file_name,
                "mime_type": kwargs["file"].mime_type,
                "data": await kwargs["file"].read(),
                "caption": kwargs["caption"],
                "reply_message_id": kwargs["reply_message_id"],
            }
        )
        return SendFileResponse(
            chat_id=kwargs["chat_id"],
            message_id="voice-sent",
            file_id="voice-upload",
            timestamp=1_788_000_000_123,
        )

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = get_user_display_name
    bot.download_file_by_id = download_file_by_id
    bot.send_document = send_document
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    delivered = asyncio.Event()
    events = []

    async def on_message(event):
        events.append(event)
        if len(events) == 2:
            delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True
    await sdk_handler["handler"](
        _sdk_voice_message(
            bot,
            chat_id="Voice-Chat",
            author_id="alice@video.example.com",
            message_id="voice-message",
            file_id="voice-file",
            file_size=len(payload),
            mime_type="audio/ogg",
        )
    )
    await sdk_handler["handler"](
        _sdk_attachment_message(
            bot,
            chat_id="Audio-File-Chat",
            author_id="alice@video.example.com",
            message_id="audio-file-message",
            file_id="audio-file",
            file_name="Song.MP3",
            file_size=len(payload),
            mime_type="audio/mpeg",
        )
    )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    voice_event, audio_file_event = events
    assert voice_event.message_type is MessageType.VOICE
    assert voice_event.text == "Voice message"
    assert voice_event.media_types == ["audio/ogg"]
    assert Path(voice_event.media_urls[0]).read_bytes() == payload
    assert voice_event.reply_to_message_id == "voice-parent"
    assert audio_file_event.message_type is MessageType.DOCUMENT
    assert audio_file_event.text == "Document: Song.MP3"
    assert audio_file_event.media_types == ["audio/mpeg"]
    assert Path(audio_file_event.media_urls[0]).read_bytes() == payload
    assert downloads == [("voice-file", None), ("audio-file", None)]

    result = await adapter.send_voice(
        "Exact-Voice-Chat",
        str(source),
        caption="A voice reply",
        reply_to="voice-reply-parent",
    )
    assert result.success is True
    assert result.message_id == "voice-sent"
    assert sent == [
        {
            "chat_id": "Exact-Voice-Chat",
            "file_name": "caller-voice.ogg",
            "mime_type": "audio/ogg",
            "data": payload,
            "caption": "A voice reply",
            "reply_message_id": "voice-reply-parent",
        }
    ]
    assert source.read_bytes() == payload
    await adapter.disconnect()


def test_voice_media_contract_handles_policy_failures_and_cancellation(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asyncio.run(_voice_media_failures(registered_plugin, monkeypatch, tmp_path))


async def _voice_media_failures(registered_plugin, monkeypatch, tmp_path: Path) -> None:
    from trueconf.exceptions import ApiErrorException

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    (tmp_path / "hermes-home" / "config.yaml").write_text(
        "gateway:\n  max_inbound_media_bytes: 8\n",
        encoding="utf-8",
    )
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    source = tmp_path / "caller-voice.ogg"
    source.write_bytes(b"caller voice")
    bot = ControlledBot()
    downloads = []
    download_started = asyncio.Event()

    async def get_chat_by_id(chat_id):
        return type("ChatInfo", (), {"title": chat_id, "chat_type": 1})()

    async def download_file_by_id(file_id, dest_path=None):
        downloads.append((file_id, dest_path))
        if file_id == "voice-cancel":
            download_started.set()
            await asyncio.Event().wait()
        raise OSError("download failed")

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = lambda _user: None
    bot.download_file_by_id = download_file_by_id

    async def failing_send_document(**_kwargs):
        raise ApiErrorException(100, "upload")

    bot.send_document = failing_send_document
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda user, _kind, _chat: user != "unauthorized@video.example.com")
    events = []
    delivered = asyncio.Event()

    async def on_message(event):
        events.append(event)
        if len(events) == 4:
            delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True
    baseline = set(tmp_path.iterdir())
    cases = [
        ("unauthorized@video.example.com", "secret", 7, "audio/ogg"),
        ("oversize@video.example.com", "oversize", 9, "audio/ogg"),
        ("malformed@video.example.com", "", 7, ""),
        ("failure@video.example.com", "download-fails", 7, "audio/ogg"),
    ]
    for index, (author_id, file_id, file_size, mime_type) in enumerate(cases):
        await sdk_handler["handler"](
            _sdk_voice_message(
                bot,
                chat_id=f"Voice-Failure-{index}",
                author_id=author_id,
                message_id=f"voice-failure-{index}",
                file_id=file_id,
                file_size=file_size,
                mime_type=mime_type,
            )
        )
    await asyncio.wait_for(delivered.wait(), timeout=1)
    assert downloads == [("download-fails", None)]
    assert [event.media_urls for event in events] == [[], [], [], []]
    assert events[0].text == "Voice message"
    assert [event.text for event in events[1:]] == [
        "Voice message unavailable",
        "Voice message unavailable",
        "Voice message unavailable",
    ]

    failure = await adapter.send_voice("chat", str(source))
    assert (failure.error_kind, failure.retryable) == ("transient", True)
    assert set(tmp_path.iterdir()) == baseline

    download_task = asyncio.create_task(
        sdk_handler["handler"](
            _sdk_voice_message(
                bot,
                chat_id="Voice-Cancel",
                author_id="cancel@video.example.com",
                message_id="voice-cancel",
                file_id="voice-cancel",
                file_size=7,
                mime_type="audio/ogg",
            )
        )
    )
    await download_started.wait()
    download_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await download_task

    send_started = asyncio.Event()

    async def cancelled_send_document(**_kwargs):
        send_started.set()
        await asyncio.Event().wait()

    bot.send_document = cancelled_send_document
    send_task = asyncio.create_task(adapter.send_voice("chat", str(source)))
    await send_started.wait()
    send_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await send_task
    assert source.read_bytes() == b"caller voice"
    assert set(tmp_path.iterdir()) == baseline
    await adapter.disconnect()


def test_inbound_supported_chat_types_map_through_the_public_router(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_supported_chat_type_mapping(registered_plugin, monkeypatch))


async def _supported_chat_type_mapping(registered_plugin, monkeypatch) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()
    chat_types = {
        "P2P-ID": 1,
        "GROUP-ID": 2,
        "CHANNEL-ID": 6,
        "FAVORITES-ID": 5,
        "FUTURE-ID": 999,
    }

    async def get_chat_by_id(chat_id):
        return type(
            "ChatInfo",
            (),
            {
                "chat_id": chat_id,
                "title": f"Title {chat_id}",
                "chat_type": chat_types[chat_id],
            },
        )()

    async def get_user_display_name(_user_id):
        return type("UserInfo", (), {"display_name": "Visible User"})()

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = get_user_display_name
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin, require_mention=False)
    authorization_calls = []
    adapter.set_authorization_check(lambda user, kind, chat: authorization_calls.append((user, kind, chat)) or True)
    events = []
    delivered = asyncio.Event()

    async def on_message(event):
        events.append(event)
        if len(events) == 3:
            delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True

    for index, chat_id in enumerate(chat_types):
        await sdk_handler["handler"](
            _sdk_text_message(
                bot,
                chat_id=chat_id,
                chat_title=f"Title {chat_id}",
                chat_type=chat_types[chat_id],
                author_id="CaseSensitive@Video.Example",
                text=f"message {index}",
                message_id=f"message-{index}",
            )
        )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert [event.source.chat_type for event in events] == ["dm", "group", "channel"]
    assert [event.source.chat_id for event in events] == [
        "P2P-ID",
        "GROUP-ID",
        "CHANNEL-ID",
    ]
    assert [event.source.chat_name for event in events] == [
        "Title P2P-ID",
        "Title GROUP-ID",
        "Title CHANNEL-ID",
    ]
    assert all(event.user_name == "Visible User" for event in events)
    assert all(event.reply_to_message_id == "parent-message" for event in events)
    assert authorization_calls == [
        ("CaseSensitive@Video.Example", "dm", "P2P-ID"),
        ("CaseSensitive@Video.Example", "group", "GROUP-ID"),
        ("CaseSensitive@Video.Example", "channel", "CHANNEL-ID"),
    ]
    assert [(await adapter.get_chat_info(chat_id))["type"] for chat_id in chat_types] == [
        "dm",
        "group",
        "channel",
        "unknown",
        "unknown",
    ]
    await adapter.disconnect()


def test_get_chat_info_returns_minimal_info_when_chat_is_not_found(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    asyncio.run(
        _get_chat_info_returns_minimal_info_when_chat_is_not_found(
            registered_plugin,
            monkeypatch,
            caplog,
        )
    )


async def _get_chat_info_returns_minimal_info_when_chat_is_not_found(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from trueconf.exceptions import ApiErrorException

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()

    async def get_chat_by_id(_chat_id):
        raise ApiErrorException(304, "CHAT_NOT_FOUND")

    bot.get_chat_by_id = get_chat_by_id
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    with caplog.at_level(logging.DEBUG):
        result = await adapter.get_chat_info("deleted-chat")

    assert result == {
        "chat_id": "deleted-chat",
        "name": "deleted-chat",
        "type": "unknown",
    }
    assert "code=304" in caplog.text
    assert "chat=deleted-chat" in caplog.text

    async def cancelled_get_chat_by_id(_chat_id):
        raise asyncio.CancelledError

    bot.get_chat_by_id = cancelled_get_chat_by_id
    with pytest.raises(asyncio.CancelledError):
        await adapter.get_chat_info("cancelled-chat")
    await adapter.disconnect()


def test_native_mentions_gate_groups_and_channels_but_never_p2p(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_native_mention_gating(registered_plugin, monkeypatch))


def test_allowed_chats_limits_groups_and_channels_but_never_p2p(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_allowed_chat_scope(registered_plugin, monkeypatch))


async def _allowed_chat_scope(registered_plugin, monkeypatch) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()

    async def get_user_display_name(user_id):
        return type("UserInfo", (), {"display_name": user_id})()

    bot.get_user_display_name = get_user_display_name
    install_bot(monkeypatch, bot)
    adapter = create_adapter(
        registered_plugin,
        require_mention=False,
        allowed_chats=["allowed-group", "allowed-channel"],
    )
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    events = []
    delivered = asyncio.Event()

    async def on_message(event):
        events.append(event)
        if len(events) == 3:
            delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True

    cases = [
        ("outside-direct", 1, "direct-kept"),
        ("allowed-group", 2, "group-kept"),
        ("outside-group", 2, "group-dropped"),
        ("allowed-channel", 6, "channel-kept"),
        ("outside-channel", 6, "channel-dropped"),
    ]
    for chat_id, chat_type, message_id in cases:
        await sdk_handler["handler"](
            _sdk_text_message(
                bot,
                chat_id=chat_id,
                chat_type=chat_type,
                author_id="sender@video.example.com",
                text="ordinary text",
                message_id=message_id,
            )
        )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert [event.message_id for event in events] == [
        "direct-kept",
        "group-kept",
        "channel-kept",
    ]
    await adapter.disconnect()


def test_free_response_chats_bypass_only_the_mention_requirement(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_free_response_chat_gating(registered_plugin, monkeypatch))


async def _free_response_chat_gating(registered_plugin, monkeypatch) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()

    async def get_user_display_name(user_id):
        return type("UserInfo", (), {"display_name": user_id})()

    bot.get_user_display_name = get_user_display_name
    install_bot(monkeypatch, bot)
    adapter = create_adapter(
        registered_plugin,
        require_mention=True,
        allowed_chats=["agent-room", "ordinary-group"],
        free_response_chats=["agent-room", "outside-scope"],
    )
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    events = []
    delivered = asyncio.Event()

    async def on_message(event):
        events.append(event)
        if len(events) == 3:
            delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True

    cases = [
        ("outside-direct", 1, "ordinary text", "direct-kept"),
        ("agent-room", 2, "ordinary text", "free-group-kept"),
        ("ordinary-group", 2, "ordinary text", "ordinary-group-dropped"),
        ("ordinary-group", 2, "@all", "mentioned-group-kept"),
        ("outside-scope", 2, "ordinary text", "free-but-outside-dropped"),
    ]
    for chat_id, chat_type, text, message_id in cases:
        await sdk_handler["handler"](
            _sdk_text_message(
                bot,
                chat_id=chat_id,
                chat_type=chat_type,
                author_id="sender@video.example.com",
                text=text,
                message_id=message_id,
            )
        )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert [event.message_id for event in events] == [
        "direct-kept",
        "free-group-kept",
        "mentioned-group-kept",
    ]
    await adapter.disconnect()


def test_observed_context_is_limited_to_explicit_addressed_group_scope(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    adapter = create_adapter(
        registered_plugin,
        require_mention=True,
        allowed_chats=["observed-group", "free-group"],
        free_response_chats=["free-group"],
        observe_unmentioned_group_messages=True,
    )

    assert adapter.observe_context_limit == 20
    assert adapter._should_fetch_observed_context("observed-group", "group") is True
    assert adapter._should_fetch_observed_context("observed-group", "channel") is True
    assert adapter._should_fetch_observed_context("observed-group", "dm") is False
    assert adapter._should_fetch_observed_context("free-group", "group") is False
    assert adapter._should_fetch_observed_context("outside-group", "group") is False

    disabled = create_adapter(
        registered_plugin,
        allowed_chats=["observed-group"],
        observe_unmentioned_group_messages=False,
    )
    zero_limit = create_adapter(
        registered_plugin,
        allowed_chats=["observed-group"],
        observe_unmentioned_group_messages=True,
        observe_context_limit=0,
    )
    implicit_scope = create_adapter(
        registered_plugin,
        observe_unmentioned_group_messages=True,
    )
    assert disabled._should_fetch_observed_context("observed-group", "group") is False
    assert zero_limit._should_fetch_observed_context("observed-group", "group") is False
    assert implicit_scope._should_fetch_observed_context("observed-group", "group") is False


def test_observed_context_fetches_since_the_previous_authorized_mention(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_observed_context_history(registered_plugin, monkeypatch))


async def _observed_context_history(registered_plugin, monkeypatch) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()
    chat_id = "observed-group"
    trigger_id = "trigger-message"

    def history_message(
        author_id: str,
        text: str,
        message_id: str,
        position: str,
    ):
        return _sdk_text_message(
            bot,
            chat_id=chat_id,
            chat_type=2,
            author_id=author_id,
            text=text,
            message_id=message_id,
            box_position=position,
        )

    old = history_message("alice", "old context", "old", "01")
    boundary = history_message("moderator", "@all", "boundary", "02")
    kept = history_message(
        "alice",
        "Keep <b>HTML</b><br>and line breaks",
        "kept",
        "03",
    )
    blocked = history_message("blocked", "do not include", "blocked", "04")
    own = history_message(bot.me_id.upper(), "own response", "own", "05")
    long_text = history_message("alice", "x" * 1100, "long", "06")
    trigger = history_message("moderator", "@all", trigger_id, "07")
    history_calls = []

    async def get_chat_history(requested_chat_id, *, count, from_message_id):
        history_calls.append((requested_chat_id, count, from_message_id))
        return SimpleNamespace(messages=[long_text, trigger, kept, old, own, blocked, boundary])

    async def get_user_display_name(user_id):
        return SimpleNamespace(display_name=user_id)

    bot.get_chat_history = get_chat_history
    bot.get_user_display_name = get_user_display_name
    install_bot(monkeypatch, bot)
    adapter = create_adapter(
        registered_plugin,
        allowed_chats=[chat_id],
        observe_unmentioned_group_messages=True,
        observe_context_limit=20,
    )
    adapter.set_authorization_check(lambda user, _kind, _chat: user in {"alice", "moderator"})
    events = []
    delivered = asyncio.Event()

    async def on_message(event):
        events.append(event)
        delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True
    await sdk_handler["handler"](trigger)
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert history_calls == [(chat_id, 20, trigger_id)]
    assert len(events) == 1
    context = events[0].channel_context
    assert context is not None
    assert context.startswith("[Recent TrueConf group messages - context only, not requests]\n")
    assert "[alice] Keep <b>HTML</b><br>and line breaks" in context
    assert f"[alice] {'x' * 1000}" in context
    assert "old context" not in context
    assert "do not include" not in context
    assert "own response" not in context
    assert "@all" not in context
    await adapter.disconnect()


def test_observed_context_failure_does_not_drop_the_trigger(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_observed_context_failure(registered_plugin, monkeypatch))


async def _observed_context_failure(registered_plugin, monkeypatch) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()

    async def get_chat_history(*_args, **_kwargs):
        raise RuntimeError("history unavailable")

    bot.get_chat_history = get_chat_history
    install_bot(monkeypatch, bot)
    adapter = create_adapter(
        registered_plugin,
        allowed_chats=["observed-group"],
        observe_unmentioned_group_messages=True,
    )
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    events = []
    delivered = asyncio.Event()

    async def on_message(event):
        events.append(event)
        delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True
    await sdk_handler["handler"](
        _sdk_text_message(
            bot,
            chat_id="observed-group",
            chat_type=2,
            author_id="moderator",
            text="@all",
            message_id="trigger-message",
        )
    )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert len(events) == 1
    assert events[0].channel_context is None
    await adapter.disconnect()


def test_observed_context_keeps_the_newest_messages_within_the_total_cap(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_observed_context_total_cap(registered_plugin, monkeypatch))


async def _observed_context_total_cap(registered_plugin, monkeypatch) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    messages = [
        _sdk_text_message(
            bot,
            chat_id="observed-group",
            chat_type=2,
            author_id="alice",
            text=f"message-{index}-" + "x" * 1100,
            message_id=f"message-{index}",
            box_position=f"{index:02d}",
        )
        for index in range(15)
    ]

    async def get_chat_history(*_args, **_kwargs):
        return SimpleNamespace(messages=list(reversed(messages)))

    bot.get_chat_history = get_chat_history
    adapter = create_adapter(
        registered_plugin,
        allowed_chats=["observed-group"],
        observe_unmentioned_group_messages=True,
        observe_context_limit=20,
    )
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)

    context = await adapter._fetch_observed_context(
        bot,
        chat_id="observed-group",
        chat_type="group",
        trigger_message_id="trigger-message",
    )

    assert context is not None
    assert len(context) <= 12_000
    assert "message-0-" not in context
    assert "message-14-" in context


def test_unauthorized_trigger_does_not_fetch_observed_context(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_unauthorized_trigger_does_not_fetch_history(registered_plugin, monkeypatch))


async def _unauthorized_trigger_does_not_fetch_history(
    registered_plugin,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()
    history_calls = []

    async def get_chat_history(*args, **kwargs):
        history_calls.append((args, kwargs))
        return SimpleNamespace(messages=[])

    bot.get_chat_history = get_chat_history
    install_bot(monkeypatch, bot)
    adapter = create_adapter(
        registered_plugin,
        allowed_chats=["observed-group"],
        observe_unmentioned_group_messages=True,
    )
    adapter.set_authorization_check(lambda _user, _kind, _chat: False)
    delivered = asyncio.Event()

    async def on_message(_event):
        delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True
    await sdk_handler["handler"](
        _sdk_text_message(
            bot,
            chat_id="observed-group",
            chat_type=2,
            author_id="blocked",
            text="@all",
            message_id="blocked-trigger",
        )
    )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert history_calls == []
    await adapter.disconnect()


async def _native_mention_gating(registered_plugin, monkeypatch) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    sdk_handler = _capture_public_sdk_message_handler(monkeypatch)
    bot = ControlledBot()
    chat_types = {"direct": 1, "group": 2, "channel": 6}

    async def get_chat_by_id(chat_id):
        return type(
            "ChatInfo",
            (),
            {"chat_id": chat_id, "title": chat_id, "chat_type": chat_types[chat_id]},
        )()

    async def get_user_display_name(user_id):
        return type("UserInfo", (), {"display_name": user_id})()

    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = get_user_display_name
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    events = []
    delivered = asyncio.Event()

    async def on_message(event):
        events.append(event)
        if len(events) == 3:
            delivered.set()

    adapter.set_message_handler(on_message)
    assert await adapter.connect() is True

    cases = [
        ("direct", "ordinary direct text", "direct-kept"),
        ("group", "ordinary group text", "group-dropped"),
        ("group", "@all", "group-kept"),
        ("channel", "ordinary channel text", "channel-dropped"),
        (
            "channel",
            '<a href="trueconf:hermes-bot@video.example.com">Hermes</a>',
            "channel-kept",
        ),
    ]
    for chat_id, text, message_id in cases:
        await sdk_handler["handler"](
            _sdk_text_message(
                bot,
                chat_id=chat_id,
                chat_type=chat_types[chat_id],
                author_id="sender@video.example.com",
                text=text,
                message_id=message_id,
            )
        )
    await asyncio.wait_for(delivered.wait(), timeout=1)

    assert [event.message_id for event in events] == [
        "direct-kept",
        "group-kept",
        "channel-kept",
    ]
    await adapter.disconnect()


def test_public_sdk_self_filter_drops_only_the_current_bot_identity() -> None:
    asyncio.run(_public_sdk_self_filter())


async def _public_sdk_self_filter() -> None:
    from trueconf.middleware import SkipSelfMessages

    bot = ControlledBot()
    handled = []

    async def handler(event, _data):
        handled.append(event.message_id)

    own = _sdk_text_message(
        bot,
        chat_id="chat",
        author_id=bot.me_id,
        text="self",
        message_id="self-message",
    )
    other_bot = _sdk_text_message(
        bot,
        chat_id="chat",
        author_id="another-bot@video.example.com",
        text="other bot",
        message_id="other-bot-message",
    )
    middleware = SkipSelfMessages()
    await middleware(handler, own, {"bot": bot})
    await middleware(handler, other_bot, {"bot": bot})

    assert handled == ["other-bot-message"]


def test_send_passes_the_exact_trueconf_chat_id_without_resolution(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_exact_chat_id_delivery(registered_plugin, monkeypatch))


async def _exact_chat_id_delivery(registered_plugin, monkeypatch) -> None:
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sent_chat_ids = []

    async def send_message(**kwargs):
        sent_chat_ids.append(kwargs["chat_id"])
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id=f"sent-{len(sent_chat_ids)}",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.send("UUID-Chat-AbC", "message", reply_to="reply-ID")

    assert result.success is True
    assert sent_chat_ids == ["UUID-Chat-AbC"]
    await adapter.disconnect()


def test_clarify_buttons_resolve_the_selected_choice(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_clarify_buttons_resolve(registered_plugin, monkeypatch))


async def _clarify_buttons_resolve(registered_plugin, monkeypatch) -> None:
    from tools import clarify_gateway
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sends = []

    async def send_message(**kwargs):
        sends.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="prompt-1",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    resolved = []
    monkeypatch.setattr(
        clarify_gateway,
        "resolve_gateway_clarify",
        lambda clarify_id, response: resolved.append((clarify_id, response)) or True,
    )
    result = await adapter.send_clarify(
        "chat-1",
        "Which environment?",
        ["Staging", "Production"],
        "clarify-7",
        "session-1",
        {"reply_to_message_id": "trigger-1"},
    )

    assert result.success is True
    keyboard = sends[0]["buttons"].buttons
    assert [button.text for row in keyboard for button in row] == ["1", "2", "Other"]
    assert {button.to for row in keyboard for button in row} == {None}
    assert keyboard[0][1].custom_data == "clarify:clarify-7:1"

    answers = []

    class Callback:
        command = "hermes"
        custom_data = "clarify:clarify-7:1"
        command_id = "cb-1"
        chat = SimpleNamespace(chat_id="chat-1")

        async def answer(self, **kwargs):
            answers.append(kwargs["text"])

    await adapter._handle_trueconf_callback(Callback(), adapter._generation)

    assert resolved == [("clarify-7", "Production")]
    assert answers == ["Production"]
    await adapter.disconnect()


def test_clarify_button_without_command_id_resolves_without_ack(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_clarify_without_command_id(registered_plugin, monkeypatch))


async def _clarify_without_command_id(registered_plugin, monkeypatch) -> None:
    from tools import clarify_gateway
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sends = []

    async def send_message(**kwargs):
        sends.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="prompt-1",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    resolved = []
    monkeypatch.setattr(
        clarify_gateway,
        "resolve_gateway_clarify",
        lambda clarify_id, response: resolved.append((clarify_id, response)) or True,
    )
    result = await adapter.send_clarify(
        "chat-1",
        "Which environment?",
        ["Staging", "Production"],
        "clarify-8",
        "session-1",
    )
    assert result.success is True

    answered = []

    class NoCommandIdCallback:
        command = "hermes"
        custom_data = "clarify:clarify-8:0"
        command_id = None
        chat = SimpleNamespace(chat_id="chat-1")

        async def answer(self, **kwargs):
            answered.append(kwargs["text"])

    await adapter._handle_trueconf_callback(NoCommandIdCallback(), adapter._generation)

    assert resolved == [("clarify-8", "Staging")]
    assert answered == []
    await adapter.disconnect()


def test_exec_approval_button_resolves_only_in_its_source_chat(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_exec_approval_button_chat_binding(registered_plugin, monkeypatch))


async def _exec_approval_button_chat_binding(registered_plugin, monkeypatch) -> None:
    from tools import approval
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sends = []

    async def send_message(**kwargs):
        sends.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="approval-prompt",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True
    resolved = []
    monkeypatch.setattr(
        approval,
        "resolve_gateway_approval",
        lambda session_key, choice: resolved.append((session_key, choice)) or 1,
    )

    result = await adapter.send_exec_approval("chat-1", "deploy", "session-1", allow_permanent=False)
    assert result.success is True
    buttons = [button for row in sends[0]["buttons"].buttons for button in row]
    assert [button.text for button in buttons] == [
        "Allow once",
        "Allow session",
        "Deny",
    ]

    answers = []

    class WrongChatCallback:
        command = "hermes"
        custom_data = "approval:1:once"
        command_id = "cb-2"
        chat = SimpleNamespace(chat_id="chat-2")

        async def answer(self, **kwargs):
            answers.append(kwargs["text"])

    await adapter._handle_trueconf_callback(WrongChatCallback(), adapter._generation)

    assert resolved == []
    assert answers == ["This prompt has expired"]
    await adapter.disconnect()


def test_send_splits_long_content_into_chained_continuations(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_send_long_content_splits_into_continuations(registered_plugin, monkeypatch))


async def _send_long_content_splits_into_continuations(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import itertools

    from trueconf.types.responses.send_message_response import SendMessageResponse
    from trueconf.utils.split_text import visible_len

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sent: list[dict[str, Any]] = []
    counter = itertools.count(1)

    async def send_message(**kwargs):
        sent.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id=f"chunk-{next(counter)}",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.send("chat", "x" * 5000, reply_to="message-17")

    assert result.success is True
    assert result.message_id == "chunk-2"
    assert result.continuation_message_ids == ("chunk-1",)
    assert len(sent) == 2
    assert sent[0]["reply_message_id"] == "message-17"
    assert sent[1]["reply_message_id"] == "chunk-1"
    assert all(visible_len(call["text"]) <= 4096 for call in sent)
    assert "".join(call["text"] for call in sent) == "x" * 5000
    await adapter.disconnect()


def test_edit_splits_long_content_into_chained_continuations(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_edit_long_content_splits_into_continuations(registered_plugin, monkeypatch))


async def _edit_long_content_splits_into_continuations(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import itertools

    from trueconf.types.responses.edit_message_response import EditMessageResponse
    from trueconf.types.responses.send_message_response import SendMessageResponse
    from trueconf.utils.split_text import visible_len

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    edited: list[dict[str, Any]] = []
    sent: list[dict[str, Any]] = []
    counter = itertools.count(1)

    async def edit_message(**kwargs):
        edited.append(kwargs)
        return EditMessageResponse(message_id="head-message", timestamp=1)

    async def send_message(**kwargs):
        sent.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id=f"chunk-{next(counter)}",
            timestamp=1_788_000_000_123,
        )

    bot.edit_message = edit_message
    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.edit_message("chat", "message-42", "x" * 5000)

    assert result.success is True
    assert result.message_id == "chunk-1"
    assert result.continuation_message_ids == ("head-message",)
    assert [call["text"] for call in edited] == ["x" * 4096]
    assert [call["text"] for call in sent] == ["x" * 904]
    assert sent[0]["reply_message_id"] == "head-message"
    assert all(visible_len(call["text"]) <= 4096 for call in edited + sent)
    await adapter.disconnect()


def test_send_reports_partial_success_when_a_tail_chunk_fails(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_send_partial_success_when_tail_fails(registered_plugin, monkeypatch))


async def _send_partial_success_when_tail_fails(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import itertools

    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sent: list[dict[str, Any]] = []
    counter = itertools.count(1)

    async def send_message(**kwargs):
        sent.append(kwargs)
        if len(sent) == 2:
            raise RuntimeError("tail exploded")
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id=f"chunk-{next(counter)}",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.send("chat", "x" * 5000)

    assert result.success is True
    assert result.message_id == "chunk-1"
    assert result.continuation_message_ids == ()
    assert "tail chunk failed" in (result.error or "")
    assert len(sent) == 2
    await adapter.disconnect()


def test_standalone_splits_long_text_into_chained_messages(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_standalone_split_long_text_into_chained_messages(registered_plugin, monkeypatch))


async def _standalone_split_long_text_into_chained_messages(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import itertools

    from gateway.config import PlatformConfig
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    counter = itertools.count(1)
    send_count = 0

    async def send_message(**kwargs):
        nonlocal send_count
        send_count += 1
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id=f"cron-chunk-{next(counter)}",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)

    sender = registered_plugin.get("trueconf").standalone_sender_fn
    assert sender is not None
    result = await sender(
        PlatformConfig(
            enabled=True,
            extra={
                "server": "video.example.com",
                "port": 8443,
                "https": True,
                "verify_ssl": True,
                "parse_mode": "html",
            },
        ),
        "Exact-Cron-Chat",
        "x" * 5000,
    )

    assert result == {
        "success": True,
        "message_id": "cron-chunk-2",
    }
    assert send_count == 2
    assert bot.shutdown_calls == 1


def test_next_button_id_is_unique_across_calls(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_next_button_id_unique(registered_plugin, monkeypatch))


async def _next_button_id_unique(registered_plugin, monkeypatch) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)

    first = adapter._next_button_id("approval")
    second = adapter._next_button_id("approval")
    assert first != second
    assert ":" not in first
    assert ":" not in second
    await adapter.disconnect()


def test_clarify_negative_index_is_invalid_choice(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_clarify_negative_index(registered_plugin, monkeypatch))


async def _clarify_negative_index(registered_plugin, monkeypatch) -> None:
    from tools import clarify_gateway
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sends = []

    async def send_message(**kwargs):
        sends.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="prompt-neg",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    resolved = []
    monkeypatch.setattr(
        clarify_gateway,
        "resolve_gateway_clarify",
        lambda clarify_id, response: resolved.append((clarify_id, response)) or True,
    )
    result = await adapter.send_clarify(
        "chat-1",
        "Which environment?",
        ["Staging", "Production"],
        "clarify-neg",
        "session-1",
    )
    assert result.success is True

    answered = []

    class Callback:
        command = "hermes"
        custom_data = "clarify:clarify-neg:-1"
        command_id = "cb-neg"
        chat = SimpleNamespace(chat_id="chat-1")

        async def answer(self, **kwargs):
            answered.append(kwargs["text"])

    await adapter._handle_trueconf_callback(Callback(), adapter._generation)

    assert resolved == []
    assert answered == ["Invalid choice"]
    await adapter.disconnect()


def test_group_clarify_falls_back_to_numbered_text_prompt(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_group_clarify_text_fallback(registered_plugin, monkeypatch))


async def _group_clarify_text_fallback(registered_plugin, monkeypatch) -> None:
    from tools import clarify_gateway
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.text import TextContent
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sends = []

    async def send_message(**kwargs):
        sends.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="group-prompt-1",
            timestamp=1_788_000_000_123,
        )

    async def get_chat_by_id(chat_id):
        return type(
            "ChatInfo",
            (),
            {"chat_id": chat_id, "title": "Team chat", "chat_type": 2},
        )()

    async def get_user_display_name(user_id):
        return type("UserInfo", (), {"display_name": "Alice"})()

    bot.send_message = send_message
    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = get_user_display_name
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin, require_mention=False)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.send_clarify(
        "group-1",
        "Which environment?",
        ["Staging", "Production"],
        "clarify-g1",
        "session-1",
    )
    assert result.success is True
    assert len(sends) == 1
    assert "buttons" not in sends[0]
    assert "Reply with a number" in sends[0]["text"]
    assert "1. Staging" in sends[0]["text"]

    resolved = []
    monkeypatch.setattr(
        clarify_gateway,
        "resolve_gateway_clarify",
        lambda clarify_id, response: resolved.append((clarify_id, response)) or True,
    )

    message = Message(
        chat=_sdk_chat("group-1", title="Team chat", chat_type=2),
        timestamp=1_788_000_000_123,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id="Alice@Video.Example", type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=7, position="inbox"),
        content=TextContent(text="2", parse_mode="text"),
        message_id="message-g1",
        is_edited=False,
        reply_message_id=None,
    ).bind(bot)
    await adapter._handle_trueconf_message(message, adapter._generation)

    assert resolved == [("clarify-g1", "Production")]
    await adapter.disconnect()


def test_group_approval_falls_back_to_text_and_number_reply_resolves(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_group_approval_text_fallback(registered_plugin, monkeypatch))


async def _group_approval_text_fallback(registered_plugin, monkeypatch) -> None:
    from tools import approval
    from trueconf.enums import EnvelopeAuthorType, MessageType
    from trueconf.types import Message
    from trueconf.types.author_box import EnvelopeAuthor, EnvelopeBox
    from trueconf.types.content.text import TextContent
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sends = []

    async def send_message(**kwargs):
        sends.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="group-prompt-2",
            timestamp=1_788_000_000_123,
        )

    async def get_chat_by_id(chat_id):
        return type(
            "ChatInfo",
            (),
            {"chat_id": chat_id, "title": "Team chat", "chat_type": 2},
        )()

    async def get_user_display_name(user_id):
        return type("UserInfo", (), {"display_name": "Alice"})()

    bot.send_message = send_message
    bot.get_chat_by_id = get_chat_by_id
    bot.get_user_display_name = get_user_display_name
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin, require_mention=False)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.send_exec_approval("group-1", "echo approval-test", "session-g", metadata={})
    assert result.success is True
    assert len(sends) == 1
    assert "buttons" not in sends[0]
    assert "Reply with a number" in sends[0]["text"]
    assert "1. Allow once" in sends[0]["text"]
    assert "4. Deny" in sends[0]["text"]

    resolved = []
    monkeypatch.setattr(
        approval,
        "resolve_gateway_approval",
        lambda session_key, choice: resolved.append((session_key, choice)) or 1,
    )

    message = Message(
        chat=_sdk_chat("group-1", title="Team chat", chat_type=2),
        timestamp=1_788_000_000_123,
        type=MessageType.PLAIN_MESSAGE,
        author=EnvelopeAuthor(id="Bob@Video.Example", type=EnvelopeAuthorType.USER),
        box=EnvelopeBox(id=7, position="inbox"),
        content=TextContent(text="4", parse_mode="text"),
        message_id="message-g2",
        is_edited=False,
        reply_message_id=None,
    ).bind(bot)
    await adapter._handle_trueconf_message(message, adapter._generation)

    assert resolved == [("session-g", "deny")]
    await adapter.disconnect()


def test_adapter_capability_flags_match_trueconf_limits(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_capability_flags(registered_plugin, monkeypatch))


async def _capability_flags(registered_plugin, monkeypatch) -> None:
    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)

    assert adapter.splits_long_messages is True
    assert adapter.supports_code_blocks is True
    assert adapter.MAX_MESSAGE_LENGTH == 4096
    await adapter.disconnect()


def test_reply_to_mode_off_suppresses_the_reply_anchor(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_reply_to_mode_off(registered_plugin, monkeypatch))


async def _reply_to_mode_off(registered_plugin, monkeypatch) -> None:
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sends = []

    async def send_message(**kwargs):
        sends.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id="sent-off-1",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin, reply_to_mode="off")
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.send("chat-1", "hello", reply_to="trigger-1")

    assert result.success is True
    assert sends[0]["reply_message_id"] is None
    await adapter.disconnect()


def test_reply_to_mode_all_anchors_every_chunk(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_reply_to_mode_all(registered_plugin, monkeypatch))


async def _reply_to_mode_all(registered_plugin, monkeypatch) -> None:
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sends = []

    async def send_message(**kwargs):
        sends.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id=f"sent-all-{len(sends)}",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin, reply_to_mode="all")
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.send("chat-1", "x" * 5000, reply_to="trigger-1")

    assert result.success is True
    assert len(sends) >= 2
    assert {kwargs["reply_message_id"] for kwargs in sends} == {"trigger-1"}
    await adapter.disconnect()


def test_reply_to_mode_first_keeps_only_the_first_chunk_anchored(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_reply_to_mode_first(registered_plugin, monkeypatch))


async def _reply_to_mode_first(registered_plugin, monkeypatch) -> None:
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sends = []

    async def send_message(**kwargs):
        sends.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id=f"sent-first-{len(sends)}",
            timestamp=1_788_000_000_123,
        )

    bot.send_message = send_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin, reply_to_mode="first")
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    result = await adapter.send("chat-1", "x" * 5000, reply_to="trigger-1")

    assert result.success is True
    assert len(sends) >= 2
    assert sends[0]["reply_message_id"] == "trigger-1"
    assert sends[1]["reply_message_id"] == "sent-first-1"
    await adapter.disconnect()


def test_send_or_update_status_edits_the_same_bubble(
    registered_plugin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_status_bubble_dedup(registered_plugin, monkeypatch))


async def _status_bubble_dedup(registered_plugin, monkeypatch) -> None:
    from trueconf.types.responses.edit_message_response import EditMessageResponse
    from trueconf.types.responses.send_message_response import SendMessageResponse

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    bot = ControlledBot()
    sends = []
    edits = []

    async def send_message(**kwargs):
        sends.append(kwargs)
        return SendMessageResponse(
            chat_id=kwargs["chat_id"],
            message_id=f"status-{len(sends)}",
            timestamp=1_788_000_000_123,
        )

    async def edit_message(**kwargs):
        edits.append(kwargs)
        return EditMessageResponse(message_id=kwargs["message_id"], timestamp=1_788_000_000_124)

    bot.send_message = send_message
    bot.edit_message = edit_message
    install_bot(monkeypatch, bot)
    adapter = create_adapter(registered_plugin)
    adapter.set_authorization_check(lambda _user, _kind, _chat: True)
    assert await adapter.connect() is True

    first = await adapter.send_or_update_status("chat-1", "compress", "💭 compressing…")
    second = await adapter.send_or_update_status("chat-1", "compress", "💭 compressing (50%)")

    assert first.success is True
    assert second.success is True
    assert len(sends) == 1
    assert len(edits) == 1
    assert edits[0]["message_id"] == "status-1"
    await adapter.disconnect()


def test_config_error_rejects_unknown_reply_to_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    import sys

    monkeypatch.setenv("TRUECONF_USERNAME", "bot")
    monkeypatch.setenv("TRUECONF_PASSWORD", "secret")
    spec = importlib.util.spec_from_file_location(
        "trueconf_plugin_under_test",
        PROJECT_ROOT / "__init__.py",
        submodule_search_locations=[str(PROJECT_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    error = module._config_error(
        SimpleNamespace(
            extra={
                "server": "video.example.com",
                "reply_to_mode": "sometimes",
            },
            home_channel=None,
        )
    )
    assert error == "reply_to_mode must be one of: first, all, off"

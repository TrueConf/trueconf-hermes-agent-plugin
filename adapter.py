"""TrueConf adapter for the public Hermes platform interface."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import sys
import unicodedata
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from agent.secret_scope import get_secret
from gateway.config import Platform
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    SendResult,
    cache_media_bytes,
    get_inbound_media_max_bytes,
    validate_inbound_media_size,
)
from gateway.platforms.base import MessageType as HermesMessageType
from gateway.status import acquire_scoped_lock, release_scoped_lock
from trueconf import (
    Bot,
    Dispatcher,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ParseMode,
    Router,
)
from trueconf.enums import (
    ButtonStyle,
    ChatActivity,
    ChatType,
)
from trueconf.enums import MessageType as TrueConfMessageType
from trueconf.exceptions import (
    ApiErrorException,
    FileCaptionTooLongError,
    InvalidGrantError,
    TextMessageTooLongError,
    TokenValidationError,
    WSConnectionError,
)
from trueconf.types import FSInputFile
from trueconf.utils import ChatActivitySender, safe_split_text

from .formatter import render_trueconf_html

_LOGGER = logging.getLogger(__name__)
_OBSERVED_CONTEXT_HEADER = (
    "[Recent TrueConf group messages - context only, not requests]"
)
_OBSERVED_MESSAGE_MAX_CHARS = 1000
_OBSERVED_CONTEXT_MAX_CHARS = 12_000
_BUTTON_COMMAND = "hermes"
_MAX_BUTTON_PROMPTS = 512
_MAX_TEXT_PROMPTS = 64
_MAX_STATUS_MESSAGES = 256
_TRUE_CONF_MESSAGE_LIMIT = 4096


def _split_safe_chunks(html: str) -> list[str]:
    """Split rendered HTML into SDK-valid chunks of at most 4096 visible chars.

    ``safe_split_text`` sizes by the same visible-length metric the SDK's
    ``send_message``/``edit_message`` enforce, carries open markup tags across
    chunk boundaries, and treats ``<br>`` as an atomic zero-width break point.
    """
    return safe_split_text(html, _TRUE_CONF_MESSAGE_LIMIT)


def _empty_message_failure(operation: str, server: str) -> SendResult:
    return SendResult(
        success=False,
        error=(f"TrueConf {operation} failed on {server}: message has no visible text"),
        error_kind="unknown",
        retryable=False,
    )


class TrueConfAdapter(BasePlatformAdapter):
    """Translate between Hermes and one TrueConf bot identity."""

    # send()/edit_message() chunk natively via _split_safe_chunks — the gateway
    # must deliver full payloads instead of pre-truncating to 4000 chars.
    splits_long_messages = True
    # The formatter renders fenced code blocks as multi-line <i> blocks.
    supports_code_blocks = True
    MAX_MESSAGE_LENGTH = _TRUE_CONF_MESSAGE_LIMIT

    def __init__(self, config: Any):
        super().__init__(config=config, platform=Platform("trueconf"))
        extra = getattr(config, "extra", {}) or {}

        self.server = str(get_secret("TRUECONF_SERVER", "") or extra.get("server", ""))
        self.username = str(get_secret("TRUECONF_USERNAME", "") or "")
        self.password = str(get_secret("TRUECONF_PASSWORD", "") or "")
        self.port = int(extra.get("port", 443))
        self.https = extra.get("https", True)
        self.verify_ssl = extra.get("verify_ssl", True)
        self.require_mention = extra.get("require_mention", True)
        allowed_chats = extra.get("allowed_chats")
        self.allowed_chats = frozenset(
            allowed_chats if isinstance(allowed_chats, list) else ()
        )
        free_response_chats = extra.get("free_response_chats")
        self.free_response_chats = frozenset(
            free_response_chats if isinstance(free_response_chats, list) else ()
        )
        self.observe_unmentioned_group_messages = extra.get(
            "observe_unmentioned_group_messages", False
        )
        self.observe_context_limit = extra.get("observe_context_limit", 20)
        reply_to_mode = extra.get("reply_to_mode", "first")
        self.reply_to_mode = (
            reply_to_mode if reply_to_mode in ("first", "all", "off") else "first"
        )
        configured_media_limit = get_inbound_media_max_bytes()
        self.inbound_media_max_bytes = configured_media_limit
        self.sdk_in_memory_download_limit = (
            configured_media_limit if configured_media_limit > 0 else sys.maxsize
        )
        if self.verify_ssl is False:
            _LOGGER.warning(
                "TrueConf TLS certificate verification is disabled for %s",
                self.server,
            )
        # The platform prompt and TrueConf wire format intentionally share one
        # contract. Configuration validation rejects every non-HTML mode; keep
        # the adapter fixed as a defensive guarantee for direct callers too.
        self.parse_mode = ParseMode.HTML
        self._bot: Any = None
        self._bot_creation_task: asyncio.Task[Any] | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._disconnect_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._generation = 0
        self._lock_identity = f"{self.server.lower()}\n{self.username}"
        self._lock_acquired = False
        self._early_authorization_check: Any = None
        self._typing_senders: dict[str, ChatActivitySender] = {}
        self._typing_sender_lock = asyncio.Lock()
        self._button_state: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        # Group-chat fallback state: one pending text prompt per chat.
        self._text_prompts: dict[str, dict[str, Any]] = {}
        # Status bubble dedup: (chat_id, status_key) -> message id, mirroring
        # the Telegram adapter (#30045).
        self._status_message_ids: OrderedDict[tuple[str, str], str] = OrderedDict()

    def set_authorization_check(self, callback: Any) -> None:
        super().set_authorization_check(callback)
        self._early_authorization_check = callback

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if self.is_connected:
            return True
        if self._early_authorization_check is None:
            self._set_fatal_error(
                "authorization_hook_missing",
                "TrueConf requires the public Hermes authorization hook",
                retryable=False,
            )
            return False

        self._stopping = False
        self._generation += 1
        generation = self._generation
        acquired, _owner = acquire_scoped_lock(
            "trueconf",
            self._lock_identity,
            metadata={"platform": "trueconf", "server": self.server},
        )
        if not acquired:
            self._set_fatal_error(
                "credential_lock",
                f"TrueConf identity is already in use on {self.server}",
                retryable=True,
            )
            return False
        self._lock_acquired = True

        dispatcher = Dispatcher()
        router = Router()

        @router.message()
        async def receive_message(message: Any) -> None:
            await self._handle_trueconf_message(message, generation)

        @router.callback_query()
        async def receive_callback(callback: Any) -> None:
            await self._handle_trueconf_callback(callback, generation)

        dispatcher.include_router(router)
        self._bot_creation_task = asyncio.create_task(
            asyncio.to_thread(
                Bot.from_credentials,
                self.server,
                self.username,
                self.password,
                dispatcher=dispatcher,
                receive_unread_messages=False,
                receive_system_messages=False,
                skip_self_messages=True,
                max_in_memory_download_size=self.sdk_in_memory_download_limit,
                verify_ssl=self.verify_ssl,
                web_port=self.port,
                https=self.https,
            ),
            name="trueconf-create-bot",
        )
        try:
            self._bot = await asyncio.shield(self._bot_creation_task)
            self._bot_creation_task = None
            if self._stopping or generation != self._generation:
                await self.disconnect()
                return False
            self._run_task = asyncio.create_task(
                self._bot.run(handle_signals=False),
                name="trueconf-run",
            )
            ready_task = asyncio.create_task(
                self._bot.authorized_event.wait(),
                name="trueconf-ready",
            )
            try:
                done, _pending = await asyncio.wait(
                    {ready_task, self._run_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                if not ready_task.done():
                    ready_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await ready_task

            if self._run_task in done:
                error = (
                    None if self._run_task.cancelled() else self._run_task.exception()
                )
                await self._fail_startup(error)
                return False
            if self._stopping or generation != self._generation:
                await self.disconnect()
                return False

            self._mark_connected()
            self._watch_task = asyncio.create_task(
                self._watch_run_loop(generation),
                name="trueconf-watch-run",
            )
            return True
        except asyncio.CancelledError:
            await self.disconnect()
            raise
        except Exception as error:
            self._bot_creation_task = None
            await self._fail_startup(error)
            return False

    async def disconnect(self) -> None:
        task = self._disconnect_task
        if task is None:
            task = asyncio.create_task(
                self._disconnect_impl(),
                name="trueconf-disconnect",
            )
            self._disconnect_task = task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise
        finally:
            if task.done() and self._disconnect_task is task:
                self._disconnect_task = None

    async def _disconnect_impl(self) -> None:
        self._stopping = True
        self._generation += 1
        current = asyncio.current_task()
        creation_task = self._bot_creation_task
        if creation_task is not None:
            try:
                created_bot = await asyncio.shield(creation_task)
            except (asyncio.CancelledError, Exception):
                created_bot = None
            if self._bot is None:
                self._bot = created_bot
            if self._bot_creation_task is creation_task:
                self._bot_creation_task = None
        bot = self._bot
        if bot is not None:
            await self._stop_all_typing()
            with contextlib.suppress(Exception):
                # A wedged WebSocket transport must not hang disconnect (and
                # with it every standalone send that waits on this task).
                await asyncio.wait_for(bot.shutdown(), timeout=10)
        await self.cancel_background_tasks()

        run_task = self._run_task
        if run_task is not None and run_task is not current:
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        watch_task = self._watch_task
        if watch_task is not None and watch_task is not current:
            watch_task.cancel()
            await asyncio.gather(watch_task, return_exceptions=True)

        self._bot = None
        self._run_task = None
        self._watch_task = None
        self._release_lock()
        self._mark_disconnected()

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        chunks = _split_safe_chunks(self.format_message(content))
        if not chunks:
            return _empty_message_failure("send", self.server)
        if len(chunks) == 1:
            return await self._send_text_chunk(chat_id, chunks[0], reply_to)
        first = await self._send_text_chunk(chat_id, chunks[0], reply_to)
        if not first.success:
            return first
        return await self._deliver_chunk_chain(
            chat_id, chunks, first, operation="send", reply_to=reply_to
        )

    async def _deliver_chunk_chain(
        self,
        chat_id: str,
        chunks: list[str],
        first: SendResult,
        *,
        operation: str,
        reply_to: str | None = None,
    ) -> SendResult:
        """Deliver ``chunks[1:]`` chained after an already-sent first chunk.

        The first chunk was delivered by the caller (a plain send or an edit of
        the target message); each tail replies to the previous message.  Every
        delivered id is reported through ``message_id`` (last) and
        ``continuation_message_ids`` (earlier ids, send order) so Hermes
        re-targets later edits at the most recent continuation.
        """
        delivered_ids = [str(first.message_id)]
        previous_id = delivered_ids[0]
        for chunk in chunks[1:]:
            anchor = (
                reply_to if self.reply_to_mode == "all" and reply_to else previous_id
            )
            result = await self._send_text_chunk(chat_id, chunk, anchor)
            if not result.success:
                _LOGGER.warning(
                    "TrueConf multi-chunk %s on %s delivered %d/%d chunks "
                    "before a tail failed: %s",
                    operation,
                    self.server,
                    len(delivered_ids),
                    len(chunks),
                    result.error,
                )
                # The head content is already on screen; reporting a hard
                # failure would make Hermes re-send the whole payload and
                # duplicate it.  Report partial success with the delivered ids
                # and surface the gap through ``error`` instead.
                return SendResult(
                    success=True,
                    message_id=delivered_ids[-1],
                    continuation_message_ids=tuple(delivered_ids[:-1]),
                    error=(
                        f"TrueConf {operation} on {self.server} delivered "
                        f"{len(delivered_ids)}/{len(chunks)} chunks before a "
                        "tail chunk failed"
                    ),
                )
            chunk_id = str(result.message_id)
            delivered_ids.append(chunk_id)
            previous_id = chunk_id
        return SendResult(
            success=True,
            message_id=delivered_ids[-1],
            continuation_message_ids=tuple(delivered_ids[:-1]),
        )

    async def _send_text_chunk(
        self,
        chat_id: str,
        text: str,
        reply_to: str | None,
    ) -> SendResult:
        bot = self._bot
        if bot is None or not self.is_connected:
            return SendResult(
                success=False,
                error=f"TrueConf send failed on {self.server}: not connected",
                error_kind="transient",
                retryable=True,
            )
        try:
            response = await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=self.parse_mode,
                reply_message_id=self._anchor_reply(reply_to),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return _send_failure(error, self.server)
        return SendResult(
            success=True,
            message_id=str(response.message_id),
            raw_response=response,
        )

    def _anchor_reply(self, reply_to: str | None) -> str | None:
        """Resolve the reply anchor for a send under ``reply_to_mode``."""

        if self.reply_to_mode == "off":
            return None
        return reply_to

    async def send_exec_approval(
        self,
        chat_id: str,
        command: str,
        session_key: str,
        description: str = "dangerous command",
        metadata: dict[str, Any] | None = None,
        *,
        allow_permanent: bool = True,
        allow_session: bool = True,
        smart_denied: bool = False,
    ) -> SendResult:
        """Render Hermes' dangerous-command approval as TrueConf buttons."""
        choices = [("Allow once", "once", ButtonStyle.SUCCESS)]
        if allow_session:
            choices.append(("Allow session", "session", ButtonStyle.SUCCESS))
        if allow_permanent:
            choices.append(("Always allow", "always", ButtonStyle.SUCCESS))
        choices.append(("Deny", "deny", ButtonStyle.DANGER))
        request_id = self._next_button_id("approval")
        command_block = self.format_message(f"```\n{command}\n```")
        text = f"<b>Command approval required</b><br><br>{command_block}"
        if description:
            text += f"<br><br>Reason: {self.format_message(description)}"
        if smart_denied:
            text += "<br><br>The command was blocked by the safety policy."
        return await self._send_button_prompt(
            chat_id=chat_id,
            text=text,
            kind="approval",
            request_id=request_id,
            session_key=session_key,
            choices=choices,
            metadata=metadata,
        )

    async def send_slash_confirm(
        self,
        chat_id: str,
        title: str,
        message: str,
        session_key: str,
        confirm_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        """Render Hermes' slash-command confirmation as TrueConf buttons."""
        return await self._send_button_prompt(
            chat_id=chat_id,
            text=self.format_message(message),
            kind="slash",
            request_id=confirm_id,
            session_key=session_key,
            choices=[
                ("Approve once", "once", ButtonStyle.SUCCESS),
                ("Always approve", "always", ButtonStyle.PRIMARY),
                ("Cancel", "cancel", ButtonStyle.DANGER),
            ],
            metadata=metadata,
        )

    async def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: list | None,
        clarify_id: str,
        session_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        """Render a Hermes clarify prompt with numbered TrueConf buttons."""
        if not choices or len(choices) > 63:
            return await super().send_clarify(
                chat_id, question, choices, clarify_id, session_key, metadata
            )
        rendered_choices = [str(choice) for choice in choices]
        button_choices = [
            (str(index + 1), str(index), ButtonStyle.PRIMARY)
            for index in range(len(rendered_choices))
        ]
        button_choices.append(("Other", "other", ButtonStyle.DEFAULT))
        option_lines = "<br>".join(
            f"{index + 1}. {self.format_message(choice)}"
            for index, choice in enumerate(rendered_choices)
        )
        return await self._send_button_prompt(
            chat_id=chat_id,
            text=f"<b>{self.format_message(question)}</b><br><br>{option_lines}",
            kind="clarify",
            request_id=clarify_id,
            session_key=session_key,
            choices=button_choices,
            metadata=metadata,
            extra_state={"choices": rendered_choices},
        )

    async def _send_button_prompt(
        self,
        *,
        chat_id: str,
        text: str,
        kind: str,
        request_id: str,
        session_key: str,
        choices: list[tuple[str, str, ButtonStyle]],
        metadata: dict[str, Any] | None,
        extra_state: dict[str, Any] | None = None,
    ) -> SendResult:
        bot = self._bot
        if bot is None or not self.is_connected:
            return SendResult(success=False, error="Not connected")
        if ":" in request_id:
            # custom_data is parsed with split(":", 2); a colon in the id
            # would corrupt the choice field and break every button click.
            return SendResult(
                success=False,
                error="TrueConf prompt request id must not contain ':'",
                error_kind="unknown",
                retryable=False,
            )
        if await self._chat_is_group(chat_id):
            return await self._send_text_prompt(
                chat_id=chat_id,
                text=text,
                kind=kind,
                request_id=request_id,
                session_key=session_key,
                choices=choices,
                metadata=metadata,
                extra_state=extra_state,
            )
        buttons = [
            InlineKeyboardButton(
                text=label,
                command=_BUTTON_COMMAND,
                custom_data=f"{kind}:{request_id}:{value}",
                style=style,
            )
            for label, value, style in choices
        ]
        rows = [buttons[index : index + 8] for index in range(0, len(buttons), 8)]
        state_key = (kind, request_id)
        state = {"chat_id": str(chat_id), "session_key": session_key}
        if extra_state:
            state.update(extra_state)
        self._button_state[state_key] = state
        self._button_state.move_to_end(state_key)
        while len(self._button_state) > _MAX_BUTTON_PROMPTS:
            self._button_state.popitem(last=False)
        try:
            response = await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=self.parse_mode,
                reply_message_id=self._anchor_reply(
                    (metadata or {}).get("reply_to_message_id")
                ),
                buttons=InlineKeyboardMarkup(buttons=rows),
            )
        except asyncio.CancelledError:
            self._button_state.pop(state_key, None)
            raise
        except Exception as error:
            self._button_state.pop(state_key, None)
            return _send_failure(error, self.server)
        return SendResult(
            success=True,
            message_id=str(response.message_id),
            raw_response=response,
        )

    async def _chat_is_group(self, chat_id: str) -> bool:
        """Report whether the chat renders button prompts unsafe.

        ``CallbackQuery`` carries no clicker identity, so in group chats any
        member could resolve an approval or clarify. Group chats get a text
        prompt instead: the reply path goes through the normal message
        authorization and shows the sender.
        """

        chat_type = await self.get_chat_info(str(chat_id))
        return chat_type["type"] in {"group", "channel"}

    async def _send_text_prompt(
        self,
        *,
        chat_id: str,
        text: str,
        kind: str,
        request_id: str,
        session_key: str,
        choices: list[tuple[str, str, ButtonStyle]],
        metadata: dict[str, Any] | None,
        extra_state: dict[str, Any] | None = None,
    ) -> SendResult:
        """Deliver a button prompt as numbered text for group chats."""

        bot = self._bot
        if bot is None or not self.is_connected:
            return SendResult(success=False, error="Not connected")
        options = [(label, value) for label, value, _style in choices]
        state = {
            "chat_id": str(chat_id),
            "session_key": session_key,
            "kind": kind,
            "request_id": request_id,
            "options": options,
        }
        if extra_state:
            state.update(extra_state)
        self._text_prompts[str(chat_id)] = state
        while len(self._text_prompts) > _MAX_TEXT_PROMPTS:
            self._text_prompts.pop(next(iter(self._text_prompts)))
        prompt = text
        if kind != "clarify":
            # Clarify prompts already render their numbered options.
            option_lines = "<br>".join(
                f"{index + 1}. {label}" for index, (label, _value) in enumerate(options)
            )
            prompt = f"{text}<br><br>Reply with a number to choose:<br>{option_lines}"
        else:
            prompt = f"{text}<br><br>Reply with a number to answer."
        try:
            response = await bot.send_message(
                chat_id=chat_id,
                text=prompt,
                parse_mode=self.parse_mode,
                reply_message_id=self._anchor_reply(
                    (metadata or {}).get("reply_to_message_id")
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._text_prompts.pop(str(chat_id), None)
            return _send_failure(error, self.server)
        return SendResult(
            success=True,
            message_id=str(response.message_id),
            raw_response=response,
        )

    async def _consume_text_prompt(self, chat_id: str, text: str) -> bool:
        """Resolve a pending group text prompt from a chat message.

        Returns True when the message was consumed as a prompt answer and
        must not be forwarded to the agent.
        """

        state = self._text_prompts.get(str(chat_id))
        if state is None:
            return False
        stripped = text.strip()
        if not stripped:
            return False
        options: list[tuple[str, str]] = state["options"]
        responses = state.get("choices") if state["kind"] == "clarify" else None
        match_targets = [(label, value) for label, value in options]
        if responses:
            match_targets = [
                (str(index + 1), response_text)
                for index, response_text in enumerate(responses)
            ]
        index: int | None = None
        if stripped.isdigit():
            candidate = int(stripped) - 1
            if 0 <= candidate < len(match_targets):
                index = candidate
        else:
            for offset, (label, _value) in enumerate(match_targets):
                if label.casefold() == stripped.casefold():
                    index = offset
                    break
        if index is None:
            return False
        self._text_prompts.pop(str(chat_id), None)
        kind = state["kind"]
        if kind == "approval":
            from tools.approval import resolve_gateway_approval

            resolved = resolve_gateway_approval(
                state["session_key"], match_targets[index][1]
            )
            if resolved:
                self.resume_typing_for_chat(state["chat_id"])
            return True
        if kind == "slash":
            from tools import slash_confirm

            result = await slash_confirm.resolve(
                state["session_key"], state["request_id"], match_targets[index][1]
            )
            if result:
                await self.send(state["chat_id"], result)
            return True
        if kind == "clarify":
            from tools.clarify_gateway import resolve_gateway_clarify

            resolve_gateway_clarify(state["request_id"], match_targets[index][1])
            return True
        return False

    def _next_button_id(self, kind: str) -> str:
        """Return a prompt id that is unique across gateway restarts.

        Old button messages stay clickable in chats after a restart; a
        monotonic counter would re-issue ids like ``1`` so a click on a
        stale prompt could resolve a fresh approval it never belonged to.
        """
        del kind
        return uuid.uuid4().hex

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        # TrueConf locates edits by exact message ID. ``chat_id`` is part of
        # the Hermes contract, while ``finalize`` has no TrueConf wire meaning.
        bot = self._bot
        if bot is None or not self.is_connected:
            return SendResult(
                success=False,
                error=f"TrueConf edit failed on {self.server}: not connected",
                error_kind="transient",
                retryable=True,
            )
        chunks = _split_safe_chunks(self.format_message(content))
        if not chunks:
            return _empty_message_failure("edit", self.server)
        if len(chunks) == 1:
            return await self._edit_text_chunk(message_id, chunks[0])
        # Oversized edit: update the target message with the first chunk, then
        # deliver the remaining chunks as chained continuation messages.  The
        # last visible id is returned as ``message_id`` with the earlier ids in
        # ``continuation_message_ids`` so Hermes re-targets subsequent edits at
        # the most recent continuation (stream_consumer contract).
        first = await self._edit_text_chunk(message_id, chunks[0])
        if not first.success:
            return first
        return await self._deliver_chunk_chain(chat_id, chunks, first, operation="edit")

    async def _edit_text_chunk(
        self,
        message_id: str,
        text: str,
    ) -> SendResult:
        bot = self._bot
        if bot is None or not self.is_connected:
            return SendResult(
                success=False,
                error=f"TrueConf edit failed on {self.server}: not connected",
                error_kind="transient",
                retryable=True,
            )
        try:
            response = await bot.edit_message(
                message_id=message_id,
                text=text,
                parse_mode=self.parse_mode,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return _send_failure(error, self.server, operation="edit")
        return SendResult(
            success=True,
            message_id=str(response.message_id),
            raw_response=response,
        )

    async def get_chat_info(self, chat_id: str) -> dict[str, str]:
        bot = self._bot
        if bot is None:
            return {"chat_id": chat_id, "name": chat_id, "type": "dm"}
        try:
            info = await bot.get_chat_by_id(chat_id)
        except asyncio.CancelledError:
            raise
        except ApiErrorException as error:
            _LOGGER.debug(
                "TrueConf get_chat_info failed: chat=%s code=%s",
                chat_id,
                error.code,
            )
            return {"chat_id": chat_id, "name": chat_id, "type": "unknown"}
        except Exception:
            _LOGGER.debug(
                "TrueConf get_chat_info failed: chat=%s",
                chat_id,
                exc_info=True,
            )
            return {"chat_id": chat_id, "name": chat_id, "type": "unknown"}
        chat_type = _hermes_chat_type(info.chat_type) or "unknown"
        return {"chat_id": chat_id, "name": info.title or chat_id, "type": chat_type}

    async def send_or_update_status(
        self,
        chat_id: str,
        status_key: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        """Send a status bubble once per (chat, key); later calls edit it.

        Mirrors the Telegram adapter (#30045): progress and lifecycle status
        events would otherwise append a fresh bubble per event, while TrueConf
        edits let the same bubble update in place. Only single-chunk bubbles
        are cached — a shrunken multi-chunk edit would leave stale heads
        behind (accepted trade-off, see AGENTS.md).
        """

        key = (str(chat_id), str(status_key))
        cached_id = self._status_message_ids.get(key)
        if cached_id is not None:
            result = await self.edit_message(chat_id, cached_id, content)
            if result.success:
                return result
            self._status_message_ids.pop(key, None)
        result = await self.send(chat_id, content, metadata=metadata)
        if result.success and result.message_id and not result.continuation_message_ids:
            while len(self._status_message_ids) >= _MAX_STATUS_MESSAGES:
                self._status_message_ids.popitem(last=False)
            self._status_message_ids[key] = str(result.message_id)
        return result

    async def send_typing(
        self,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Show TrueConf's native typing activity for the current Hermes turn.

        Hermes calls this method repeatedly while the turn is running.  The
        first call starts one SDK context per chat; later calls leave it alone
        so the SDK can follow the server-provided ``retryAfter`` cadence.
        """

        bot = self._bot
        if bot is None or not self.is_connected:
            return
        async with self._typing_sender_lock:
            sender = self._typing_senders.get(chat_id)
            if sender is not None and sender.running:
                return
            sender = ChatActivitySender.typing(bot=bot, chat_id=chat_id)
            try:
                await sender.__aenter__()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.debug(
                    "TrueConf typing activity failed for chat=%s",
                    chat_id,
                    exc_info=True,
                )
                return
            self._typing_senders[chat_id] = sender

    async def stop_typing(self, chat_id: str) -> None:
        """Stop the SDK-managed typing activity for one chat."""

        async with self._typing_sender_lock:
            sender = self._typing_senders.pop(chat_id, None)
            if sender is None:
                return
            try:
                await sender.__aexit__(None, None, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.debug(
                    "TrueConf typing activity cleanup failed for chat=%s",
                    chat_id,
                    exc_info=True,
                )

    async def _stop_all_typing(self) -> None:
        """Close every typing context before the bot transport shuts down."""

        async with self._typing_sender_lock:
            senders = tuple(self._typing_senders.items())
            self._typing_senders.clear()
            for chat_id, sender in senders:
                try:
                    await sender.__aexit__(None, None, None)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.debug(
                        "TrueConf typing activity cleanup failed for chat=%s",
                        chat_id,
                        exc_info=True,
                    )

    def format_message(self, content: str) -> str:
        """Translate agent Markdown into the native TrueConf HTML subset."""

        return render_trueconf_html(content)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
        file_name: str | None = None,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> SendResult:
        return await self._send_file_attachment(
            operation="document",
            chat_id=chat_id,
            file_path=file_path,
            file_name=file_name,
            caption=caption,
            reply_to=reply_to,
        )

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> SendResult:
        bot = self._bot
        if bot is None or not self.is_connected:
            return SendResult(
                success=False,
                error=f"TrueConf image send failed on {self.server}: not connected",
                error_kind="transient",
                retryable=True,
            )
        try:
            file = FSInputFile(image_path)
            async with ChatActivitySender(
                bot=bot,
                chat_id=chat_id,
                activity=ChatActivity.UPLOADING_FILE,
            ):
                response = await bot.send_photo(
                    chat_id=chat_id,
                    file=file,
                    preview=file.clone(),
                    caption=(
                        self.format_message(caption) if caption is not None else None
                    ),
                    parse_mode=self.parse_mode,
                    reply_message_id=self._anchor_reply(reply_to),
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return _send_failure(error, self.server)
        return SendResult(
            success=True,
            message_id=str(response.message_id),
            raw_response=response,
        )

    async def send_sticker(
        self,
        chat_id: str,
        sticker_path: str,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> SendResult:
        """Send a WebP sticker with TrueConf's dedicated sticker activity."""

        bot = self._bot
        if bot is None or not self.is_connected:
            return SendResult(
                success=False,
                error=f"TrueConf sticker send failed on {self.server}: not connected",
                error_kind="transient",
                retryable=True,
            )
        try:
            async with ChatActivitySender(
                bot=bot,
                chat_id=chat_id,
                activity=ChatActivity.CHOOSING_STICKER,
            ):
                response = await bot.send_sticker(
                    chat_id=chat_id,
                    file=FSInputFile(sticker_path),
                    reply_message_id=self._anchor_reply(reply_to),
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return _send_failure(error, self.server)
        return SendResult(
            success=True,
            message_id=str(response.message_id),
            raw_response=response,
        )

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> SendResult:
        return await self._send_file_attachment(
            operation="video",
            chat_id=chat_id,
            file_path=video_path,
            caption=caption,
            reply_to=reply_to,
        )

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> SendResult:
        return await self._send_file_attachment(
            operation="voice",
            chat_id=chat_id,
            file_path=audio_path,
            caption=caption,
            reply_to=reply_to,
        )

    async def _send_file_attachment(
        self,
        *,
        operation: str,
        chat_id: str,
        file_path: str,
        caption: str | None,
        reply_to: str | None,
        file_name: str | None = None,
    ) -> SendResult:
        """Send caller-owned bytes with TrueConf's generic public file API."""
        bot = self._bot
        if bot is None or not self.is_connected:
            return SendResult(
                success=False,
                error=f"TrueConf {operation} send failed on {self.server}: not connected",
                error_kind="transient",
                retryable=True,
            )
        try:
            async with ChatActivitySender(
                bot=bot,
                chat_id=chat_id,
                activity=ChatActivity.UPLOADING_FILE,
            ):
                response = await bot.send_document(
                    chat_id=chat_id,
                    file=FSInputFile(file_path, file_name=file_name),
                    caption=(
                        self.format_message(caption) if caption is not None else None
                    ),
                    parse_mode=self.parse_mode,
                    reply_message_id=self._anchor_reply(reply_to),
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return _send_failure(error, self.server)
        return SendResult(
            success=True,
            message_id=str(response.message_id),
            raw_response=response,
        )

    async def _handle_trueconf_message(
        self,
        message: Any,
        generation: int,
    ) -> None:
        if generation != self._generation or self._stopping:
            return
        try:
            await self._process_trueconf_message(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "TrueConf inbound message handling failed: message=%s",
                getattr(message, "message_id", None),
            )

    async def _process_trueconf_message(self, message: Any) -> None:
        is_plain_text = message.type is TrueConfMessageType.PLAIN_MESSAGE
        is_forwarded = message.type is TrueConfMessageType.FORWARDED_MESSAGE
        is_location = message.type is TrueConfMessageType.LOCATION
        inbound_text: str | None = None
        if is_plain_text:
            inbound_text = message.text
        elif is_forwarded:
            forwarded_text = getattr(message.content, "text", None)
            if isinstance(forwarded_text, str) and forwarded_text:
                inbound_text = f"[Forwarded message]\n{forwarded_text}"
        elif is_location:
            latitude = getattr(message.content, "latitude", None)
            longitude = getattr(message.content, "longitude", None)
            if latitude is not None and longitude is not None:
                parts = ["[The user shared a location pin.]"]
                title = getattr(message.content, "title", None)
                if isinstance(title, str) and title:
                    parts.append(f"Venue: {title}")
                parts.extend(
                    (
                        f"latitude: {latitude}",
                        f"longitude: {longitude}",
                        "Map: https://www.google.com/maps/search/"
                        f"?api=1&query={latitude},{longitude}",
                        "Ask what they'd like to find nearby (restaurants, cafes, "
                        "etc.) and any preferences.",
                    )
                )
                inbound_text = "\n".join(parts)
        document = None
        photo = None
        video = None
        voice = None
        generic_audio = None
        if message.type is TrueConfMessageType.ATTACHMENT:
            document = message.document
            photo = message.photo
            video = message.video
            content_mime = getattr(message.content, "mime_type", "")
            if (
                document is None
                and photo is None
                and video is None
                and isinstance(content_mime, str)
                and content_mime.startswith("audio/")
            ):
                generic_audio = message.content
        elif message.type is TrueConfMessageType.VOICE_MESSAGE:
            voice = message.voice
        attachment = photo or video or document or generic_audio or voice
        if inbound_text is None and attachment is None:
            _LOGGER.debug(
                "Ignoring unsupported or empty TrueConf message type: type=%s "
                "message=%s",
                getattr(message.type, "name", message.type),
                getattr(message, "message_id", None),
            )
            return

        bot = self._bot
        if bot is None:
            return
        chat = message.chat
        chat_id = str(chat.chat_id)
        chat_type = _hermes_chat_type(chat.chat_type)
        if chat_type is None:
            return
        if (
            chat_type in {"group", "channel"}
            and self.allowed_chats
            and chat_id not in self.allowed_chats
        ):
            return
        if (
            chat_type in {"group", "channel"}
            and self.require_mention
            and chat_id not in self.free_response_chats
            and message.mention is not True
        ):
            return

        user_id = str(message.author.id)
        authorized: bool | None = None
        check = self._early_authorization_check
        if check is not None:
            try:
                decision = check(user_id, chat_type, chat_id)
            except Exception:
                decision = None
            if decision is True or decision is False:
                authorized = decision

        if (
            is_plain_text
            and authorized is True
            and await self._consume_text_prompt(chat_id, inbound_text or "")
        ):
            return

        channel_context = None
        if authorized is True and self._should_fetch_observed_context(
            chat_id, chat_type
        ):
            channel_context = await self._fetch_observed_context(
                bot,
                chat_id=chat_id,
                chat_type=chat_type,
                trigger_message_id=str(message.message_id),
            )

        user_name = user_id
        if authorized is True:
            with contextlib.suppress(Exception):
                user = await bot.get_user_display_name(user_id)
                if user.display_name:
                    user_name = str(user.display_name)

        if inbound_text is not None:
            text = inbound_text
            message_type = (
                HermesMessageType.LOCATION if is_location else HermesMessageType.TEXT
            )
            media_kind = ""
            attachment_name = ""
            unavailable_text = ""
        elif photo is not None:
            attachment_name = photo.file_name
            text = f"Image: {attachment_name}"
            message_type = HermesMessageType.PHOTO
            media_kind = "image"
            unavailable_text = f"Image unavailable: {attachment_name}"
        elif video is not None:
            attachment_name = video.file_name
            text = f"Video: {attachment_name}"
            message_type = HermesMessageType.VIDEO
            media_kind = "video"
            unavailable_text = f"Video unavailable: {attachment_name}"
        elif voice is not None:
            attachment_name = "voice-message"
            text = "Voice message"
            message_type = HermesMessageType.VOICE
            media_kind = "audio"
            unavailable_text = "Voice message unavailable"
        else:
            assert attachment is not None
            attachment_name = attachment.file_name
            text = f"Document: {attachment_name}"
            message_type = HermesMessageType.DOCUMENT
            media_kind = "document"
            unavailable_text = f"Document unavailable: {attachment_name}"
        media_urls: list[str] = []
        media_types: list[str] = []
        if attachment is not None and authorized is True:
            try:
                if (
                    not isinstance(attachment.file_id, str)
                    or not attachment.file_id
                    or not isinstance(attachment_name, str)
                    or not attachment_name
                    or isinstance(attachment.file_size, bool)
                    or not isinstance(attachment.file_size, int)
                    or attachment.file_size < 0
                    or not isinstance(attachment.mime_type, str)
                    or not attachment.mime_type
                ):
                    raise ValueError("malformed TrueConf attachment metadata")
                validate_inbound_media_size(
                    attachment.file_size,
                    media_type=media_kind,
                    max_bytes=self.inbound_media_max_bytes,
                )
                data = await bot.download_file_by_id(attachment.file_id)
                if not isinstance(data, bytes):
                    raise ValueError("TrueConf attachment download returned no bytes")
                cached = cache_media_bytes(
                    data,
                    filename=attachment_name,
                    mime_type=attachment.mime_type,
                    default_kind=media_kind,
                )
                if cached is None:
                    raise ValueError("Hermes rejected the downloaded attachment")
                media_urls.append(cached.path)
                media_types.append(cached.media_type)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning(
                    "TrueConf %s unavailable: message=%s chat=%s",
                    media_kind,
                    message.message_id,
                    chat_id,
                )
                text = unavailable_text

        source = self.build_source(
            chat_id=chat_id,
            chat_name=str(chat.chat_title or chat_id),
            chat_type=chat_type,
            user_id=user_id,
            user_name=user_name,
            message_id=str(message.message_id),
        )
        replied = message.reply_message
        reply_text = None
        reply_author_id = None
        reply_to_is_own_message = False
        if replied is not None:
            replied_chat_id = getattr(getattr(replied, "chat", None), "chat_id", None)
            if str(replied_chat_id) != chat_id:
                _LOGGER.warning(
                    "TrueConf reply context rejected for a different chat: chat=%s",
                    chat_id,
                )
            else:
                content = getattr(replied, "content", None)
                reply_text = getattr(content, "text", None)
                if not isinstance(reply_text, str) or not reply_text:
                    reply_text = None
                reply_author_id = getattr(getattr(replied, "author", None), "id", None)
                if reply_author_id is not None:
                    reply_author_id = str(reply_author_id)
                bot_id = getattr(bot, "me_id", None)
                reply_to_is_own_message = bool(
                    reply_author_id
                    and bot_id
                    and reply_author_id.casefold() == str(bot_id).casefold()
                )
        quote = getattr(message, "quote", None)
        if isinstance(quote, str) and quote:
            reply_text = quote
        event = MessageEvent(
            text=text,
            message_type=message_type,
            user_id=user_id,
            user_name=user_name,
            source=source,
            raw_message=message,
            message_id=str(message.message_id),
            media_urls=media_urls,
            media_types=media_types,
            reply_to_message_id=message.reply_message_id,
            reply_to_text=reply_text,
            reply_to_author_id=reply_author_id,
            reply_to_is_own_message=reply_to_is_own_message,
            channel_context=channel_context,
            timestamp=datetime.fromtimestamp(
                message.timestamp / 1000,
                tz=timezone.utc,
            ),
        )
        await self.handle_message(event)

    async def _handle_trueconf_callback(
        self,
        callback: Any,
        generation: int,
    ) -> None:
        """Resolve a TrueConf command button through Hermes' existing primitives."""
        if generation != self._generation or self._stopping:
            return
        if getattr(callback, "command", None) != _BUTTON_COMMAND:
            return
        data = getattr(callback, "custom_data", None)
        if not isinstance(data, str):
            return
        parts = data.split(":", 2)
        if len(parts) != 3:
            await self._answer_callback(callback, "Invalid button data")
            return
        kind, request_id, choice = parts
        state = self._button_state.get((kind, request_id))
        callback_chat = getattr(getattr(callback, "chat", None), "chat_id", None)
        if state is None or str(callback_chat) != state["chat_id"]:
            await self._answer_callback(callback, "This prompt has expired")
            return

        if kind == "approval":
            self._button_state.pop((kind, request_id), None)
            from tools.approval import resolve_gateway_approval

            resolved = resolve_gateway_approval(state["session_key"], choice)
            if resolved:
                self.resume_typing_for_chat(state["chat_id"])
            await self._answer_callback(
                callback,
                "Approved"
                if choice != "deny" and resolved
                else "Denied"
                if resolved
                else "This prompt has expired",
            )
            return

        if kind == "slash":
            self._button_state.pop((kind, request_id), None)
            from tools import slash_confirm

            result = await slash_confirm.resolve(
                state["session_key"], request_id, choice
            )
            await self._answer_callback(
                callback,
                "Done"
                if result is not None or choice == "cancel"
                else "This prompt has expired",
            )
            if result:
                await self.send(state["chat_id"], result)
            return

        if kind == "clarify":
            from tools.clarify_gateway import (
                mark_awaiting_text,
                resolve_gateway_clarify,
            )

            if choice == "other":
                if mark_awaiting_text(request_id):
                    await self._answer_callback(
                        callback, "Type your answer in the chat"
                    )
                else:
                    self._button_state.pop((kind, request_id), None)
                    await self._answer_callback(callback, "This prompt has expired")
                return
            try:
                index = int(choice)
            except ValueError:
                await self._answer_callback(callback, "Invalid choice")
                return
            stored_choices = state.get("choices") or []
            if not 0 <= index < len(stored_choices):
                await self._answer_callback(callback, "Invalid choice")
                return
            response = stored_choices[index]
            self._button_state.pop((kind, request_id), None)
            resolved = resolve_gateway_clarify(request_id, response)
            await self._answer_callback(
                callback,
                response[:120] if resolved else "This prompt has expired",
            )

    async def _answer_callback(self, callback: Any, text: str) -> None:
        # TODO(trueconf): buttons are sent without wait_reply, so the server
        # never supplies commandId and answerCommand cannot acknowledge the
        # click in the GUI. Re-enable once QT supports commandId delivery.
        if getattr(callback, "command_id", None) is None:
            return
        with contextlib.suppress(Exception):
            await callback.answer(text=text)

    def _should_fetch_observed_context(self, chat_id: str, chat_type: str) -> bool:
        return bool(
            self.observe_unmentioned_group_messages
            and self.observe_context_limit > 0
            and chat_type in {"group", "channel"}
            and self.require_mention
            and chat_id not in self.free_response_chats
            and self.allowed_chats
            and chat_id in self.allowed_chats
        )

    def _history_sender_is_authorized(
        self,
        sender_id: str,
        chat_type: str,
        chat_id: str,
    ) -> bool:
        check = self._early_authorization_check
        if check is None:
            return False
        try:
            return check(sender_id, chat_type, chat_id) is True
        except Exception:
            return False

    async def _fetch_observed_context(
        self,
        bot: Any,
        *,
        chat_id: str,
        chat_type: str,
        trigger_message_id: str,
    ) -> str | None:
        """Load unmentioned TrueConf chatter since the previous addressed turn."""

        try:
            response = await bot.get_chat_history(
                chat_id,
                count=min(self.observe_context_limit, 100),
                from_message_id=trigger_message_id,
            )
            messages = sorted(
                response.messages,
                key=lambda item: (item.box.id, item.box.position),
            )
            bot_id = str(bot.me_id).casefold()

            boundary = -1
            for index, historical in enumerate(messages):
                if str(historical.message_id) == trigger_message_id:
                    continue
                sender_id = str(historical.author.id)
                if (
                    sender_id.casefold() != bot_id
                    and historical.mention is True
                    and self._history_sender_is_authorized(
                        sender_id,
                        chat_type,
                        chat_id,
                    )
                ):
                    boundary = index

            lines: list[str] = []
            for historical in messages[boundary + 1 :]:
                if str(historical.message_id) == trigger_message_id:
                    continue
                sender_id = str(historical.author.id)
                if sender_id.casefold() == bot_id:
                    continue
                if historical.type is not TrueConfMessageType.PLAIN_MESSAGE:
                    continue
                text = historical.text
                if not isinstance(text, str) or not text.strip():
                    continue
                if historical.mention is True:
                    continue
                if not self._history_sender_is_authorized(
                    sender_id,
                    chat_type,
                    chat_id,
                ):
                    continue
                safe_sender = _strip_control_characters(sender_id).strip()
                safe_text = _strip_control_characters(text).strip()
                if not safe_sender or not safe_text:
                    continue
                safe_text = _truncate_context_html(
                    safe_text,
                    _OBSERVED_MESSAGE_MAX_CHARS,
                )
                lines.append(f"[{safe_sender}] {safe_text}")

            if not lines:
                return None

            kept_reversed: list[str] = []
            used = len(_OBSERVED_CONTEXT_HEADER)
            for line in reversed(lines):
                added = len(line) + 1
                if used + added > _OBSERVED_CONTEXT_MAX_CHARS:
                    break
                kept_reversed.append(line)
                used += added
            if not kept_reversed:
                return None
            return "\n".join((_OBSERVED_CONTEXT_HEADER, *reversed(kept_reversed)))
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug(
                "Unable to load TrueConf observed context: chat=%s trigger=%s",
                chat_id,
                trigger_message_id,
                exc_info=True,
            )
            return None

    async def _fail_startup(self, error: BaseException | None) -> None:
        bot = self._bot
        if bot is not None:
            with contextlib.suppress(Exception):
                await bot.shutdown()
        if self._run_task is not None:
            await asyncio.gather(self._run_task, return_exceptions=True)
        self._bot = None
        self._run_task = None
        self._release_lock()
        code, message, retryable = _classify_lifecycle_error(error, self.server)
        self._set_fatal_error(code, message, retryable=retryable)

    async def _watch_run_loop(self, generation: int) -> None:
        run_task = self._run_task
        if run_task is None:
            return
        error: BaseException | None = None
        try:
            await run_task
        except asyncio.CancelledError:
            raise
        except BaseException as caught:
            error = caught
        if self._stopping or generation != self._generation:
            return

        bot = self._bot
        if bot is not None:
            with contextlib.suppress(Exception):
                await bot.shutdown()
        self._bot = None
        self._run_task = None
        self._release_lock()
        code, message, retryable = _classify_lifecycle_error(error, self.server)
        self._set_fatal_error(code, message, retryable=retryable)
        await self._notify_fatal_error()

    def _release_lock(self) -> None:
        if not self._lock_acquired:
            return
        release_scoped_lock("trueconf", self._lock_identity)
        self._lock_acquired = False


def _strip_control_characters(value: str) -> str:
    """Remove characters that can forge prompt line boundaries."""

    return "".join(
        character
        for character in value
        if not unicodedata.category(character).startswith("C")
    )


def _truncate_context_html(value: str, limit: int) -> str:
    """Bound context while avoiding a trailing partial HTML tag."""

    if len(value) <= limit:
        return value
    truncated = value[:limit]
    if truncated.rfind("<") > truncated.rfind(">"):
        truncated = truncated[: truncated.rfind("<")]
    return truncated.rstrip()


def _classify_lifecycle_error(
    error: BaseException | None,
    server: str,
) -> tuple[str, str, bool]:
    if error is None:
        return (
            "transport_stopped",
            f"TrueConf connection to {server} stopped unexpectedly",
            True,
        )
    causes: list[BaseException] = []
    candidate: BaseException | None = error
    while candidate is not None and candidate not in causes:
        causes.append(candidate)
        candidate = candidate.__cause__ or candidate.__context__

    if any(
        isinstance(cause, (InvalidGrantError, TokenValidationError)) for cause in causes
    ):
        return (
            "authentication_failed",
            f"TrueConf authentication failed on {server}",
            False,
        )
    for cause in causes:
        if isinstance(cause, ApiErrorException) and cause.code in range(200, 205):
            return (
                "authentication_failed",
                f"TrueConf authentication failed on {server} (code {cause.code})",
                False,
            )
    if any(isinstance(cause, ssl.SSLCertVerificationError) for cause in causes):
        return (
            "tls_verification_failed",
            f"TrueConf TLS certificate verification failed on {server}",
            False,
        )
    if any(
        isinstance(cause, (TimeoutError, OSError, WSConnectionError))
        for cause in causes
    ):
        return (
            "transport_unavailable",
            f"TrueConf connection to {server} is unavailable",
            True,
        )
    return (
        "transport_unavailable",
        f"TrueConf connection to {server} failed: {type(error).__name__}",
        True,
    )


def _hermes_chat_type(chat_type: Any) -> str | None:
    try:
        trueconf_type = ChatType(chat_type)
    except (TypeError, ValueError):
        return None
    return {
        ChatType.P2P: "dm",
        ChatType.GROUP: "group",
        ChatType.CHANNEL: "channel",
    }.get(trueconf_type)


def _send_failure(
    error: Exception,
    server: str,
    *,
    operation: str = "send",
) -> SendResult:
    if isinstance(error, (TextMessageTooLongError, FileCaptionTooLongError)):
        kind, retryable = "too_long", False
    elif isinstance(error, WSConnectionError):
        kind, retryable = "transient", True
    elif isinstance(error, ApiErrorException):
        if error.code in {100, 101, 300, 301}:
            kind, retryable = "transient", True
        elif error.code in {302, 303}:
            kind, retryable = "forbidden", False
        elif error.code in {304, 306, 307, 308, 404}:
            kind, retryable = "not_found", False
        else:
            kind, retryable = "unknown", False
        return SendResult(
            success=False,
            error=f"TrueConf {operation} failed on {server} (code {error.code})",
            error_kind=kind,
            retryable=retryable,
        )
    else:
        kind, retryable = "unknown", False
    return SendResult(
        success=False,
        error=f"TrueConf {operation} failed on {server}: {type(error).__name__}",
        error_kind=kind,
        retryable=retryable,
    )

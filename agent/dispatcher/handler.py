"""Core Session Dispatcher — entry point for all WeChat messages.

Orchestrates the Redis session state, side-effect tracking, and
AgentCore streaming invocation. This module wires everything together.

Usage in the webhook service::

    dispatcher = Dispatcher(session, side_effects, agentcore, send_to_wechat)
    await dispatcher.on_message(user_id, incoming_message)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Awaitable, Callable

from .agentcore_client import AgentCoreClient
from .config import DEDUP_TTL, REDIS_VERSION_CHECK_INTERVAL, SIDE_EFFECT_TOOLS
from .models import (
    ChunkEvent,
    CompleteEvent,
    ErrorEvent,
    IncomingMessage,
    ToolUseEvent,
)
from .session import SessionState
from .side_effects import SideEffectTracker

logger = logging.getLogger(__name__)

# Type alias for the WeChat send callback
WeChatSender = Callable[[str, str], Awaitable[None]]


class Dispatcher:
    """Core dispatcher — entry point for all WeChat messages.

    Each incoming WeChat message flows through :meth:`on_message`,
    which updates Redis state and spawns a background task to
    invoke AgentCore if appropriate.

    The dispatcher ensures that:
    - Only the latest message's handler completes and sends a response.
    - Stale handlers self-cancel by checking the Redis version counter.
    - Side-effect tools from cancelled requests are tracked and
      carried over to the next invocation.
    - Consultant messages silence the AI immediately.
    """

    def __init__(
        self,
        session: SessionState,
        side_effects: SideEffectTracker,
        agentcore: AgentCoreClient,
        wechat_sender: WeChatSender,
    ):
        """
        Args:
            session: Redis-backed session state manager.
            side_effects: Side-effect tracker for cancelled requests.
            agentcore: AgentCore Runtime streaming client.
            wechat_sender: Async callable ``(user_id, text) -> None``
                           that sends a message to the user via WeChat API.
        """
        self._session = session
        self._side_effects = side_effects
        self._agentcore = agentcore
        self._wechat_sender = wechat_sender

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def on_message(self, user_id: str, message: IncomingMessage) -> None:
        """Handle an incoming WeChat message.

        This is the single entry point called by the webhook handler
        for every message — user or consultant.

        The method returns immediately. AgentCore invocation (if any)
        runs as a background ``asyncio`` task.

        Args:
            user_id: Parent/user identifier.
            message: The parsed incoming message.
        """
        # === Consultant message: cancel AI, go silent ===
        if message.source == "consultant":
            new_version = await self._session.mark_consultant(user_id)
            logger.info(
                "Consultant message for user=%s — AI silenced (version=%d)",
                user_id, new_version,
            )
            return

        # === User message: append + version++ (atomic) ===
        new_version, prev_state = await self._session.append_message(
            user_id,
            text=message.text,
            images=message.images,
        )

        logger.info(
            "dispatcher.message_received user=%s version=%d prev_state=%s "
            "text=%s",
            user_id, new_version, prev_state, message.text[:50],
        )

        # === Immediately try to invoke AgentCore ===
        asyncio.create_task(
            self._try_invoke(user_id, new_version, message, prev_state),
            name=f"dispatch-{user_id}-v{new_version}",
        )

    # ------------------------------------------------------------------
    # Invoke logic
    # ------------------------------------------------------------------

    async def _try_invoke(
        self,
        user_id: str,
        my_version: int,
        message: IncomingMessage,
        prev_state: str = "idle",
    ) -> None:
        """Attempt to invoke AgentCore for this version.

        If a newer message has arrived, this handler exits gracefully.
        Otherwise it streams the AgentCore response, tracking tool usage,
        and sends the final response to WeChat.

        Args:
            user_id: Parent/user identifier.
            my_version: The version counter assigned to this handler.
            message: The original incoming message (carries
                     conversation_history and session_id).
            prev_state: Session state before this message was appended.
                        Used to decide whether to poll for side effects.
        """
        request_id = str(uuid.uuid4())[:12]

        # --- Step 1: Atomic claim ---
        claimed = await self._session.try_claim(user_id, my_version, request_id)
        if not claimed:
            logger.info(
                "dispatcher.invoke_skipped user=%s version=%d request_id=%s "
                "reason=superseded",
                user_id, my_version, request_id,
            )
            return

        # --- Step 1.5: Wait for previous handler to finish cancellation ---
        # If the session was actively processing, the old handler may still
        # be mid-stream and need time to detect the version mismatch and
        # save side-effect data → poll for up to 2s.
        # Otherwise (idle/consultant), do a single immediate check in case
        # a side effect was saved before the session went idle.
        pending = None
        if prev_state == "processing":
            pending = await self._wait_for_side_effect(user_id, max_wait=2.0)
        else:
            pending = await self._side_effects.get_and_clear(user_id)

        # --- Step 2: Pop buffered messages ---
        messages, images = await self._session.pop_messages(user_id)
        merged_prompt = "\n".join(messages) if messages else message.text
        if pending:
            hint = SideEffectTracker.build_system_hint(pending)
            merged_prompt = hint + merged_prompt

        # Merge images from buffer with any from the message
        all_images = images if images else (message.images or [])

        logger.info(
            "dispatcher.invoke_started user=%s version=%d request_id=%s "
            "prompt_len=%d image_count=%d had_side_effect=%s",
            user_id, my_version, request_id,
            len(merged_prompt), len(all_images), bool(pending),
        )

        # --- Step 3: Invoke AgentCore (streaming) ---
        executed_tools: list[str] = []
        response_chunks: list[str] = []
        chunk_count = 0

        try:
            async for event in self._agentcore.invoke_stream(
                prompt=merged_prompt,
                parent_id=user_id,
                conversation_history=message.conversation_history,
                images=all_images if all_images else None,
                session_id=message.session_id,
            ):
                if isinstance(event, ToolUseEvent):
                    executed_tools.append(event.tool_name)

                elif isinstance(event, ChunkEvent):
                    response_chunks.append(event.data)
                    chunk_count += 1

                    # Periodic version check to detect supersession
                    if chunk_count % REDIS_VERSION_CHECK_INTERVAL == 0:
                        current_version = await self._session.get_version(user_id)
                        if current_version != my_version:
                            logger.info(
                                "dispatcher.invoke_cancelled user=%s "
                                "request_id=%s reason=superseded_mid_stream "
                                "my_version=%d current=%d chunks=%d",
                                user_id, request_id, my_version,
                                current_version, chunk_count,
                            )
                            await self._handle_cancel(
                                user_id, executed_tools, "".join(response_chunks),
                            )
                            return

                elif isinstance(event, CompleteEvent):
                    break

                elif isinstance(event, ErrorEvent):
                    logger.error(
                        "dispatcher.invoke_error user=%s request_id=%s "
                        "error=%s",
                        user_id, request_id, event.message,
                    )
                    # On error, don't send anything; let the next message retry
                    await self._session.set_state(user_id, "idle")
                    return

        except Exception as e:
            logger.error(
                "dispatcher.invoke_exception user=%s request_id=%s "
                "error=%s",
                user_id, request_id, e,
                exc_info=True,
            )
            await self._handle_cancel(
                user_id, executed_tools, "".join(response_chunks),
            )
            await self._session.set_state(user_id, "idle")
            return

        # --- Step 4: Final version check before sending ---
        current_version = await self._session.get_version(user_id)
        if current_version != my_version:
            logger.info(
                "dispatcher.invoke_cancelled user=%s request_id=%s "
                "reason=superseded_after_complete my_version=%d current=%d",
                user_id, request_id, my_version, current_version,
            )
            await self._handle_cancel(
                user_id, executed_tools, "".join(response_chunks),
            )
            return

        # --- Step 5: Still valid — send to WeChat ---
        full_response = "".join(response_chunks)

        if full_response.strip():
            try:
                await self._wechat_sender(user_id, full_response)
            except Exception as e:
                logger.error(
                    "dispatcher.wechat_send_failed user=%s request_id=%s "
                    "error=%s",
                    user_id, request_id, e,
                )

        await self._session.set_state(user_id, "idle")

        logger.info(
            "dispatcher.response_sent user=%s request_id=%s "
            "response_len=%d tool_count=%d chunk_count=%d",
            user_id, request_id,
            len(full_response), len(executed_tools), chunk_count,
        )

    # ------------------------------------------------------------------
    # Side-effect polling
    # ------------------------------------------------------------------

    async def _wait_for_side_effect(
        self,
        user_id: str,
        max_wait: float = 1.0,
        poll_interval: float = 0.1,
    ) -> dict | None:
        """Poll for a pending side-effect key in Redis.

        When this handler supersedes a previous one that was mid-stream,
        the old handler may still be finishing up — it needs to detect the
        version mismatch, then call ``_handle_cancel`` to save the side
        effect.  We poll briefly so we don't race ahead and miss it.

        Args:
            user_id: Parent/user identifier.
            max_wait: Maximum total seconds to poll (default 1.0s).
            poll_interval: Seconds between polls (default 0.1s).

        Returns:
            The side-effect dict if found within the window, else ``None``.
        """
        elapsed = 0.0
        while elapsed < max_wait:
            if await self._side_effects.check_exists(user_id):
                result = await self._side_effects.get_and_clear(user_id)
                if result:
                    logger.info(
                        "dispatcher.side_effect_found user=%s "
                        "wait_time=%.2fs tools=%s",
                        user_id, elapsed, result.get("tools"),
                    )
                    return result
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        # One final check after the wait window
        result = await self._side_effects.get_and_clear(user_id)
        if result:
            logger.info(
                "dispatcher.side_effect_found_final user=%s "
                "wait_time=%.2fs tools=%s",
                user_id, elapsed, result.get("tools"),
            )
        return result

    # ------------------------------------------------------------------
    # Cancel handling
    # ------------------------------------------------------------------

    async def _handle_cancel(
        self,
        user_id: str,
        executed_tools: list[str],
        partial_response: str,
    ) -> None:
        """Handle a request that was superseded by a newer message.

        If the cancelled request had executed any side-effect tools,
        save the information to Redis for the next invocation to pick up.
        Otherwise, simply discard.

        Args:
            user_id: Parent/user identifier.
            executed_tools: Names of tools that were called during this request.
            partial_response: The accumulated (partial) AI response text.
        """
        side_effects = [t for t in executed_tools if t in SIDE_EFFECT_TOOLS]

        if side_effects:
            await self._side_effects.save(
                user_id, side_effects, partial_response,
            )
            logger.warning(
                "dispatcher.cancel_with_side_effects user=%s "
                "tools=%s response_len=%d",
                user_id, side_effects, len(partial_response),
            )
        else:
            logger.info(
                "dispatcher.cancel_clean user=%s tools=%s",
                user_id, executed_tools,
            )

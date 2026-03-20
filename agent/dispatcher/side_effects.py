"""Side-effect tracking for cancelled requests.

When a request is superseded by a newer message but has already
executed state-modifying tools (book_class, cancel_class, etc.),
the side-effect information is persisted to Redis so the next
invocation can pick it up and inform the agent.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from .config import REDIS_SIDE_EFFECT_TTL

if TYPE_CHECKING:
    from .redis_client import RedisClient

logger = logging.getLogger(__name__)


def _side_effect_key(user_id: str) -> str:
    return f"side_effect:{user_id}"


class SideEffectTracker:
    """Track and recover from cancelled requests with side effects."""

    def __init__(self, redis: "RedisClient"):
        self._redis = redis

    async def save(
        self,
        user_id: str,
        tools: list[str],
        response: str,
    ) -> None:
        """Save pending side-effect information to Redis.

        Called when a cancelled request had executed state-modifying tools.
        The data is stored with a TTL and will be consumed by the next
        invocation via :meth:`get_and_clear`.

        Args:
            user_id: Parent/user identifier.
            tools: List of side-effect tool names that were executed
                   (e.g. ``["cancel_class"]``).
            response: The partial/full AI response that was discarded.
        """
        payload = json.dumps(
            {
                "tools": tools,
                "response": response,
                "timestamp": int(time.time()),
            },
            ensure_ascii=False,
        )
        await self._redis.client.setex(
            _side_effect_key(user_id),
            REDIS_SIDE_EFFECT_TTL,
            payload,
        )
        logger.warning(
            "Saved side effect for user=%s tools=%s response_len=%d",
            user_id, tools, len(response),
        )

    async def get_and_clear(self, user_id: str) -> dict | None:
        """Atomically read and delete pending side-effect data.

        Returns ``None`` if no pending side effect exists for this user.
        This is a single-use operation — the second call returns ``None``.

        Returns:
            Dict with keys ``tools``, ``response``, ``timestamp``
            or ``None``.
        """
        key = _side_effect_key(user_id)

        # Use pipeline for atomic GET + DEL
        pipe = self._redis.client.pipeline()
        pipe.get(key)
        pipe.delete(key)
        results = await pipe.execute()

        raw = results[0]
        if not raw:
            return None

        data = json.loads(raw)
        logger.info(
            "Retrieved side effect for user=%s tools=%s",
            user_id, data.get("tools"),
        )
        return data

    async def check_exists(self, user_id: str) -> bool:
        """Check whether a pending side-effect key exists (non-destructive).

        Used by the polling loop in the dispatcher to wait for the
        previous handler to finish saving its side-effect data.

        Returns:
            ``True`` if the key exists, ``False`` otherwise.
        """
        return bool(await self._redis.client.exists(_side_effect_key(user_id)))

    @staticmethod
    def build_system_hint(pending: dict) -> str:
        """Build the system prompt injection for the agent.

        This hint tells the agent that a previous operation was executed
        but the response was never delivered to the user, so it should
        verify current state before acting.

        Args:
            pending: Dict from :meth:`get_and_clear`.

        Returns:
            A Chinese-language system hint string to prepend to the prompt.
        """
        tools = pending.get("tools", [])
        return (
            f"[系统提示] 上一次对话中已执行操作 {tools}，"
            f"但回复未送达用户。请先用 get_booking_records 确认当前状态，"
            f"再结合确认结果回复用户最新消息。\n\n"
        )

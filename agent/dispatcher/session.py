"""Session state management backed by Redis.

All Redis operations for a single user's dispatcher session.
Uses Lua scripts for atomicity where needed.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from .config import REDIS_SESSION_TTL

if TYPE_CHECKING:
    from .redis_client import RedisClient

logger = logging.getLogger(__name__)


def _session_key(user_id: str) -> str:
    return f"session:{user_id}"


class SessionState:
    """All Redis session operations for a single user.

    Each method is a thin wrapper around a Redis command or Lua script,
    operating on the ``session:{user_id}`` hash key.
    """

    def __init__(self, redis: "RedisClient"):
        self._redis = redis

    # ------------------------------------------------------------------
    # Atomic Lua script operations
    # ------------------------------------------------------------------

    async def append_message(
        self,
        user_id: str,
        text: str,
        images: list[str] | None = None,
        timestamp: int | None = None,
    ) -> tuple[int, str]:
        """Append a message (and optional images) to the buffer, version++.

        This is an atomic operation (Lua script). It:
        1. Reads the current state.
        2. Increments the version counter.
        3. Appends `text` to the messages JSON array.
        4. Merges `images` into the images JSON array.
        5. Refreshes the key TTL.

        Args:
            user_id: Parent/user identifier.
            text: The message text to buffer.
            images: Image URLs to accumulate (default: none).
            timestamp: Unix timestamp (auto-generated if omitted).

        Returns:
            (new_version, previous_state) — e.g. ``(3, "idle")``.
        """
        import time as _time

        ts = timestamp or int(_time.time())
        images_json = json.dumps(images or [])

        result = await self._redis.eval_script(
            "append_and_increment",
            keys=[_session_key(user_id)],
            args=[text, str(ts), images_json],
        )

        new_version = int(result[0])
        prev_state = result[1] if result[1] else "idle"

        logger.debug(
            "append_message user=%s version=%d prev_state=%s",
            user_id, new_version, prev_state,
        )
        return new_version, prev_state

    async def try_claim(
        self,
        user_id: str,
        version: int,
        request_id: str,
    ) -> bool:
        """Atomically claim the right to invoke AgentCore.

        Only succeeds if `version` matches the current Redis version,
        meaning no newer message has arrived since this handler started.

        Args:
            user_id: Parent/user identifier.
            version: The version this handler was assigned.
            request_id: UUID for logging/tracing.

        Returns:
            True if claimed, False if superseded by a newer message.
        """
        result = await self._redis.eval_script(
            "try_claim",
            keys=[_session_key(user_id)],
            args=[str(version), request_id],
        )

        claimed = int(result) == 1
        logger.debug(
            "try_claim user=%s version=%d request_id=%s claimed=%s",
            user_id, version, request_id, claimed,
        )
        return claimed

    async def pop_messages(self, user_id: str) -> tuple[list[str], list[str]]:
        """Atomically read and clear message + image buffers.

        Args:
            user_id: Parent/user identifier.

        Returns:
            (messages, images) — both are lists of strings.
        """
        result = await self._redis.eval_script(
            "pop_messages",
            keys=[_session_key(user_id)],
            args=[],
        )

        messages = json.loads(result[0]) if result[0] else []
        images = json.loads(result[1]) if result[1] else []

        logger.debug(
            "pop_messages user=%s messages=%d images=%d",
            user_id, len(messages), len(images),
        )
        return messages, images

    # ------------------------------------------------------------------
    # Simple Redis operations (non-Lua)
    # ------------------------------------------------------------------

    async def get_version(self, user_id: str) -> int:
        """Read the current version counter.

        Used for periodic version checks during streaming.

        Returns:
            Current version, or 0 if session does not exist.
        """
        val = await self._redis.client.hget(_session_key(user_id), "version")
        return int(val) if val else 0

    async def set_state(self, user_id: str, state: str) -> None:
        """Set the session state.

        Args:
            state: One of "idle", "processing", "consultant".
        """
        await self._redis.client.hset(_session_key(user_id), "state", state)

    async def mark_consultant(self, user_id: str) -> int:
        """Mark session as consultant-active and increment version.

        This causes any in-flight AI processing to self-cancel
        on its next version check.

        Returns:
            The new version after incrementing.
        """
        key = _session_key(user_id)
        pipe = self._redis.client.pipeline()
        pipe.hincrby(key, "version", 1)
        pipe.hset(key, "state", "consultant")
        pipe.expire(key, REDIS_SESSION_TTL)
        results = await pipe.execute()

        new_version = int(results[0])
        logger.info(
            "mark_consultant user=%s version=%d",
            user_id, new_version,
        )
        return new_version

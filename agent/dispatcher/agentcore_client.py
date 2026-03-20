"""Async streaming client for AgentCore Runtime.

Wraps the ``invoke_agent_runtime`` API (boto3 bedrock-agentcore)
and yields typed :class:`AgentCoreEvent` objects.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import AsyncIterator

import boto3

from .config import AGENTCORE_INVOKE_TIMEOUT
from .models import (
    AgentCoreEvent,
    ChunkEvent,
    CompleteEvent,
    ErrorEvent,
    ToolUseEvent,
)

logger = logging.getLogger(__name__)


class AgentCoreClient:
    """Async-friendly streaming client for AgentCore Runtime.

    Note: ``invoke_agent_runtime`` is a synchronous boto3 call.
    We run it in a thread executor from the async caller to avoid
    blocking the event loop. The response body itself is a
    streaming iterator that yields JSON chunks.
    """

    def __init__(self, runtime_arn: str, region: str = "us-west-2"):
        self.runtime_arn = runtime_arn
        self.region = region
        self._boto_client = boto3.client(
            "bedrock-agentcore",
            region_name=region,
        )

    def invoke_stream_sync(
        self,
        prompt: str,
        parent_id: str,
        conversation_history: str = "",
        images: list[str] | None = None,
        session_id: str = "",
    ) -> list[AgentCoreEvent]:
        """Synchronously invoke AgentCore Runtime and return all events.

        This is the low-level call. For async usage, wrap with
        ``asyncio.to_thread`` or use :meth:`invoke_stream`.

        Returns:
            List of :class:`AgentCoreEvent` objects in order.
        """
        payload = {
            "prompt": prompt,
            "parent_id": parent_id,
            "session_id": session_id or f"dispatch-{int(time.time())}",
            "conversation_history": conversation_history,
            "images": images or [],
        }
        # boto3 blob type auto-encodes bytes to base64 for the API call,
        # so we pass raw bytes here (NOT pre-encoded base64).
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        runtime_session_id = f"{uuid.uuid4()}-{int(time.time())}"

        logger.info(
            "Invoking AgentCore: parent_id=%s prompt_len=%d session=%s",
            parent_id, len(prompt), runtime_session_id[:20],
        )

        response = self._boto_client.invoke_agent_runtime(
            agentRuntimeArn=self.runtime_arn,
            runtimeSessionId=runtime_session_id,
            payload=payload_bytes,
        )

        # Parse the streaming response body.
        # invoke_agent_runtime returns the streaming body under the
        # ``response`` key (not ``body``).
        events: list[AgentCoreEvent] = []
        body = response.get("response")
        if body is None:
            events.append(ErrorEvent(message="No response body from AgentCore"))
            return events

        # body is a StreamingBody — read it in full.
        # AgentCore returns SSE format: "data: {json}\n\n" per event.
        raw = body.read().decode("utf-8")

        for block in raw.split("\n\n"):
            block = block.strip()
            if not block:
                continue

            # Strip SSE "data: " prefix if present
            if block.startswith("data: "):
                block = block[len("data: "):]
            elif block.startswith("data:"):
                block = block[len("data:"):]

            try:
                event_data = json.loads(block)
            except json.JSONDecodeError:
                # Try fixing unescaped newlines in data field
                import re
                fixed = re.sub(
                    r'("data":\s*")(.*?)("\s*})',
                    lambda m: m.group(1) + m.group(2).replace("\n", "\\n") + m.group(3),
                    block,
                    flags=re.DOTALL,
                )
                try:
                    event_data = json.loads(fixed)
                except json.JSONDecodeError:
                    logger.warning("Unparseable AgentCore event: %s", block[:100])
                    continue

            event_type = event_data.get("type", "")
            if event_type == "chunk":
                events.append(ChunkEvent(data=event_data.get("data", "")))
            elif event_type == "tool_use":
                events.append(ToolUseEvent(tool_name=event_data.get("tool_name", "")))
            elif event_type == "complete":
                events.append(CompleteEvent(session_id=event_data.get("session_id", "")))
            elif event_type == "error":
                events.append(ErrorEvent(message=event_data.get("message", "")))
            else:
                logger.debug("Unknown AgentCore event type: %s", event_type)

        return events

    async def invoke_stream(
        self,
        prompt: str,
        parent_id: str,
        conversation_history: str = "",
        images: list[str] | None = None,
        session_id: str = "",
    ) -> AsyncIterator[AgentCoreEvent]:
        """Invoke AgentCore Runtime and yield streaming events asynchronously.

        Runs the synchronous boto3 call in a thread to avoid blocking
        the event loop, then yields events one by one.

        Yields:
            :class:`AgentCoreEvent` instances (ChunkEvent, ToolUseEvent, etc.)
        """
        import asyncio

        try:
            events = await asyncio.wait_for(
                asyncio.to_thread(
                    self.invoke_stream_sync,
                    prompt,
                    parent_id,
                    conversation_history,
                    images,
                    session_id,
                ),
                timeout=AGENTCORE_INVOKE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(
                "AgentCore invoke timed out after %ds for parent_id=%s",
                AGENTCORE_INVOKE_TIMEOUT, parent_id,
            )
            yield ErrorEvent(message=f"AgentCore invoke timed out after {AGENTCORE_INVOKE_TIMEOUT}s")
            return
        except Exception as e:
            logger.error("AgentCore invoke failed: %s", e)
            yield ErrorEvent(message=str(e))
            return

        for event in events:
            yield event

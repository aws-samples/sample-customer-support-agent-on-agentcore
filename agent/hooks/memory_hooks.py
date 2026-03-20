"""XXXX Memory Hooks - Claude Agent SDK hook implementations for memory integration"""

import json
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .memory_manager import MemoryManager

from ..observability import get_tracer, trace_memory_operation

logger = logging.getLogger(__name__)

# Global reference to memory manager - set during agent initialization
# Note: This approach works for single-agent scenarios. For concurrent multi-agent
# usage with different parent_ids, consider passing manager through hook context.
_memory_manager: "MemoryManager | None" = None


def set_memory_manager(manager: "MemoryManager") -> None:
    """Set the global memory manager instance for hooks to use."""
    global _memory_manager
    _memory_manager = manager


def get_memory_manager() -> "MemoryManager | None":
    """Get the current memory manager instance."""
    return _memory_manager


async def user_prompt_submit_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    UserPromptSubmit hook - searches memories and user preferences before processing.

    This hook:
    1. Extracts the user prompt from input_data
    2. Searches long-term memories for relevant information
    3. Retrieves user preferences from UserPreferenceStrategy
    4. Returns additional context to be injected into the conversation

    Args:
        input_data: Hook input containing 'prompt' field
        tool_use_id: Unique identifier for this hook invocation (may be None)
        context: Additional context from the agent

    Returns:
        HookJSONOutput with additionalContext field containing memories and preferences
    """
    manager = get_memory_manager()
    if not manager or not manager.is_enabled:
        return {}

    # Extract user prompt
    prompt = input_data.get("prompt", "")
    if not prompt:
        logger.debug("No prompt in input_data, skipping memory search")
        return {}

    # Use session_id from input_data (preferred) or generate a stable one
    session_id = input_data.get("session_id", "default_session")

    tracer = get_tracer()
    try:
        # 1. Search for relevant semantic memories
        with trace_memory_operation(tracer, "search_semantic", query=prompt[:100]) as span:
            memories = manager.search_memories(
                query=prompt,
                session_id=session_id,
                top_k=3,
            )
            span.set_attribute("xxxx.memory.result_count", len(memories))

        # 2. Search for relevant user preferences
        with trace_memory_operation(tracer, "search_preferences", query=prompt[:100]) as span:
            preferences = manager.search_user_preferences(
                query=prompt,
                session_id=session_id,
                top_k=3,
            )
            span.set_attribute("xxxx.memory.result_count", len(preferences))

        # If no memories and no preferences found, skip
        if not memories and not preferences:
            logger.debug("No relevant memories or preferences found")
            return {}

        # Format both as context
        formatted_context = manager.format_memories_as_context(
            memories=memories,
            preferences=preferences,
        )

        total_count = len(memories) + len(preferences)
        logger.info(
            f"Injecting context: {len(memories)} memories, {len(preferences)} preferences"
        )
        logger.debug(f"Context content:\n{formatted_context}")

        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": formatted_context,
            }
        }

    except Exception as e:
        logger.error(f"Error in user_prompt_submit_hook: {e}")
        return {}


async def stop_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Stop hook - saves conversation turns after agent responses.

    This hook:
    1. Gets the session_id and transcript_path from input_data
    2. Reads the transcript file to extract conversation
    3. Saves the last conversation turn to long-term memory

    Args:
        input_data: Hook input containing session_id and transcript_path
        tool_use_id: Unique identifier for this hook invocation (may be None)
        context: Additional context from the agent

    Returns:
        Empty dict (stop hooks don't modify output)
    """
    manager = get_memory_manager()
    if not manager or not manager.is_enabled:
        return {}

    # Extract session_id from input_data (StopHookInput has session_id field)
    session_id = input_data.get("session_id", "default_session")

    user_prompt = ""
    assistant_response = ""

    # Primary: use buffered data from chat_stream (avoids transcript timing issue)
    if manager:
        user_prompt, assistant_response = manager.get_and_clear_last_turn()
        if user_prompt and assistant_response:
            logger.debug(f"Using buffered conversation turn (prompt={len(user_prompt)} chars, response={len(assistant_response)} chars)")

    # Fallback: try transcript file
    if not user_prompt or not assistant_response:
        transcript_path = input_data.get("transcript_path", "")
        if transcript_path:
            user_prompt, assistant_response = _read_transcript_file(transcript_path)

    if not user_prompt or not assistant_response:
        logger.info(f"Could not extract conversation, skipping memory save (user_prompt={bool(user_prompt)}, assistant_response={bool(assistant_response)})")
        return {}

    tracer = get_tracer()
    try:
        with trace_memory_operation(
            tracer, "save_turn",
            session_id=session_id,
            user_length=len(user_prompt),
            response_length=len(assistant_response),
        ) as span:
            success = manager.save_conversation_turn(
                session_id=session_id,
                user_message=user_prompt,
                assistant_response=assistant_response,
            )
            span.set_attribute("xxxx.memory.save_success", success)

            if success:
                logger.info("Saved conversation turn to memory")
            else:
                logger.warning("Failed to save conversation turn")

    except Exception as e:
        logger.error(f"Error in stop_hook: {e}")

    # Stop hooks don't return output modifications
    return {}


def _read_transcript_file(transcript_path: str) -> tuple[str, str]:
    """
    Read transcript from JSONL file and extract the last user prompt and assistant response.

    Transcript message types from Claude Agent SDK:
    - "user": User messages
    - "assistant": Assistant responses (content can include text, thinking, tool_use, tool_result blocks)
    - "system": System metadata
    - "result": Execution statistics
    - "stream_event": Streaming events

    Args:
        transcript_path: Path to the transcript JSONL file

    Returns:
        Tuple of (user_prompt, assistant_response)
    """
    user_prompt = ""
    assistant_response = ""

    try:
        path = Path(transcript_path)
        if not path.exists():
            logger.warning(f"Transcript file not found: {transcript_path}")
            return "", ""

        logger.debug(f"Reading transcript file: {transcript_path}")

        # Read JSONL file - each line is a JSON object
        messages = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse line: {e}")
                        continue

        if messages:
            logger.debug(f"Message types: {[m.get('type', 'unknown') for m in messages]}")

        # Collect ALL text from assistant messages (there may be multiple assistant messages
        # in one turn: first with tool_use, then with tool_result, finally with text response)
        all_assistant_text = []
        last_user_prompt = ""

        for entry in messages:
            entry_type = entry.get("type", "")
            inner_message = entry.get("message", {})
            content = inner_message.get("content", "")

            if entry_type == "user":
                # Extract user prompt text
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                    user_text = " ".join(text_parts)
                else:
                    user_text = str(content) if content else ""

                if user_text:
                    # New user message resets the assistant collection
                    last_user_prompt = user_text
                    all_assistant_text = []

            elif entry_type == "assistant":
                # Extract text from assistant content blocks
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                all_assistant_text.append(text)
                elif isinstance(content, str) and content:
                    all_assistant_text.append(content)

        user_prompt = last_user_prompt
        assistant_response = " ".join(all_assistant_text)

    except Exception as e:
        logger.error(f"Error reading transcript file: {e}")

    return user_prompt, assistant_response

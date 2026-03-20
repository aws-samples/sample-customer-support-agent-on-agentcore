"""Data models for the Session Dispatcher."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IncomingMessage:
    """Represents an incoming WeChat message passed from the webhook handler.

    Attributes:
        text: Current message text (the user's prompt).
        source: Who sent this message — "parent" (user) or "consultant" (human agent).
        conversation_history: Historical conversation context from WeChat system.
        images: Image URLs attached to this message.
        session_id: WeChat session identifier.
        parent_id: Parent/user identifier.
    """

    text: str
    source: str  # "parent" | "consultant"
    conversation_history: str = ""
    images: list[str] = field(default_factory=list)
    session_id: str = ""
    parent_id: str = ""


# ============================================================
# AgentCore streaming event types
# ============================================================


@dataclass
class ChunkEvent:
    """A text chunk from the agent's response stream."""
    data: str


@dataclass
class ToolUseEvent:
    """Indicates the agent is calling a tool."""
    tool_name: str


@dataclass
class CompleteEvent:
    """The agent has finished processing."""
    session_id: str = ""


@dataclass
class ErrorEvent:
    """An error occurred during agent processing."""
    message: str


# Union type for all streaming events
AgentCoreEvent = ChunkEvent | ToolUseEvent | CompleteEvent | ErrorEvent

"""Session Dispatcher — WeChat message scheduling and coordination.

This package provides the Gateway-layer dispatcher that sits between
the WeChat webhook and AgentCore Runtime. It handles:

- Concurrent message merging (rapid user messages)
- Stale request cancellation (version-check pattern)
- Consultant takeover (AI goes silent)
- Side-effect tracking for cancelled requests

Architecture::

    WeChat Webhook
        |
        v
    Dispatcher.on_message()
        |
        +---> Redis (session state, version counter)
        +---> AgentCore Runtime (streaming invoke)
        +---> WeChat API (send response)

Quick start::

    from agent.dispatcher import (
        Dispatcher,
        RedisClient,
        SessionState,
        SideEffectTracker,
        AgentCoreClient,
        IncomingMessage,
    )

    # Initialize components
    redis = RedisClient(os.getenv("REDIS_URL"))
    await redis.connect()

    session = SessionState(redis)
    side_effects = SideEffectTracker(redis)
    agentcore = AgentCoreClient(runtime_arn, region)

    dispatcher = Dispatcher(session, side_effects, agentcore, send_to_wechat)

    # Handle an incoming message
    await dispatcher.on_message(user_id, IncomingMessage(
        text="帮我约课",
        source="parent",
        conversation_history="...",
    ))
"""

from .agentcore_client import AgentCoreClient
from .config import (
    AGENTCORE_INVOKE_TIMEOUT,
    AGENTCORE_REGION,
    AGENTCORE_RUNTIME_ARN,
    DEDUP_TTL,
    REDIS_SESSION_TTL,
    REDIS_SIDE_EFFECT_TTL,
    REDIS_URL,
    REDIS_VERSION_CHECK_INTERVAL,
    SIDE_EFFECT_TOOLS,
)
from .handler import Dispatcher
from .models import (
    AgentCoreEvent,
    ChunkEvent,
    CompleteEvent,
    ErrorEvent,
    IncomingMessage,
    ToolUseEvent,
)
from .redis_client import RedisClient
from .session import SessionState
from .side_effects import SideEffectTracker

__all__ = [
    # Core classes
    "Dispatcher",
    "RedisClient",
    "SessionState",
    "SideEffectTracker",
    "AgentCoreClient",
    # Data models
    "IncomingMessage",
    "AgentCoreEvent",
    "ChunkEvent",
    "ToolUseEvent",
    "CompleteEvent",
    "ErrorEvent",
    # Configuration
    "REDIS_URL",
    "REDIS_SESSION_TTL",
    "REDIS_SIDE_EFFECT_TTL",
    "REDIS_VERSION_CHECK_INTERVAL",
    "AGENTCORE_RUNTIME_ARN",
    "AGENTCORE_REGION",
    "AGENTCORE_INVOKE_TIMEOUT",
    "DEDUP_TTL",
    "SIDE_EFFECT_TOOLS",
]

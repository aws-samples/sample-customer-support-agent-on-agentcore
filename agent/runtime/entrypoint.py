"""
AgentCore Runtime Entry Point for XXXX Agent

This module provides the entry point for deploying XXXX Agent to
AWS Bedrock AgentCore Runtime using BedrockAgentCoreApp.

Endpoints:
    - /invocations (POST): Main agent invocation endpoint
    - /ping (GET): Health check endpoint (automatic, built-in)

Usage:
    # Local development (runs on port 8080)
    python -m agent.runtime

    # Import for deployment
    from agent.runtime import app
"""

import os
import logging
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Suppress verbose logs from dependencies
logging.getLogger("bedrock_agentcore.memory").setLevel(logging.WARNING)
logging.getLogger("claude_agent_sdk._internal").setLevel(logging.WARNING)

# Initialize manual OTEL SDK for AgentCore Evaluations compatibility.
# Must be called BEFORE creating any ClaudeSDKClient.
try:
    from ..observability import init_otel
    runtime_id = os.getenv("AGENTCORE_RUNTIME_ID", "")
    if init_otel(service_name="xxxx-agent", runtime_id=runtime_id):
        logger.info("Manual OTEL SDK initialized for AgentCore Evaluations")
    else:
        logger.info("OTEL SDK not initialized (dependencies missing)")
except Exception as e:
    logger.warning(f"Failed to initialize OTEL SDK: {e}")

# Bedrock prompt caching is handled by the proxy sidecar (bedrock-effort-proxy)
# running on 127.0.0.1:8888. All Claude Code CLI calls are routed through it
# via ANTHROPIC_BEDROCK_BASE_URL env var. See Dockerfile and scripts/start.sh.

# Try to import BedrockAgentCoreApp
try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    AGENTCORE_AVAILABLE = True
except ImportError:
    AGENTCORE_AVAILABLE = False
    logger.warning("bedrock_agentcore.runtime not available - using fallback")

# Import agent and observability
from ..agent import CustomerServiceAgent
from ..observability import get_tracer, trace_agent_invocation

# Global agent cache (keyed by parent_id)
_agent_cache: dict[str, CustomerServiceAgent] = {}

# Pre-initialized agent (created at startup)
_default_agent: CustomerServiceAgent | None = None
_agent_init_lock = None  # Will be initialized lazily


def _get_agent_config() -> dict:
    """Get agent configuration from environment variables."""
    return {
        "memory_id": os.getenv("MEMORY_ID"),
        "model": os.getenv("BEDROCK_MODEL_ID", CustomerServiceAgent.DEFAULT_MODEL),
        "use_skills": os.getenv("USE_SKILLS", "true").lower() == "true",
        "minimal_mode": os.getenv("MINIMAL_MODE", "false").lower() == "true",
    }


async def _create_minimal_agent(parent_id: str) -> "MinimalAgent":
    """Create a minimal agent for testing."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    import time

    logger.info(f"Creating MINIMAL agent for parent_id={parent_id}")
    start = time.time()

    options = ClaudeAgentOptions(
        system_prompt="You are a helpful assistant. Respond briefly.",
        permission_mode="bypassPermissions",
        model=os.getenv("BEDROCK_MODEL_ID", CustomerServiceAgent.DEFAULT_MODEL),
    )

    client = ClaudeSDKClient(options=options)
    logger.info(f"[{time.time()-start:.2f}s] ClaudeSDKClient created")

    await client.connect()
    logger.info(f"[{time.time()-start:.2f}s] Client connected")

    class MinimalAgent:
        def __init__(self, client):
            self._client = client

        async def chat_stream(self, message: str):
            from claude_agent_sdk import AssistantMessage, TextBlock
            await self._client.query(message)
            async for msg in self._client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            yield block.text

        async def disconnect(self):
            await self._client.disconnect()

    return MinimalAgent(client)


async def _create_agent(parent_id: str) -> CustomerServiceAgent:
    """Create and connect an agent instance."""
    config = _get_agent_config()
    logger.info(f"Creating agent for parent_id={parent_id}, config={config}")

    # Use minimal mode for testing
    if config["minimal_mode"]:
        return await _create_minimal_agent(parent_id)

    agent = CustomerServiceAgent(
        parent_id=parent_id,
        model=config["model"],
        memory_id=config["memory_id"],
        memory_mode="tool",
        use_skills=config["use_skills"],
    )
    await agent.connect()
    return agent


async def get_or_create_agent(parent_id: str) -> CustomerServiceAgent:
    """Get or create an agent instance for the given parent_id.

    Args:
        parent_id: The parent/user ID

    Returns:
        CustomerServiceAgent instance
    """
    # Check cache first
    if parent_id in _agent_cache:
        return _agent_cache[parent_id]

    # Create new agent
    agent = await _create_agent(parent_id)
    _agent_cache[parent_id] = agent

    return agent


async def cleanup_agent(parent_id: str):
    """Clean up an agent instance.

    Args:
        parent_id: The parent/user ID
    """
    if parent_id in _agent_cache:
        agent = _agent_cache.pop(parent_id)
        await agent.disconnect()
        logger.info(f"Cleaned up agent for parent_id={parent_id}")


# Initialize BedrockAgentCoreApp if available
if AGENTCORE_AVAILABLE:
    app = BedrockAgentCoreApp()

    @app.entrypoint
    async def process_request(payload: dict) -> AsyncIterator[dict]:
        """
        AgentCore Runtime request handler.

        Payload format:
        {
            "prompt": "User message (required)",
            "parent_id": "Parent/user ID (optional, default: default_user)",
            "session_id": "Session ID for memory (optional)",
            "conversation_history": "Historical conversation context (optional)",
            "images": ["list of image URLs (optional)"]
        }

        Yields:
            Streaming response chunks:
            - {"type": "chunk", "data": "text chunk"}
            - {"type": "tool_use", "tool_name": "...", "tool_input": {...}}
            - {"type": "complete", "session_id": "..."}
            - {"type": "error", "message": "..."}
        """
        import time as _time

        prompt = payload.get("prompt", "")
        parent_id = payload.get("parent_id", "default_user")
        session_id = payload.get("session_id", "")
        conversation_history = payload.get("conversation_history", "")
        images = payload.get("images", [])

        if not prompt:
            yield {"type": "error", "message": "Missing 'prompt' in payload"}
            return

        logger.info(f"Processing request: parent_id={parent_id}, prompt={prompt[:50]}...")
        if conversation_history:
            logger.info(f"  with conversation_history: {len(conversation_history)} chars")
        if images:
            logger.info(f"  with {len(images)} images")

        tracer = get_tracer()
        config = _get_agent_config()
        model = config.get("model", CustomerServiceAgent.DEFAULT_MODEL)

        with trace_agent_invocation(
            tracer,
            parent_id=parent_id,
            session_id=session_id or f"runtime-{int(_time.time())}",
            model=model,
            prompt_preview=prompt[:100],
        ) as invocation_span:
            try:
                # Track conversation context size
                invocation_span.set_attribute("xxxx.history_length", len(conversation_history))
                invocation_span.set_attribute("xxxx.image_count", len(images))

                agent = await get_or_create_agent(parent_id)

                chunk_count = 0
                tool_count = 0
                response_length = 0

                async for chunk in agent.chat_stream(
                    prompt,
                    conversation_history=conversation_history if conversation_history else None,
                    images=images if images else None,
                ):
                    # Check if it's a tool use marker
                    if chunk.startswith("\n[调用工具:"):
                        tool_name = chunk.replace("\n[调用工具: ", "").replace("]\n", "")
                        tool_count += 1
                        yield {
                            "type": "tool_use",
                            "tool_name": tool_name,
                        }
                    else:
                        chunk_count += 1
                        response_length += len(chunk)
                        yield {
                            "type": "chunk",
                            "data": chunk,
                        }

                # Record final metrics on the invocation span
                invocation_span.set_attribute("xxxx.tool_count", tool_count)
                invocation_span.set_attribute("xxxx.chunk_count", chunk_count)
                invocation_span.set_attribute("xxxx.response_length", response_length)

                yield {
                    "type": "complete",
                    "session_id": session_id,
                }

            except Exception as e:
                logger.error(f"Error processing request: {e}")
                invocation_span.set_attribute("xxxx.error", str(e))
                yield {
                    "type": "error",
                    "message": str(e),
                }
            finally:
                # Clean up agent after each request to avoid stale SDK state
                # (Skill tool background ops, hook failures can block subsequent requests)
                await cleanup_agent(parent_id)

else:
    # Fallback: Create a simple FastAPI app for local development
    try:
        from fastapi import FastAPI, HTTPException
        from pydantic import BaseModel

        app = FastAPI(title="XXXX Agent Runtime", version="1.0.0")

        class InvocationRequest(BaseModel):
            prompt: str
            parent_id: str = "default_user"
            session_id: str = ""
            conversation_history: str = ""
            images: list[str] = []

        class InvocationResponse(BaseModel):
            response: str
            session_id: str

        @app.post("/invocations", response_model=InvocationResponse)
        async def invoke_agent(request: InvocationRequest):
            """Main agent invocation endpoint."""
            if not request.prompt:
                raise HTTPException(status_code=400, detail="Missing 'prompt'")

            logger.info(f"Processing request: parent_id={request.parent_id}")

            try:
                agent = await get_or_create_agent(request.parent_id)

                # Collect full response (non-streaming)
                response_text = ""
                async for chunk in agent.chat_stream(
                    request.prompt,
                    conversation_history=request.conversation_history if request.conversation_history else None,
                    images=request.images if request.images else None,
                ):
                    if not chunk.startswith("\n[调用工具:"):
                        response_text += chunk

                return InvocationResponse(
                    response=response_text,
                    session_id=request.session_id,
                )

            except Exception as e:
                logger.error(f"Error: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/ping")
        async def ping():
            """Health check endpoint."""
            return {
                "status": "healthy",
                "service": "xxxx-agent",
                "memory_enabled": bool(os.getenv("MEMORY_ID")),
            }

        # For compatibility with BedrockAgentCoreApp pattern
        async def process_request(payload: dict) -> AsyncIterator[dict]:
            """Fallback process_request for compatibility."""
            prompt = payload.get("prompt", "")
            parent_id = payload.get("parent_id", "default_user")
            session_id = payload.get("session_id", "")
            conversation_history = payload.get("conversation_history", "")
            images = payload.get("images", [])

            if not prompt:
                yield {"type": "error", "message": "Missing 'prompt' in payload"}
                return

            try:
                agent = await get_or_create_agent(parent_id)
                async for chunk in agent.chat_stream(
                    prompt,
                    conversation_history=conversation_history if conversation_history else None,
                    images=images if images else None,
                ):
                    if chunk.startswith("\n[调用工具:"):
                        tool_name = chunk.replace("\n[调用工具: ", "").replace("]\n", "")
                        yield {"type": "tool_use", "tool_name": tool_name}
                    else:
                        yield {"type": "chunk", "data": chunk}
                yield {"type": "complete", "session_id": session_id}
            except Exception as e:
                yield {"type": "error", "message": str(e)}

    except ImportError:
        logger.error("Neither bedrock_agentcore.runtime nor fastapi available")
        app = None

        async def process_request(payload: dict) -> AsyncIterator[dict]:
            yield {"type": "error", "message": "Runtime not properly configured"}


def run_local(host: str = "0.0.0.0", port: int = 8080):
    """Run the agent locally using uvicorn.

    Args:
        host: Host to bind to (default: 0.0.0.0)
        port: Port to listen on (default: 8080)
    """
    if AGENTCORE_AVAILABLE:
        # Use BedrockAgentCoreApp's built-in server
        app.run()
    else:
        # Use uvicorn with FastAPI fallback
        import uvicorn
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_local()

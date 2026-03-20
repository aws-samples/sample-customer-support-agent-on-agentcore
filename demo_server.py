"""XXXX Agent Demo Server

A FastAPI server that provides a web chat UI to demonstrate the Agent
and the Session Dispatcher.  Three roles are supported:

- **User (parent)** — sends messages that trigger AgentCore invocation.
- **Consultant** — sends messages that silence the AI (顾问介入).
- **AI** — the Agent's response, delivered back via WebSocket.

Usage::

    # Requires: Redis (ElastiCache or local) + AWS credentials for AgentCore
    # Environment variables: REDIS_URL (required)

    # Start the server
    python demo_server.py

    # With SSM tunnel to ElastiCache (skip hostname verification)
    REDIS_URL=rediss://localhost:16379 python demo_server.py --ssl-no-verify

    # Open in browser
    http://localhost:8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

load_dotenv()

from agent.dispatcher import (
    AgentCoreClient,
    Dispatcher,
    IncomingMessage,
    RedisClient,
    SessionState,
    SideEffectTracker,
)

logger = logging.getLogger("demo_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

# Active WebSocket connections: user_id → set of WebSocket
_connections: dict[str, set[WebSocket]] = {}

# Conversation history per user (kept in memory for this demo)
_history: dict[str, list[dict]] = {}

# Dispatcher instance (initialized at startup)
_dispatcher: Dispatcher | None = None
_redis: RedisClient | None = None

# CLI arguments (set in main)
_ssl_no_verify: bool = False


# ---------------------------------------------------------------------------
# WeChat sender replacement — push AI response to all WebSockets for this user
# ---------------------------------------------------------------------------

async def _send_to_websockets(user_id: str, text: str) -> None:
    """Callback used by Dispatcher in place of WeChat API send."""
    msg = {"role": "ai", "text": text, "timestamp": time.time()}

    # Store in history
    _history.setdefault(user_id, []).append(msg)

    # Push to all connected WebSocket clients for this user
    ws_set = _connections.get(user_id, set())
    closed = set()
    for ws in ws_set:
        try:
            await ws.send_json(msg)
        except Exception:
            closed.add(ws)
    # Clean up closed connections
    for ws in closed:
        ws_set.discard(ws)


# ---------------------------------------------------------------------------
# Lifespan — init / cleanup Redis
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _dispatcher, _redis

    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        logger.error("REDIS_URL is required. Set it as an environment variable.")
        raise RuntimeError("REDIS_URL not set")

    _redis = RedisClient(
        url=redis_url,
        ssl_cert_reqs="none" if _ssl_no_verify else None,
    )
    await _redis.connect()
    logger.info("Redis connected")

    session = SessionState(_redis)
    side_effects = SideEffectTracker(_redis)
    agentcore = AgentCoreClient(
        runtime_arn=os.getenv(
            "AGENTCORE_RUNTIME_ARN",
            "arn:aws:bedrock-agentcore:us-west-2:<ACCOUNT_ID>:runtime/<RUNTIME_ID>",
        ),
        region=os.getenv("AGENTCORE_REGION", "us-west-2"),
    )

    _dispatcher = Dispatcher(
        session=session,
        side_effects=side_effects,
        agentcore=agentcore,
        wechat_sender=_send_to_websockets,
    )
    logger.info("Dispatcher initialized")

    yield

    # Cleanup
    if _redis:
        await _redis.close()
        logger.info("Redis closed")


app = FastAPI(title="XXXX Agent Demo", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Serve the single-page HTML
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = _STATIC_DIR / "demo.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# WebSocket — real-time bidirectional communication
# ---------------------------------------------------------------------------

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()

    # Register connection
    _connections.setdefault(user_id, set()).add(websocket)
    logger.info("WebSocket connected: user_id=%s (total=%d)", user_id, len(_connections[user_id]))

    # Send existing history on connect
    for msg in _history.get(user_id, []):
        await websocket.send_json(msg)

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            role = data.get("role", "parent")  # "parent" or "consultant"
            text = data.get("text", "").strip()

            if not text:
                continue

            # Store in history
            msg_record = {"role": role, "text": text, "timestamp": time.time()}
            _history.setdefault(user_id, []).append(msg_record)

            # Broadcast to all WebSocket clients for this user (so other tabs see it)
            ws_set = _connections.get(user_id, set())
            for ws in ws_set:
                if ws != websocket:
                    try:
                        await ws.send_json(msg_record)
                    except Exception:
                        pass

            # conversation_history is provided by the frontend user,
            # simulating what WeChat system would pass in production.
            conversation_history = data.get("conversation_history", "")

            # Dispatch to agent
            incoming = IncomingMessage(
                text=text,
                source=role,
                parent_id=user_id,
                session_id=f"demo-{user_id}",
                conversation_history=conversation_history,
            )

            if _dispatcher:
                await _dispatcher.on_message(user_id, incoming)
                logger.info("Message dispatched: user=%s role=%s text=%s", user_id, role, text[:50])

    except WebSocketDisconnect:
        _connections.get(user_id, set()).discard(websocket)
        logger.info("WebSocket disconnected: user_id=%s", user_id)
    except Exception as e:
        _connections.get(user_id, set()).discard(websocket)
        logger.error("WebSocket error: user_id=%s error=%s", user_id, e)


# ---------------------------------------------------------------------------
# REST API — for non-WebSocket testing
# ---------------------------------------------------------------------------

@app.get("/api/history/{user_id}")
async def get_history(user_id: str):
    """Get conversation history for a user."""
    return {"user_id": user_id, "messages": _history.get(user_id, [])}


@app.delete("/api/history/{user_id}")
async def clear_history(user_id: str):
    """Clear conversation history for a user."""
    _history.pop(user_id, None)
    return {"status": "cleared"}


@app.get("/api/redis/{user_id}")
async def get_redis_state(user_id: str):
    """Get current Redis session state for a user (for the debug panel)."""
    if not _redis or not _redis.client:
        return {"error": "Redis not connected"}

    try:
        session_key = f"session:{user_id}"
        data = await _redis.client.hgetall(session_key)

        side_key = f"side_effect:{user_id}"
        side_data = await _redis.client.get(side_key)

        result = {
            "version": data.get("version", "-"),
            "state": data.get("state", "-"),
            "request_id": data.get("request_id", "-"),
            "messages": data.get("messages", "[]"),
            "images": data.get("images", "[]"),
            "last_updated": data.get("last_updated", "-"),
            "side_effect": side_data or None,
        }
        return result
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _ssl_no_verify

    parser = argparse.ArgumentParser(description="XXXX Agent Demo Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument(
        "--ssl-no-verify",
        action="store_true",
        help="Skip Redis SSL hostname verification (for SSM tunnel)",
    )
    args = parser.parse_args()

    _ssl_no_verify = args.ssl_no_verify

    # Pass ``app`` directly (not as string "demo_server:app") so that
    # the global ``_ssl_no_verify`` set above is visible in the lifespan
    # handler.  String-based import would re-import the module and reset
    # the global to False.
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()

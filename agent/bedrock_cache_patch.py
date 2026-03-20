"""Bedrock Prompt Caching - Inject cache_control markers for 1-hour TTL.

Two approaches:
1. Proxy sidecar (recommended): Set ANTHROPIC_BEDROCK_BASE_URL to the proxy
   that auto-injects cache_control. Works for ALL Bedrock calls from Claude Code CLI.
2. botocore monkey-patch (this module): Patches boto3 Bedrock calls to inject
   cache_control. Only covers direct boto3 calls (not Claude Code CLI subprocess).

Usage:
    # In entrypoint.py (before creating any clients):
    from agent.bedrock_cache_patch import patch_bedrock_client
    patch_bedrock_client()

Cache injection rules (matching claudecode-bedrock-proxy):
- Max 4 cache breakpoints per request
- Priority: last tool → system prompt → last assistant message
- Skips thinking/redacted_thinking blocks
- Upgrades existing 5m TTL to 1h
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

MAX_CACHE_BREAKPOINTS = 4
CACHE_TTL = os.environ.get("CACHE_TTL", "1h")


def _new_marker() -> dict:
    """Create a cache_control marker."""
    marker = {"type": "ephemeral"}
    if CACHE_TTL != "5m":
        marker["ttl"] = CACHE_TTL
    return marker


def _collect_cache_blocks(data: dict) -> list[dict]:
    """Find all blocks with existing cache_control."""
    blocks = []
    for tool in data.get("tools") or []:
        if isinstance(tool, dict) and "cache_control" in tool:
            blocks.append(tool)
    for item in data.get("system") or []:
        if isinstance(item, dict) and "cache_control" in item:
            blocks.append(item)
    for msg in data.get("messages") or []:
        if isinstance(msg, dict):
            for block in msg.get("content") or []:
                if isinstance(block, dict) and "cache_control" in block:
                    blocks.append(block)
    return blocks


def inject_cache_control(data: dict) -> tuple[int, str]:
    """Inject cache_control markers into a Bedrock API request body.

    Args:
        data: The Bedrock InvokeModel/Converse request body (mutable dict).

    Returns:
        (markers_added, action_description) tuple.
    """
    existing_blocks = _collect_cache_blocks(data)
    existing = len(existing_blocks)

    # Upgrade existing TTLs
    upgraded = 0
    if CACHE_TTL != "5m":
        for block in existing_blocks:
            cc = block.get("cache_control")
            if isinstance(cc, dict):
                cc["ttl"] = CACHE_TTL
                upgraded += 1

    budget = MAX_CACHE_BREAKPOINTS - existing
    added = 0
    parts = []

    if budget <= 0:
        if upgraded > 0:
            return 0, f"ttl-upgrade({upgraded}->{CACHE_TTL},existing={existing})"
        return 0, f"no-op(existing={existing})"

    # 1. Last tool
    tools = data.get("tools") or []
    if tools and added < budget:
        last_tool = tools[-1]
        if isinstance(last_tool, dict) and "cache_control" not in last_tool:
            last_tool["cache_control"] = _new_marker()
            added += 1
            parts.append("tools")

    # 2. System prompt
    if added < budget:
        system = data.get("system")
        if isinstance(system, str) and system:
            data["system"] = [{"type": "text", "text": system, "cache_control": _new_marker()}]
            added += 1
            parts.append("system")
        elif isinstance(system, list) and system:
            last_sys = system[-1]
            if isinstance(last_sys, dict) and "cache_control" not in last_sys:
                last_sys["cache_control"] = _new_marker()
                added += 1
                parts.append("system")

    # 3. Last assistant message (skip thinking blocks)
    if added < budget:
        for msg in reversed(data.get("messages") or []):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = [{"type": "text", "text": content, "cache_control": _new_marker()}]
                added += 1
                parts.append("msgs")
            elif isinstance(content, list):
                for block in reversed(content):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") in ("thinking", "redacted_thinking"):
                        continue
                    if "cache_control" not in block:
                        block["cache_control"] = _new_marker()
                        added += 1
                        parts.append("msgs")
                    break
            break

    upg = f",upg={upgraded}" if upgraded else ""
    if added > 0:
        return added, f"{added}bp({'+'.join(parts)},{CACHE_TTL},pre={existing}{upg})"
    return 0, f"no-op(existing={existing}{upg})"


# ---------------------------------------------------------------------------
# botocore monkey-patch
# ---------------------------------------------------------------------------

_original_make_api_call = None
_patched = False
_stats = {"calls": 0, "injected": 0, "markers_added": 0}


def patch_bedrock_client():
    """Monkey-patch botocore to inject cache_control into Bedrock API calls.

    Note: This only covers direct boto3 calls. Claude Agent SDK's main LLM
    calls go through Claude Code CLI subprocess and are NOT intercepted here.
    For those, use the proxy sidecar approach (ANTHROPIC_BEDROCK_BASE_URL).
    """
    global _original_make_api_call, _patched

    if _patched:
        return

    try:
        import botocore.client
    except ImportError:
        logger.info("botocore not available, skipping cache patch")
        return

    _original_make_api_call = botocore.client.BaseClient._make_api_call

    CACHED_OPS = {
        "Converse", "ConverseStream",
        "InvokeModel", "InvokeModelWithResponseStream",
    }

    def _patched_api_call(self, operation_name, api_params):
        if operation_name in CACHED_OPS:
            _stats["calls"] += 1
            try:
                # InvokeModel: body is JSON string/bytes
                if "body" in api_params and isinstance(api_params["body"], (str, bytes)):
                    body = json.loads(api_params["body"])
                    added, action = inject_cache_control(body)
                    api_params["body"] = json.dumps(body)
                    if added > 0:
                        _stats["injected"] += 1
                        _stats["markers_added"] += added
                        logger.debug(f"Cache inject ({operation_name}): {action}")
                # Converse API: direct dict
                else:
                    added, action = inject_cache_control(api_params)
                    if added > 0:
                        _stats["injected"] += 1
                        _stats["markers_added"] += added
                        logger.debug(f"Cache inject ({operation_name}): {action}")
            except Exception as e:
                logger.debug(f"Cache inject skipped: {e}")

        return _original_make_api_call(self, operation_name, api_params)

    botocore.client.BaseClient._make_api_call = _patched_api_call
    _patched = True
    logger.info(f"Bedrock cache patch applied (TTL={CACHE_TTL}, max={MAX_CACHE_BREAKPOINTS} breakpoints)")


def get_cache_stats() -> dict:
    """Get cache injection statistics."""
    return dict(_stats)

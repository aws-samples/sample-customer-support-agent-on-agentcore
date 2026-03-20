"""Session Dispatcher Configuration

All tunable parameters in one place.
"""

import os

# ============================================================
# Redis
# ============================================================

REDIS_URL = os.getenv("REDIS_URL", "")

# Session key TTL (seconds) — refreshed on every new message
REDIS_SESSION_TTL = 300

# Side effect key TTL (seconds) — consumed once then deleted
REDIS_SIDE_EFFECT_TTL = 600

# Check Redis version every N chunks during streaming
REDIS_VERSION_CHECK_INTERVAL = 5

# ============================================================
# AgentCore
# ============================================================

AGENTCORE_RUNTIME_ARN = os.getenv(
    "AGENTCORE_RUNTIME_ARN",
    "arn:aws:bedrock-agentcore:us-west-2:<ACCOUNT_ID>:runtime/<RUNTIME_ID>",
)
AGENTCORE_REGION = os.getenv("AGENTCORE_REGION", "us-west-2")

# Max seconds to wait for AgentCore response before treating as timeout
AGENTCORE_INVOKE_TIMEOUT = 120

# ============================================================
# Dispatcher
# ============================================================

# Dedup key TTL for duplicate WeChat webhook messages
DEDUP_TTL = 60

# ============================================================
# Tool Safety Classification
# ============================================================

# Tools that modify state — need side-effect tracking when cancelled
SIDE_EFFECT_TOOLS = frozenset({
    "book_class",
    "cancel_class",
    "reschedule_class",
})

# Tools that only read — safe to cancel at any time
SAFE_TOOLS = frozenset({
    "search_knowledge_base",
    "get_booking_records",
    "get_available_slots",
    "query_account_info",
    "get_course_schedule",
    "get_parent_timezone",
    "search_user_preferences",
    "search_episodic_memories",
    "fetch_image",
})

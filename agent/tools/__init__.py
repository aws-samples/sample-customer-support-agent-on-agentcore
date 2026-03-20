# XXXX Customer Service Agent - Tools
from .knowledge_search import search_knowledge_base
from .booking_operations import (
    get_booking_records,
    book_class,
    cancel_class,
    reschedule_class,
    get_available_slots,
)
from .account_query import query_account_info, get_course_schedule
from .timezone_utils import get_parent_timezone

# MCP Tools (Claude Agent SDK)
from .mcp_tools import (
    create_mcp_server,
    TOOLS,
    TOOLS_WITH_MEMORY,
)

__all__ = [
    # 原始 Mock 函数
    "search_knowledge_base",
    "get_booking_records",
    "book_class",
    "cancel_class",
    "reschedule_class",
    "get_available_slots",
    "query_account_info",
    "get_course_schedule",
    "get_parent_timezone",
    # MCP Server
    "create_mcp_server",
    "TOOLS",
    "TOOLS_WITH_MEMORY",
]

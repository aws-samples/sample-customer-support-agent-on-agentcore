"""XXXX MCP Tools - 基于 Claude Agent SDK 的 MCP 工具定义"""

import base64
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx
from claude_agent_sdk import tool, create_sdk_mcp_server

logger = logging.getLogger(__name__)

# 导入实现
from .knowledge_search import search_knowledge_base as _search_kb
from .booking_operations import (
    get_booking_records as _get_booking_records,
    get_available_slots as _get_available_slots,
    book_class as _book_class,
    cancel_class as _cancel_class,
    reschedule_class as _reschedule_class,
)
from .account_query import (
    query_account_info as _query_account_info,
    get_course_schedule as _get_course_schedule,
)
from .timezone_utils import get_parent_timezone as _get_parent_timezone


# ============================================================
# 辅助函数
# ============================================================

def _format_result(result: dict) -> str:
    """格式化结果为 JSON 字符串"""
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


def _error_response(error: Exception) -> dict[str, Any]:
    """生成错误响应"""
    return {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "success": False,
                "error": f"{type(error).__name__}: {str(error)}"
            }, ensure_ascii=False)
        }]
    }


# ============================================================
# MCP Tool 定义
# ============================================================

@tool(
    "search_knowledge_base",
    "搜索知识库获取FAQ信息。用于回答用户关于平台操作、规则、课程体系等问题。",
    {
        "query": str,  # 必填: 搜索关键词
    }
)
async def search_knowledge_base(args: dict[str, Any]) -> dict[str, Any]:
    """知识库检索（Bedrock Knowledge Base）"""
    try:
        result = _search_kb(
            query=args["query"],
            kb_type=args.get("kb_type", "all"),
        )
        return {"content": [{"type": "text", "text": _format_result(result)}]}
    except Exception as e:
        return _error_response(e)


@tool(
    "get_booking_records",
    "查询用户已预约的课程记录。用于取消课或调课前确认课程信息。",
    {
        "parent_id": str,  # 必填: 家长用户ID
    }
)
async def get_booking_records(args: dict[str, Any]) -> dict[str, Any]:
    """查询已约课程"""
    try:
        result = _get_booking_records(
            parent_id=args["parent_id"],
            start_date=args.get("start_date"),
            end_date=args.get("end_date"),
        )
        return {"content": [{"type": "text", "text": _format_result(result)}]}
    except Exception as e:
        return _error_response(e)


@tool(
    "get_available_slots",
    "查询指定日期的可约时段和老师。用于约课或调课时查看可选时间。",
    {
        "date": str,  # 必填: 查询日期，格式 YYYY-MM-DD
    }
)
async def get_available_slots(args: dict[str, Any]) -> dict[str, Any]:
    """查询可约时段"""
    try:
        result = _get_available_slots(
            date=args["date"],
            tutor_id=args.get("tutor_id"),
        )
        return {"content": [{"type": "text", "text": _format_result(result)}]}
    except Exception as e:
        return _error_response(e)


@tool(
    "book_class",
    "预约新课程。需要在用户确认后调用。",
    {
        "parent_id": str,   # 必填: 家长用户ID
        "student_id": str,  # 必填: 学生ID
        "tutor_id": str,    # 必填: 老师ID
        "time_slot": str,   # 必填: 时间段，格式 YYYY-MM-DD HH:MM
    }
)
async def book_class(args: dict[str, Any]) -> dict[str, Any]:
    """预约课程"""
    try:
        result = _book_class(
            parent_id=args["parent_id"],
            student_id=args["student_id"],
            tutor_id=args["tutor_id"],
            time_slot=args["time_slot"],
            course_type=args.get("course_type", "中文标准版"),
        )
        return {"content": [{"type": "text", "text": _format_result(result)}]}
    except Exception as e:
        return _error_response(e)


@tool(
    "cancel_class",
    "取消已预约的课程。需要在用户确认后调用。",
    {
        "parent_id": str,   # 必填: 家长用户ID
        "booking_id": str,  # 必填: 预约记录ID
    }
)
async def cancel_class(args: dict[str, Any]) -> dict[str, Any]:
    """取消课程"""
    try:
        result = _cancel_class(
            parent_id=args["parent_id"],
            booking_id=args["booking_id"],
        )
        return {"content": [{"type": "text", "text": _format_result(result)}]}
    except Exception as e:
        return _error_response(e)


@tool(
    "reschedule_class",
    "调整已预约课程的时间。需要在用户确认后调用。",
    {
        "parent_id": str,   # 必填: 家长用户ID
        "booking_id": str,  # 必填: 预约记录ID
        "new_time": str,    # 必填: 新时间，格式 YYYY-MM-DD HH:MM
    }
)
async def reschedule_class(args: dict[str, Any]) -> dict[str, Any]:
    """调课"""
    try:
        result = _reschedule_class(
            parent_id=args["parent_id"],
            booking_id=args["booking_id"],
            new_time=args["new_time"],
            new_tutor_id=args.get("new_tutor_id"),
        )
        return {"content": [{"type": "text", "text": _format_result(result)}]}
    except Exception as e:
        return _error_response(e)


@tool(
    "query_account_info",
    """查询账户信息，包括课时余额、打卡次数、积分余额。

参数:
- parent_id: 家长用户ID
- info_type: 查询类型，必须是以下值之一:
  - "all" — 查询全部信息（推荐）
  - "lesson_balance" — 仅查询课时余额
  - "checkin_count" — 仅查询打卡次数
  - "points_balance" — 仅查询积分余额""",
    {
        "parent_id": str,
        "info_type": str,
    }
)
async def query_account_info(args: dict[str, Any]) -> dict[str, Any]:
    """查询账户信息"""
    try:
        result = _query_account_info(
            parent_id=args["parent_id"],
            info_type=args["info_type"],
        )
        return {"content": [{"type": "text", "text": _format_result(result)}]}
    except Exception as e:
        return _error_response(e)


@tool(
    "get_course_schedule",
    "查询未来的课程安排。",
    {
        "parent_id": str,  # 必填: 家长用户ID
    }
)
async def get_course_schedule(args: dict[str, Any]) -> dict[str, Any]:
    """查询课程安排"""
    try:
        result = _get_course_schedule(
            parent_id=args["parent_id"],
            days_ahead=args.get("days_ahead", 7),
        )
        return {"content": [{"type": "text", "text": _format_result(result)}]}
    except Exception as e:
        return _error_response(e)


@tool(
    "get_parent_timezone",
    "获取家长所在时区，用于时间计算和展示。",
    {
        "parent_id": str,  # 必填: 家长用户ID
    }
)
async def get_parent_timezone(args: dict[str, Any]) -> dict[str, Any]:
    """获取时区"""
    try:
        result = _get_parent_timezone(parent_id=args["parent_id"])
        return {"content": [{"type": "text", "text": _format_result(result)}]}
    except Exception as e:
        return _error_response(e)


# ============================================================
# 图片获取工具
# ============================================================

def _get_image_media_type(url: str, content_type: str | None = None) -> str:
    """根据 URL 或 Content-Type 推断图片 MIME 类型"""
    if content_type and content_type.startswith("image/"):
        return content_type
    path = urlparse(url).path.lower()
    if ".jpg" in path or ".jpeg" in path:
        return "image/jpeg"
    elif ".png" in path:
        return "image/png"
    elif ".gif" in path:
        return "image/gif"
    elif ".webp" in path:
        return "image/webp"
    return "image/jpeg"


@tool(
    "fetch_image",
    """获取并查看图片内容。当用户发送图片URL需要你查看分析时，使用此工具获取图片。

使用场景:
- 用户发送了图片链接需要你查看
- 用户问"这张图片是什么"
- 需要分析截图、照片等图片内容

返回: 图片内容（你可以直接看到并分析）""",
    {
        "url": str,  # 必填: 图片URL
    }
)
async def fetch_image(args: dict[str, Any]) -> dict[str, Any]:
    """获取图片内容"""
    url = args.get("url", "")
    if not url:
        return {"content": [{"type": "text", "text": "错误: 未提供图片URL"}]}

    try:
        logger.info(f"Fetching image: {url[:80]}...")
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            media_type = _get_image_media_type(url, content_type)
            image_data = base64.b64encode(response.content).decode("utf-8")
            logger.info(f"Image fetched: {len(response.content)} bytes, {media_type}")

            return {
                "content": [{
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    }
                }]
            }

    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP错误 {e.response.status_code}: 无法访问图片URL"
        return {"content": [{"type": "text", "text": error_msg}]}
    except Exception as e:
        error_msg = f"获取图片失败: {type(e).__name__}: {str(e)}"
        return {"content": [{"type": "text", "text": error_msg}]}


# ============================================================
# 长期记忆 Tool (可选，替代 Hook 自动搜索)
# ============================================================

@tool(
    "search_user_preferences",
    """搜索用户偏好记忆。查找用户的个人偏好设置。

包含内容:
- 喜欢的老师（如：用户偏好王老师）
- 常用上课时间（如：通常约周六上午10点）
- 课程偏好（如：偏好1对1课程）
- 沟通偏好（如：偏好中文沟通）

何时使用:
- 用户说"帮我约上次那个老师"
- 用户说"和之前一样的时间"
- 用户说"还是老规矩"
- 需要个性化推荐时""",
    {
        "query": str,  # 必填: 搜索关键词
    }
)
async def search_user_preferences(args: dict[str, Any]) -> dict[str, Any]:
    """搜索用户偏好"""
    try:
        from ..hooks import get_memory_manager

        manager = get_memory_manager()
        if not manager or not manager.is_enabled:
            return {"content": [{"type": "text", "text": _format_result({
                "success": True, "preferences": [], "message": "长期记忆功能未启用"
            })}]}

        preferences = manager.search_user_preferences(
            query=args["query"], session_id="default_session", top_k=args.get("top_k", 3),
        )

        if not preferences:
            return {"content": [{"type": "text", "text": _format_result({
                "success": True, "preferences": [], "message": "未找到相关用户偏好"
            })}]}

        return {"content": [{"type": "text", "text": _format_result({
            "success": True, "preferences": preferences, "count": len(preferences),
        })}]}
    except Exception as e:
        return _error_response(e)


@tool(
    "search_episodic_memories",
    """搜索情景记忆。查找用户的历史交互和操作记录。

包含内容:
- 历史预约记录（如：上周约了周三的课）
- 取消/调课记录（如：之前取消过周五的课）
- 问题解决历史（如：之前反馈过音频问题，已解决）
- 投诉和反馈记录

何时使用:
- 用户说"我之前取消的那节课"
- 用户说"上次预约的情况"
- 用户问"之前那个问题解决了吗"
- 需要了解用户历史操作时""",
    {
        "query": str,  # 必填: 搜索关键词
    }
)
async def search_episodic_memories(args: dict[str, Any]) -> dict[str, Any]:
    """搜索情景记忆"""
    try:
        from ..hooks import get_memory_manager

        manager = get_memory_manager()
        if not manager or not manager.is_enabled:
            return {"content": [{"type": "text", "text": _format_result({
                "success": True, "episodes": [], "message": "长期记忆功能未启用"
            })}]}

        episodes = manager.search_episodic_memories(
            query=args["query"], session_id="default_session", top_k=args.get("top_k", 3),
        )

        if not episodes:
            return {"content": [{"type": "text", "text": _format_result({
                "success": True, "episodes": [], "message": "未找到相关历史记录"
            })}]}

        return {"content": [{"type": "text", "text": _format_result({
            "success": True, "episodes": episodes, "count": len(episodes),
        })}]}
    except Exception as e:
        return _error_response(e)


# ============================================================
# 创建 MCP Server
# ============================================================

_BASE_TOOLS = [
    search_knowledge_base,
    get_booking_records,
    get_available_slots,
    book_class,
    cancel_class,
    reschedule_class,
    query_account_info,
    get_course_schedule,
    get_parent_timezone,
    fetch_image,
]

_MEMORY_TOOLS = [
    search_user_preferences,
    search_episodic_memories,
]


def create_mcp_server(include_memory_tools: bool = False) -> dict:
    """创建 MCP Server

    Args:
        include_memory_tools: 是否包含长期记忆搜索 tools

    Returns:
        MCP Server 配置字典
    """
    tools = _BASE_TOOLS.copy()
    if include_memory_tools:
        tools.extend(_MEMORY_TOOLS)

    return create_sdk_mcp_server(
        name="tools",
        version="1.0.0",
        tools=tools,
    )


# 预定义的 tool 名称列表（用于 allowed_tools）
TOOLS = [
    "mcp__search_knowledge_base",
    "mcp__get_booking_records",
    "mcp__get_available_slots",
    "mcp__book_class",
    "mcp__cancel_class",
    "mcp__reschedule_class",
    "mcp__query_account_info",
    "mcp__get_course_schedule",
    "mcp__get_parent_timezone",
    "mcp__fetch_image",
]

MEMORY_TOOLS = [
    "mcp__search_user_preferences",
    "mcp__search_episodic_memories",
]

TOOLS_WITH_MEMORY = TOOLS + MEMORY_TOOLS

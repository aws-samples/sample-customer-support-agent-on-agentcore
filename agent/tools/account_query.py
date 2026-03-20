"""账户信息查询工具 - DynamoDB 实现"""

import logging
from datetime import datetime, timedelta
from typing import Literal

from boto3.dynamodb.conditions import Key

from .db import get_table

logger = logging.getLogger(__name__)


def _convert_decimals(obj):
    """Convert DynamoDB Decimal types to int/float for JSON serialization."""
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    if isinstance(obj, dict):
        return {k: _convert_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_decimals(i) for i in obj]
    return obj


def query_account_info(
    parent_id: str,
    info_type: Literal["lesson_balance", "checkin_count", "points_balance", "all"],
) -> dict:
    """查询账户信息（从 DynamoDB 读取）。"""
    table = get_table("accounts")

    resp = table.get_item(Key={"parent_id": parent_id})
    account = resp.get("Item")

    if not account:
        return {"success": False, "message": f"未找到账户: {parent_id}"}

    account = _convert_decimals(account)

    result = {
        "success": True,
        "parent_id": parent_id,
        "parent_name": account.get("parent_name", ""),
        "students": account.get("students", []),
    }

    if info_type in ["lesson_balance", "all"]:
        result["lesson_balance"] = account.get("lesson_balance", {})
    if info_type in ["checkin_count", "all"]:
        result["checkin_count"] = account.get("checkin_count", {})
    if info_type in ["points_balance", "all"]:
        result["points_balance"] = account.get("points_balance", 0)

    return result


def get_course_schedule(
    parent_id: str,
    days_ahead: int = 7,
) -> dict:
    """查询课程安排（从 DynamoDB bookings 表读取 confirmed 记录）。"""
    table = get_table("bookings")

    resp = table.query(KeyConditionExpression=Key("parent_id").eq(parent_id))
    bookings = resp.get("Items", [])

    now = datetime.now()
    end_date = now + timedelta(days=days_ahead)

    schedules = []
    for b in bookings:
        if b.get("status") != "confirmed":
            continue
        start = datetime.fromisoformat(b["start_time"])
        if now <= start <= end_date:
            schedules.append({
                "student_name": b.get("student_name", ""),
                "tutor_name": b.get("tutor_name", ""),
                "lesson_desc": b.get("course_name", ""),
                "start_time": b["start_time"],
                "booking_id": b["booking_id"],
            })

    schedules.sort(key=lambda x: x["start_time"])

    return {
        "success": True,
        "parent_id": parent_id,
        "query_range": f"未来{days_ahead}天",
        "total_classes": len(schedules),
        "schedules": schedules,
    }

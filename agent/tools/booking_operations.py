"""课程预约操作工具 - DynamoDB 实现"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from boto3.dynamodb.conditions import Key

from .db import get_table

logger = logging.getLogger(__name__)


def _parse_date(date_str: str, field_name: str = "date") -> datetime:
    """解析日期字符串，支持多种格式。"""
    formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        pass
    raise ValueError(f"无效的{field_name}格式: '{date_str}'。请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM 格式")


def get_booking_records(
    parent_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """查询已约课程记录（从 DynamoDB 读取）。"""
    table = get_table("bookings")

    resp = table.query(KeyConditionExpression=Key("parent_id").eq(parent_id))
    bookings = resp.get("Items", [])

    # Date filter
    if start_date:
        start_dt = _parse_date(start_date, "start_date")
        bookings = [b for b in bookings if datetime.fromisoformat(b["start_time"]) >= start_dt]
    if end_date:
        end_dt = _parse_date(end_date, "end_date").replace(hour=23, minute=59, second=59)
        bookings = [b for b in bookings if datetime.fromisoformat(b["start_time"]) <= end_dt]

    # Only return confirmed
    bookings = [b for b in bookings if b.get("status") == "confirmed"]

    return {
        "success": True,
        "parent_id": parent_id,
        "total_records": len(bookings),
        "records": bookings,
    }


def get_available_slots(
    date: str,
    tutor_id: Optional[str] = None,
) -> dict:
    """查询可约时段和老师（从 DynamoDB 读取）。"""
    _parse_date(date, "date")  # validate format

    table = get_table("slots")
    results = []

    if tutor_id:
        # Query specific tutor + date
        resp = table.get_item(Key={"tutor_id": tutor_id, "date": date})
        item = resp.get("Item")
        if item:
            results.append({
                "tutor_id": item["tutor_id"],
                "tutor_name": item.get("tutor_name", tutor_id),
                "date": date,
                "available_slots": item.get("slots", []),
            })
    else:
        # Scan for all tutors on this date (fine for demo-scale data)
        resp = table.scan(FilterExpression=Key("date").eq(date))
        for item in resp.get("Items", []):
            results.append({
                "tutor_id": item["tutor_id"],
                "tutor_name": item.get("tutor_name", item["tutor_id"]),
                "date": date,
                "available_slots": item.get("slots", []),
            })

    return {
        "success": True,
        "date": date,
        "total_tutors": len(results),
        "availability": results,
    }


def book_class(
    parent_id: str,
    student_id: str,
    tutor_id: str,
    time_slot: str,
    course_type: str = "中文标准版",
) -> dict:
    """预约课程（写入 DynamoDB）。"""
    table = get_table("bookings")

    booking_id = f"BK{datetime.now().strftime('%Y%m%d%H%M%S')}"
    start_time = _parse_date(time_slot, "time_slot")
    end_time = start_time + timedelta(hours=1)

    # Resolve tutor name from slots table
    slots_table = get_table("slots")
    tutor_name = tutor_id
    try:
        any_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        resp = slots_table.get_item(Key={"tutor_id": tutor_id, "date": any_date})
        item = resp.get("Item")
        if item:
            tutor_name = item.get("tutor_name", tutor_id)
    except Exception:
        pass

    item = {
        "parent_id": parent_id,
        "booking_id": booking_id,
        "student_id": student_id,
        "student_name": "",
        "course_name": course_type,
        "tutor_id": tutor_id,
        "tutor_name": tutor_name,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "status": "confirmed",
    }

    table.put_item(Item=item)

    return {
        "success": True,
        "message": "预约成功",
        "booking_id": booking_id,
        "details": {
            "student_id": student_id,
            "tutor_id": tutor_id,
            "tutor_name": tutor_name,
            "time_slot": time_slot,
            "course_type": course_type,
        },
    }


def cancel_class(
    parent_id: str,
    booking_id: str,
) -> dict:
    """取消课程（更新 DynamoDB 状态）。"""
    table = get_table("bookings")

    resp = table.get_item(Key={"parent_id": parent_id, "booking_id": booking_id})
    booking = resp.get("Item")

    if not booking:
        return {"success": False, "message": f"未找到预约记录: {booking_id}"}

    # Update status
    table.update_item(
        Key={"parent_id": parent_id, "booking_id": booking_id},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "cancelled"},
    )

    return {
        "success": True,
        "message": "课程已成功取消",
        "refund_info": {"lesson_refunded": True, "refund_amount": 1},
        "cancelled_booking": {
            "booking_id": booking_id,
            "student_name": booking.get("student_name", ""),
            "course_name": booking.get("course_name", ""),
            "original_time": booking.get("start_time", ""),
        },
    }


def reschedule_class(
    parent_id: str,
    booking_id: str,
    new_time: str,
    new_tutor_id: Optional[str] = None,
) -> dict:
    """调整课程时间（更新 DynamoDB）。"""
    table = get_table("bookings")

    resp = table.get_item(Key={"parent_id": parent_id, "booking_id": booking_id})
    booking = resp.get("Item")

    if not booking:
        return {"success": False, "message": f"未找到预约记录: {booking_id}"}

    new_start = _parse_date(new_time, "new_time")
    new_end = new_start + timedelta(hours=1)
    tutor_id = new_tutor_id or booking["tutor_id"]
    tutor_name = booking.get("tutor_name", tutor_id)

    update_expr = "SET start_time = :st, end_time = :et, tutor_id = :tid, tutor_name = :tn"
    table.update_item(
        Key={"parent_id": parent_id, "booking_id": booking_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues={
            ":st": new_start.isoformat(),
            ":et": new_end.isoformat(),
            ":tid": tutor_id,
            ":tn": tutor_name,
        },
    )

    return {
        "success": True,
        "message": "调课成功",
        "details": {
            "booking_id": booking_id,
            "student_name": booking.get("student_name", ""),
            "course_name": booking.get("course_name", ""),
            "original_time": booking.get("start_time", ""),
            "new_time": new_time,
            "tutor_id": tutor_id,
            "tutor_name": tutor_name,
        },
    }

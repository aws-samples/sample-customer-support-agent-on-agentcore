"""时区工具 - DynamoDB 实现"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .db import get_table

logger = logging.getLogger(__name__)


def get_parent_timezone(parent_id: str) -> dict:
    """获取家长时区（从 DynamoDB accounts 表读取）。"""
    table = get_table("accounts")

    resp = table.get_item(Key={"parent_id": parent_id})
    account = resp.get("Item")

    timezone = account.get("timezone", "UTC") if account else "UTC"

    tz = ZoneInfo(timezone)
    local_time = datetime.now(tz)

    return {
        "success": True,
        "parent_id": parent_id,
        "timezone": timezone,
        "current_local_time": local_time.isoformat(),
        "utc_offset": local_time.strftime("%z"),
    }

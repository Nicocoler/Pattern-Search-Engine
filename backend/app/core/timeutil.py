# -*- coding: utf-8 -*-
"""业务统一使用北京时间（Asia/Shanghai）。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
BEIJING_TZ_NAME = "Asia/Shanghai"


def now_beijing() -> datetime:
    """当前北京时间（aware）。"""
    return datetime.now(BEIJING_TZ)


def today_beijing() -> date:
    """当前北京日历日。"""
    return now_beijing().date()


def to_beijing(value: datetime | date | None) -> datetime | date | None:
    """将 datetime 转为北京时间；date 原样返回。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # 无时区：按库会话已是北京墙钟的约定，钉上 +08:00
            return value.replace(tzinfo=BEIJING_TZ)
        return value.astimezone(BEIJING_TZ)
    return value


def isoformat_beijing(value: Any) -> str | None:
    """API/状态字符串统一输出带 +08:00 的北京时间；date 输出 YYYY-MM-DD。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_beijing(value).isoformat()  # type: ignore[union-attr]
    if isinstance(value, date):
        return value.isoformat()
    return str(value)

"""数据库 UTC 时间与对外 DTO 时间类型。"""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BeforeValidator


def utcnow() -> datetime:
    """当前 UTC 时间（naive，与 DATETIME(6) 列一致）。

    业务时间统一由应用写入 UTC，不依赖数据库会话时区。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mark_utc(value: datetime) -> datetime:
    """把数据库读出的 naive UTC 时间标注为 UTC。"""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


UtcDateTime = Annotated[datetime, BeforeValidator(mark_utc)]

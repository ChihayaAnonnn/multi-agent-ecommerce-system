"""价格领域 DTO。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PriceView(BaseModel):
    """SKU 的现行价格；`read_at` 是本次读取的时间。"""

    model_config = ConfigDict(from_attributes=True)

    sku_id: str
    currency: str
    amount_minor: int
    version: int
    effective_at: datetime
    read_at: datetime

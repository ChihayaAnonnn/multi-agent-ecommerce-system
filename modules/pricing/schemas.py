"""价格领域 DTO。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from shared.time import UtcDateTime


class PriceView(BaseModel):
    """SKU 的现行价格；`read_at` 是本次读取的时间。"""

    model_config = ConfigDict(from_attributes=True)

    sku_id: str
    currency: str
    amount_minor: int
    version: int
    effective_at: UtcDateTime
    read_at: UtcDateTime


__all__ = [
    'PriceView',
]

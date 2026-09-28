"""库存领域 DTO。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AvailabilityStatus = Literal["available", "out_of_stock", "unknown"]


class BalanceView(BaseModel):
    """某个 SKU 在某个库位的余额。"""

    model_config = ConfigDict(from_attributes=True)

    sku_id: str
    location_id: str
    on_hand: int
    reserved: int
    available: int
    version: int


class AvailabilityView(BaseModel):
    """SKU 的库位汇总可售状态。

    - `available`：存在余额行且可售量大于 0
    - `out_of_stock`：存在余额行但可售量为 0
    - `unknown`：没有余额行，数量与版本为空；未知不能当作有货
    """

    sku_id: str
    status: AvailabilityStatus
    available_quantity: int | None = None
    version: int | None = None
    read_at: datetime
    locations: list[BalanceView] = Field(default_factory=list)

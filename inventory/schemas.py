"""库存领域 DTO。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from db.base import UtcDateTime

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


class AdjustmentView(BalanceView):
    """库存调整的结果。

    `replayed=True` 表示这次调用命中了已有幂等键、没有再次加减。此时返回的是
    **当前**余额，而不是当初那次调整的结果；调用方若需要精确的原结果，
    应结合库存流水判断，不要把它当成原结果的回执。
    """

    replayed: bool = False


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
    read_at: UtcDateTime
    locations: list[BalanceView] = Field(default_factory=list)

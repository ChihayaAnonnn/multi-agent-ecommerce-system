"""目录与消费者之间的异步查询协议，不暴露 ORM 实体。"""

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class SKUExistenceReader(Protocol):
    """在调用方会话内确认 SKU 存在；缺失抛 CatalogNotFoundError。"""

    async def require_sku_exists(self, db: AsyncSession, sku_id: str) -> None: ...


class PriceReader(Protocol):
    """目录通过只读价格协议确认所有可售 SKU 均具有现行价格。"""

    async def has_current_prices(self, sku_ids: list[str]) -> bool: ...


__all__ = [
    'PriceReader',
    'SKUExistenceReader',
]

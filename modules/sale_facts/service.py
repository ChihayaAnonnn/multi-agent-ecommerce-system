"""顺序组合目录、价格与库存，保留锁定和非锁定报价规则。"""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from kernel.errors import CommerceDependencyError
from kernel.quotes import (
    SKUQuote,
    evaluate_legacy_cart_purchasability,
    evaluate_purchasability,
)
from modules.catalog.schemas import SaleFactView
from modules.catalog.service import CatalogError, CatalogService
from modules.inventory.service import InventoryDependencyError, InventoryService
from modules.pricing.service import PricingDependencyError, PricingService
from shared.time import utcnow


class SaleFactsReader:
    def __init__(
        self, catalog: CatalogService, pricing: PricingService,
        inventory: InventoryService,
    ) -> None:
        self.catalog, self.pricing, self.inventory = catalog, pricing, inventory

    async def quote_many(self, sku_ids: list[str]) -> dict[str, SKUQuote]:
        ids = list(dict.fromkeys(sku_ids))
        if not ids:
            return {}
        try:
            skus = await self.catalog.get_skus(ids)
            prices = await self.pricing.get_prices(ids)
            stock = await self.inventory.get_availability(ids)
        except (SQLAlchemyError, CatalogError, PricingDependencyError,
                InventoryDependencyError) as exc:
            raise CommerceDependencyError('可信商品事实暂不可用') from exc
        quotes = {}
        for sku_id in ids:
            sku = skus.get(sku_id)
            price, availability = prices.get(sku_id), stock.get(sku_id)
            reason = evaluate_purchasability(sku, price, availability)
            quotes[sku_id] = SKUQuote(sku_id=sku_id, sku=sku, price=price,
                availability=availability, purchasable=reason == 'purchasable',
                reason=reason, read_at=utcnow())
        return quotes

    async def lock_quotes(
        self, db: AsyncSession, sku_ids: list[str],
    ) -> tuple[dict[str, SKUQuote], dict[str, SaleFactView]]:
        """复用调用方事务，按目录→现价→库存顺序加锁，不提交。"""
        try:
            sales = await self.catalog.lock_sale_facts(db, sku_ids)
            prices = await self.pricing.lock_prices(db, sku_ids)
            stock = await self.inventory.lock_availability(db, sku_ids)
        except (SQLAlchemyError, CatalogError, PricingDependencyError,
                InventoryDependencyError) as exc:
            raise CommerceDependencyError('可信交易事实暂不可用') from exc
        quotes = {}
        for sku_id in sku_ids:
            sale = sales.get(sku_id)
            price, availability = prices.get(sku_id), stock.get(sku_id)
            reason = evaluate_legacy_cart_purchasability(sale, price, availability)
            quotes[sku_id] = SKUQuote(sku_id=sku_id, sku=sale.sku if sale else None,
                price=price, availability=availability,
                purchasable=reason == 'purchasable',
                reason=reason, read_at=utcnow())
        return quotes, sales


__all__ = [
    'SaleFactsReader',
]

"""仅从目录、价格与库存读取事实；依赖故障不转换为空结果。"""
from sqlalchemy.exc import SQLAlchemyError
from catalog.service import CatalogService, CatalogError
from pricing.service import PricingService, PricingDependencyError
from inventory.service import InventoryService, InventoryDependencyError
from commerce_orchestrator.schemas import ShoppingIntent, SKUQuote, CandidatePage
from db.base import utcnow


class CommerceDependencyError(Exception):
    """可信事实源无法读取。"""


class CommerceFactsReader:
    def __init__(self, catalog: CatalogService, pricing: PricingService, inventory: InventoryService):
        self.catalog, self.pricing, self.inventory = catalog, pricing, inventory

    async def quote_many(self, sku_ids: list[str]) -> dict[str, SKUQuote]:
        ids = list(dict.fromkeys(sku_ids))
        if not ids:
            return {}
        try:
            skus = await self.catalog.get_skus(ids)
            prices = await self.pricing.get_prices(ids)
            stock = await self.inventory.get_availability(ids)
        except (SQLAlchemyError, CatalogError, PricingDependencyError, InventoryDependencyError) as exc:
            raise CommerceDependencyError('可信商品事实暂不可用') from exc
        result = {}
        for sku_id in ids:
            sku, price, availability = skus.get(sku_id), prices.get(sku_id), stock.get(sku_id)
            if sku is None:
                reason = 'sku_not_found'
            elif sku.status != 'active':
                reason = 'sku_inactive'
            elif sku.product_status != 'active':
                reason = 'product_not_active'
            elif price is None or price.currency != 'CNY':
                reason = 'price_missing'
            elif availability is None or availability.status == 'unknown':
                reason = 'stock_unknown'
            elif availability.status != 'available' or not availability.available_quantity or availability.available_quantity < 1:
                reason = 'out_of_stock'
            else:
                reason = 'purchasable'
            result[sku_id] = SKUQuote(sku_id=sku_id, sku=sku, price=price,
                availability=availability, purchasable=reason == 'purchasable',
                reason=reason, read_at=utcnow())
        return result

    async def scan(self, intent: ShoppingIntent, max_scanned: int, page_size: int = 20) -> CandidatePage:
        if max_scanned < 1 or not 1 <= page_size <= 100:
            raise ValueError('扫描上限须为正数，page_size 须在 1–100 之间')
        scanned, cursor, items = 0, None, []
        while scanned < max_scanned:
            try:
                page = await self.catalog.search(category_id=intent.category_id, text=intent.query_text,
                    limit=min(page_size, max_scanned - scanned), cursor=cursor)
                details = await self.catalog.get_products([p.id for p in page.items])
            except (SQLAlchemyError, CatalogError) as exc:
                raise CommerceDependencyError('目录暂不可用') from exc
            scanned += len(page.items)
            eligible = []
            for product in details.values():
                if product.status != 'active' or (intent.category_id and product.category_id != intent.category_id):
                    continue
                definitions = {a.code: a for a in product.category_attributes if a.is_filterable}
                attrs = product.attributes or {}
                if any(c.source != 'inferred' and (c.code not in definitions or
                    c.code not in attrs or type(attrs[c.code]) is not type(c.value) or attrs[c.code] != c.value)
                    for c in intent.hard_conditions):
                    continue
                eligible.extend(s.id for s in product.skus)
            quotes = await self.quote_many(eligible)
            items.extend(q for q in quotes.values() if q.purchasable and q.price is not None and
                (intent.budget_max_minor is None or q.price.amount_minor <= intent.budget_max_minor))
            cursor = page.next_cursor
            if cursor is None:
                break
        return CandidatePage(items=items, scanned=scanned, incomplete=cursor is not None)

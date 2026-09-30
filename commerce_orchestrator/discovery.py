"""可信 SKU 硬过滤后的商品族短名单。"""
from commerce_orchestrator.schemas import ShoppingIntent
from commerce_orchestrator.facts import CommerceFactsReader
from catalog.service import CatalogService


class DiscoveryService:
    def __init__(self, facts: CommerceFactsReader, catalog: CatalogService, scan_limit: int = 200):
        self.facts, self.catalog, self.scan_limit = facts, catalog, scan_limit

    async def discover(self, intent: ShoppingIntent) -> tuple[list[dict], bool]:
        page = await self.facts.scan(intent, self.scan_limit)
        quotes = sorted(page.items, key=lambda q:(q.price.amount_minor, q.sku.product_id, q.sku_id))
        chosen, ids = [], set()
        for q in quotes:
            if q.sku.product_id not in ids:
                ids.add(q.sku.product_id); chosen.append(q)
            if len(chosen) == 3:
                break
        products = await self.catalog.get_products(list(ids))
        return [dict(q.model_dump(mode='json'), product_id=q.sku.product_id,
            name=products[q.sku.product_id].name, attributes=products[q.sku.product_id].attributes,
            reason_text='符合已确认条件，价格与库存已核验') for q in chosen], page.incomplete

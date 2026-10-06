"""可信 SKU 硬过滤后的商品族短名单。"""
from ecom.application.shopping.facts import CommerceFactsReader
from ecom.kernel.intent import ShoppingIntent
from ecom.modules.catalog.service import CatalogService


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

    async def select(self,principal,session_id: str,product_id: str | None,sku_id: str | None,sessions,intent: ShoppingIntent) -> tuple[list[dict],bool]:
        from ecom.kernel.errors import CommerceError
        seen,_=await sessions.seen(session_id,principal)
        products=await self.catalog.get_products([product_id] if product_id else [])
        product=products.get(product_id)
        if product is None or product.id not in seen or sku_id not in {s.id for s in product.skus}:
            raise CommerceError('not_seen')
        page=await self.facts.scan(intent,self.scan_limit)
        q=next((q for q in page.items if q.sku_id==sku_id),None)
        if q is None:
            raise CommerceError('constraints_mismatch')
        return [dict(q.model_dump(mode='json'),product_id=product.id,name=product.name,attributes=product.attributes)],page.incomplete

"""批量拼装目录与报价；不执行购物写操作。"""
from ecom.application.storefront.schemas import (
    StorefrontPage,
    StorefrontProduct,
    StorefrontResolved,
)
from ecom.modules.catalog.service import CatalogService
from ecom.modules.sale_facts.service import SaleFactsReader


class StorefrontService:
    def __init__(self,catalog: CatalogService,facts: SaleFactsReader):
        self.catalog,self.facts=catalog,facts

    async def list_products(self,*,limit: int=24,cursor: str | None=None) -> StorefrontPage:
        page=await self.catalog.search(limit=limit,cursor=cursor)
        resolved=await self.resolve_products([p.id for p in page.items])
        return StorefrontPage(items=resolved.items,next_cursor=page.next_cursor)

    async def resolve_products(self,product_ids: list[str]) -> StorefrontResolved:
        ids=list(dict.fromkeys(product_ids))
        details=await self.catalog.get_products(ids) if ids else {}
        active={i:details[i] for i in ids if i in details and details[i].status=='active'}
        selected={i:next(iter(sorted((s for s in p.skus if s.status=='active'),key=lambda s:(s.sku_code,s.id))),None) for i,p in active.items()}
        quotes=await self.facts.quote_many([s.id for s in selected.values() if s is not None])
        cards=[]
        for i,p in active.items():
            sku=selected[i];quote=quotes.get(sku.id) if sku else None
            images=[m for m in p.media if m.media_type=='image' and m.url.startswith('https://')]
            image=min(images,key=lambda m:(m.sort_order,m.id)).url if images else None
            attrs=p.attributes or {}
            cards.append(StorefrontProduct(id=i,name=p.name,brand=p.brand,category=p.category,
                image_url=image,sku_id=sku.id if sku else None,price=quote.price if quote else None,
                availability=quote.availability if quote else None,purchasable=bool(quote and quote.purchasable),
                reason=quote.reason if quote else 'sku_missing',simulated=attrs.get('simulation_price') is True or attrs.get('simulation_sku') is True))
        return StorefrontResolved(items=cards,missing_product_ids=[i for i in ids if i not in active])


__all__ = [
    'StorefrontService',
]

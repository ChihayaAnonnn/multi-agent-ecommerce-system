"""同类商品只按目录声明的可比较属性展示。"""
from pydantic import BaseModel, Field

from ecom.kernel.errors import CommerceError
from ecom.kernel.intent import ShoppingIntent


class CompareResponse(BaseModel):
    dimensions: list[dict] = Field(default_factory=list)
    products: list[dict] = Field(default_factory=list)
    differences: list[str] = Field(default_factory=list)


class CompareService:
    def __init__(self, sessions, catalog, facts):
        self.sessions, self.catalog, self.facts = sessions, catalog, facts

    async def compare(self, principal, session_id: str, product_ids: list[str]) -> CompareResponse:
        if not 2 <= len(product_ids) <= 3 or len(set(product_ids)) != len(product_ids):
            raise CommerceError('invalid_comparison')
        view = await self.sessions.get_owned(session_id, principal)
        seen, _ = await self.sessions.seen(session_id, principal)
        if not set(product_ids) <= seen:
            raise CommerceError('not_seen')
        products = await self.catalog.get_products(product_ids)
        if len(products) != len(product_ids):
            raise CommerceError('not_found')
        categories = {p.category_id for p in products.values()}
        if len(categories) != 1:
            raise CommerceError('comparison_category_mismatch')
        intent = ShoppingIntent.model_validate(view.state)
        allowed = {q.sku.product_id for q in (await self.facts.scan(intent, 200)).items}
        if not set(product_ids) <= allowed:
            raise CommerceError('constraints_mismatch')
        first = products[product_ids[0]]
        dimensions = [dict(code=a.code, name=a.name, unit=a.unit, value_type=a.value_type) for a in first.category_attributes if a.is_comparable]
        result = []
        for pid in product_ids:
            p = products[pid]; attrs = p.attributes or {}
            result.append({'product_id':pid,'name':p.name,'values':{d['code']:attrs.get(d['code'],'未提供') for d in dimensions}})
        return CompareResponse(dimensions=dimensions, products=result,
            differences=[d['code'] for d in dimensions if len({str(p['values'][d['code']]) for p in result}) > 1])

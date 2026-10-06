"""仅从目录、价格与库存读取事实；依赖故障不转换为空结果。"""
from sqlalchemy.exc import SQLAlchemyError

from catalog.service import CatalogError, CatalogService
from kernel.constraints import matches_budget, matches_product_constraints
from kernel.errors import CommerceDependencyError
from kernel.intent import ShoppingIntent
from kernel.quotes import CandidatePage, SKUQuote
from sale_facts.service import SaleFactsReader

__all__ = ['CommerceDependencyError', 'CommerceFactsReader']


class CommerceFactsReader:
    def __init__(self, catalog: CatalogService, facts: SaleFactsReader) -> None:
        self.catalog, self.facts = catalog, facts

    async def quote_many(self, sku_ids: list[str]) -> dict[str, SKUQuote]:
        return await self.facts.quote_many(sku_ids)

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
                if not matches_product_constraints(
                    intent, product, product.category_attributes,
                ):
                    continue
                eligible.extend(s.id for s in product.skus)
            quotes = await self.quote_many(eligible)
            items.extend(quote for quote in quotes.values()
                if quote.purchasable and matches_budget(intent, quote.price))
            cursor = page.next_cursor
            if cursor is None:
                break
        return CandidatePage(items=items, scanned=scanned, incomplete=cursor is not None)

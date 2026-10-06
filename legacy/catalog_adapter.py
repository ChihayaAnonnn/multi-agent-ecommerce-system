"""旧推荐接口的显式适配器（过渡契约）。

`models.schemas.Product` 是为旧推荐响应保留的展示模型，带浮点 `price` 与单层
`stock`，不是业务事实来源。新领域对象通过本适配器单向转换为该模型，
只用于让旧接口在过渡期继续可用；新接口全程使用整数分金额。

本阶段不把样例库存或样例商品写入真实表，旧 Agent 仍走样例流程。
"""

from __future__ import annotations

from legacy.models.schemas import Product as LegacyProduct
from modules.catalog.schemas import ProductDetailView, ProductView
from modules.inventory.schemas import AvailabilityView
from modules.pricing.schemas import PriceView


def to_legacy_product(
    product: ProductView | ProductDetailView,
    price: PriceView | None,
    availability: AvailabilityView | None,
) -> LegacyProduct:
    """把新领域对象拼成旧展示模型。

    - `price` 由整数分换算为元，仅用于旧接口展示；缺失时记为 0.0。
    - `stock` 取库位汇总可售量；库存未知时记为 0，不冒充有货。
    - 首版平台自营，因此没有卖家 ID。
    """
    category = getattr(product, "category", None)
    available = availability.available_quantity if availability else None

    return LegacyProduct(
        product_id=product.id,
        name=product.name,
        category=category.name if category is not None else "",
        price=price.amount_minor / 100 if price is not None else 0.0,
        description=product.description or "",
        brand=product.brand or "",
        seller_id="",
        stock=available if available is not None else 0,
    )

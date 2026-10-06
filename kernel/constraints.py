"""分别校验商品条件与现价预算，不读取任何事实源。"""

from collections.abc import Sequence

from kernel.intent import ShoppingIntent
from modules.catalog.schemas import AttributeView, ProductView
from modules.pricing.schemas import PriceView

__all__ = ['matches_product_constraints', 'matches_budget']


def matches_product_constraints(
    intent: ShoppingIntent,
    product: ProductView,
    category_attributes: Sequence[AttributeView],
) -> bool:
    if product.status != 'active' or (
        intent.category_id and product.category_id != intent.category_id
    ):
        return False
    definitions = {
        attribute.code for attribute in category_attributes if attribute.is_filterable
    }
    attributes = product.attributes or {}
    return not any(
        condition.source != 'inferred' and (
            condition.code not in definitions or condition.code not in attributes
            or type(attributes[condition.code]) is not type(condition.value)
            or attributes[condition.code] != condition.value
        ) for condition in intent.hard_conditions
    )


def matches_budget(intent: ShoppingIntent, price: PriceView | None) -> bool:
    return price is not None and (
        intent.budget_max_minor is None or price.amount_minor <= intent.budget_max_minor
    )

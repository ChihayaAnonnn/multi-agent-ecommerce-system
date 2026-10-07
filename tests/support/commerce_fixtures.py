"""真实 MySQL 购物验收共用的装配夹具：目录种子、会话/购物车状态与 ORM 泄漏检查。

来自 `tests/integration/test_commerce_facts_mysql.py`、
`test_cart_confirmation_mysql.py`、`test_checkout_mysql.py` 与
`test_cart_transactions_mysql.py`；初始化次序与 tuple 返回合同保持不变。
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import inspect

from ecom.application.shopping.actions import CartActionService
from ecom.infra.db import create_session_factory
from ecom.modules.cart.service import CartService
from ecom.modules.catalog.schemas import (
    AttributeInput,
    CategoryInput,
    ProductInput,
    SKUInput,
)
from ecom.modules.catalog.service import CatalogService, CatalogSKUReader
from ecom.modules.identity.types import make_principal
from ecom.modules.inventory.models import DEFAULT_LOCATION_ID
from ecom.modules.inventory.service import InventoryService
from ecom.modules.pricing.service import PricingService
from ecom.modules.sale_facts.service import SaleFactsReader
from ecom.modules.sessions.service import ShoppingSessionService


async def seed(url):
    factory = create_session_factory(url)
    sku_reader = CatalogSKUReader(factory)
    prices = PricingService(factory, sku_reader)
    stock = InventoryService(factory, sku_reader)
    catalog = CatalogService(factory, prices)
    cats = []
    for code in ['phone', 'headphone']:
        c = await catalog.create_category(CategoryInput(code=code, name=code))
        await catalog.add_category_attribute(c.id, AttributeInput(code='wireless',
            name='无线', value_type='boolean', is_filterable=True, is_comparable=True))
        cats.append(c)
    products, skus = [], []
    for n in range(8):
        p = await catalog.create_product(ProductInput(category_id=cats[n % 2].id,
            product_code=f'p{n}', name=f'商品{n}', attributes={'wireless': n < 4}))
        s = await catalog.create_sku(p.id, SKUInput(sku_code=f's{n}'))
        await prices.set_price(s.id, 100 + n * 100, None, '初始化')
        await stock.create_balance(s.id)
        await stock.adjust(s.id, DEFAULT_LOCATION_ID, 5, f'stock{n}', '初始化')
        await catalog.activate_product(p.id, p.version)
        products.append(p)
        skus.append(s)
    return factory, catalog, prices, stock, cats, products, skus


async def setup(url):
    factory,catalog,prices,stock,cats,products,skus=await seed(url)
    sessions=ShoppingSessionService(factory)
    a,b=make_principal('test','a'),make_principal('test','b')
    session=await sessions.create(a)
    cart_service=CartService(factory,SaleFactsReader(catalog,prices,stock))
    cart=await cart_service.get_cart(a)
    await sessions.record_seen(session.session_id,a,[products[0].id],[skus[0].id])
    return factory,sessions,cart_service,a,b,session,cart,prices,stock,skus


async def ready_cart(url):
    state=await setup(url)
    factory,sessions,cart,a,b,s,view,prices,stock,skus=state
    actions = CartActionService(factory, sessions, cart, None)
    action = await actions.propose_action(
        a, s.session_id, "add", {"sku_id": skus[0].id, "quantity": 1}, view.version
    )
    result=await actions.confirm(a,s.session_id,action.action_id,True,'add')
    return state,result.cart


def reject_orm(value):
    assert not getattr(inspect(value, raiseerr=False), 'mapper', None), (
        'ORM escaped snapshot'
    )
    if isinstance(value, BaseModel):
        for nested in dict(value).values():
            reject_orm(nested)
    elif isinstance(value, dict):
        for nested in value.values():
            reject_orm(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            reject_orm(nested)

"""商品卡片必须来自批量事实读取，缺失事实不变成可售。"""
import asyncio
from types import SimpleNamespace as NS

import pytest

from ecom.application.storefront.service import StorefrontService
from ecom.kernel.errors import CommerceDependencyError
from ecom.modules.catalog.schemas import CategoryRefView
from ecom.modules.sale_facts.service import SaleFactsReader
from tests.unit.test_commerce_facts import Catalog, Inventory, Prices


class Products(Catalog):
    def __init__(self, count=3):
        self.calls=0
        self.products={str(i): NS(id=str(i),name='商品'+str(i),brand=None,
            status='archived' if i==2 else 'active',
            category=CategoryRefView(id='c',code='test',name='测试'),
            attributes={'simulation_price':True},media=[],skus=[
                NS(id='zero',sku_code='B',status='active'),
                NS(id='unknown',sku_code='A',status='active'),
                NS(id='ok',sku_code='0',status='inactive')]) for i in range(count)}
    async def get_products(self, ids):
        self.calls+=1
        return {i:self.products[i] for i in ids if i in self.products}
    async def search(self, **kwargs):
        return NS(items=[NS(id=i) for i in self.products][:kwargs['limit']],next_cursor='next')


def reader(c):return SaleFactsReader(c,Prices(),Inventory())

def test_card_uses_first_active_sku():
    c=Products();r=asyncio.run(StorefrontService(c,reader(c)).resolve_products(['0']))
    card=r.items[0]
    assert card.sku_id=='unknown'
    assert card.reason=='stock_unknown' and not card.purchasable
    assert card.price.amount_minor==100 and card.image_url is None and card.simulated


def test_resolve_preserves_order_and_reports_missing():
    c=Products();r=asyncio.run(StorefrontService(c,reader(c)).resolve_products(['1','0','1','2','404']))
    assert [x.id for x in r.items]==['1','0']
    assert r.missing_product_ids==['2','404']

@pytest.mark.parametrize('sku,want',[('ok','purchasable'),('no-price','price_missing'),('unknown','stock_unknown'),('zero','out_of_stock'),(None,'sku_missing')])
def test_missing_facts_remain_explicit(sku,want):
    c=Products();c.products['0'].skus=[] if sku is None else [NS(id=sku,sku_code='A',status='active')]
    r=asyncio.run(StorefrontService(c,reader(c)).resolve_products(['0']))
    assert r.items[0].reason==want
    assert r.items[0].purchasable==(want=='purchasable')

@pytest.mark.parametrize('count',[1,24,100])
def test_batch_read_count_is_bounded(count):
    c=Products(count);r=asyncio.run(StorefrontService(c,reader(c)).list_products(limit=count))
    assert c.calls==1
    assert len(r.items)==count-(1 if count>2 else 0)
    assert r.next_cursor=='next'


def test_dependency_failure_is_not_empty_results():
    class Broken:
        async def quote_many(self, ids):raise CommerceDependencyError('offline')
    with pytest.raises(CommerceDependencyError):
        asyncio.run(StorefrontService(Products(),Broken()).resolve_products(['0']))

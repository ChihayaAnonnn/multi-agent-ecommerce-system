"""只读购物接口的本地桩：目录、价格、库存与商品卡片。

多个测试模块共用同一份实现，避免测试模块互相 import 形成拆分耦合。
"""

from types import SimpleNamespace as NS

from ecom.modules.catalog.schemas import CategoryRefView, SKUDetailView
from ecom.modules.inventory.schemas import AvailabilityView
from ecom.modules.pricing.schemas import PriceView
from ecom.shared.time import utcnow


class Catalog:
    async def get_skus(self, ids):
        return {i: SKUDetailView(id=i, product_id='p', sku_code=i,
                    option_signature='default', status='active', version=1,
                    product_status='active') for i in ids if i != 'missing'}


class Prices:
    async def get_prices(self, ids):
        return {i: PriceView(sku_id=i, currency='CNY', amount_minor=100,
                    version=1, effective_at=utcnow(), read_at=utcnow())
                for i in ids if i != 'no-price'}


class Inventory:
    async def get_availability(self, ids):
        return {i: AvailabilityView(sku_id=i,
                status=('unknown' if i == 'unknown'
                        else 'out_of_stock' if i == 'zero' else 'available'),
                available_quantity=None if i == 'unknown' else 0 if i == 'zero' else 3,
                read_at=utcnow()) for i in ids}


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
        return NS(
            items=[NS(id=i) for i in self.products][:kwargs['limit']],
            next_cursor='next',
        )

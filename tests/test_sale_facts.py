"""可复用事实读取保持来源顺序、事务边界与既有双规则。"""

import asyncio
import importlib.util
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from modules.catalog.schemas import ProductView, SaleFactView, SKUDetailView
from modules.catalog.service import CatalogError
from modules.inventory.schemas import AvailabilityView
from modules.inventory.service import InventoryDependencyError
from modules.pricing.schemas import PriceView
from modules.pricing.service import PricingDependencyError
from shared.time import utcnow


@pytest.fixture
def reader_case():
    class ReaderCase(SimpleNamespace):
        @property
        def reader(self):
            spec = importlib.util.find_spec('modules.sale_facts')
            assert spec is not None, (
                'SaleFactsReader 尚未实现'
            )
            from modules.sale_facts.service import SaleFactsReader

            return SaleFactsReader(self.catalog, self.pricing, self.inventory)

    now = utcnow()
    sku = SKUDetailView(id='sku-1', product_id='product-1', sku_code='SKU-1',
        option_signature='default', status='active', version=1, product_status='active')
    product = ProductView(id='product-1', category_id='category-1', product_code='P-1',
        name='测试商品', brand=None, description=None, attributes={}, status='active',
        version=1, created_at=now, updated_at=now)
    sale = SaleFactView(product=product, sku=sku)
    price = PriceView(sku_id=sku.id, currency='CNY', amount_minor=100,
        version=1, effective_at=now, read_at=now)
    availability = AvailabilityView(sku_id=sku.id, status='available',
        available_quantity=3, read_at=now)
    case = ReaderCase(events=[], sessions=[], requests=[], commits=0,
        sku=sku, sale=sale, price=price, availability=availability, failures={})

    class Transaction:
        async def commit(self):
            case.commits += 1

    case.db = Transaction()

    async def read(source, ids, db=None):
        case.events.append(source)
        case.requests.append(list(ids))
        if db is not None:
            case.sessions.append(db)
        if source in case.failures:
            raise case.failures[source]
        value = {'catalog': case.sale if db is not None else case.sku,
            'pricing': case.price, 'inventory': case.availability}[source]
        if case.sku.id in ids and value is not None:
            return {case.sku.id: value}
        return {}

    class Catalog:
        async def get_skus(self, ids):
            return await read('catalog', ids)

        async def lock_sale_facts(self, db, ids):
            return await read('catalog', ids, db)

    class Pricing:
        async def get_prices(self, ids):
            return await read('pricing', ids)

        async def lock_prices(self, db, ids):
            return await read('pricing', ids, db)

    class Inventory:
        async def get_availability(self, ids):
            return await read('inventory', ids)

        async def lock_availability(self, db, ids):
            return await read('inventory', ids, db)

    case.catalog, case.pricing, case.inventory = Catalog(), Pricing(), Inventory()
    return case


def test_locked_reader_uses_one_session_and_order(reader_case):
    quotes, sales = asyncio.run(
        reader_case.reader.lock_quotes(reader_case.db, ['sku-1'])
    )
    assert reader_case.events == ['catalog', 'pricing', 'inventory']
    assert all(session is reader_case.db for session in reader_case.sessions)
    assert len(reader_case.sessions) == 3
    assert reader_case.commits == 0
    assert quotes['sku-1'].purchasable and sales['sku-1'] is reader_case.sale


def test_unlocked_reader_preserves_order_and_deduplicates(reader_case):
    quotes = asyncio.run(reader_case.reader.quote_many(['missing', 'sku-1', 'missing']))
    assert list(quotes) == ['missing', 'sku-1']
    assert reader_case.events == ['catalog', 'pricing', 'inventory']
    assert reader_case.requests == [['missing', 'sku-1']] * 3
    assert quotes['missing'].reason == 'sku_not_found'
    assert not quotes['missing'].purchasable


def test_locked_reader_preserves_source_input_and_result_order(reader_case):
    quotes, sales = asyncio.run(reader_case.reader.lock_quotes(
        reader_case.db, ['missing', 'sku-1', 'missing']))
    assert list(quotes) == ['missing', 'sku-1']
    assert reader_case.requests == [['missing', 'sku-1', 'missing']] * 3
    assert quotes['missing'].reason == 'sku_not_found' and 'missing' not in sales


def test_unlocked_empty_input_does_not_read_sources(reader_case):
    assert asyncio.run(reader_case.reader.quote_many([])) == {}
    assert reader_case.events == []


def test_locked_empty_input_keeps_legacy_source_calls(reader_case):
    assert asyncio.run(reader_case.reader.lock_quotes(reader_case.db, [])) == ({}, {})
    assert reader_case.events == ['catalog', 'pricing', 'inventory']
    assert reader_case.commits == 0


@pytest.mark.parametrize('locked', [False, True])
@pytest.mark.parametrize('source,error_type', [
    ('catalog', CatalogError), ('pricing', PricingDependencyError),
    ('inventory', InventoryDependencyError), ('catalog', OperationalError),
])
def test_explicit_dependency_errors_are_translated(
    reader_case, locked, source, error_type,
):
    reader = reader_case.reader
    from kernel.errors import CommerceDependencyError
    from shared.errors import DomainError

    error = (error_type('query', {}, Exception('offline'))
        if error_type is OperationalError else error_type('offline'))
    reader_case.failures[source] = error
    with pytest.raises(CommerceDependencyError) as caught:
        asyncio.run(reader.lock_quotes(reader_case.db, ['sku-1'])
            if locked else reader.quote_many(['sku-1']))
    assert caught.value.__cause__ is error
    assert isinstance(caught.value, DomainError)
    expected = '可信交易事实暂不可用' if locked else '可信商品事实暂不可用'
    assert str(caught.value) == expected
    assert reader_case.events == ['catalog', 'pricing', 'inventory'][
        :['catalog', 'pricing', 'inventory'].index(source) + 1]


@pytest.mark.parametrize('locked', [False, True])
@pytest.mark.parametrize('source', ['catalog', 'pricing', 'inventory'])
def test_programming_errors_propagate(reader_case, locked, source):
    error = ValueError('programming error')
    reader_case.failures[source] = error
    with pytest.raises(ValueError) as caught:
        asyncio.run(reader_case.reader.lock_quotes(reader_case.db, ['sku-1'])
            if locked else reader_case.reader.quote_many(['sku-1']))
    assert caught.value is error


@pytest.mark.parametrize('currency,status,quantity,unlocked_reason', [
    ('USD', 'available', 3, 'price_missing'),
    ('CNY', 'available', -1, 'out_of_stock'),
    ('CNY', 'out_of_stock', 3, 'out_of_stock'),
])
def test_locked_quotes_keep_legacy_rule_differences(
    reader_case, currency, status, quantity, unlocked_reason,
):
    reader_case.price.currency = currency
    reader_case.availability.status = status
    reader_case.availability.available_quantity = quantity
    unlocked = asyncio.run(reader_case.reader.quote_many(['sku-1']))
    locked, _ = asyncio.run(reader_case.reader.lock_quotes(reader_case.db, ['sku-1']))
    assert unlocked['sku-1'].reason == unlocked_reason
    assert locked['sku-1'].reason == 'purchasable'


def test_locked_quotes_use_sale_product_status_authority(reader_case):
    reader_case.sale.product.status = 'archived'
    unlocked = asyncio.run(reader_case.reader.quote_many(['sku-1']))
    locked, _ = asyncio.run(reader_case.reader.lock_quotes(reader_case.db, ['sku-1']))
    assert unlocked['sku-1'].purchasable
    assert locked['sku-1'].reason == 'product_not_active'


def test_application_reader_delegates_quotes(reader_case):
    from application.shopping.facts import CommerceFactsReader

    reader = CommerceFactsReader(reader_case.catalog, reader_case.reader)
    quotes = asyncio.run(reader.quote_many(['sku-1']))
    assert quotes['sku-1'].purchasable
    assert reader_case.events == ['catalog', 'pricing', 'inventory']


def test_cart_locking_delegates_without_source_access(reader_case):
    from modules.cart.service import CartService

    class Facts:
        async def lock_quotes(self, db, ids):
            return await reader_case.reader.lock_quotes(db, ids)

    cart = CartService(None, Facts())
    quotes, sales = asyncio.run(cart.lock_quotes(reader_case.db, ['sku-1']))
    assert quotes['sku-1'].purchasable and sales['sku-1'] is reader_case.sale


def test_dependency_error_compatibility_alias_is_same_object(reader_case):
    assert reader_case.reader is not None
    from application.shopping.facts import CommerceDependencyError as OldError
    from kernel.errors import CommerceDependencyError

    assert OldError is CommerceDependencyError


def test_locked_reader_keeps_real_mysql_transaction(migrated_mysql_database):
    from sqlalchemy import update

    from modules.pricing.models import SKUPrice
    from modules.sale_facts.service import SaleFactsReader
    from tests.test_commerce_facts_mysql import seed

    async def scenario():
        seeded = await seed(migrated_mysql_database)
        factory, catalog, pricing, inventory, _, _, skus = seeded
        sku_id = skus[0].id
        original = (await pricing.get_prices([sku_id]))[sku_id].amount_minor
        reader = SaleFactsReader(catalog, pricing, inventory)
        async with factory() as db:
            await db.begin()
            await db.execute(update(SKUPrice).where(SKUPrice.sku_id == sku_id)
                .values(amount_minor=original + 17))
            quotes, sales = await reader.lock_quotes(db, [sku_id])
            assert db.in_transaction()
            assert quotes[sku_id].price.amount_minor == original + 17
            assert isinstance(sales[sku_id], SaleFactView)
            await db.rollback()
        restored = (await pricing.get_prices([sku_id]))[sku_id]
        assert restored.amount_minor == original

    asyncio.run(scenario())

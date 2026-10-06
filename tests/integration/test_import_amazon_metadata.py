"""真实 MySQL 验证批量导入，确保重跑不重置测试中的价格和库存。"""
import asyncio
import json

import pytest
from sqlalchemy import func, select

from ecom.infra.db import create_session_factory
from ecom.modules.catalog.models import SKU, Product, ProductMedia
from ecom.modules.catalog.service import CatalogSKUReader
from ecom.modules.inventory.models import (
    DEFAULT_LOCATION_ID,
    InventoryBalance,
    InventoryMovement,
)
from ecom.modules.inventory.service import InventoryService
from ecom.modules.pricing.models import SKUPrice, SKUPriceChange
from ecom.modules.pricing.service import PricingService
from tests.support.amazon_metadata import importer, make_source, record


def test_batch_import_is_atomic_and_repeat_keeps_business_changes(migrated_mysql_database):
    async def scenario():
        module = importer()
        factory = create_session_factory(migrated_mysql_database)
        row = module.normalize(record(), 'Electronics','42','rev')
        row['stock'] = 5
        async with factory() as db, db.begin():
            await module.ensure_categories(db, ['Electronics'])
            result = await module.write_batch(db, [row,row])
            assert result == 1
        sid = row['sku']['id']
        sku_reader = CatalogSKUReader(factory)
        pricing = PricingService(factory, sku_reader)
        inventory = InventoryService(factory, sku_reader)
        await pricing.set_price(sid,12345,1,'测试调价')
        await inventory.adjust(sid,DEFAULT_LOCATION_ID,-2,'test-adjust','测试库存调整')
        async with factory() as db, db.begin():
            assert await module.write_batch(db,[row]) == 0
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(Product)) == 1
            assert await db.scalar(select(func.count()).select_from(SKU)) == 1
            assert await db.scalar(select(func.count()).select_from(ProductMedia)) == 1
            assert (await db.get(SKUPrice,(sid,'CNY'))).amount_minor == 12345
            assert (await db.get(InventoryBalance,(sid,DEFAULT_LOCATION_ID))).on_hand == 3
            assert await db.scalar(select(func.count()).select_from(SKUPriceChange)) == 2
            assert await db.scalar(select(func.count()).select_from(InventoryMovement)) == 2
        bad = module.normalize(record('B000000002'), 'Electronics','42','rev')
        bad['price_minor'] = -1
        with pytest.raises(ValueError):
            async with factory() as db, db.begin():
                await module.write_batch(db,[bad])
        async with factory() as db:
            assert await db.get(Product,bad['product']['id']) is None
        await factory.kw['bind'].dispose()
    asyncio.run(scenario())


def test_resume_then_expand_limit_and_unknown_stock(migrated_mysql_database,tmp_path):
    async def scenario():
        module=importer(); source=make_source(tmp_path,[record(f'B{i:09}') for i in range(8)])
        checkpoint=tmp_path/'checkpoint.json'
        args=module.parse_args(['--source-dir',str(source),'--categories','Electronics','--limit','3','--batch-size','2','--checkpoint',str(checkpoint)])
        factory=create_session_factory(migrated_mysql_database)
        first=await module.import_files(args,factory,'isolated-test')
        assert first['inserted']==3
        again=await module.import_files(args,factory,'isolated-test')
        assert again['inserted']==3
        args.limit=0
        final=await module.import_files(args,factory,'isolated-test')
        assert final['inserted']==8
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(Product))==8
        saved=json.loads(checkpoint.read_text())
        assert 'password' not in saved and saved['files']['Electronics']['eof']
        args.seed='different'
        with pytest.raises(ValueError,match='不匹配'):
            await module.import_files(args,factory,'isolated-test')
        await factory.kw['bind'].dispose()
    asyncio.run(scenario())


def test_unknown_stock_and_late_failure_rollback(migrated_mysql_database):
    async def scenario():
        module=importer(); factory=create_session_factory(migrated_mysql_database)
        unknown=module.normalize(record('B000000003'),'Electronics','42','rev')
        unknown['stock']=None
        async with factory() as db, db.begin():
            await module.ensure_categories(db,['Electronics'])
            await module.write_batch(db,[unknown])
        sku=unknown['sku']['id']
        inventory = InventoryService(factory, CatalogSKUReader(factory))
        availability=await inventory.get_availability([sku])
        assert availability[sku].status=='unknown'
        async with factory() as db:
            assert await db.get(InventoryBalance,(sku,DEFAULT_LOCATION_ID)) is None
            assert await db.scalar(select(func.count()).select_from(InventoryMovement))==0
        # 库存初始化发生在目录和价格 INSERT 之后，检验真正的跨模块回滚。
        good=module.normalize(record('B000000004'),'Electronics','42','rev')
        bad=module.normalize(record('B000000005'),'Electronics','42','rev'); bad['stock']=-1
        with pytest.raises(ValueError):
            async with factory() as db, db.begin():
                await module.write_batch(db,[good,bad])
        async with factory() as db:
            for row in [good,bad]:
                assert await db.get(Product,row['product']['id']) is None
                assert await db.get(SKUPrice,(row['sku']['id'],'CNY')) is None
        await factory.kw['bind'].dispose()
    asyncio.run(scenario())


def test_commit_before_checkpoint_failure_is_safe_to_replay(migrated_mysql_database,tmp_path,monkeypatch):
    async def scenario():
        module=importer(); factory=create_session_factory(migrated_mysql_database)
        source=make_source(tmp_path,[record(f'B{i:09}') for i in range(3)])
        args=module.parse_args(['--source-dir',str(source),'--categories','Electronics','--limit','0','--checkpoint',str(tmp_path/'state.json')])
        save=module.atomic_save
        monkeypatch.setattr(module,'atomic_save',lambda *args: (_ for _ in ()).throw(OSError('checkpoint disk full')))
        with pytest.raises(OSError,match='checkpoint disk full'):
            await module.import_files(args,factory,'isolated-test')
        monkeypatch.setattr(module,'atomic_save',save)
        result=await module.import_files(args,factory,'isolated-test')
        assert result['database_products']==3
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(Product))==3
            assert await db.scalar(select(func.count()).select_from(SKUPriceChange))==3
        await factory.kw['bind'].dispose()
    asyncio.run(scenario())


def test_database_lock_rejects_second_importer(migrated_mysql_database):
    async def scenario():
        module=importer(); factory=create_session_factory(migrated_mysql_database)
        engine=factory.kw['bind']
        async with module.import_lock(engine,'test-db'):
            with pytest.raises(ValueError,match='已有导入任务'):
                async with module.import_lock(engine,'test-db'):
                    pytest.fail('第二个导入器不应取得锁')
        async with module.import_lock(engine,'test-db'):
            pass
        await engine.dispose()
    asyncio.run(scenario())

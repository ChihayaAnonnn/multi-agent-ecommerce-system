"""真实 MySQL 验证批量导入，确保重跑不重置测试中的价格和库存。"""
import asyncio
import importlib
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from catalog.models import SKU, Product, ProductMedia
from catalog.service import CatalogSKUReader
from db.session import create_session_factory
from inventory.models import DEFAULT_LOCATION_ID, InventoryBalance, InventoryMovement
from inventory.service import InventoryService
from pricing.models import SKUPrice, SKUPriceChange
from pricing.service import PricingService


def importer():
    # 延后导入，使 RED 明确显示缺失的实现，而非打断其他测试收集。
    return importlib.import_module('data.import_amazon_metadata')


def record(asin='B000000001', title='测试耳机'):
    return {'parent_asin':asin, 'title':title, 'price':24.95, 'details':{'Brand':'Acme'},
            'categories':['Electronics','Headphones'], 'description':['适合通勤'],
            'images':[{'large':'https://example.com/item.jpg'}], 'average_rating':4.3, 'rating_number':12}


def test_simulation_is_reproducible_and_covers_stock_states():
    module = importer()
    stock = [module.simulate(f'sku-{i}', 'Electronics', '42') for i in range(1000)]
    assert stock == [module.simulate(f'sku-{i}', 'Electronics', '42') for i in range(1000)]
    assert {row['inventory_state'] for row in stock} == {'normal','low','out_of_stock','unknown'}
    for row in stock:
        assert type(row['price_minor']) is int and row['price_minor'] > 0
        assert (row['stock'] is None) == (row['inventory_state'] == 'unknown')
        if row['stock'] is not None:
            assert type(row['stock']) is int and row['stock'] >= 0
    assert module.simulate('sku-1','Electronics','42') != module.simulate('sku-1','Electronics','43')


def test_normalization_preserves_source_and_marks_simulation():
    module = importer()
    row = module.normalize(record(title='耳'*300), 'Electronics', '42', 'rev')
    assert row['product']['name'] == '耳'*255
    assert row['product']['brand'] == 'Acme'
    assert row['product']['attributes']['source_title'] == '耳'*300
    assert row['product']['attributes']['source_price_usd'] == '24.95'
    assert row['product']['attributes']['simulation_price'] is True
    assert row['product']['attributes']['simulation_sku'] is True
    assert row['sku']['option_signature'] == 'default'
    assert row['media']['url'] == 'https://example.com/item.jpg'
    assert row['product']['id'] == module.normalize(record(), 'Pet_Supplies', '42', 'rev')['product']['id']
    with pytest.raises(ValueError): module.normalize({'parent_asin':'invalid','title':'x'}, 'Electronics','42','rev')
    with pytest.raises(ValueError): module.normalize(record(title=''), 'Electronics','42','rev')


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


def make_source(tmp_path, rows):
    root=tmp_path/'source'; path=root/'raw/meta_categories/meta_Electronics.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
    (root/'download_manifest.json').write_text(json.dumps({'status':'complete','repo_id':'McAuley-Lab/Amazon-Reviews-2023','revision':'test','files':{'raw/meta_categories/meta_Electronics.jsonl':{'size_bytes':path.stat().st_size}}}))
    return root


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


def test_dry_run_never_opens_database_or_writes_checkpoint(tmp_path):
    module=importer(); source=make_source(tmp_path,[record(),{'bad':'record'}])
    checkpoint=tmp_path/'checkpoint.json'
    args=module.parse_args(['--source-dir',str(source),'--categories','Electronics','--limit','0','--dry-run','--checkpoint',str(checkpoint)])
    result=asyncio.run(module.import_files(args,None,'preview'))
    assert result['valid']==1 and result['rejected']==1
    assert not checkpoint.exists()


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

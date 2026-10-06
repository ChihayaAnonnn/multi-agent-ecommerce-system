"""导入器的纯桩用例：模拟可复现、归一化保真、dry-run 不碰数据库。"""
import asyncio

import pytest

from tests.support.amazon_metadata import importer, make_source, record


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


def test_dry_run_never_opens_database_or_writes_checkpoint(tmp_path):
    module=importer(); source=make_source(tmp_path,[record(),{'bad':'record'}])
    checkpoint=tmp_path/'checkpoint.json'
    args=module.parse_args(['--source-dir',str(source),'--categories','Electronics','--limit','0','--dry-run','--checkpoint',str(checkpoint)])
    result=asyncio.run(module.import_files(args,None,'preview'))
    assert result['valid']==1 and result['rejected']==1
    assert not checkpoint.exists()

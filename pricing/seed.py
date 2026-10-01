"""内部离线导入的新 SKU 测试价格；不覆盖现价、不自行提交。"""
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession
from db.base import utcnow, new_id
from pricing.models import SKUPrice, SKUPriceChange, CURRENCY
from pricing.service import to_minor_units


async def insert_seed_prices(db: AsyncSession, rows: list[dict]) -> None:
    """为新 SKU 同时写入现价和首条流水，唯一约束拒绝覆盖已有价格。"""
    if not rows:
        return
    now = utcnow()
    prices, changes = [], []
    for row in rows:
        amount = to_minor_units(row['price_minor'])
        sku_id = row['sku']['id']
        prices.append(dict(sku_id=sku_id, currency=CURRENCY, amount_minor=amount, version=1, effective_at=now))
        changes.append(dict(id=new_id(), sku_id=sku_id, currency=CURRENCY, old_amount_minor=None,
            new_amount_minor=amount, price_version=1, reason='Amazon 模拟测试价格初始化', changed_at=now))
    await db.execute(insert(SKUPrice), prices)
    await db.execute(insert(SKUPriceChange), changes)

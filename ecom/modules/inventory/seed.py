"""库存模块持有的离线初始化写入口；未知库存不创建余额。"""
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ecom.modules.inventory.models import (
    DEFAULT_LOCATION_ID,
    InventoryBalance,
    InventoryMovement,
)
from ecom.shared.ids import new_id
from ecom.shared.time import utcnow


async def insert_seed_inventory(db: AsyncSession, rows: list[dict]) -> None:
    """余额与初始化流水同事务插入，既有余额由唯一约束保护。"""
    balances, movements = [], []
    now = utcnow()
    for row in rows:
        stock = row['stock']
        if stock is None:
            continue
        if type(stock) is not int or stock < 0:
            raise ValueError('模拟库存必须是非负整数或未知')
        sku_id = row['sku']['id']
        balances.append(dict(sku_id=sku_id, location_id=DEFAULT_LOCATION_ID, on_hand=stock, reserved=0, version=1))
        movements.append(dict(id=new_id(), sku_id=sku_id, location_id=DEFAULT_LOCATION_ID,
            delta=stock, reason='Amazon 模拟库存初始化', idempotency_key='amazon-seed:' + sku_id,
            balance_version=1, occurred_at=now))
    if balances:
        await db.execute(insert(InventoryBalance), balances)
        await db.execute(insert(InventoryMovement), movements)

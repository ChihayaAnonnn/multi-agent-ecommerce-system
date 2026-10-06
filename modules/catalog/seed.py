"""内部离线导入的目录批量写入；调用方负责同批价格、库存和事务提交。"""
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from modules.catalog.models import SKU, Product, ProductMedia


async def insert_catalog_batch(db: AsyncSession, rows: list[dict]) -> None:
    """仅插入调用方已去重的新草稿商品，不修改已有目录实体。"""
    if not rows:
        return
    await db.execute(insert(Product), [row['product'] for row in rows])
    await db.execute(insert(SKU), [row['sku'] for row in rows])
    media = [row['media'] for row in rows if row['media']]
    if media:
        await db.execute(insert(ProductMedia), media)

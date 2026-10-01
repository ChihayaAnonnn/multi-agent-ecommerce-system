#!/usr/bin/env python3
"""流式导入 Amazon 商品资料，并初始化明确标记的模拟价格和库存。"""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import select, update, text, func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, async_sessionmaker
from tqdm import tqdm
from catalog.models import Category, CategoryAttribute, Product
from catalog.seed import insert_catalog_batch
from inventory.models import StockLocation, DEFAULT_LOCATION_ID
from inventory.seed import insert_seed_inventory
from pricing.seed import insert_seed_prices
from data.download_amazon_metadata import CATEGORIES, REPO_ID

NAMESPACE = UUID('699e5fb0-bf94-4c5a-92c7-e4a575ebf39a')
RULE_VERSION = 'amazon-demo-v1'
CATEGORY_NAMES = dict(zip(CATEGORIES, ['数码电子','家居厨房','美妆个护','运动户外','宠物用品','食品杂货']))
# 只是测试区间，不是国内市场价格或真实供货量。
PRICE_RANGES = dict(zip(CATEGORIES, [(20,8000),(10,3000),(10,1500),(20,5000),(10,1500),(5,500)]))
STOCK_RANGES = dict(zip(CATEGORIES, [(6,30),(6,30),(10,80),(6,40),(10,80),(20,100)]))
ATTRIBUTES = {
    'source_parent_asin':('Amazon 商品 ID','text',False),
    'source_category':('来源品类','text',False),
    'source_categories':('来源类目路径','text',False),
    'source_details':('来源属性 JSON','text',False),
    'source_title':('原始名称','text',False),
    'source_price_usd':('历史美元价格','text',False),
    'source_revision':('数据源版本','text',False),
    'average_rating':('评分','number',True),
    'rating_number':('评价数量','number',True),
    'simulation_seed':('模拟种子','text',False),
    'simulation_version':('模拟规则版本','text',False),
    'simulation_inventory_state':('初始模拟库存状态','text',False),
    'simulation_price':('模拟价格','boolean',False),
    'simulation_sku':('模拟默认规格','boolean',False),
}


def stable_id(name: str) -> str:
    return str(uuid5(NAMESPACE, name))


def number(seed: str, sku_id: str, field: str) -> int:
    # 使用无歧义编码，字段独立取种子，不依赖文件顺序或 Python hash 随机化。
    raw = json.dumps([RULE_VERSION, seed, sku_id, field], separators=(',', ':'))
    return int.from_bytes(hashlib.sha256(raw.encode()).digest()[:8], 'big')


def simulate(sku_id: str, category: str, seed: str) -> dict:
    bucket = number(seed, sku_id, 'inventory-v1/state') % 100
    if bucket < 80:
        state = 'normal'; low, high = STOCK_RANGES[category]
        stock = low + number(seed, sku_id, 'inventory-v1/quantity') % (high-low+1)
    elif bucket < 90:
        state = 'low'; stock = 1 + number(seed, sku_id, 'inventory-v1/quantity') % 5
    elif bucket < 98:
        state = 'out_of_stock'; stock = 0
    else:
        state = 'unknown'; stock = None
    low, high = PRICE_RANGES[category]
    fraction = number(seed, sku_id, 'price-v1/amount') / (2**64-1)
    # 对数区间让低、中价测试样本多于高价样本，不以历史美元价格冒充现价。
    price = round(math.exp(math.log(low*100) + fraction * math.log(high/low)))
    return dict(inventory_state=state, stock=stock, price_minor=price)


def normalize(record: dict, category: str, seed: str, revision: str) -> dict:
    if not isinstance(record, dict):
        raise ValueError('记录不是 JSON 对象')
    asin = record.get('parent_asin')
    title = record.get('title')
    if not isinstance(asin,str) or not re.fullmatch(r'[A-Z0-9]{10}', asin):
        raise ValueError('缺少有效 parent_asin')
    if not isinstance(title,str) or not title.strip():
        raise ValueError('缺少商品名称')
    title = title.strip()
    pid, sid = stable_id('product:'+asin), stable_id('sku:'+asin)
    simulation = simulate(sid, category, seed)
    details = record.get('details') if isinstance(record.get('details'),dict) else {}
    brand = details.get('Brand') or details.get('Brand Name')
    # store 可能是商店而非品牌，不自动把商店补成品牌。
    brand = brand.strip()[:128] if isinstance(brand,str) and brand.strip() else None
    attrs = dict(source_parent_asin=asin, source_category=category, source_title=title,
        source_categories=json.dumps(record.get('categories') or [], ensure_ascii=False, allow_nan=False),
        source_details=json.dumps(details, ensure_ascii=False, allow_nan=False), source_revision=revision,
        simulation_seed=seed, simulation_version=RULE_VERSION, simulation_price=True, simulation_sku=True,
        simulation_inventory_state=simulation['inventory_state'])
    if record.get('price') is not None:
        attrs['source_price_usd'] = str(record['price'])
    for key in ['average_rating','rating_number']:
        value = record.get(key)
        if type(value) in (int,float) and math.isfinite(value) and value >= 0:
            attrs[key] = value
    parts = []
    for key in ['description','features']:
        value = record.get(key)
        if isinstance(value,str): parts.append(value)
        elif isinstance(value,list): parts.extend(item for item in value if isinstance(item,str))
    # MySQL TEXT 上限按字节计算；原文件保留完整描述。
    description = '\n'.join(parts).encode('utf-8')[:60000].decode('utf-8',errors='ignore') or None
    media = None
    images = record.get('images') if isinstance(record.get('images'),list) else []
    for image in images:
        if not isinstance(image,dict): continue
        url = image.get('hi_res') or image.get('large') or image.get('thumb')
        if isinstance(url,str) and url.startswith('https://') and len(url) <= 512:
            media = dict(id=stable_id('media:'+asin), product_id=pid, sku_id=None,
                url=url,media_type='image',alt_text=title[:255],sort_order=0)
            break
    return dict(product=dict(id=pid, category_id=stable_id('category:'+category), product_code='AMZ-'+asin,
        name=title[:255],brand=brand,description=description,attributes=attrs,status='draft',version=1),
        sku=dict(id=sid,product_id=pid,sku_code='AMZ-'+asin+'-DEFAULT',option_signature='default',status='active',version=1),
        media=media, stock=simulation['stock'], price_minor=simulation['price_minor'])


async def ensure_categories(db: AsyncSession, categories: list[str]) -> None:
    for category in categories:
        cid = stable_id('category:'+category)
        code = 'amazon_'+category.lower()
        existing = await db.scalar(select(Category).where(Category.code==code))
        if existing and existing.id != cid:
            raise ValueError('来源类目编码已被其他记录使用，请选择独立测试库')
        if not existing:
            await db.execute(mysql_insert(Category).values(id=cid,code=code,name=CATEGORY_NAMES[category],status='active',sort_order=CATEGORIES.index(category)))
        for key,(name,value_type,comparable) in ATTRIBUTES.items():
            stmt = mysql_insert(CategoryAttribute).values(id=stable_id(f'attribute:{category}:{key}'),
                category_id=cid,code=key,name=name,value_type=value_type,is_comparable=comparable,is_filterable=False)
            await db.execute(stmt.on_duplicate_key_update(id=CategoryAttribute.id))


async def write_batch(db: AsyncSession, rows: list[dict]) -> int:
    unique = {row['product']['id']:row for row in reversed(rows)}
    if not unique: return 0
    existing = {row.id:row for row in await db.scalars(select(Product).where(Product.id.in_(unique)))}
    for pid, product in existing.items():
        old, new = product.attributes or {}, unique[pid]['product']['attributes']
        if old.get('simulation_seed') != new['simulation_seed'] or old.get('simulation_version') != RULE_VERSION or old.get('source_parent_asin') != new['source_parent_asin']:
            raise ValueError('已有商品的来源或模拟配置不匹配；请使用独立测试库')
    fresh = [row for pid,row in unique.items() if pid not in existing]
    for row in fresh:
        if type(row['price_minor']) is not int or row['price_minor'] <= 0:
            raise ValueError('模拟价格必须是正整数分')
    await insert_catalog_batch(db, fresh)
    await insert_seed_prices(db, fresh)
    await insert_seed_inventory(db, fresh)
    if fresh:
        await db.execute(update(Product).where(Product.id.in_([row['product']['id'] for row in fresh])).values(status='active'))
    return len(fresh)


def nonnegative(value: str) -> int:
    n = int(value)
    if n < 0: raise argparse.ArgumentTypeError('不能为负数')
    return n


def batch_size(value: str) -> int:
    n = int(value)
    if not 1 <= n <= 1000: raise argparse.ArgumentTypeError('批量大小须为 1–1000')
    return n


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,default=ROOT/'data/amazon_reviews_2023')
    parser.add_argument('--categories',nargs='+',choices=CATEGORIES,default=list(CATEGORIES))
    parser.add_argument('--database-name',help='必须明确指定目标本机 MySQL 库；不创建或迁移数据库')
    parser.add_argument('--limit',type=nonnegative,default=100000,help='均衡处理的有效源记录上限，0 表示全量；跨类目去重后商品数可能更少')
    parser.add_argument('--batch-size',type=batch_size,default=500)
    parser.add_argument('--seed',default='42',help='固定生成种子，默认 42')
    parser.add_argument('--checkpoint',type=Path,help='断点文件，默认按目标数据库生成独立文件名')
    parser.add_argument('--dry-run',action='store_true',help='只读取和统计，绝不连接数据库或保存断点')
    return parser.parse_args(argv)


def atomic_save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(state,stream,ensure_ascii=False,indent=2); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


async def import_files(args: argparse.Namespace, factory: async_sessionmaker[AsyncSession] | None, target: str) -> dict:
    source = args.source_dir.expanduser().resolve()
    manifest = json.loads((source/'download_manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') != 'complete' or manifest.get('repo_id') != REPO_ID:
        raise ValueError('下载记录不是该数据集的完整状态，请先完成下载')
    categories = list(dict.fromkeys(args.categories))
    paths = {category:source/f'raw/meta_categories/meta_{category}.jsonl' for category in categories}
    fingerprints = {}
    for category,path in paths.items():
        stat = path.stat()
        expected = manifest.get('files',{}).get(f'raw/meta_categories/meta_{category}.jsonl',{}).get('size_bytes')
        if stat.st_size != expected: raise ValueError(f'{path.name} 大小与下载记录不一致')
        fingerprints[category] = dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns)
    config = dict(version=RULE_VERSION,seed=args.seed,target=target,source=str(source),revision=manifest['revision'],files=fingerprints)
    checkpoint = args.checkpoint or ROOT/'data/import_state'/f'{hashlib.sha256(target.encode()).hexdigest()[:16]}.json'
    state = dict(config=config, files={}, inserted=0,valid=0,rejected=0,states={})
    if not args.dry_run and checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding='utf-8'))
        if state.get('config') != config:
            raise ValueError('断点与数据源、种子、类别或目标数据库不匹配；不要复用其他任务的断点')
    if not args.dry_run:
        if factory is None: raise ValueError('写入模式缺少数据库连接')
        async with factory() as db, db.begin():
            await ensure_categories(db,categories)
    for index,category in enumerate(categories):
        quota = args.limit//len(categories) + (index < args.limit%len(categories)) if args.limit else None
        stats = state['files'].setdefault(category,dict(offset=0,valid=0,rejected=0,eof=False))
        if stats['eof'] or (quota is not None and stats['valid'] >= quota): continue
        path = paths[category]
        with path.open('rb') as stream, tqdm(total=quota if quota is not None else path.stat().st_size,
                initial=stats['valid'] if quota is not None else stats['offset'],
                desc=CATEGORY_NAMES[category],unit='条' if quota is not None else 'B',unit_scale=quota is None) as bar:
            stream.seek(stats['offset'])
            reached_eof = False
            while not reached_eof and (quota is None or stats['valid'] < quota):
                rows = []; valid = rejected = 0
                before = stream.tell()
                # 无效记录也占批量窗口，避免异常文件导致无界读取。
                for _ in range(args.batch_size):
                    if quota is not None and stats['valid']+valid >= quota: break
                    line = stream.readline()
                    if not line:
                        reached_eof=True; break
                    try:
                        row = normalize(json.loads(line),category,args.seed,manifest['revision'])
                    except (ValueError,TypeError,UnicodeDecodeError):
                        rejected+=1; continue
                    valid+=1; rows.append(row)
                inserted = 0
                if not args.dry_run and rows:
                    async with factory() as db, db.begin():
                        inserted = await write_batch(db,rows)
                # 只有提交成功后推进断点；崩溃后旧批重读时由稳定 ID 去重。
                stats.update(offset=stream.tell(),valid=stats['valid']+valid,rejected=stats['rejected']+rejected,eof=reached_eof)
                state['valid']+=valid; state['rejected']+=rejected; state['inserted']+=inserted
                for row in rows:
                    key=row['product']['attributes']['simulation_inventory_state']
                    state['states'][key]=state['states'].get(key,0)+1
                if not args.dry_run: atomic_save(checkpoint,state)
                bar.update(valid if quota is not None else stream.tell()-before)
                bar.set_postfix(写入=state['inserted'],跳过异常=state['rejected'],refresh=False)
    summary = {key:state[key] for key in ['valid','inserted','rejected','states']}
    if not args.dry_run:
        async with factory() as db:
            summary['database_products'] = await db.scalar(select(func.count()).select_from(Product).where(
                Product.category_id.in_([stable_id('category:'+category) for category in categories]),
                Product.product_code.like('AMZ-%'),
                Product.attributes['simulation_seed'].as_string()==args.seed,
                Product.attributes['simulation_version'].as_string()==RULE_VERSION))
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    print('预览完成，未连接数据库。' if args.dry_run else f'导入完成；断点：{checkpoint}',flush=True)
    return summary


@asynccontextmanager
async def import_lock(engine: AsyncEngine, database: str) -> AsyncIterator[None]:
    name = 'amazon-import:'+hashlib.sha256(database.encode()).hexdigest()[:40]
    async with engine.connect() as connection:
        acquired = await connection.scalar(text('SELECT GET_LOCK(:name,0)'),dict(name=name))
        if acquired != 1: raise ValueError('同一个数据库已有导入任务，请勿同时启动')
        try: yield
        finally: await connection.execute(text('SELECT RELEASE_LOCK(:name)'),dict(name=name))


async def run(args: argparse.Namespace) -> dict:
    if args.dry_run: return await import_files(args,None,'preview')
    if not args.database_name or not re.fullmatch(r'[a-zA-Z0-9_]+',args.database_name):
        raise ValueError('写入前须用 --database-name 明确指定目标库名')
    from config import get_settings
    from db.session import create_session_factory
    url = make_url(get_settings().database_url)
    if url.drivername != 'mysql+asyncmy' or url.host not in ['localhost','127.0.0.1','::1']:
        raise ValueError('此测试数据脚本仅支持配置中的本机 mysql+asyncmy 连接')
    url = url.set(database=args.database_name)
    target = f'{url.host}:{url.port or 3306}/{url.database}'
    factory = create_session_factory(url.render_as_string(hide_password=False))
    engine = factory.kw['bind']
    print(f'目标数据库：{target}；规则：{RULE_VERSION}；种子：{args.seed}',flush=True)
    try:
        async with import_lock(engine,args.database_name):
            async with engine.connect() as connection:
                tables = set(await connection.scalars(text('SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE()')))
                required={'categories','category_attributes','products','skus','product_media','sku_prices','sku_price_changes','stock_locations','inventory_balances','inventory_movements'}
                if not required<=tables: raise ValueError('目标库缺少目录、价格或库存表，请先执行已有迁移')
            async with factory() as db:
                location=await db.get(StockLocation,DEFAULT_LOCATION_ID)
                if not location or location.status!='active': raise ValueError('默认库位不存在或已停用')
            return await import_files(args,factory,target)
    finally: await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try: asyncio.run(run(args))
    except KeyboardInterrupt:
        print('\n已中断；重跑同一命令可继续，未提交的批次会回滚。',file=sys.stderr); return 130
    except (ValueError,OSError) as error:
        print(f'导入未完成：{error}',file=sys.stderr); return 1
    except Exception as error:
        # 不输出完整数据库 URL、SQL 参数或巨大源记录。
        print(f'导入未完成（{type(error).__name__}）；请检查连接和迁移后重跑原命令。',file=sys.stderr); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

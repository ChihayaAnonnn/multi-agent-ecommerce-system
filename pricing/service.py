"""价格领域服务：读取现行价格、按版本写入新价格。

规则要点：
- 金额只接受整数分或精确 Decimal，拒绝浮点数与负数；数据库只存 amount_minor。
- 首次写价 expected_version 必须为空，之后必须匹配现行版本，防止覆盖他人修改。
- 价格更新与变更流水在同一事务内提交；版本冲突时不写任何历史。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from catalog.ports import SKUExistenceReader
from catalog.service import CatalogNotFoundError
from pricing.models import CURRENCY, SKUPrice, SKUPriceChange
from pricing.schemas import PriceView
from shared.errors import DomainError
from shared.ids import new_id
from shared.time import utcnow


class PricingError(DomainError):
    """价格领域异常基类。"""


class PricingNotFoundError(PricingError):
    """引用的 SKU 不存在。"""


class PricingConflictError(PricingError):
    """版本冲突或写价前置条件不满足。"""


class PricingValidationError(PricingError):
    """金额或币种不合法。"""


class PricingDependencyError(PricingError):
    """价格后端不可用；出现该错误时不得把 SKU 当作有价。"""


def to_minor_units(amount: int | Decimal) -> int:
    """把整数分或精确 Decimal 金额转换为整数分。

    拒绝 float、bool、超过两位小数的 Decimal 与负数。
    """
    if isinstance(amount, bool):
        raise PricingValidationError("金额不接受布尔值")
    if isinstance(amount, int):
        minor = amount
    elif isinstance(amount, Decimal):
        if amount != amount.quantize(Decimal("0.01")):
            raise PricingValidationError("Decimal 金额最多保留两位小数")
        minor = int((amount * 100).to_integral_value())
    else:
        raise PricingValidationError(
            f"金额必须是整数分或精确 Decimal，不接受 {type(amount).__name__}"
        )
    if minor < 0:
        raise PricingValidationError("金额不能为负数")
    return minor


class PricingService:
    """SKU 价格读写服务；同时满足目录模块的只读价格边界。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sku_reader: SKUExistenceReader,
    ) -> None:
        self._session_factory = session_factory
        self._sku_reader = sku_reader

    async def get_prices(self, sku_ids: list[str]) -> dict[str, PriceView]:
        """返回有现行 CNY 价格的 SKU；无价 SKU 不会出现在结果里。

        查询失败抛出依赖错误，调用方不得据此认为无价或可购买。
        """
        if not sku_ids:
            return {}
        read_at = utcnow()
        try:
            async with self._session_factory() as session:
                rows = await session.scalars(
                    select(SKUPrice).where(
                        SKUPrice.sku_id.in_(sku_ids), SKUPrice.currency == CURRENCY
                    )
                )
                return {
                    row.sku_id: PriceView(
                        sku_id=row.sku_id,
                        currency=row.currency,
                        amount_minor=row.amount_minor,
                        version=row.version,
                        effective_at=row.effective_at,
                        read_at=read_at,
                    )
                    for row in rows
                }
        except SQLAlchemyError as exc:
            raise PricingDependencyError("价格查询失败") from exc

    async def lock_prices(self, db: AsyncSession, sku_ids: list[str]) -> dict[str, PriceView]:
        """在调用方事务内锁定 CNY 现价，不提交。"""
        if not sku_ids:
            return {}
        rows = await db.scalars(select(SKUPrice).where(SKUPrice.sku_id.in_(sku_ids),
            SKUPrice.currency == CURRENCY).order_by(SKUPrice.sku_id).with_for_update())
        now = utcnow()
        return {row.sku_id: PriceView(sku_id=row.sku_id, currency=row.currency,
            amount_minor=row.amount_minor, version=row.version, effective_at=row.effective_at, read_at=now) for row in rows}

    async def has_current_prices(self, sku_ids: list[str]) -> bool:
        """目录上架使用的只读边界：每个 SKU 都必须有现行 CNY 价格。

        查询失败抛出依赖错误，不会返回 False 让上架误以为「只是缺价」。
        """
        if not sku_ids:
            return True
        try:
            async with self._session_factory() as session:
                found = set(
                    await session.scalars(
                        select(SKUPrice.sku_id).where(
                            SKUPrice.sku_id.in_(sku_ids), SKUPrice.currency == CURRENCY
                        )
                    )
                )
        except SQLAlchemyError as exc:
            raise PricingDependencyError("价格查询失败") from exc
        return found == set(sku_ids)

    async def set_price(
        self,
        sku_id: str,
        amount_minor: int | Decimal,
        expected_version: int | None,
        reason: str,
    ) -> PriceView:
        """写入新价格并记录变更流水。

        首次写价 expected_version 为 None；之后必须与现行版本一致。
        """
        minor = to_minor_units(amount_minor)
        now = utcnow()

        async with self._session_factory() as session:
            await self._require_sku(session, sku_id)
            current = await session.get(SKUPrice, (sku_id, CURRENCY))

            if current is None:
                if expected_version is not None:
                    raise PricingConflictError(
                        "该 SKU 尚无价格，首次写价的 expected_version 必须为空"
                    )
                session.add(
                    SKUPrice(
                        sku_id=sku_id,
                        currency=CURRENCY,
                        amount_minor=minor,
                        version=1,
                        effective_at=now,
                    )
                )
                session.add(
                    SKUPriceChange(
                        id=new_id(),
                        sku_id=sku_id,
                        currency=CURRENCY,
                        old_amount_minor=None,
                        new_amount_minor=minor,
                        price_version=1,
                        reason=reason,
                        changed_at=now,
                    )
                )
                await self._commit(session, f"价格写入冲突: {sku_id}")
                return PriceView(
                    sku_id=sku_id,
                    currency=CURRENCY,
                    amount_minor=minor,
                    version=1,
                    effective_at=now,
                    read_at=now,
                )

            if expected_version is None:
                raise PricingConflictError(
                    "该 SKU 已有价格，必须提供 expected_version"
                )
            if expected_version != current.version:
                raise PricingConflictError(
                    f"价格版本已变化：期望 {expected_version}，现行 {current.version}"
                )
            old_amount = current.amount_minor

            # 条件更新：持有相同旧版本的并发写入只能有一个成功。
            result = await session.execute(
                update(SKUPrice)
                .where(
                    SKUPrice.sku_id == sku_id,
                    SKUPrice.currency == CURRENCY,
                    SKUPrice.version == expected_version,
                )
                .values(
                    amount_minor=minor,
                    version=SKUPrice.version + 1,
                    effective_at=now,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                raise PricingConflictError(
                    f"价格版本已变化：期望 {expected_version}，请重新读取后再写入"
                )

            session.add(
                SKUPriceChange(
                    id=new_id(),
                    sku_id=sku_id,
                    currency=CURRENCY,
                    old_amount_minor=old_amount,
                    new_amount_minor=minor,
                    price_version=expected_version + 1,
                    reason=reason,
                    changed_at=now,
                )
            )
            await self._commit(session, f"价格版本写入冲突: {sku_id}")
            return PriceView(
                sku_id=sku_id,
                currency=CURRENCY,
                amount_minor=minor,
                version=expected_version + 1,
                effective_at=now,
                read_at=now,
            )

    # ---------- 内部工具 ----------

    async def _require_sku(self, session: AsyncSession, sku_id: str) -> None:
        try:
            await self._sku_reader.require_sku_exists(session, sku_id)
        except CatalogNotFoundError as exc:
            raise PricingNotFoundError(f"SKU 不存在: {sku_id}") from exc

    @staticmethod
    async def _commit(session: AsyncSession, conflict_message: str) -> None:
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise PricingConflictError(conflict_message) from exc


__all__ = [
    'PricingConflictError',
    'PricingDependencyError',
    'PricingError',
    'PricingNotFoundError',
    'PricingService',
    'PricingValidationError',
    'to_minor_units',
]

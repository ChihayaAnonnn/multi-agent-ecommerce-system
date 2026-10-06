"""库存领域服务：可售查询与原子库存调整。

规则要点：
- 可售量 = on_hand - reserved；第一阶段不提供预留写入，reserved 保持 0。
- 调整在同一事务内锁定 (sku_id, location_id) 余额行，校验后递增版本并写流水。
- 幂等键按请求字段核对：同键同载荷只记一次，同键不同载荷报冲突。
- 查询故障抛出依赖错误，调用方不得据此认为有货。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.catalog.ports import SKUExistenceReader
from modules.catalog.service import CatalogNotFoundError
from modules.inventory.models import (
    DEFAULT_LOCATION_ID,
    InventoryBalance,
    InventoryMovement,
    StockLocation,
)
from modules.inventory.schemas import AdjustmentView, AvailabilityView, BalanceView
from shared.errors import DomainError
from shared.ids import new_id
from shared.time import utcnow


class InventoryError(DomainError):
    """库存领域异常基类。"""


class InventoryNotFoundError(InventoryError):
    """引用的 SKU、库位或余额行不存在。"""


class InventoryConflictError(InventoryError):
    """幂等键冲突或状态不允许。"""


class InventoryValidationError(InventoryError):
    """调整量或其他输入不合法。"""


class InventoryDependencyError(InventoryError):
    """库存后端不可用；出现该错误时不得把 SKU 当作有货。"""


def to_balance_view(balance: InventoryBalance) -> BalanceView:
    """把余额行转换为视图，并算出售卖量。"""
    return BalanceView(
        sku_id=balance.sku_id,
        location_id=balance.location_id,
        on_hand=balance.on_hand,
        reserved=balance.reserved,
        available=balance.on_hand - balance.reserved,
        version=balance.version,
    )


def to_adjustment_view(
    balance: InventoryBalance, replayed: bool = False
) -> AdjustmentView:
    """把余额行转换为调整结果；`replayed` 只属于写路径。"""
    return AdjustmentView(
        **to_balance_view(balance).model_dump(), replayed=replayed
    )


class InventoryService:
    """库存读写服务。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sku_reader: SKUExistenceReader,
    ) -> None:
        self._session_factory = session_factory
        self._sku_reader = sku_reader

    # ---------- 只读查询 ----------

    async def get_availability(self, sku_ids: list[str]) -> dict[str, AvailabilityView]:
        """返回每个 SKU 的库位汇总可售状态。

        没有余额行的 SKU 返回 `unknown`，其数量与版本为空；查询失败抛出依赖错误。
        """
        if not sku_ids:
            return {}
        read_at = utcnow()
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.scalars(
                        select(InventoryBalance)
                        .where(InventoryBalance.sku_id.in_(sku_ids))
                        .order_by(InventoryBalance.sku_id, InventoryBalance.location_id)
                    )
                ).all()
        except SQLAlchemyError as exc:
            raise InventoryDependencyError("库存查询失败") from exc

        grouped: dict[str, list[InventoryBalance]] = {}
        for row in rows:
            grouped.setdefault(row.sku_id, []).append(row)

        result: dict[str, AvailabilityView] = {}
        for sku_id in sku_ids:
            balances = grouped.get(sku_id, [])
            if not balances:
                result[sku_id] = AvailabilityView(
                    sku_id=sku_id, status="unknown", read_at=read_at
                )
                continue
            available = sum(item.on_hand - item.reserved for item in balances)
            result[sku_id] = AvailabilityView(
                sku_id=sku_id,
                status="available" if available > 0 else "out_of_stock",
                available_quantity=available,
                version=max(item.version for item in balances),
                read_at=read_at,
                locations=[to_balance_view(item) for item in balances],
            )
        return result

    async def lock_availability(self, db: AsyncSession, sku_ids: list[str]) -> dict[str, AvailabilityView]:
        """按 SKU/库位顺序锁余额，在调用方事务内汇总，不提交。"""
        if not sku_ids:
            return {}
        rows = list(await db.scalars(select(InventoryBalance).where(InventoryBalance.sku_id.in_(sku_ids))
            .order_by(InventoryBalance.sku_id, InventoryBalance.location_id).with_for_update()))
        result = {}
        now = utcnow()
        for sku_id in sku_ids:
            balances = [row for row in rows if row.sku_id == sku_id]
            quantity = sum(row.on_hand - row.reserved for row in balances) if balances else None
            result[sku_id] = AvailabilityView(sku_id=sku_id,
                status='unknown' if quantity is None else 'available' if quantity > 0 else 'out_of_stock',
                available_quantity=quantity, version=max((row.version for row in balances), default=None),
                read_at=now, locations=[to_balance_view(row) for row in balances])
        return result

    # ---------- 建档 ----------

    async def create_balance(
        self, sku_id: str, location_id: str = DEFAULT_LOCATION_ID
    ) -> BalanceView:
        """为 SKU 与库位建立零余额记录（on_hand = reserved = 0）。"""
        async with self._session_factory() as session:
            await self._require_sku(session, sku_id)
            location = await session.get(StockLocation, location_id)
            if location is None:
                raise InventoryNotFoundError(f"库位不存在: {location_id}")

            balance = InventoryBalance(
                sku_id=sku_id, location_id=location_id, on_hand=0, reserved=0
            )
            session.add(balance)
            await self._commit(session, f"余额记录已存在: {sku_id} @ {location_id}")
            await session.refresh(balance)
            return to_balance_view(balance)

    # ---------- 调整 ----------

    async def adjust(
        self,
        sku_id: str,
        location_id: str,
        delta: int,
        idempotency_key: str,
        reason: str,
    ) -> AdjustmentView:
        """在指定库位原子调整库存，并记录流水。

        重复请求（同一幂等键且载荷一致）不会再次加减，返回当前余额并标记 `replayed`。
        """
        self._validate_delta(delta)

        async with self._session_factory() as session:
            existing = await session.scalar(
                select(InventoryMovement).where(
                    InventoryMovement.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                return await self._replay(session, existing, sku_id, location_id, delta)

            balance = await session.scalar(
                select(InventoryBalance)
                .where(
                    InventoryBalance.sku_id == sku_id,
                    InventoryBalance.location_id == location_id,
                )
                .with_for_update()
            )
            if balance is None:
                raise InventoryNotFoundError(
                    f"余额记录不存在，请先建档: {sku_id} @ {location_id}"
                )

            new_on_hand = balance.on_hand + delta
            if new_on_hand < 0:
                raise InventoryValidationError(
                    f"库存不足：当前 {balance.on_hand}，本次调整 {delta}"
                )
            if new_on_hand < balance.reserved:
                raise InventoryValidationError(
                    f"调整后数量低于已预留量：{new_on_hand} < {balance.reserved}"
                )

            new_version = balance.version + 1
            balance.on_hand = new_on_hand
            balance.version = new_version
            session.add(
                InventoryMovement(
                    id=new_id(),
                    sku_id=sku_id,
                    location_id=location_id,
                    delta=delta,
                    reason=reason,
                    idempotency_key=idempotency_key,
                    balance_version=new_version,
                    occurred_at=utcnow(),
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                # 并发请求可能刚写入了同一个幂等键：按同键处理，不能重复加减
                return await self._handle_idempotency_race(
                    sku_id, location_id, delta, idempotency_key
                )

            await session.refresh(balance)
            return to_adjustment_view(balance)

    # ---------- 内部工具 ----------

    @staticmethod
    def _validate_delta(delta: int) -> None:
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise InventoryValidationError(
                f"调整量必须是整数，不接受 {type(delta).__name__}"
            )
        if delta == 0:
            raise InventoryValidationError("调整量不能为 0")

    async def _replay(
        self,
        session: AsyncSession,
        movement: InventoryMovement,
        sku_id: str,
        location_id: str,
        delta: int,
    ) -> AdjustmentView:
        """同一幂等键重复请求：载荷一致则返回当前余额并标记为重复，不一致则冲突。

        流水里没有记录「调整后的余额」，因此这里无法重建当初那次的结果；
        与其把当前余额冒充成原结果，不如用 `replayed=True` 明确标成重复请求。
        """
        if (
            movement.sku_id != sku_id
            or movement.location_id != location_id
            or movement.delta != delta
        ):
            raise InventoryConflictError(
                "幂等键已被用于不同的库存调整，拒绝返回其他请求的结果"
            )
        balance = await session.scalar(
            select(InventoryBalance).where(
                InventoryBalance.sku_id == sku_id,
                InventoryBalance.location_id == location_id,
            )
        )
        if balance is None:
            raise InventoryNotFoundError(f"余额记录不存在: {sku_id} @ {location_id}")
        return to_adjustment_view(balance, replayed=True)

    async def _handle_idempotency_race(
        self, sku_id: str, location_id: str, delta: int, idempotency_key: str
    ) -> AdjustmentView:
        async with self._session_factory() as session:
            movement = await session.scalar(
                select(InventoryMovement).where(
                    InventoryMovement.idempotency_key == idempotency_key
                )
            )
            if movement is None:
                raise InventoryConflictError("库存调整写入冲突，请重试")
            return await self._replay(session, movement, sku_id, location_id, delta)

    async def _require_sku(self, session: AsyncSession, sku_id: str) -> None:
        try:
            await self._sku_reader.require_sku_exists(session, sku_id)
        except CatalogNotFoundError as exc:
            raise InventoryNotFoundError(f"SKU 不存在: {sku_id}") from exc

    @staticmethod
    async def _commit(session: AsyncSession, conflict_message: str) -> None:
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise InventoryConflictError(conflict_message) from exc


__all__ = [
    'InventoryConflictError',
    'InventoryDependencyError',
    'InventoryError',
    'InventoryNotFoundError',
    'InventoryService',
    'InventoryValidationError',
    'to_adjustment_view',
    'to_balance_view',
]

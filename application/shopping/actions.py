"""应用层拥有会话、轮次、展示来源和购物车的共同事务。"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kernel.errors import CommerceError
from kernel.intent import ShoppingIntent
from modules.cart.schemas import (
    ActionProposalCommand,
    CartConfirmationView,
    PendingActionView,
)
from modules.cart.service import CartService
from modules.checkout.schemas import CheckoutPreview, HandoffResult
from modules.checkout.service import CheckoutService
from modules.sessions.schemas import ShoppingSessionView, TurnClaim
from modules.sessions.service import ShoppingSessionService
from shared.hashing import JsonObject, request_hash
from shared.principal import Principal


class CartActionService:
    def __init__(
        self, factory: async_sessionmaker[AsyncSession],
        sessions: ShoppingSessionService, cart: CartService,
        checkout: CheckoutService | None,
    ) -> None:
        self.factory, self.sessions = factory, sessions
        self.cart, self.checkout = cart, checkout

    async def _lock_session(
        self, db: AsyncSession, principal: Principal, session_id: str,
        source_turn: TurnClaim | None,
    ) -> ShoppingSessionView:
        session = await self.sessions.lock_owned_session(
            db, session_id, principal.principal_key
        )
        if source_turn is not None:
            if (
                source_turn.session_id != session_id
                or source_turn.principal_key != principal.principal_key
            ):
                raise CommerceError('turn_lease_lost')
            await self.sessions.assert_current_turn(db, source_turn)
        return session

    async def propose_action(
        self, principal: Principal, session_id: str, kind: str,
        target: JsonObject, expected_cart_version: int,
        source_turn: TurnClaim | None = None,
    ) -> PendingActionView:
        target = self.cart.validate_target(kind, target, self.cart.max_quantity)
        command = ActionProposalCommand(
            kind=kind, target=target, expected_cart_version=expected_cart_version,
            source_turn_id=source_turn.turn_id if source_turn else None,
        )
        async with self.factory() as db, db.begin():
            session = await self._lock_session(db, principal, session_id, source_turn)
            cart = await self.cart.lock_cart_snapshot(db, principal)
            existing = await self.cart.find_source_action_in_transaction(
                db, principal, session_id, command.source_turn_id,
                request_hash({
                    'kind': kind, 'target': target,
                    'cart_version': expected_cart_version,
                }),
            )
            if existing is not None:
                return self.cart.action_view(existing)
            if cart.version != expected_cart_version:
                raise CommerceError(
                    "cart_version_conflict", {"version": cart.version}
                )
            if kind == 'add' and not await self.sessions.has_seen_sku(
                db, session_id, target['sku_id']
            ):
                raise CommerceError('not_seen')
            intent = ShoppingIntent.model_validate(
                source_turn.state if source_turn else session.state
            )
            return await self.cart.propose_action_in_transaction(
                db, principal, session_id, command, intent
            )

    async def confirm(
        self, principal: Principal, session_id: str, action_id: str,
        approved: bool, idempotency_key: str,
        source_turn: TurnClaim | None = None,
    ) -> CartConfirmationView:
        async with self.factory() as db, db.begin():
            await self._lock_session(db, principal, session_id, source_turn)
            return await self.cart.confirm_in_transaction(
                db, principal, session_id, action_id, approved, idempotency_key
            )

    async def get_action(
        self, principal: Principal, session_id: str, action_id: str,
    ) -> PendingActionView:
        await self.sessions.get_owned(session_id, principal)
        return await self.cart.get_action(principal, session_id, action_id)

    def _require_checkout(self) -> CheckoutService:
        if self.checkout is None:
            raise CommerceError('checkout_unavailable')
        return self.checkout

    async def preview_checkout(
        self, principal: Principal, session_id: str, cart_version: int,
        source_turn: TurnClaim | None = None,
    ) -> CheckoutPreview:
        checkout = self._require_checkout()
        async with self.factory() as db, db.begin():
            await self._lock_session(db, principal, session_id, source_turn)
            return await checkout.preview_in_transaction(
                db, principal, session_id, cart_version,
                source_turn.turn_id if source_turn else None,
            )

    async def confirm_handoff(
        self, principal: Principal, session_id: str, action_id: str,
        approved: bool, idempotency_key: str,
        source_turn: TurnClaim | None = None,
    ) -> HandoffResult:
        checkout = self._require_checkout()
        async with self.factory() as db, db.begin():
            await self._lock_session(db, principal, session_id, source_turn)
            prepared = await checkout.prepare_handoff_in_transaction(
                db, principal, session_id, action_id, approved, idempotency_key
            )
        if isinstance(prepared, HandoffResult):
            return prepared
        destination = await checkout.request_destination(prepared)
        async with self.factory() as db, db.begin():
            return await checkout.finalize_handoff_in_transaction(
                db, principal, prepared, destination
            )

    async def get_handoff(
        self, principal: Principal, session_id: str, handoff_id: str,
    ) -> HandoffResult:
        checkout = self._require_checkout()
        await self.sessions.get_owned(session_id, principal)
        async with self.factory() as db:
            return await checkout.get_owned_in_transaction(
                db, principal, session_id, handoff_id
            )

    async def is_only_pending(
        self, principal: Principal, session_id: str, action_id: str,
    ) -> bool:
        await self.sessions.get_owned(session_id, principal)
        return await self.cart.is_only_pending(principal, session_id, action_id)

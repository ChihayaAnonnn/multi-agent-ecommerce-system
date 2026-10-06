"""调用方提交 preparing，锁外请求宿主，再由独立事务收尾。"""

import asyncio
import re
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kernel.errors import CommerceError
from modules.cart.schemas import CartSnapshot, PendingActionRecord
from modules.cart.service import CartService
from modules.checkout.models import CheckoutHandoff
from modules.checkout.ports import HostCheckoutGateway
from modules.checkout.schemas import (
    CheckoutPreview,
    Destination,
    HandoffAttempt,
    HandoffResult,
    HandoffSnapshot,
)
from shared.hashing import JsonObject, request_hash
from shared.ids import new_id
from shared.principal import Principal
from shared.time import utcnow


def validate_destination(
    destination: Destination, allowed_hosts: tuple[str, ...] = (),
    allowed_path: str = '/checkout/',
) -> Destination:
    if destination.kind == 'internal':
        if not destination.demo or not re.fullmatch(
            r'/demo/commerce/handoffs/[0-9a-f-]{36}', destination.ref,
        ):
            raise ValueError('非法内部交接路由')
    else:
        url = urlsplit(destination.ref)
        if (
            url.scheme != 'https' or url.hostname not in allowed_hosts
            or url.port not in (None, 443) or url.username or url.password
            or url.query or url.fragment or not url.path.startswith(allowed_path)
            or '%' in url.path or '..' in url.path or '\\' in url.path
        ):
            raise ValueError('非法宿主交接目的地')
    return destination


class CheckoutService:
    def __init__(
        self, factory: async_sessionmaker[AsyncSession],
        cart: CartService,
        gateway: HostCheckoutGateway, allowed_hosts: tuple[str, ...] = (),
        allowed_path: str = '/checkout/', timeout_seconds: float = 5.0,
    ) -> None:
        self.factory = factory
        self.cart, self.gateway = cart, gateway
        self.allowed_hosts, self.allowed_path = allowed_hosts, allowed_path
        self.timeout_seconds = timeout_seconds

    async def cart_snapshot(
        self, db: AsyncSession, cart: CartSnapshot
    ) -> tuple[JsonObject, list[JsonObject]]:
        quotes, sales = await self.cart.lock_quotes(
            db, [line.sku_id for line in cart.lines]
        )
        items, errors, subtotal = [], [], 0
        if not cart.lines:
            errors.append({'code': 'empty_cart'})
        for line in cart.lines:
            quote = quotes[line.sku_id]
            item = self.cart.quote_snapshot(quote, sales.get(line.sku_id))
            item['quantity'] = line.quantity
            items.append(item)
            if quote.price is None:
                subtotal = None
            elif subtotal is not None:
                subtotal += line.quantity * quote.price.amount_minor
            try:
                self.cart.assert_buyable(quote, line.quantity)
            except CommerceError as exc:
                errors.append({'sku_id': line.sku_id, 'code': exc.code})
        return {
            'items': items, 'current_subtotal': subtotal,
            'currency': 'CNY', 'cart_version': cart.version,
        }, errors

    @staticmethod
    def preview_view(action: PendingActionRecord) -> CheckoutPreview:
        return CheckoutPreview(
            action_id=action.action_id, checkout_eligible=True,
            checked_at=action.created_at, expires_at=action.expires_at,
            **action.summary,
        )

    async def preview_in_transaction(
        self, db: AsyncSession, principal: Principal, session_id: str,
        cart_version: int, source_turn_id: str | None = None,
    ) -> CheckoutPreview:
        digest = request_hash(
            {'kind': 'checkout_handoff', 'cart_version': cart_version}
        )
        cart = await self.cart.lock_cart_snapshot(db, principal)
        existing = await self.cart.find_source_action_in_transaction(
            db, principal, session_id, source_turn_id, digest
        )
        if existing:
            return self.preview_view(existing)
        if cart.version != cart_version:
            raise CommerceError('cart_version_conflict', {'version': cart.version})
        snapshot, errors = await self.cart_snapshot(db, cart)
        if errors:
            return CheckoutPreview(checked_at=utcnow(), errors=errors, **snapshot)
        action = await self.cart.create_pending_action_in_transaction(
            db, principal, session_id, cart, 'checkout_handoff',
            {'cart_version': cart_version}, snapshot, digest, source_turn_id,
        )
        return self.preview_view(action)

    @staticmethod
    def result(row: CheckoutHandoff, replayed: bool = False) -> HandoffResult:
        if row.expires_at <= utcnow():
            return HandoffResult(
                status='unavailable', handoff_id=row.id, error_code='handoff_expired'
            )
        return HandoffResult(
            status='handoff' if row.status == 'ready' else 'unavailable',
            handoff_id=row.id,
            destination=Destination(
                kind=row.destination_kind, ref=row.destination_ref,
                demo=row.destination_demo,
            ) if row.status == 'ready' else None,
            expires_at=row.expires_at,
            error_code=None if row.status == 'ready' else 'checkout_unavailable',
            retryable=row.status in {'preparing', 'failed'}, replayed=replayed,
        )

    async def get_owned_in_transaction(
        self, db: AsyncSession, principal: Principal, session_id: str, handoff_id: str
    ) -> HandoffResult:
        row = await db.scalar(select(CheckoutHandoff).where(
            CheckoutHandoff.id == handoff_id,
            CheckoutHandoff.principal_key == principal.principal_key,
        ))
        if row is None:
            raise CommerceError('not_found')
        owner = await self.cart.find_action_owner(db, principal, row.action_id)
        if owner != session_id:
            raise CommerceError('not_found')
        return self.result(row)

    async def prepare_handoff_in_transaction(
        self, db: AsyncSession, principal: Principal, session_id: str,
        action_id: str, approved: bool, idempotency_key: str,
    ) -> HandoffAttempt | HandoffResult:
        cart = await self.cart.lock_cart_snapshot(db, principal)
        action = await self.cart.lock_pending_action(
            db, principal, session_id, action_id
        )
        if action.kind != 'checkout_handoff':
            raise CommerceError('invalid_action')
        replay = await self.cart.confirmation_replay_in_transaction(
            db, principal, action, approved, idempotency_key
        )
        row = await db.scalar(select(CheckoutHandoff).where(
            CheckoutHandoff.action_id == action.action_id
        ).with_for_update())
        if replay is not None and row is None:
            return HandoffResult.model_validate(dict(replay, replayed=True))
        if row:
            if row.status == 'ready' or row.expires_at <= utcnow():
                return self.result(row, True)
        elif not approved:
            result = HandoffResult(status='declined')
            await self.cart.resolve_action_in_transaction(
                db, principal, session_id, action_id, 'declined', approved,
                idempotency_key, result.model_dump(mode='json'),
            )
            return result
        else:
            if not self.gateway.available:
                raise CommerceError('checkout_unavailable')
            if cart.version != action.expected_cart_version:
                raise CommerceError(
                    'cart_version_conflict', {'version': cart.version}
                )
            snapshot, errors = await self.cart_snapshot(db, cart)
            if errors or snapshot != action.summary:
                await self.cart.invalidate_action_in_transaction(
                    db, principal, session_id, action_id
                )
                replacement = None
                if not errors:
                    replacement = self.preview_view(
                        await self.cart.create_pending_action_in_transaction(
                            db, principal, session_id, cart, 'checkout_handoff',
                            {'cart_version': cart.version}, snapshot,
                            request_hash({
                                'kind': 'checkout_handoff', 'cart_version': cart.version
                            }),
                        )
                    )
                result = HandoffResult(
                    status='confirm_action', error_code='reconfirmation_required',
                    preview=replacement,
                )
                await self.cart.resolve_action_in_transaction(
                    db, principal, session_id, action_id, 'invalidated', approved,
                    idempotency_key, result.model_dump(mode='json'),
                )
                return result
            handoff_id = new_id()
            expiry = utcnow() + timedelta(seconds=self.cart.action_ttl)
            immutable = HandoffSnapshot(
                handoff_id=handoff_id, principal_key=principal.principal_key,
                issuer=principal.issuer, subject=principal.subject,
                cart_id=cart.cart_id, expires_at=expiry, **snapshot,
            )
            payload = immutable.model_dump(mode='json')
            row = CheckoutHandoff(
                id=handoff_id, principal_key=principal.principal_key,
                cart_id=cart.cart_id, cart_version=cart.version,
                action_id=action.action_id, snapshot_json=payload,
                snapshot_hash=request_hash(payload), status='preparing',
                expires_at=expiry, destination_demo=False,
            )
            db.add(row)
            await self.cart.resolve_action_in_transaction(
                db, principal, session_id, action_id, 'consumed', approved,
                idempotency_key, HandoffResult(
                    status='unavailable', handoff_id=handoff_id, retryable=True
                ).model_dump(mode='json'),
            )
        return HandoffAttempt(
            snapshot=HandoffSnapshot.model_validate(row.snapshot_json),
            replayed=replay is not None,
        ).model_copy(deep=True)

    async def request_destination(self, attempt: HandoffAttempt) -> Destination | None:
        try:
            destination = await asyncio.wait_for(
                self.gateway.create_handoff(
                    attempt.snapshot, attempt.snapshot.handoff_id
                ),
                self.timeout_seconds,
            )
            return validate_destination(
                Destination.model_validate(destination),
                self.allowed_hosts, self.allowed_path,
            )
        except Exception:
            return None

    async def finalize_handoff_in_transaction(
        self, db: AsyncSession, principal: Principal,
        attempt: HandoffAttempt, destination: Destination | None,
    ) -> HandoffResult:
        row = await db.scalar(select(CheckoutHandoff).where(
            CheckoutHandoff.id == attempt.snapshot.handoff_id,
            CheckoutHandoff.principal_key == principal.principal_key,
        ).with_for_update())
        if row is None:
            raise CommerceError('not_found')
        if row.status != 'ready':
            if destination is None:
                row.status = 'failed'
            elif row.expires_at > utcnow():
                row.status = 'ready'
                row.destination_kind = destination.kind
                row.destination_ref = destination.ref
                row.destination_demo = destination.demo
            else:
                row.status = 'expired'
        return self.result(row, attempt.replayed)


__all__ = [
    'CheckoutService',
    'validate_destination',
]

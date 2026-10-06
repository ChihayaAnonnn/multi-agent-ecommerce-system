"""应用层门禁和购物车写入共用真实 MySQL 事务。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, update

from ecom.api.routes.auth import require_principal
from ecom.api.routes.commerce import router
from ecom.modules.cart.models import PendingAction
from ecom.modules.cart.schemas import PendingActionView
from ecom.modules.cart.service import CartService
from ecom.modules.sale_facts.service import SaleFactsReader
from ecom.modules.sessions.models import CommerceSession, SeenSKU
from ecom.modules.sessions.service import CommerceError, ShoppingSessionService
from ecom.shared.principal import make_principal
from ecom.shared.time import utcnow
from tests.integration.test_commerce_facts_mysql import seed


@dataclass
class CartActionCase:
    factory: object
    sessions: ShoppingSessionService
    cart: CartService
    actions: object
    principal: object
    foreign: object
    session_id: str
    foreign_session_id: str
    sku_id: str
    unseen_sku_id: str

    async def propose(self, source_turn=None, session_id=None, version=1):
        return await self.actions.propose_action(
            self.principal, session_id or self.session_id, 'add',
            {'sku_id': self.sku_id, 'quantity': 2}, version, source_turn,
        )

    async def confirm_four_times_same_key(self):
        proposal = await self.propose()
        return await asyncio.gather(*(
            self.actions.confirm(
                self.principal, self.session_id, proposal.action_id, True, 'same'
            ) for _ in range(4)
        ))

    async def read_cart(self):
        return await self.cart.get_cart(self.principal)

    async def action_count(self):
        async with self.factory() as db:
            return await db.scalar(select(func.count()).select_from(PendingAction))


@pytest.fixture
def cart_action_case(migrated_mysql_database):
    @asynccontextmanager
    async def scenario():
        try:
            actions_type = import_module(
                "ecom.application.shopping.actions"
            ).CartActionService
        except ModuleNotFoundError:
            pytest.fail("application CartActionService is not implemented")
        factory, catalog, prices, stock, _, products, skus = await seed(
            migrated_mysql_database
        )
        try:
            sessions = ShoppingSessionService(factory)
            principal = make_principal('test', 'cart-actions')
            foreign = make_principal('test', 'foreign')
            owned = await sessions.create(principal)
            other = await sessions.create(foreign)
            cart = CartService(factory, SaleFactsReader(catalog, prices, stock))
            await sessions.record_seen(
                owned.session_id, principal, [products[0].id], [skus[0].id]
            )
            yield CartActionCase(
                factory, sessions, cart, actions_type(factory, sessions, cart, None),
                principal, foreign, owned.session_id, other.session_id,
                skus[0].id, skus[1].id,
            )
        finally:
            await factory.kw['bind'].dispose()
    return scenario


def test_lease_takeover_blocks_proposal_and_confirmation(cart_action_case):
    async def scenario():
        async with cart_action_case() as case:
            claim = await case.sessions.claim_turn(
                case.session_id, case.principal, 'source', 'hash'
            )
            connections = {}

            def capture(connection, cursor, statement, parameters, context, many):
                for table in ('commerce_sessions', 'seen_skus', 'pending_actions'):
                    if table in statement:
                        connections.setdefault(table, set()).add(connection)

            engine = case.factory.kw['bind'].sync_engine
            event.listen(engine, 'before_cursor_execute', capture)
            try:
                proposal = await case.propose(claim)
            finally:
                event.remove(engine, 'before_cursor_execute', capture)
            assert set(connections) == {
                'commerce_sessions', 'seen_skus', 'pending_actions'
            }
            assert len(set.union(*connections.values())) == 1
            async with case.factory() as db, db.begin():
                await db.execute(update(CommerceSession).where(
                    CommerceSession.id == case.session_id
                ).values(turn_lease_expires_at=utcnow() - timedelta(seconds=1)))
            recovered = await case.sessions.claim_turn(
                case.session_id, case.principal, 'source', 'hash'
            )
            with pytest.raises(CommerceError, match='turn_lease_lost'):
                await case.propose(claim)
            with pytest.raises(CommerceError, match='turn_lease_lost'):
                await case.actions.confirm(
                    case.principal, case.session_id, proposal.action_id,
                    True, 'stale', claim,
                )
            assert (await case.read_cart()).version == 1
            assert not (await case.read_cart()).items
            assert await case.action_count() == 1
            action = await case.actions.get_action(
                case.principal, case.session_id, proposal.action_id
            )
            assert isinstance(action, PendingActionView)
            assert action.status == 'pending'
            assert (await case.propose(recovered)).action_id == proposal.action_id
    asyncio.run(scenario())


def test_direct_api_checks_session_without_source_turn(cart_action_case):
    async def scenario():
        async with cart_action_case() as case:
            with pytest.raises(CommerceError, match='not_found'):
                await case.propose(session_id=case.foreign_session_id)
            proposal = await case.propose()
            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[require_principal] = lambda: case.principal
            app.state.runtime = SimpleNamespace(cart_actions=case.actions)
            async with AsyncClient(
                transport=ASGITransport(app), base_url='https://test'
            ) as client:
                response = await client.post(
                    f'/api/v1/commerce/sessions/{case.foreign_session_id}'
                    f'/actions/{proposal.action_id}/confirm',
                    json={'approved': True, 'idempotency_key': 'foreign-api'},
                )
                assert response.status_code == 404
                assert response.json() == {'detail': {'code': 'not_found'}}
            for operation in ('confirm', 'get_action', 'is_only_pending'):
                arguments = [
                    case.principal,
                    case.foreign_session_id,
                    proposal.action_id,
                ]
                if operation == 'confirm':
                    arguments += [True, 'foreign']
                with pytest.raises(CommerceError, match='not_found'):
                    await getattr(case.actions, operation)(*arguments)
            async with case.factory() as db, db.begin():
                await db.execute(update(CommerceSession).where(
                    CommerceSession.id == case.session_id
                ).values(expires_at=utcnow() - timedelta(seconds=1)))
            with pytest.raises(CommerceError, match='session_expired'):
                await case.propose()
            with pytest.raises(CommerceError, match='session_expired'):
                await case.actions.confirm(
                    case.principal, case.session_id, proposal.action_id, True, 'expired'
                )
            assert not (await case.read_cart()).items
            assert await case.action_count() == 1
    asyncio.run(scenario())


def test_same_principal_sessions_share_cart_version(cart_action_case):
    async def scenario():
        async with cart_action_case() as case:
            other = await case.sessions.create(case.principal)
            await case.sessions.record_seen(
                other.session_id, case.principal, [], [case.sku_id]
            )
            first = await case.propose()
            stale = await case.propose(session_id=other.session_id)
            confirmed = await case.actions.confirm(
                case.principal, case.session_id, first.action_id, True, 'first'
            )
            assert confirmed.cart.version == 2
            with pytest.raises(CommerceError, match='cart_version_conflict'):
                await case.actions.confirm(
                    case.principal, other.session_id, stale.action_id, True, 'stale'
                )
            assert (await case.read_cart()).items[0].quantity == 2
    asyncio.run(scenario())


def test_committed_confirmation_retries_once(cart_action_case):
    async def scenario():
        async with cart_action_case() as case:
            replies = await case.confirm_four_times_same_key()
            view = await case.read_cart()
            assert all(reply.status == 'cart_updated' for reply in replies)
            assert sum(reply.replayed for reply in replies) == 3
            assert view.version == 2
            assert view.items[0].quantity == 2
            assert view.current_subtotal == 200
    asyncio.run(scenario())


def test_source_replay_and_version_conflict_precede_seen_check(cart_action_case):
    async def scenario():
        async with cart_action_case() as case:
            claim = await case.sessions.claim_turn(
                case.session_id, case.principal, 'source', 'hash'
            )
            proposal = await case.propose(claim)
            async with case.factory() as db, db.begin():
                row = await db.get(SeenSKU, (case.session_id, case.sku_id))
                await db.delete(row)
            assert (await case.propose(claim)).action_id == proposal.action_id
            with pytest.raises(CommerceError, match='cart_version_conflict'):
                await case.propose(version=99)
            with pytest.raises(CommerceError, match='not_seen'):
                await case.propose()
            assert await case.action_count() == 1
    asyncio.run(scenario())


def test_action_reads_do_not_lock_session_for_update(cart_action_case):
    async def scenario():
        async with cart_action_case() as case:
            proposal = await case.propose()
            reads = []

            def capture(connection, cursor, statement, parameters, context, many):
                if 'commerce_sessions' in statement:
                    reads.append(statement)

            engine = case.factory.kw['bind'].sync_engine
            event.listen(engine, 'before_cursor_execute', capture)
            try:
                action = await case.actions.get_action(
                    case.principal, case.session_id, proposal.action_id
                )
                assert await case.actions.is_only_pending(
                    case.principal, case.session_id, proposal.action_id
                )
            finally:
                event.remove(engine, 'before_cursor_execute', capture)
            assert action.action_id == proposal.action_id
            assert len(reads) == 2
            assert all('FOR UPDATE' not in statement.upper() for statement in reads)
    asyncio.run(scenario())

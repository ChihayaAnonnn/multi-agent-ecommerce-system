"""应用层结账门禁、提交边界与持久重放的真实 MySQL 证据。"""

import asyncio
import inspect
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, update

from ecom.api.routes.auth import require_principal
from ecom.api.routes.commerce import router
from ecom.application.shopping.actions import CartActionService
from ecom.infra.checkout_gateway import DemoHostCheckoutGateway
from ecom.kernel.errors import CommerceError
from ecom.modules.cart.models import PendingAction
from ecom.modules.checkout.models import CheckoutHandoff
from ecom.modules.checkout.service import CheckoutService
from ecom.modules.sessions.models import CommerceSession
from ecom.shared.time import utcnow
from tests.test_checkout_mysql import ready_cart


@pytest.fixture
def checkout_action_case(migrated_mysql_database):
    @asynccontextmanager
    async def scenario():
        state, view = await ready_cart(migrated_mysql_database)
        factory, sessions, cart, principal, foreign, owned, _, prices, _, skus = state
        transactions = set()
        case = SimpleNamespace(
            factory=factory, sessions=sessions, cart=cart, principal=principal,
            foreign=foreign, session_id=owned.session_id, cart_version=view.version,
            prices=prices, sku_id=skus[0].id, prepare_calls=0,
            gateway_observed_preparing=False, gateway_observed_consumed=False,
            open_transaction_at_gateway=None, ids=[], fail_once=False,
            expire_lease_at_gateway=False,
        )

        class Gateway(DemoHostCheckoutGateway):
            async def create_handoff(self, snapshot, request_id):
                case.ids.append(request_id)
                case.open_transaction_at_gateway = bool(transactions)
                async with factory() as db:
                    row = await db.get(CheckoutHandoff, request_id)
                    action = await db.get(PendingAction, row.action_id)
                    case.gateway_observed_preparing = row.status in {
                        'preparing', 'failed'
                    }
                    case.gateway_observed_consumed = action.status == 'consumed'
                if case.expire_lease_at_gateway:
                    async with factory() as db, db.begin():
                        await db.execute(update(CommerceSession).where(
                            CommerceSession.id == case.session_id
                        ).values(turn_lease_expires_at=utcnow() - timedelta(seconds=1)))
                if case.fail_once and len(case.ids) == 1:
                    raise TimeoutError('timeout')
                return await super().create_handoff(snapshot, request_id)

        class Checkout(CheckoutService):
            async def prepare_handoff_in_transaction(self, *args, **kwargs):
                case.prepare_calls += 1
                return await super().prepare_handoff_in_transaction(*args, **kwargs)

        case.checkout = Checkout(factory, cart, Gateway())
        case.actions = CartActionService(factory, sessions, cart, case.checkout)

        async def preview(source_turn=None, session_id=None):
            return await case.actions.preview_checkout(
                principal, session_id or case.session_id, case.cart_version, source_turn
            )

        async def confirm(
            action_id, source_turn=None, approved=True, idempotency_key='handoff',
        ):
            return await case.actions.confirm_handoff(
                principal, case.session_id, action_id, approved,
                idempotency_key, source_turn,
            )

        case.preview, case.confirm = preview, confirm
        engine = factory.kw['bind'].sync_engine

        def begin(connection):
            transactions.add(connection)

        def end(connection):
            transactions.discard(connection)

        event.listen(engine, 'begin', begin)
        event.listen(engine, 'commit', end)
        event.listen(engine, 'rollback', end)
        try:
            yield case
        finally:
            event.remove(engine, 'begin', begin)
            event.remove(engine, 'commit', end)
            event.remove(engine, 'rollback', end)
            await factory.kw['bind'].dispose()
    return scenario


def test_handoff_source_lease_checked_before_preparing(checkout_action_case):
    async def scenario():
        async with checkout_action_case() as case:
            preview = await case.preview()
            claim = await case.sessions.claim_turn(
                case.session_id, case.principal, 'source', 'hash'
            )
            async with case.factory() as db, db.begin():
                await db.execute(update(CommerceSession).where(
                    CommerceSession.id == case.session_id
                ).values(turn_lease_expires_at=utcnow() - timedelta(seconds=1)))
            replacement = await case.sessions.claim_turn(
                case.session_id, case.principal, 'source', 'hash'
            )
            with pytest.raises(CommerceError, match='turn_lease_lost'):
                await case.confirm(preview.action_id, claim)
            assert case.prepare_calls == 0 and case.ids == []
            async with case.factory() as db:
                count = await db.scalar(
                    select(func.count()).select_from(CheckoutHandoff)
                )
                assert count == 0
                action = await db.get(PendingAction, preview.action_id)
                assert action.status == 'pending'
            connections = {}

            def capture(connection, cursor, statement, parameters, context, many):
                for table in (
                    'commerce_sessions', 'pending_actions', 'checkout_handoffs',
                ):
                    if table in statement:
                        connections.setdefault(table, set()).add(connection)

            engine = case.factory.kw['bind'].sync_engine
            event.listen(engine, 'before_cursor_execute', capture)
            try:
                result = await case.confirm(
                    preview.action_id, replacement, approved=False
                )
            finally:
                event.remove(engine, 'before_cursor_execute', capture)
            assert result.status == 'declined' and case.ids == []
            assert set(connections) == {
                'commerce_sessions', 'pending_actions', 'checkout_handoffs'
            }
            assert len(set.union(*connections.values())) == 1
    asyncio.run(scenario())


def test_network_runs_after_prepare_commit(checkout_action_case):
    async def scenario():
        async with checkout_action_case() as case:
            result = await case.confirm((await case.preview()).action_id)
            assert result.status == 'handoff'
            assert case.gateway_observed_preparing is True
            assert case.gateway_observed_consumed is True
            assert case.open_transaction_at_gateway is False
    asyncio.run(scenario())


def test_same_key_retry_after_response_loss_reuses_ready_result(checkout_action_case):
    async def scenario():
        async with checkout_action_case() as case:
            preview = await case.preview()
            with pytest.raises(ConnectionError, match='response lost'):
                first = await case.confirm(preview.action_id)
                raise ConnectionError('response lost')
            replay = await case.confirm(preview.action_id)
            assert replay.status == 'handoff' and replay.replayed
            assert replay.handoff_id == first.handoff_id
            assert replay.destination == first.destination
            assert case.ids == [first.handoff_id]
    asyncio.run(scenario())


def test_handoff_lookup_rejects_other_session_or_principal(checkout_action_case):
    async def scenario():
        async with checkout_action_case() as case:
            result = await case.confirm((await case.preview()).action_id)
            other = await case.sessions.create(case.principal)
            foreign = await case.sessions.create(case.foreign)
            for principal, session_id in (
                (case.principal, other.session_id), (case.foreign, case.session_id),
                (case.foreign, foreign.session_id),
            ):
                with pytest.raises(CommerceError, match='not_found'):
                    await case.actions.get_handoff(
                        principal, session_id, result.handoff_id
                    )
            statements = []

            def capture(connection, cursor, statement, parameters, context, many):
                statements.append(statement)

            engine = case.factory.kw['bind'].sync_engine
            event.listen(engine, 'before_cursor_execute', capture)
            try:
                owned = await case.actions.get_handoff(
                    case.principal, case.session_id, result.handoff_id
                )
            finally:
                event.remove(engine, 'before_cursor_execute', capture)
            assert owned.status == 'handoff'
            assert all(
                'FOR UPDATE' not in statement.upper() for statement in statements
            )
    asyncio.run(scenario())


def test_preview_and_direct_api_enforce_session_owner(checkout_action_case):
    async def scenario():
        async with checkout_action_case() as case:
            foreign = await case.sessions.create(case.foreign)
            with pytest.raises(CommerceError, match='not_found'):
                await case.preview(session_id=foreign.session_id)
            preview = await case.preview()
            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[require_principal] = lambda: case.principal
            app.state.runtime = SimpleNamespace(cart_actions=case.actions)
            async with AsyncClient(
                transport=ASGITransport(app), base_url='https://test'
            ) as client:
                rejected = await client.post(
                    f'/api/v1/commerce/sessions/{foreign.session_id}'
                    '/checkout-handoff',
                    json={
                        'action_id': preview.action_id, 'approved': True,
                        'idempotency_key': 'handoff',
                    },
                )
                assert rejected.status_code == 404
                assert case.prepare_calls == 0 and case.ids == []
                response = await client.post(
                    f'/api/v1/commerce/sessions/{case.session_id}/checkout-handoff',
                    json={
                        'action_id': preview.action_id, 'approved': True,
                        'idempotency_key': 'handoff',
                    },
                )
                assert response.status_code == 200
                handoff_id = response.json()['handoff_id']
                for session_id, status in (
                    (case.session_id, 200), (foreign.session_id, 404),
                ):
                    lookup = await client.get(
                        f'/api/v1/commerce/sessions/{session_id}'
                        f'/handoffs/{handoff_id}'
                    )
                    assert lookup.status_code == status
                rejected = await client.post(
                    f'/api/v1/commerce/sessions/{foreign.session_id}/checkout-preview',
                    json={'cart_version': case.cart_version},
                )
                assert rejected.status_code == 404
            assert case.ids == [handoff_id]
    asyncio.run(scenario())


def test_retry_recovers_same_id_and_no_post_network_lease_check(checkout_action_case):
    async def scenario():
        async with checkout_action_case() as case:
            preview = await case.preview()
            case.fail_once = True
            failed = await case.confirm(preview.action_id)
            claim = await case.sessions.claim_turn(
                case.session_id, case.principal, 'source', 'hash'
            )
            case.expire_lease_at_gateway = True
            recovered = await case.confirm(preview.action_id, claim)
            assert failed.retryable and recovered.status == 'handoff'
            assert failed.handoff_id == recovered.handoff_id
            assert case.ids == [recovered.handoff_id, recovered.handoff_id]
            assert case.open_transaction_at_gateway is False
    asyncio.run(scenario())


def test_checkout_no_longer_coordinates_session_or_turn():
    parameters = inspect.signature(CheckoutService).parameters
    assert 'sessions' not in parameters
    assert not any(
        hasattr(CheckoutService, name)
        for name in ('preview', 'confirm_handoff', 'get_owned')
    )


def test_reconfirmation_and_decline_replay_do_not_call_host(checkout_action_case):
    async def scenario():
        async with checkout_action_case() as case:
            preview = await case.preview()
            await case.prices.set_price(case.sku_id, 150, 1, '改价')
            changed = await case.confirm(preview.action_id)
            assert changed.error_code == 'reconfirmation_required'
            assert changed.preview.action_id and case.ids == []
            replay = await case.confirm(preview.action_id)
            assert replay.replayed and replay.preview == changed.preview
            declined = await case.confirm(
                changed.preview.action_id, approved=False, idempotency_key='decline'
            )
            repeated = await case.confirm(
                changed.preview.action_id, approved=False, idempotency_key='decline'
            )
            assert declined.status == repeated.status == 'declined'
            assert repeated.replayed and case.ids == []
    asyncio.run(scenario())


def test_preview_rejects_stale_or_mismatched_source(checkout_action_case):
    async def scenario():
        async with checkout_action_case() as case:
            claim = await case.sessions.claim_turn(
                case.session_id, case.principal, 'source', 'hash'
            )
            other = await case.sessions.create(case.principal)
            for invalid in (
                claim.model_copy(update={'session_id': other.session_id}),
                claim.model_copy(update={'principal_key': case.foreign.principal_key}),
                claim.model_copy(update={'lease_token': 'stale'}),
            ):
                with pytest.raises(CommerceError, match='turn_lease_lost'):
                    await case.preview(invalid)
            preview = await case.preview(claim)
            assert (await case.preview(claim)).action_id == preview.action_id
            async with case.factory() as db, db.begin():
                await db.execute(update(CommerceSession).where(
                    CommerceSession.id == case.session_id
                ).values(expires_at=utcnow() - timedelta(seconds=1)))
            for operation in (
                case.preview(), case.confirm(preview.action_id),
                case.actions.get_handoff(case.principal, case.session_id, 'missing'),
            ):
                with pytest.raises(CommerceError, match='session_expired'):
                    await operation
            assert case.ids == []
    asyncio.run(scenario())

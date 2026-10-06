"""真实 MySQL 调用方事务与锁外交接协议。"""

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import get_type_hints

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from ecom.infra.checkout_gateway import DemoHostCheckoutGateway
from ecom.kernel.intent import ShoppingIntent
from ecom.modules.cart.models import PendingAction
from ecom.modules.cart.schemas import ActionProposalCommand
from ecom.modules.cart.service import CartService
from ecom.modules.checkout import schemas
from ecom.modules.checkout.models import CheckoutHandoff
from ecom.modules.checkout.service import CheckoutService
from ecom.modules.sale_facts.service import SaleFactsReader
from ecom.modules.sessions.service import CommerceError, ShoppingSessionService
from ecom.shared.principal import Principal, make_principal
from ecom.shared.time import mark_utc, utcnow
from tests.test_cart_transactions_mysql import reject_orm
from tests.test_commerce_facts_mysql import seed


class TransactionProbeGateway(DemoHostCheckoutGateway):
    def __init__(self):
        super().__init__()
        self.case = None
        self.ids = []
        self.violations = []
        self.timeout_once = False

    async def create_handoff(self, snapshot, request_id):
        case = self.case
        self.ids.append(request_id)
        if any(db.in_transaction() for db in case.caller_sessions):
            self.violations.append('caller transaction is open')
        await asyncio.wait_for(case.cart.get_cart(case.principal), 0.5)
        async with case.factory() as db:
            row = await db.get(CheckoutHandoff, request_id)
            action = await db.get(PendingAction, row.action_id)
            if row.status not in {'preparing', 'failed'} or action.status != 'consumed':
                self.violations.append('preparation is not committed atomically')
        if self.timeout_once and len(self.ids) == 1:
            raise TimeoutError('timeout')
        return await super().create_handoff(snapshot, request_id)


@dataclass
class CheckoutCase:
    factory: async_sessionmaker[AsyncSession]
    sessions: ShoppingSessionService
    cart: CartService
    principal: Principal
    session_id: str
    cart_version: int
    gateway: TransactionProbeGateway
    service: CheckoutService
    caller_sessions: list[AsyncSession] = field(default_factory=list)

    @property
    def gateway_ids(self):
        return self.gateway.ids

    async def preview(self):
        async with self.factory() as db, db.begin():
            self.caller_sessions.append(db)
            return await self.service.preview_in_transaction(
                db, self.principal, self.session_id, self.cart_version
            )

    async def confirm(self, action_id):
        async with self.factory() as db, db.begin():
            self.caller_sessions.append(db)
            prepared = await self.service.prepare_handoff_in_transaction(
                db, self.principal, self.session_id, action_id, True, 'handoff'
            )
        if isinstance(prepared, schemas.HandoffResult):
            return prepared
        destination = await self.service.request_destination(prepared)
        async with self.factory() as db, db.begin():
            self.caller_sessions.append(db)
            return await self.service.finalize_handoff_in_transaction(
                db, self.principal, prepared, destination
            )

    async def timeout_then_retry(self):
        self.gateway.timeout_once = True
        preview = await self.preview()
        first = await self.confirm(preview.action_id)
        recovered = await self.confirm(preview.action_id)
        return first, recovered


@pytest.fixture
def checkout_case(migrated_mysql_database):
    async def setup():
        factory, catalog, prices, stock, _, _, skus = await seed(
            migrated_mysql_database
        )
        sessions = ShoppingSessionService(factory)
        principal = make_principal('test', 'checkout-transaction')
        session = await sessions.create(principal)
        await sessions.record_seen(session.session_id, principal, [], [skus[0].id])
        cart = CartService(factory, SaleFactsReader(catalog, prices, stock))
        async with factory() as db, db.begin():
            proposal = await cart.propose_action_in_transaction(
                db, principal, session.session_id,
                ActionProposalCommand(
                    kind='add', target={'sku_id': skus[0].id, 'quantity': 1},
                    expected_cart_version=1,
                ), ShoppingIntent(),
            )
            confirmed = await cart.confirm_in_transaction(
                db, principal, session.session_id, proposal.action_id, True, 'add'
            )
        gateway = TransactionProbeGateway()
        service = CheckoutService(factory, cart, gateway)
        case = CheckoutCase(
            factory, sessions, cart, principal, session.session_id,
            confirmed.cart.version, gateway, service,
        )
        gateway.case = case
        await factory.kw['bind'].dispose()
        return case

    return asyncio.run(setup())


def test_preparing_and_action_consumption_roll_back_together(checkout_case):
    async def scenario():
        case = checkout_case
        preview = await case.preview()
        with pytest.raises(RuntimeError, match='rollback-probe'):
            async with case.factory() as db, db.begin():
                attempt = await case.service.prepare_handoff_in_transaction(
                    db, case.principal, case.session_id,
                    preview.action_id, True, 'handoff',
                )
                assert isinstance(attempt, schemas.HandoffAttempt)
                await db.flush()
                row = await db.get(CheckoutHandoff, attempt.snapshot.handoff_id)
                action = await db.get(PendingAction, preview.action_id)
                assert row.status == 'preparing' and action.status == 'consumed'
                raise RuntimeError('rollback-probe')
        async with case.factory() as db:
            count = await db.scalar(
                select(func.count()).select_from(CheckoutHandoff)
            )
            assert count == 0
            action = await db.get(PendingAction, preview.action_id)
            assert action.status == 'pending' and action.confirmation_key is None
        assert case.gateway_ids == []

    asyncio.run(scenario())


def test_checkout_returns_no_cart_orm(checkout_case):
    async def scenario():
        case = checkout_case
        preview = await case.preview()
        reject_orm(preview)
        async with case.factory() as db, db.begin():
            action = await case.cart.lock_pending_action(
                db, case.principal, case.session_id, preview.action_id
            )
            assert preview.checked_at == action.created_at
            assert preview.expires_at == action.expires_at
            assert preview.items == action.summary['items']
            attempt = await case.service.prepare_handoff_in_transaction(
                db, case.principal, case.session_id, preview.action_id, True, 'handoff'
            )
            reject_orm(attempt)
            assert attempt.snapshot.cart_id == action.cart_id
            assert mark_utc(utcnow()) < attempt.snapshot.expires_at
        destination = await case.service.request_destination(attempt)
        async with case.factory() as db, db.begin():
            result = await case.service.finalize_handoff_in_transaction(
                db, case.principal, attempt, destination
            )
            reject_orm(result)
        async with case.factory() as db, db.begin():
            owned = await case.service.get_owned_in_transaction(
                db, case.principal, case.session_id, result.handoff_id
            )
            reject_orm(owned)
            assert owned.status == 'handoff'

    asyncio.run(scenario())


def test_destination_call_requires_no_open_transaction(checkout_case):
    async def scenario():
        case = checkout_case
        preview = await case.preview()
        result = await case.confirm(preview.action_id)
        assert result.status == 'handoff'
        assert case.gateway.violations == []
        assert case.gateway_ids == [result.handoff_id]
        replay = await case.confirm(preview.action_id)
        assert replay.replayed and replay.status == 'handoff'
        assert case.gateway_ids == [result.handoff_id]

    asyncio.run(scenario())


def test_retry_keeps_handoff_id(checkout_case):
    first, recovered = asyncio.run(checkout_case.timeout_then_retry())
    assert first.status == 'unavailable'
    assert recovered.status == 'handoff' and recovered.replayed
    assert checkout_case.gateway_ids == [recovered.handoff_id, recovered.handoff_id]
    assert checkout_case.gateway.violations == []


def test_transaction_methods_use_only_callers_session(checkout_case, monkeypatch):
    def forbidden_factory():
        raise AssertionError('transaction method opened a session')

    async def forbidden_commit():
        raise AssertionError('transaction method committed or rolled back')

    async def scenario():
        case = checkout_case
        with monkeypatch.context() as patch:
            patch.setattr(case.service, 'factory', forbidden_factory)
            patch.setattr(case.cart, 'factory', forbidden_factory)
            async with case.factory() as db, db.begin():
                with monkeypatch.context() as session_patch:
                    session_patch.setattr(db, 'commit', forbidden_commit)
                    session_patch.setattr(db, 'rollback', forbidden_commit)
                    preview = await case.service.preview_in_transaction(
                        db, case.principal, case.session_id, case.cart_version
                    )
                    attempt = await case.service.prepare_handoff_in_transaction(
                        db, case.principal, case.session_id,
                        preview.action_id, True, 'handoff',
                    )
                    assert db.in_transaction() and case.gateway_ids == []
            destination = await DemoHostCheckoutGateway().create_handoff(
                attempt.snapshot, attempt.snapshot.handoff_id
            )
            async with case.factory() as db, db.begin():
                with monkeypatch.context() as session_patch:
                    session_patch.setattr(db, 'commit', forbidden_commit)
                    session_patch.setattr(db, 'rollback', forbidden_commit)
                    finalized = await case.service.finalize_handoff_in_transaction(
                        db, case.principal, attempt, destination
                    )
                    owned = await case.service.get_owned_in_transaction(
                        db, case.principal, case.session_id, finalized.handoff_id
                    )
                    assert db.in_transaction() and owned.status == 'handoff'

    asyncio.run(scenario())


def test_finalization_rolls_back_and_does_not_overwrite_ready(checkout_case):
    async def scenario():
        case = checkout_case
        preview = await case.preview()
        async with case.factory() as db, db.begin():
            attempt = await case.service.prepare_handoff_in_transaction(
                db, case.principal, case.session_id, preview.action_id, True, 'handoff'
            )
        destination = await case.service.request_destination(attempt)
        with pytest.raises(RuntimeError, match='rollback-probe'):
            async with case.factory() as db, db.begin():
                result = await case.service.finalize_handoff_in_transaction(
                    db, case.principal, attempt, destination
                )
                assert result.status == 'handoff'
                await db.flush()
                raise RuntimeError('rollback-probe')
        async with case.factory() as db:
            row = await db.get(CheckoutHandoff, attempt.snapshot.handoff_id)
            action = await db.get(PendingAction, preview.action_id)
            assert row.status == 'preparing' and action.status == 'consumed'
        async with case.factory() as db, db.begin():
            ready = await case.service.finalize_handoff_in_transaction(
                db, case.principal, attempt, destination
            )
        async with case.factory() as db, db.begin():
            late_failure = await case.service.finalize_handoff_in_transaction(
                db, case.principal, attempt, None
            )
            assert late_failure == ready

    asyncio.run(scenario())


def test_expired_handoff_does_not_retry_or_become_ready(checkout_case):
    from datetime import timedelta

    async def scenario():
        case = checkout_case
        preview = await case.preview()
        async with case.factory() as db, db.begin():
            attempt = await case.service.prepare_handoff_in_transaction(
                db, case.principal, case.session_id, preview.action_id, True, 'handoff'
            )
        destination = await case.service.request_destination(attempt)
        async with case.factory() as db, db.begin():
            await db.execute(update(CheckoutHandoff).where(
                CheckoutHandoff.id == attempt.snapshot.handoff_id
            ).values(expires_at=utcnow() - timedelta(seconds=1)))
        async with case.factory() as db, db.begin():
            finalized = await case.service.finalize_handoff_in_transaction(
                db, case.principal, attempt, destination
            )
            assert finalized.error_code == 'handoff_expired'
        replay = await case.confirm(preview.action_id)
        assert replay.error_code == 'handoff_expired'
        assert case.gateway_ids == [attempt.snapshot.handoff_id]

    asyncio.run(scenario())


def test_get_owned_uses_action_session_and_principal(checkout_case):
    async def scenario():
        case = checkout_case
        preview = await case.preview()
        result = await case.confirm(preview.action_id)
        other_session = await case.sessions.create(case.principal)
        async with case.factory() as db, db.begin():
            with pytest.raises(CommerceError, match='not_found'):
                await case.service.get_owned_in_transaction(
                    db, case.principal, other_session.session_id, result.handoff_id
                )
            with pytest.raises(CommerceError, match='not_found'):
                await case.service.get_owned_in_transaction(
                    db, make_principal('test', 'foreign'),
                    case.session_id, result.handoff_id,
                )

    asyncio.run(scenario())


@pytest.mark.parametrize('retry', [False, True])
def test_handoff_attempt_nested_json_does_not_alias_orm(
    checkout_case, monkeypatch, retry,
):
    case = checkout_case
    original = case.cart.quote_snapshot

    def nested_quote(quote, sale):
        snapshot = original(quote, sale)
        snapshot['sku_options'] = [{'nested': {'items': ['original']}}]
        return snapshot

    monkeypatch.setattr(case.cart, 'quote_snapshot', nested_quote)

    async def scenario():
        preview = await case.preview()
        if retry:
            async with case.factory() as db, db.begin():
                first = await case.service.prepare_handoff_in_transaction(
                    db, case.principal, case.session_id,
                    preview.action_id, True, 'handoff',
                )
        async with case.factory() as db, db.begin():
            retained_rows = []

            def retain_row(session, row):
                if isinstance(row, CheckoutHandoff):
                    retained_rows.append(row)

            event.listen(db.sync_session, 'after_attach', retain_row)
            if retry:
                retained_rows.append(
                    await db.get(CheckoutHandoff, first.snapshot.handoff_id)
                )
            attempt = await case.service.prepare_handoff_in_transaction(
                db, case.principal, case.session_id,
                preview.action_id, True, 'handoff',
            )
            row = retained_rows[0]
            assert row is await db.get(CheckoutHandoff, attempt.snapshot.handoff_id)
            nested = attempt.snapshot.items[0]['sku_options'][0]['nested']
            nested['items'][0] = 'changed'
            stored = row.snapshot_json['items'][0]['sku_options'][0]['nested']
            assert stored['items'][0] == 'original'
            flag_modified(row, 'snapshot_json')
        async with case.factory() as db:
            row = await db.get(CheckoutHandoff, attempt.snapshot.handoff_id)
            stored = row.snapshot_json['items'][0]['sku_options'][0]['nested']
            assert stored['items'][0] == 'original'

    asyncio.run(scenario())


def test_checkout_preview_and_reconfirmation_replay_json_are_isolated(
    checkout_case, monkeypatch,
):
    case = checkout_case
    original = case.cart.quote_snapshot

    def nested_quote(quote, sale):
        snapshot = original(quote, sale)
        snapshot['sku_options'] = [{'nested': {'items': ['original']}}]
        return snapshot

    monkeypatch.setattr(case.cart, 'quote_snapshot', nested_quote)

    async def scenario():
        async with case.factory() as db, db.begin():
            retained_rows = []

            def retain_action(session, row):
                if isinstance(row, PendingAction):
                    retained_rows.append(row)

            event.listen(db.sync_session, 'after_attach', retain_action)
            preview = await case.service.preview_in_transaction(
                db, case.principal, case.session_id, case.cart_version
            )
            row = retained_rows[0]
            assert row is await db.get(PendingAction, preview.action_id)
            preview.items[0]['sku_options'][0]['nested']['items'][0] = 'changed'
            stored = row.quote_snapshot_json['items'][0]['sku_options'][0]['nested']
            assert stored['items'][0] == 'original'
            flag_modified(row, 'quote_snapshot_json')
        async with case.factory() as db, db.begin():
            row = await db.get(PendingAction, preview.action_id)
            row.quote_snapshot_json['current_subtotal'] = 999
            flag_modified(row, 'quote_snapshot_json')
        async with case.factory() as db, db.begin():
            changed = await case.service.prepare_handoff_in_transaction(
                db, case.principal, case.session_id,
                preview.action_id, True, 'handoff',
            )
            assert changed.error_code == 'reconfirmation_required'
        async with case.factory() as db, db.begin():
            row = await db.get(PendingAction, preview.action_id)
            replay = await case.service.prepare_handoff_in_transaction(
                db, case.principal, case.session_id,
                preview.action_id, True, 'handoff',
            )
            assert replay.replayed and replay.preview
            replay.preview.items[0]['sku_options'][0]['nested']['items'][0] = 'changed'
            stored = row.result_json['preview']['items'][0]['sku_options'][0]['nested']
            assert stored['items'][0] == 'original'
            flag_modified(row, 'result_json')
        async with case.factory() as db:
            row = await db.get(PendingAction, preview.action_id)
            stored = row.result_json['preview']['items'][0]['sku_options'][0]['nested']
            assert stored['items'][0] == 'original'

    asyncio.run(scenario())


@pytest.mark.parametrize('name', [
    'preview_in_transaction', 'prepare_handoff_in_transaction',
    'request_destination', 'finalize_handoff_in_transaction',
    'get_owned_in_transaction',
])
def test_checkout_transaction_public_methods_are_typed(name):
    assert hasattr(CheckoutService, name), f'missing transaction API: {name}'
    method = getattr(CheckoutService, name)
    assert inspect.iscoroutinefunction(method)
    hints = get_type_hints(method)
    assert 'return' in hints
    assert all(parameter in hints for parameter in inspect.signature(method).parameters
               if parameter != 'self')


def test_gateway_port_preserves_alias_identity():
    from ecom.infra import checkout_gateway as gateway
    from ecom.modules.checkout import ports

    assert gateway.HostCheckoutGateway is ports.HostCheckoutGateway

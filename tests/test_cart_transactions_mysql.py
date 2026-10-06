"""调用方共享真实 MySQL 事务，公共快照不携带 ORM。"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

import pytest
from pydantic import BaseModel
from sqlalchemy import func, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from cart import schemas
from cart.models import Cart, CartLine, PendingAction
from cart.service import CartService
from kernel.intent import ShoppingIntent
from sale_facts.service import SaleFactsReader
from shared.hashing import request_hash
from shared.ids import new_id
from shared.principal import Principal, make_principal
from shared.time import mark_utc, utcnow
from shopping_session.models import CommerceSession, SeenSKU
from shopping_session.service import CommerceError, ShoppingSessionService
from tests.test_commerce_facts_mysql import seed


@dataclass
class TransactionCase:
    factory: async_sessionmaker[AsyncSession]
    principal: Principal
    other_principal: Principal
    sessions: ShoppingSessionService
    cart: CartService
    session_id: str
    other_session_id: str
    foreign_session_id: str
    command: object
    intent: ShoppingIntent
    sku_id: str
    other_sku_id: str

    async def count_pending_actions(self) -> int:
        async with self.factory() as db:
            return await db.scalar(select(func.count()).select_from(PendingAction))

    async def propose(self):
        async with self.factory() as db, db.begin():
            return await self.cart.propose_action_in_transaction(
                db,
                self.principal,
                self.session_id,
                self.command,
                self.intent,
            )


@pytest.fixture
def transaction_case(migrated_mysql_database):
    async def setup():
        factory, catalog, prices, stock, _, products, skus = await seed(
            migrated_mysql_database
        )
        sessions = ShoppingSessionService(factory)
        principal = make_principal('test', 'transaction')
        other_principal = make_principal('test', 'foreign')
        session = await sessions.create(principal)
        other = await sessions.create(principal)
        foreign = await sessions.create(other_principal)
        cart = CartService(factory, SaleFactsReader(catalog, prices, stock))
        view = await cart.get_cart(principal)
        for owned in (session, other):
            await sessions.record_seen(
                owned.session_id, principal, [products[0].id], [skus[0].id]
            )
        command_type = getattr(schemas, 'ActionProposalCommand', None)
        assert command_type is not None, 'missing DTO: ActionProposalCommand'
        case = TransactionCase(
            factory,
            principal,
            other_principal,
            sessions,
            cart,
            session.session_id,
            other.session_id,
            foreign.session_id,
            command_type(
                kind='add',
                target={'sku_id': skus[0].id, 'quantity': 1},
                expected_cart_version=view.version,
            ),
            ShoppingIntent(),
            skus[0].id,
            skus[1].id,
        )
        await factory.kw['bind'].dispose()
        return case

    return asyncio.run(setup())


def reject_orm(value):
    assert not getattr(inspect(value, raiseerr=False), 'mapper', None), (
        'ORM escaped snapshot'
    )
    if isinstance(value, BaseModel):
        for nested in dict(value).values():
            reject_orm(nested)
    elif isinstance(value, dict):
        for nested in value.values():
            reject_orm(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            reject_orm(nested)


def test_public_snapshot_contains_no_orm(transaction_case):
    async def scenario():
        case = transaction_case
        proposal = await case.propose()
        async with case.factory() as db, db.begin():
            snapshot = await case.cart.lock_cart_snapshot(db, case.principal)
            action = await case.cart.lock_pending_action(
                db, case.principal, case.session_id, proposal.action_id
            )
            reject_orm(snapshot)
            reject_orm(action)
            assert isinstance(action, schemas.PendingActionRecord)
            assert (
                action.action_id == proposal.action_id
                and action.summary == proposal.summary
            )
            assert action.created_at <= mark_utc(utcnow()) < action.expires_at
            assert action.model_dump(mode='json')['created_at'].endswith('Z')
            action.payload['quantity'] = 9
        async with case.factory() as db:
            stored = await db.get(PendingAction, proposal.action_id)
            assert stored.payload_json['quantity'] == 1
        async with case.factory() as db, db.begin():
            result = await case.cart.confirm_in_transaction(
                db,
                case.principal,
                case.session_id,
                proposal.action_id,
                True,
                'snapshot',
            )
            reject_orm(result)
            snapshot = await case.cart.lock_cart_snapshot(db, case.principal)
            reject_orm(snapshot)
            assert snapshot.lines[0].last_seen_unit_amount_minor == 100
            snapshot.lines[0].sku_options_snapshot.append({'probe': True})
        async with case.factory() as db, db.begin():
            snapshot = await case.cart.lock_cart_snapshot(db, case.principal)
            assert snapshot.lines[0].sku_options_snapshot == []

    asyncio.run(scenario())


def test_proposal_rolls_back_with_outer_transaction(transaction_case):
    async def scenario():
        case = transaction_case
        with pytest.raises(RuntimeError, match='rollback-probe'):
            async with case.factory() as db, db.begin():
                await case.cart.propose_action_in_transaction(
                    db, case.principal, case.session_id, case.command, case.intent
                )
                raise RuntimeError('rollback-probe')
        assert await case.count_pending_actions() == 0

    asyncio.run(scenario())


def test_confirmation_rolls_back_with_outer_transaction(transaction_case):
    async def scenario():
        case = transaction_case
        proposal = await case.propose()
        with pytest.raises(RuntimeError, match='rollback-probe'):
            async with case.factory() as db, db.begin():
                await case.cart.confirm_in_transaction(
                    db,
                    case.principal,
                    case.session_id,
                    proposal.action_id,
                    True,
                    'rollback',
                )
                raise RuntimeError('rollback-probe')
        view = await case.cart.get_cart(case.principal)
        assert view.version == 1 and view.items == []
        async with case.factory() as db:
            action = await db.get(PendingAction, proposal.action_id)
            assert action.status == 'pending' and action.confirmation_key is None

    asyncio.run(scenario())


def test_seen_check_is_in_callers_transaction(transaction_case):
    async def scenario():
        case = transaction_case
        with pytest.raises(RuntimeError, match='rollback-probe'):
            async with case.factory() as db, db.begin():
                assert not await case.sessions.has_seen_sku(
                    db, case.foreign_session_id, case.sku_id
                )
                db.add(
                    SeenSKU(
                        session_id=case.foreign_session_id,
                        sku_id=case.sku_id,
                        first_seen_at=utcnow(),
                    )
                )
                await db.flush()
                session = await case.sessions.lock_owned_session(
                    db, case.foreign_session_id, case.other_principal.principal_key
                )
                reject_orm(session)
                assert await case.sessions.has_seen_sku(
                    db, case.foreign_session_id, case.sku_id
                )
                assert (await db.get(CommerceSession, case.foreign_session_id)) in db
                raise RuntimeError('rollback-probe')
        async with case.factory() as db:
            assert not await case.sessions.has_seen_sku(
                db, case.foreign_session_id, case.sku_id
            )

    asyncio.run(scenario())


def test_single_cart_and_single_pending_per_session(transaction_case):
    async def scenario():
        case = transaction_case
        first = await case.propose()
        second = await case.propose()
        async with case.factory() as db, db.begin():
            third = await case.cart.propose_action_in_transaction(
                db, case.principal, case.other_session_id, case.command, case.intent
            )
            assert (
                await case.cart.find_action_owner(db, case.principal, third.action_id)
                == case.other_session_id
            )
            first_record = await case.cart.lock_pending_action(
                db, case.principal, case.session_id, first.action_id
            )
            second_record = await case.cart.lock_pending_action(
                db, case.principal, case.session_id, second.action_id
            )
            assert (
                first_record.status == 'invalidated'
                and second_record.status == 'pending'
            )
            assert first_record.cart_id == second_record.cart_id
            assert (await db.scalar(select(func.count()).select_from(Cart))) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    'operation', ['owner', 'lock', 'resolve', 'invalidate', 'session']
)
def test_transaction_operations_reject_foreign_owner(transaction_case, operation):
    async def scenario():
        case = transaction_case
        proposal = await case.propose()
        async with case.factory() as db, db.begin():
            with pytest.raises(CommerceError, match='not_found'):
                if operation == 'owner':
                    await case.cart.find_action_owner(
                        db, case.other_principal, proposal.action_id
                    )
                elif operation == 'lock':
                    await case.cart.lock_pending_action(
                        db, case.other_principal, case.session_id, proposal.action_id
                    )
                elif operation == 'resolve':
                    await case.cart.resolve_action_in_transaction(
                        db,
                        case.other_principal,
                        case.session_id,
                        proposal.action_id,
                        'declined',
                        False,
                        'foreign',
                        {},
                    )
                elif operation == 'invalidate':
                    await case.cart.invalidate_action_in_transaction(
                        db, case.other_principal, case.session_id, proposal.action_id
                    )
                else:
                    await case.sessions.lock_owned_session(
                        db, case.session_id, case.other_principal.principal_key
                    )

    asyncio.run(scenario())


def test_source_record_replay_and_conflict_priority(transaction_case):
    async def scenario():
        case = transaction_case
        case.command.source_turn_id = 'source'
        proposal = await case.propose()
        digest = request_hash(
            {'kind': 'add', 'target': case.command.target, 'cart_version': 1}
        )
        async with case.factory() as db, db.begin():
            record = await case.cart.find_source_action_in_transaction(
                db, case.principal, case.session_id, 'source', digest
            )
            assert (
                isinstance(record, schemas.PendingActionRecord)
                and record.action_id == proposal.action_id
            )
            assert (
                await case.cart.find_source_action_in_transaction(
                    db, case.principal, case.session_id, None, digest
                )
                is None
            )
            assert (
                await case.cart.propose_action_in_transaction(
                    db, case.principal, case.session_id, case.command, case.intent
                )
            ).action_id == proposal.action_id
            await db.execute(
                update(PendingAction)
                .where(PendingAction.id == proposal.action_id)
                .values(expires_at=utcnow() - timedelta(seconds=1))
            )
        async with case.factory() as db, db.begin():
            with pytest.raises(CommerceError, match='idempotency_conflict'):
                await case.cart.find_source_action_in_transaction(
                    db, case.principal, case.session_id, 'source', 'different'
                )
            with pytest.raises(CommerceError, match='action_already_resolved'):
                await case.cart.find_source_action_in_transaction(
                    db, case.principal, case.session_id, 'source', digest
                )
            record = await case.cart.lock_pending_action(
                db, case.principal, case.session_id, proposal.action_id
            )
            with pytest.raises(CommerceError, match='action_expired'):
                await case.cart.confirmation_replay_in_transaction(
                    db, case.principal, record, True, 'expired'
                )

    asyncio.run(scenario())


def test_create_resolve_invalidate_and_replay_records(transaction_case):
    async def scenario():
        case = transaction_case
        async with case.factory() as db, db.begin():
            snapshot = await case.cart.lock_cart_snapshot(db, case.principal)
            record = await case.cart.create_pending_action_in_transaction(
                db,
                case.principal,
                case.session_id,
                snapshot,
                'checkout_handoff',
                {'cart_version': 1},
                {'items': []},
                'digest',
            )
            assert isinstance(record, schemas.PendingActionRecord)
            reject_orm(record)
            assert (
                await case.cart.confirmation_replay_in_transaction(
                    db, case.principal, record, False, 'decline'
                )
                is None
            )
            await case.cart.resolve_action_in_transaction(
                db,
                case.principal,
                case.session_id,
                record.action_id,
                'declined',
                False,
                'decline',
                {'status': 'declined'},
            )
        async with case.factory() as db, db.begin():
            await db.execute(
                update(PendingAction)
                .where(PendingAction.id == record.action_id)
                .values(expires_at=utcnow() - timedelta(seconds=1))
            )
            stored = await case.cart.lock_pending_action(
                db, case.principal, case.session_id, record.action_id
            )
            assert await case.cart.confirmation_replay_in_transaction(
                db, case.principal, stored, False, 'decline'
            ) == {'status': 'declined'}
            with pytest.raises(CommerceError, match='idempotency_conflict'):
                await case.cart.confirmation_replay_in_transaction(
                    db, case.principal, stored, True, 'decline'
                )
            with pytest.raises(CommerceError, match='action_already_resolved'):
                await case.cart.confirmation_replay_in_transaction(
                    db, case.principal, stored, False, 'new'
                )
            next_record = await case.cart.create_pending_action_in_transaction(
                db,
                case.principal,
                case.session_id,
                snapshot,
                'checkout_handoff',
                {},
                {},
                'next',
            )
            await case.cart.invalidate_action_in_transaction(
                db, case.principal, case.session_id, next_record.action_id
            )
            assert (
                await case.cart.lock_pending_action(
                    db, case.principal, case.session_id, next_record.action_id
                )
            ).status == 'invalidated'

    asyncio.run(scenario())


@pytest.mark.parametrize('field', ['payload', 'summary', 'result', 'line', 'state'])
def test_nested_snapshot_mutation_does_not_pollute_orm_or_save(transaction_case, field):
    async def scenario():
        case = transaction_case
        async with case.factory() as db, db.begin():
            nested = {'nested': {'items': [{'value': 'original'}]}}
            if field == 'state':
                row = await db.get(CommerceSession, case.session_id)
                column = 'state_json'
                row.state_json = nested
                await db.flush()
                snapshot = await case.sessions.lock_owned_session(
                    db,
                    case.session_id,
                    case.principal.principal_key,
                )
                value = snapshot.state
            elif field == 'line':
                cart = await case.cart.lock_cart_snapshot(db, case.principal)
                row = CartLine(
                    id=new_id(),
                    cart_id=cart.cart_id,
                    sku_id=case.sku_id,
                    quantity=1,
                    product_name_snapshot='商品0',
                    sku_options_snapshot=[nested],
                )
                db.add(row)
                await db.flush()
                column = 'sku_options_snapshot'
                snapshot = await case.cart.lock_cart_snapshot(db, case.principal)
                value = snapshot.lines[0].sku_options_snapshot[0]
            else:
                cart = await case.cart.lock_cart_snapshot(db, case.principal)
                action = await case.cart.create_pending_action_in_transaction(
                    db,
                    case.principal,
                    case.session_id,
                    cart,
                    'checkout_handoff',
                    {},
                    {},
                    'nested',
                )
                row = await db.get(PendingAction, action.action_id)
                column = {
                    'payload': 'payload_json',
                    'summary': 'quote_snapshot_json',
                    'result': 'result_json',
                }[field]
                setattr(row, column, nested)
                await db.flush()
                snapshot = await case.cart.lock_pending_action(
                    db,
                    case.principal,
                    case.session_id,
                    action.action_id,
                )
                value = getattr(snapshot, field)
            value['nested']['items'][0]['value'] = 'changed'
            stored = getattr(row, column)
            if field == 'line':
                stored = stored[0]
            assert stored['nested']['items'][0]['value'] == 'original'
            flag_modified(row, column)
            row_type, row_id = type(row), row.id
        async with case.factory() as db:
            stored = getattr(await db.get(row_type, row_id), column)
            if field == 'line':
                stored = stored[0]
            assert stored['nested']['items'][0]['value'] == 'original'

    asyncio.run(scenario())


def test_confirmation_result_does_not_alias_unchanged_line(transaction_case):
    async def scenario():
        case = transaction_case
        async with case.factory() as db, db.begin():
            cart = await case.cart.lock_cart_snapshot(db, case.principal)
            unchanged_line = CartLine(
                id=new_id(),
                cart_id=cart.cart_id,
                sku_id=case.other_sku_id,
                quantity=1,
                product_name_snapshot='商品1',
                sku_options_snapshot=[{'nested': {'items': ['original']}}],
            )
            db.add(unchanged_line)
        proposal = await case.propose()
        async with case.factory() as db, db.begin():
            row = await db.get(CartLine, unchanged_line.id)
            result = await case.cart.confirm_in_transaction(
                db,
                case.principal,
                case.session_id,
                proposal.action_id,
                True,
                'nested-confirmation',
            )
            assert result.status == 'cart_updated'
            unchanged_item = next(
                item for item in result.cart.items if item.sku_id == case.other_sku_id
            )
            unchanged_item.sku_options[0]['nested']['items'][0] = 'changed'
            assert row is await db.get(CartLine, unchanged_line.id)
            assert row.sku_options_snapshot[0]['nested']['items'][0] == 'original'
            flag_modified(row, 'sku_options_snapshot')
        async with case.factory() as db:
            row = await db.get(CartLine, unchanged_line.id)
            assert row.sku_options_snapshot[0]['nested']['items'][0] == 'original'
        async with case.factory() as db, db.begin():
            replay = await case.cart.confirm_in_transaction(
                db,
                case.principal,
                case.session_id,
                proposal.action_id,
                True,
                'nested-confirmation',
            )
            assert replay.replayed
            unchanged_item = next(
                item for item in replay.cart.items if item.sku_id == case.other_sku_id
            )
            unchanged_item.sku_options[0]['nested']['items'][0] = 'changed-replay'
            action = await db.get(PendingAction, proposal.action_id)
            stored_item = next(
                item
                for item in action.result_json['cart']['items']
                if item['sku_id'] == case.other_sku_id
            )
            assert stored_item['sku_options'][0]['nested']['items'][0] == 'original'
            flag_modified(action, 'result_json')
        async with case.factory() as db:
            action = await db.get(PendingAction, proposal.action_id)
            stored_item = next(
                item
                for item in action.result_json['cart']['items']
                if item['sku_id'] == case.other_sku_id
            )
            assert stored_item['sku_options'][0]['nested']['items'][0] == 'original'

    asyncio.run(scenario())

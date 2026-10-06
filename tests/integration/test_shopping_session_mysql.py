"""轮次租约、持久回放与主体隔离。"""
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import update

from ecom.application.shopping.contracts import TurnResponse
from ecom.infra.db import create_session_factory
from ecom.kernel.intent import ShoppingIntent
from ecom.modules.identity.types import make_principal
from ecom.modules.sessions.models import CommerceSession, CommerceTurn
from ecom.modules.sessions.schemas import TurnCompletion, TurnReplay
from ecom.modules.sessions.service import CommerceError, ShoppingSessionService
from ecom.shared.time import utcnow
from tests.conftest import downgrade_to, read_schema, upgrade_to
from tests.integration.test_commerce_facts_mysql import seed


def test_session_migration_and_ownership(migrated_mysql_database):
    schema = read_schema(migrated_mysql_database)
    assert {'commerce_sessions', 'commerce_turns', 'session_seen_products', 'session_seen_skus'} <= schema['tables']
    assert {'active_turn_id', 'turn_lease_token', 'turn_lease_expires_at'} <= schema['columns']['commerce_sessions']
    downgrade_to(migrated_mysql_database, '0004_identity')
    assert 'commerce_sessions' not in read_schema(migrated_mysql_database)['tables']
    upgrade_to(migrated_mysql_database, '0005_commerce_sessions')
    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        service = ShoppingSessionService(factory)
        a, b = make_principal('test', 'a'), make_principal('test', 'b')
        s = await service.create(a)
        with pytest.raises(CommerceError, match='not_found'):
            await service.get_owned(s.session_id, b)
        assert (await service.get_owned(s.session_id, make_principal('test', 'a'))).session_id == s.session_id
        async with factory() as db:
            await db.execute(update(CommerceSession).where(CommerceSession.id == s.session_id).values(expires_at=utcnow()-timedelta(seconds=1)))
            await db.commit()
        with pytest.raises(CommerceError, match='session_expired'):
            await service.get_owned(s.session_id, a)
    asyncio.run(scenario())


@pytest.mark.parametrize('source', ['envelope', 'payload', 'claim'])
@pytest.mark.parametrize('field', ['session_id', 'turn_id'])
def test_completion_envelope_must_match_payload_and_claim(
    migrated_mysql_database, source, field,
):
    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        service = ShoppingSessionService(factory)
        principal = make_principal('test', 'envelope')
        session = await service.create(principal)
        claim = await service.claim_turn(session.session_id, principal, 't', 'hash')
        response = TurnResponse(
            request_id='r', session_id=session.session_id, turn_id='t',
            intent='shop', status='clarify',
        )
        envelope = {'session_id': session.session_id, 'turn_id': 't'}
        if source == 'envelope':
            envelope[field] = 'different'
        elif source == 'payload':
            setattr(response, field, 'different')
        else:
            setattr(claim, field, 'different')
        completion = TurnCompletion(
            **envelope, response=response.model_dump(mode='json'),
            state={'purpose': '通勤'}, product_ids=['product'], sku_ids=['sku'],
        )
        with pytest.raises(CommerceError, match='invalid_completion'):
            await service.complete_turn(claim, completion)
        owned = await service.get_owned(session.session_id, principal)
        assert owned.version == 1 and owned.state == {}
        assert await service.seen(session.session_id, principal) == (set(), set())
        async with factory() as db:
            turn = await db.get(CommerceTurn, (session.session_id, 't'))
            assert turn.status == 'processing' and turn.response_json is None
    asyncio.run(scenario())


def test_existing_json_replays_without_wrapper_change(migrated_mysql_database):
    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        service = ShoppingSessionService(factory)
        principal = make_principal('test', 'legacy')
        session = await service.create(principal)
        await service.claim_turn(session.session_id, principal, 't', 'hash')
        payload = {
            'request_id': 'legacy-r', 'session_id': session.session_id,
            'turn_id': 't', 'intent': 'shop', 'status': 'clarify',
            'question': '想买什么？',
        }
        async with factory() as db, db.begin():
            await db.execute(update(CommerceTurn).where(
                CommerceTurn.session_id == session.session_id,
                CommerceTurn.turn_id == 't',
            ).values(status='completed', response_json=payload))
        replay = await service.claim_turn(session.session_id, principal, 't', 'hash')
        assert replay.response == payload
        assert replay.session_id == session.session_id and replay.turn_id == 't'
        async with factory() as db:
            stored = await db.get(CommerceTurn, (session.session_id, 't'))
            assert stored.response_json == payload
    asyncio.run(scenario())


@pytest.mark.parametrize('field', ['response', 'state'])
@pytest.mark.parametrize('invalid', [
    object(), (1, 2), float('nan'), ShoppingIntent(),
])
def test_non_json_completion_does_not_save(
    migrated_mysql_database, field, invalid,
):
    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        service = ShoppingSessionService(factory)
        principal = make_principal('test', 'json')
        session = await service.create(principal)
        claim = await service.claim_turn(session.session_id, principal, 't', 'hash')
        response = TurnResponse(
            request_id='r', session_id=session.session_id, turn_id='t',
            intent='shop', status='clarify',
        ).model_dump(mode='json')
        with pytest.raises(ValueError):
            completion = TurnCompletion(
                session_id=session.session_id, turn_id='t', response=response,
                product_ids=['product'], sku_ids=['sku'],
            )
            getattr(completion, field)['invalid'] = invalid
            await service.complete_turn(claim, completion)
        owned = await service.get_owned(session.session_id, principal)
        assert owned.version == 1 and owned.state == {}
        assert await service.seen(session.session_id, principal) == (set(), set())
        async with factory() as db:
            turn = await db.get(CommerceTurn, (session.session_id, 't'))
            assert turn.status == 'processing' and turn.response_json is None
    asyncio.run(scenario())


@pytest.mark.parametrize('field', ['response', 'state'])
@pytest.mark.parametrize('mutated', [False, True])
def test_bytes_key_completion_does_not_save(
    migrated_mysql_database, field, mutated,
):
    async def scenario():
        factory, catalog, prices, stock, categories, products, skus = await seed(
            migrated_mysql_database,
        )
        service = ShoppingSessionService(factory)
        principal = make_principal('test', 'bytes-key')
        session = await service.create(principal)
        claim = await service.claim_turn(session.session_id, principal, 't', 'hash')
        data = {
            'session_id': session.session_id, 'turn_id': 't',
            'response': {
                'request_id': 'r', 'session_id': session.session_id,
                'turn_id': 't', 'intent': 'shop', 'status': 'clarify',
            },
            'state': {'purpose': '通勤'},
            'product_ids': [products[0].id], 'sku_ids': [skus[0].id],
        }
        with pytest.raises(ValueError):
            if mutated:
                completion = TurnCompletion(**data)
                getattr(completion, field)[b'non_json_key'] = True
            else:
                data[field][b'non_json_key'] = True
                completion = TurnCompletion(**data)
            await service.complete_turn(claim, completion)
        owned = await service.get_owned(session.session_id, principal)
        assert owned.version == 1 and owned.state == {}
        assert await service.seen(session.session_id, principal) == (set(), set())
        async with factory() as db:
            turn = await db.get(CommerceTurn, (session.session_id, 't'))
            assert turn.status == 'processing' and turn.response_json is None
    asyncio.run(scenario())


def test_turn_claim_persists_and_recovers(migrated_mysql_database):
    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        service = ShoppingSessionService(factory)
        a = make_principal('test', 'a')
        s = await service.create(a)
        first = await service.claim_turn(s.session_id, a, 't1', 'hash')
        with pytest.raises(CommerceError, match='turn_processing'):
            await service.claim_turn(s.session_id, a, 't1', 'hash')
        with pytest.raises(CommerceError, match='idempotency_conflict'):
            await service.claim_turn(s.session_id, a, 't1', 'different')
        with pytest.raises(CommerceError, match='turn_processing'):
            await service.claim_turn(s.session_id, a, 't2', 'hash')
        async with factory() as db:
            await db.execute(update(CommerceSession).where(CommerceSession.id == s.session_id).values(turn_lease_expires_at=utcnow()-timedelta(seconds=1)))
            await db.commit()
        recovered = await service.claim_turn(s.session_id, a, 't1', 'hash')
        assert recovered.lease_token != first.lease_token
        response = TurnResponse(request_id='r', session_id=s.session_id, turn_id='t1', intent='unknown', status='clarify', question='想买什么？')
        completion = TurnCompletion(
            session_id=s.session_id, turn_id='t1',
            response=response.model_dump(mode='json'), state={'purpose':'通勤'},
        )
        with pytest.raises(CommerceError, match='turn_lease_lost'):
            await service.complete_turn(first, completion)
        async with factory() as db:
            with pytest.raises(CommerceError, match='turn_lease_lost'):
                await service.assert_current_turn(db, first)
        await service.complete_turn(recovered, completion)
        replay = await service.claim_turn(s.session_id, a, 't1', 'hash')
        assert isinstance(replay, TurnReplay)
        assert replay.response == response.model_dump(mode='json')
        async with factory() as db:
            stored = await db.get(CommerceTurn, (s.session_id, 't1'))
            assert stored.response_json == response.model_dump(mode='json')
        owned = await service.get_owned(s.session_id, a)
        assert owned.version == 2 and owned.state == {'purpose':'通勤'}
        other = await service.claim_turn(s.session_id, a, 't2', 'hash')
        assert other.session_version == 2
    asyncio.run(scenario())


def test_concurrent_claims_have_one_effective_lease(migrated_mysql_database):
    async def scenario():
        factory = create_session_factory(migrated_mysql_database)
        sessions = ShoppingSessionService(factory)
        a = make_principal('test', 'concurrent')
        s = await sessions.create(a)
        results = await asyncio.gather(*(sessions.claim_turn(s.session_id,a,'same','hash') for _ in range(5)), return_exceptions=True)
        from ecom.modules.sessions.schemas import TurnClaim
        assert sum(isinstance(r, TurnClaim) for r in results) == 1
        assert sum(isinstance(r, CommerceError) and r.code == 'turn_processing' for r in results) == 4
    asyncio.run(scenario())

"""轮次租约、持久回放与主体隔离。"""
import asyncio
from datetime import timedelta
import pytest
from sqlalchemy import update, select
from db.base import utcnow
from db.session import create_session_factory
from identity.types import make_principal
from shopping_session.service import ShoppingSessionService, CommerceError
from shopping_session.schemas import TurnCompletion, TurnReplay
from shopping_session.models import CommerceSession
from models.commerce import TurnResponse
from tests.conftest import downgrade_to, upgrade_to, read_schema


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
        completion = TurnCompletion(response=response, state={'purpose':'通勤'})
        with pytest.raises(CommerceError, match='turn_lease_lost'):
            await service.complete_turn(first, completion)
        async with factory() as db:
            with pytest.raises(CommerceError, match='turn_lease_lost'):
                await service.assert_current_turn(db, first)
        await service.complete_turn(recovered, completion)
        replay = await service.claim_turn(s.session_id, a, 't1', 'hash')
        assert isinstance(replay, TurnReplay) and replay.response == response
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
        from shopping_session.schemas import TurnClaim
        assert sum(isinstance(r, TurnClaim) for r in results) == 1
        assert sum(isinstance(r, CommerceError) and r.code == 'turn_processing' for r in results) == 4
    asyncio.run(scenario())

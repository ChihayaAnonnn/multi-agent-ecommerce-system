"""真实发现、来源记录、同轮回放和 SKU 选择。"""
import asyncio
import pytest
from db.session import create_session_factory
from identity.types import make_principal
from shopping_session.service import ShoppingSessionService, CommerceError
from commerce_orchestrator.facts import CommerceFactsReader
from commerce_orchestrator.service import CommerceOrchestrator
from models.commerce import TurnRequest
from tests.test_commerce_facts_mysql import seed


def test_turn_merges_explicit_constraints_and_replays(migrated_mysql_database):
    async def scenario():
        factory, catalog, prices, stock, cats, products, skus = await seed(migrated_mysql_database)
        sessions = ShoppingSessionService(factory)
        principal = make_principal('test','a')
        session = await sessions.create(principal)
        service = CommerceOrchestrator(sessions, CommerceFactsReader(catalog, prices, stock), catalog)
        first = await service.turn(principal, session.session_id, TurnRequest(turn_id='one', message='预算2000元', constraints={'category_id':cats[0].id}))
        assert first.status == 'shortlist' and len(first.items) <= 3
        again = await service.turn(principal, session.session_id, TurnRequest(turn_id='one', message='预算2000元', constraints={'category_id':cats[0].id}))
        assert first == again
        with pytest.raises(CommerceError, match='idempotency_conflict'):
            await service.turn(principal, session.session_id, TurnRequest(turn_id='one', message='别的消息'))
        changed = await service.turn(principal, session.session_id, TurnRequest(turn_id='two', message='改为1500元'))
        assert changed.constraints.budget_max_minor == 150000
        unsupported = await service.turn(principal, session.session_id, TurnRequest(turn_id='tracking', message='查一下我的订单'))
        assert unsupported.status == 'unsupported'
        missing_session = await sessions.create(principal)
        clarify = await service.turn(principal, missing_session.session_id, TurnRequest(turn_id='empty'))
        assert clarify.status == 'clarify' and clarify.question
    asyncio.run(scenario())


def test_seen_ids_must_come_from_server_response(migrated_mysql_database):
    async def scenario():
        factory, catalog, prices, stock, cats, products, skus = await seed(migrated_mysql_database)
        sessions = ShoppingSessionService(factory); a = make_principal('test','a')
        session = await sessions.create(a)
        service = CommerceOrchestrator(sessions, CommerceFactsReader(catalog, prices, stock), catalog)
        first = await service.turn(a, session.session_id, TurnRequest(turn_id='one', constraints={'category_id':cats[0].id, 'budget_max_minor':350}))
        seen_p, seen_s = await sessions.seen(session.session_id, a)
        assert seen_p == {products[0].id, products[2].id}
        assert seen_s == {skus[0].id, skus[2].id}
        selected = await service.turn(a, session.session_id, TurnRequest(turn_id='select', selected_product_id=products[0].id, selected_sku_id=skus[0].id))
        assert selected.items[0]['sku_id'] == skus[0].id
        with pytest.raises(CommerceError, match='not_seen'):
            await service.compare(a, session.session_id, [products[0].id,products[1].id])
        comparison = await service.compare(a, session.session_id, [products[0].id,products[2].id])
        assert comparison.dimensions[0]['code'] == 'wireless'
        assert len(comparison.products) == 2
        rejected = await service.turn(a, session.session_id, TurnRequest(turn_id='fake', selected_product_id=products[0].id, selected_sku_id=skus[1].id))
        assert rejected.status == 'clarify' and not rejected.items
        assert (await sessions.seen(session.session_id,a))[1] == seen_s
    asyncio.run(scenario())

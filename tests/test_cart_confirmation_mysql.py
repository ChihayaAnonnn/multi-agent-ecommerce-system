"""明确确认、来源租约与原子写车。"""
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from application.shopping.actions import CartActionService
from modules.cart.models import PendingAction
from modules.cart.service import CartService
from modules.identity.types import make_principal
from modules.inventory.models import DEFAULT_LOCATION_ID
from modules.sale_facts.service import SaleFactsReader
from modules.sessions.models import CommerceSession
from modules.sessions.service import CommerceError, ShoppingSessionService
from shared.time import utcnow
from tests.test_commerce_facts_mysql import seed


async def setup(url):
    factory,catalog,prices,stock,cats,products,skus=await seed(url)
    sessions=ShoppingSessionService(factory)
    a,b=make_principal('test','a'),make_principal('test','b')
    session=await sessions.create(a)
    cart_service=CartService(factory,SaleFactsReader(catalog,prices,stock))
    cart=await cart_service.get_cart(a)
    await sessions.record_seen(session.session_id,a,[products[0].id],[skus[0].id])
    return factory,sessions,cart_service,a,b,session,cart,prices,stock,skus


def test_propose_requires_seen_sku_and_explicit_confirmation(migrated_mysql_database):
    async def scenario():
        factory,sessions,service,a,b,s,cart,prices,stock,skus=await setup(migrated_mysql_database)
        actions = CartActionService(service.factory, sessions, service, None)
        with pytest.raises(CommerceError,match='not_seen'):
            await actions.propose_action(
                a,
                s.session_id,
                "add",
                {"sku_id": skus[1].id, "quantity": 1},
                cart.version,
            )
        claim=await sessions.claim_turn(s.session_id,a,'one','hash')
        p = await actions.propose_action(
            a,
            s.session_id,
            "add",
            {"sku_id": skus[0].id, "quantity": 2},
            cart.version,
            claim,
        )
        assert not (await service.get_cart(a)).items
        assert (
            await actions.propose_action(
                a,
                s.session_id,
                "add",
                {"sku_id": skus[0].id, "quantity": 2},
                cart.version,
                claim,
            )
        ).action_id == p.action_id
        with pytest.raises(CommerceError,match='idempotency_conflict'):
            await actions.propose_action(
                a,
                s.session_id,
                "add",
                {"sku_id": skus[0].id, "quantity": 1},
                cart.version,
                claim,
            )
        async with factory() as db:
            await db.execute(update(CommerceSession).where(CommerceSession.id==s.session_id).values(turn_lease_expires_at=utcnow()-timedelta(seconds=1)))
            await db.commit()
        with pytest.raises(CommerceError,match='turn_lease_lost'):
            await actions.propose_action(
                a,
                s.session_id,
                "add",
                {"sku_id": skus[0].id, "quantity": 2},
                cart.version,
                claim,
            )
        recovered=await sessions.claim_turn(s.session_id,a,'one','hash')
        assert (
            await actions.propose_action(
                a,
                s.session_id,
                "add",
                {"sku_id": skus[0].id, "quantity": 2},
                cart.version,
                recovered,
            )
        ).action_id == p.action_id
        declined=await actions.confirm(a,s.session_id,p.action_id,False,'cancel')
        assert declined.status=='declined' and not (await service.get_cart(a)).items
        with pytest.raises(CommerceError,match='action_already_resolved'):
            await actions.propose_action(
                a,
                s.session_id,
                "add",
                {"sku_id": skus[0].id, "quantity": 2},
                cart.version,
                recovered,
            )

    asyncio.run(scenario())


def test_confirm_rechecks_and_is_idempotent(migrated_mysql_database):
    async def scenario():
        factory,sessions,service,a,b,s,cart,prices,stock,skus=await setup(migrated_mysql_database)
        try:
            actions = CartActionService(service.factory, sessions, service, None)
            p = await actions.propose_action(
                a,
                s.session_id,
                "add",
                {"sku_id": skus[0].id, "quantity": 2},
                cart.version,
            )
            with pytest.raises(CommerceError,match='not_found'):
                await actions.confirm(b,s.session_id,p.action_id,True,'key')
            results = await asyncio.gather(
                *(
                    actions.confirm(a, s.session_id, p.action_id, True, "key")
                    for _ in range(4)
                )
            )
            assert all(r.status=='cart_updated' for r in results)
            view=await service.get_cart(a)
            assert (
                view.version == 2
                and view.items[0].quantity == 2
                and view.current_subtotal == 200
            )
            with pytest.raises(CommerceError,match='idempotency_conflict'):
                await actions.confirm(a,s.session_id,p.action_id,False,'key')
            p2 = await actions.propose_action(
                a,
                s.session_id,
                "set_quantity",
                {"sku_id": skus[0].id, "quantity": 3},
                view.version,
            )
            await prices.set_price(skus[0].id,150,1,'改价')
            result=await actions.confirm(a,s.session_id,p2.action_id,True,'changed')
            assert (
                result.error_code == "reconfirmation_required"
                and result.proposed_action
            )
            assert (await service.get_cart(a)).items[0].quantity==2
            confirmed = await actions.confirm(
                a, s.session_id, result.proposed_action.action_id, True, "new-price"
            )
            assert confirmed.cart.items[0].quantity==3
            current=confirmed.cart
            p3 = await actions.propose_action(
                a,
                s.session_id,
                "add",
                {"sku_id": skus[0].id, "quantity": 1},
                current.version,
            )
            await stock.adjust(skus[0].id,DEFAULT_LOCATION_ID,1,'stock-up','补货')
            changed=await actions.confirm(a,s.session_id,p3.action_id,True,'new-stock')
            assert changed.error_code=='reconfirmation_required'
            assert (await service.get_cart(a)).items[0].quantity==3
        finally:
            await factory.kw['bind'].dispose()
    asyncio.run(scenario())


def test_internal_stock_version_change_and_cart_conflict(migrated_mysql_database):
    async def scenario():
        _,sessions,service,a,_,s,cart,_,stock,skus=await setup(migrated_mysql_database)
        actions = CartActionService(service.factory, sessions, service, None)
        other=await sessions.create(a)
        await sessions.record_seen(other.session_id,a,[],[skus[0].id])
        p = await actions.propose_action(
            a, s.session_id, "add", {"sku_id": skus[0].id, "quantity": 1}, cart.version
        )
        stale = await actions.propose_action(
            a,
            other.session_id,
            "add",
            {"sku_id": skus[0].id, "quantity": 1},
            cart.version,
        )
        await stock.adjust(skus[0].id,DEFAULT_LOCATION_ID,1,'plus','调整')
        await stock.adjust(skus[0].id,DEFAULT_LOCATION_ID,-1,'minus','调整')
        assert (
            await actions.confirm(a, s.session_id, p.action_id, True, "ok")
        ).status == "cart_updated"
        with pytest.raises(CommerceError,match='cart_version_conflict'):
            await actions.confirm(a,other.session_id,stale.action_id,True,'stale')
        assert (await service.get_cart(a)).items[0].quantity==1
        remove = await actions.propose_action(
            a, s.session_id, "remove", {"sku_id": skus[0].id}, 2
        )
        assert not (
            await actions.confirm(a, s.session_id, remove.action_id, True, "remove")
        ).cart.items

    asyncio.run(scenario())


def test_confirmation_dependency_failure_rolls_back(migrated_mysql_database):
    from application.shopping.facts import CommerceDependencyError
    from modules.pricing.service import PricingDependencyError
    async def scenario():
        factory, sessions, service, a, _, s, cart, _, _, skus = await setup(
            migrated_mysql_database
        )
        actions = CartActionService(service.factory, sessions, service, None)
        p = await actions.propose_action(
            a, s.session_id, "add", {"sku_id": skus[0].id, "quantity": 1}, cart.version
        )
        original=service.facts.pricing.lock_prices
        async def offline(db,ids):
            raise PricingDependencyError('offline')
        service.facts.pricing.lock_prices=offline
        with pytest.raises(CommerceDependencyError):
            await actions.confirm(a,s.session_id,p.action_id,True,'failure')
        service.facts.pricing.lock_prices=original
        assert not (await service.get_cart(a)).items
        async with factory() as db:
            action=await db.get(PendingAction,p.action_id)
            assert action.status=='pending' and action.confirmation_key is None
    asyncio.run(scenario())


def test_seen_sku_still_must_match_current_hard_constraints(migrated_mysql_database):
    async def scenario():
        factory, sessions, service, a, _, s, cart, _, _, skus = await setup(
            migrated_mysql_database
        )
        actions = CartActionService(service.factory, sessions, service, None)
        async with factory() as db:
            await db.execute(update(CommerceSession).where(CommerceSession.id==s.session_id).values(state_json={'budget_max_minor':50}))
            await db.commit()
        with pytest.raises(CommerceError,match='constraints_mismatch'):
            await actions.propose_action(
                a,
                s.session_id,
                "add",
                {"sku_id": skus[0].id, "quantity": 1},
                cart.version,
            )
        assert not (await service.get_cart(a)).items
    asyncio.run(scenario())

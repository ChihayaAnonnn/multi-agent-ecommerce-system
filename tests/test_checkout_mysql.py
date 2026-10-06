"""预览事实围栏与宿主超时的持久恢复。"""
import asyncio

import pytest
from sqlalchemy import select

from ecom.application.shopping.actions import CartActionService
from ecom.infra.checkout_gateway import (
    DemoHostCheckoutGateway,
    UnavailableHostCheckoutGateway,
)
from ecom.modules.checkout.models import CheckoutHandoff
from ecom.modules.checkout.service import CheckoutService
from ecom.modules.sessions.service import CommerceError
from tests.test_cart_confirmation_mysql import setup


async def ready_cart(url):
    state=await setup(url)
    factory,sessions,cart,a,b,s,view,prices,stock,skus=state
    actions = CartActionService(factory, sessions, cart, None)
    action = await actions.propose_action(
        a, s.session_id, "add", {"sku_id": skus[0].id, "quantity": 1}, view.version
    )
    result=await actions.confirm(a,s.session_id,action.action_id,True,'add')
    return state,result.cart


def test_preview_rejects_empty_or_unbuyable_cart(migrated_mysql_database):
    async def scenario():
        _,sessions,cart,a,_,s,view,_,_,_=await setup(migrated_mysql_database)
        service=CheckoutService(cart.factory,cart,DemoHostCheckoutGateway())
        actions=CartActionService(cart.factory,sessions,cart,service)
        empty=await actions.preview_checkout(a,s.session_id,view.version)
        assert not empty.checkout_eligible and empty.action_id is None
        assert empty.errors[0]['code']=='empty_cart'
    asyncio.run(scenario())


def test_handoff_rechecks_and_recovers_timeout(migrated_mysql_database):
    class TimeoutOnce(DemoHostCheckoutGateway):
        def __init__(self):
            self.ids=[]
        async def create_handoff(self,snapshot,request_id):
            self.ids.append(request_id)
            if len(self.ids)==1:
                raise TimeoutError('timeout')
            return await super().create_handoff(snapshot,request_id)
    async def scenario():
        (factory,sessions,cart,a,b,s,_,prices,stock,skus),view=await ready_cart(migrated_mysql_database)
        gateway=TimeoutOnce()
        service=CheckoutService(factory,cart,gateway)
        actions=CartActionService(factory,sessions,cart,service)
        preview=await actions.preview_checkout(a,s.session_id,view.version)
        assert preview.current_subtotal==100 and preview.action_id
        await prices.set_price(skus[0].id,150,1,'改价')
        changed=await actions.confirm_handoff(
            a,s.session_id,preview.action_id,True,'old'
        )
        assert changed.error_code=='reconfirmation_required' and changed.preview.action_id
        aid=changed.preview.action_id
        failed=await actions.confirm_handoff(a,s.session_id,aid,True,'handoff')
        assert failed.status=='unavailable' and failed.destination is None
        recovered=await actions.confirm_handoff(a,s.session_id,aid,True,'handoff')
        assert recovered.status=='handoff' and recovered.destination.demo
        assert gateway.ids[0]==gateway.ids[1]==recovered.handoff_id
        async with factory() as db:
            rows=list(await db.scalars(select(CheckoutHandoff)))
            assert len(rows)==1 and rows[0].snapshot_json['items'][0]['unit_amount_minor']==150
        with pytest.raises(CommerceError,match='not_found'):
            await actions.get_handoff(b,s.session_id,recovered.handoff_id)
        replay=await actions.confirm_handoff(a,s.session_id,aid,True,'handoff')
        assert replay.handoff_id==recovered.handoff_id and len(gateway.ids)==2
    asyncio.run(scenario())


def test_no_host_is_503_in_production(migrated_mysql_database):
    async def scenario():
        (factory,sessions,cart,a,_,s,_,_,_,_),view=await ready_cart(migrated_mysql_database)
        service=CheckoutService(factory,cart,UnavailableHostCheckoutGateway())
        actions=CartActionService(factory,sessions,cart,service)
        preview=await actions.preview_checkout(a,s.session_id,view.version)
        with pytest.raises(CommerceError,match='checkout_unavailable') as error:
            await actions.confirm_handoff(
                a,s.session_id,preview.action_id,True,'no-host'
            )
        assert error.value.code == 'checkout_unavailable'
        from ecom.modules.cart.models import PendingAction
        async with factory() as db:
            action=await db.get(PendingAction,preview.action_id)
            assert action.status=='pending' and action.confirmation_key is None
    asyncio.run(scenario())


def test_preview_source_fence_reuses_only_pending_action(migrated_mysql_database):
    from datetime import timedelta

    from sqlalchemy import update

    from ecom.modules.sessions.models import CommerceSession
    from ecom.shared.time import utcnow
    async def scenario():
        (factory,sessions,cart,a,_,s,_,_,_,_),view=await ready_cart(migrated_mysql_database)
        service=CheckoutService(factory,cart,DemoHostCheckoutGateway())
        actions=CartActionService(factory,sessions,cart,service)
        claim=await sessions.claim_turn(s.session_id,a,'checkout','hash')
        first=await actions.preview_checkout(a,s.session_id,view.version,claim)
        async with factory() as db:
            await db.execute(update(CommerceSession).where(CommerceSession.id==s.session_id).values(turn_lease_expires_at=utcnow()-timedelta(seconds=1)))
            await db.commit()
        with pytest.raises(CommerceError,match='turn_lease_lost'):
            await actions.preview_checkout(a,s.session_id,view.version,claim)
        current=await sessions.claim_turn(s.session_id,a,'checkout','hash')
        reused=await actions.preview_checkout(a,s.session_id,view.version,current)
        assert reused.action_id==first.action_id
        await actions.preview_checkout(a,s.session_id,view.version)
        with pytest.raises(CommerceError,match='action_already_resolved'):
            await actions.preview_checkout(a,s.session_id,view.version,current)
    asyncio.run(scenario())


def test_invalid_destination_never_becomes_ready(migrated_mysql_database):
    from ecom.modules.checkout.schemas import Destination
    class Malicious(DemoHostCheckoutGateway):
        async def create_handoff(self,snapshot,request_id):
            return Destination(kind='https',ref='javascript:alert(1)',demo=True)
    async def scenario():
        (factory,sessions,cart,a,_,s,_,_,_,_),view=await ready_cart(migrated_mysql_database)
        service=CheckoutService(factory,cart,Malicious())
        actions=CartActionService(factory,sessions,cart,service)
        preview=await actions.preview_checkout(a,s.session_id,view.version)
        response=await actions.confirm_handoff(
            a,s.session_id,preview.action_id,True,'bad-url'
        )
        assert response.status=='unavailable' and response.destination is None
        async with factory() as db:
            row=await db.scalar(select(CheckoutHandoff))
            assert row.status=='failed' and row.destination_ref is None
    asyncio.run(scenario())


def test_checkout_migration_roundtrip(migrated_mysql_database):
    from tests.conftest import downgrade_to, read_schema, upgrade_to
    downgrade_to(migrated_mysql_database,'0006_cart')
    upgrade_to(migrated_mysql_database,'0007_checkout')
    assert ('checkout_handoffs',('action_id',)) in read_schema(migrated_mysql_database)['uniques']
    downgrade_to(migrated_mysql_database,'0006_cart')
    assert 'checkout_handoffs' not in read_schema(migrated_mysql_database)['tables']
    upgrade_to(migrated_mysql_database,'0007_checkout')


def test_slow_host_has_deadline_and_no_database_lock(migrated_mysql_database):
    class Slow(DemoHostCheckoutGateway):
        async def create_handoff(self,snapshot,request_id):
            await asyncio.wait_for(cart.get_cart(a),0.5)
            async with factory() as db:
                row=await db.get(CheckoutHandoff,snapshot.handoff_id)
                observed.append(row.status)
                action=await cart.lock_pending_action(db,a,session_id,row.action_id)
                observed_actions.append(action.status)
            await asyncio.Event().wait()
    async def scenario():
        nonlocal factory,cart,a,session_id
        (factory,sessions,cart,a,_,s,_,_,_,_),view=await ready_cart(migrated_mysql_database)
        service=CheckoutService(factory,cart,Slow(),timeout_seconds=0.5)
        actions=CartActionService(factory,sessions,cart,service)
        session_id=s.session_id
        preview=await actions.preview_checkout(a,s.session_id,view.version)
        result=await asyncio.wait_for(
            actions.confirm_handoff(a,s.session_id,preview.action_id,True,'timeout'),2
        )
        assert result.status=='unavailable' and result.retryable and result.destination is None
        assert observed==['preparing']
        assert observed_actions==['consumed']
    observed=[]
    observed_actions=[]
    factory=cart=a=session_id=None
    asyncio.run(scenario())

"""聊天确认通过确定性门禁，与按钮共用命令，绕过图。"""
import asyncio
from tests.test_cart_confirmation_mysql import setup
from commerce_orchestrator.service import CommerceOrchestrator
from models.commerce import TurnRequest


def test_chat_confirm_uses_same_command_as_button(migrated_mysql_database):
    class NeverGraph:
        async def ainvoke(self,*args):
            raise AssertionError('聊天确认不得调用图或模型')
    async def scenario():
        _,sessions,cart,a,_,s,view,_,_,skus=await setup(migrated_mysql_database)
        service=CommerceOrchestrator(sessions,cart.facts,cart.facts.catalog);service.cart=cart
        service.graph=NeverGraph()
        p=await cart.propose_action(a,s.session_id,'add',{'sku_id':skus[0].id,'quantity':1},view.version)
        no_context=await service.turn(a,s.session_id,TurnRequest(turn_id='no-context',message='确认'))
        assert no_context.status=='clarify' and not (await cart.get_cart(a)).items
        ambiguous=await service.turn(a,s.session_id,TurnRequest(turn_id='ambiguous',message='改成两件再确认',respond_to_action_id=p.action_id,idempotency_key='ambiguous'))
        assert ambiguous.status=='clarify' and not (await cart.get_cart(a)).items
        wrong=await service.turn(a,s.session_id,TurnRequest(turn_id='wrong',message='确认结账',respond_to_action_id=p.action_id,idempotency_key='wrong'))
        assert wrong.status=='clarify' and not (await cart.get_cart(a)).items
        response=await service.turn(a,s.session_id,TurnRequest(turn_id='confirm',message='确认加购',respond_to_action_id=p.action_id,idempotency_key='same'))
        assert response.status=='cart_updated' and response.cart['items'][0]['quantity']==1
        replay=await cart.confirm(a,s.session_id,p.action_id,True,'same')
        assert replay.replayed and replay.cart.items[0].quantity==1
        assert (await cart.get_cart(a)).version==2
        next_action=await cart.propose_action(a,s.session_id,'add',{'sku_id':skus[0].id,'quantity':1},2)
        cancelled=await service.turn(a,s.session_id,TurnRequest(turn_id='cancel',message='取消',respond_to_action_id=next_action.action_id,idempotency_key='cancel'))
        assert cancelled.status=='clarify' and (await cart.get_cart(a)).items[0].quantity==1
    asyncio.run(scenario())


def test_foreign_chat_action_is_404(migrated_mysql_database):
    import pytest
    from shopping_session.service import CommerceError
    async def scenario():
        _,sessions,cart,a,b,s,view,_,_,skus=await setup(migrated_mysql_database)
        action=await cart.propose_action(a,s.session_id,'add',{'sku_id':skus[0].id,'quantity':1},view.version)
        other=await sessions.create(b)
        service=CommerceOrchestrator(sessions,cart.facts,cart.facts.catalog);service.cart=cart
        with pytest.raises(CommerceError,match='not_found') as error:
            await service.turn(b,other.session_id,TurnRequest(turn_id='foreign',message='确认',respond_to_action_id=action.action_id,idempotency_key='foreign'))
        assert error.value.status_code==404
        after=await service.turn(b,other.session_id,TurnRequest(turn_id='after-foreign',message='看购物车'))
        assert after.status=='cart_updated'
        assert not (await cart.get_cart(a)).items
    asyncio.run(scenario())


def test_stale_chat_claim_cannot_confirm_cart_or_checkout(migrated_mysql_database):
    import pytest
    from datetime import timedelta
    from sqlalchemy import update,select,func
    from shopping_session.models import CommerceSession
    from shopping_session.service import CommerceError
    from checkout.models import CheckoutHandoff
    from checkout.service import CheckoutService
    from checkout.gateway import DemoHostCheckoutGateway
    from db.base import utcnow
    class Host(DemoHostCheckoutGateway):
        calls=0
        async def create_handoff(self,*args):
            self.calls+=1
            return await super().create_handoff(*args)
    async def scenario():
        factory,sessions,cart,a,_,s,view,_,_,skus=await setup(migrated_mysql_database)
        service=CommerceOrchestrator(sessions,cart.facts,cart.facts.catalog);service.cart=cart
        host=Host();service.checkout=CheckoutService(factory,sessions,cart,host)
        async def reject_stale(action,phrase,key):
            claim=await sessions.claim_turn(s.session_id,a,key,key)
            async with factory() as db:
                await db.execute(update(CommerceSession).where(CommerceSession.id==s.session_id).values(turn_lease_expires_at=utcnow()-timedelta(seconds=1)));await db.commit()
            replacement=await sessions.claim_turn(s.session_id,a,key,key)
            with pytest.raises(CommerceError,match='turn_lease_lost'):
                await service.chat_confirmation(a,claim,TurnRequest(turn_id=key,message=phrase,respond_to_action_id=action,idempotency_key=key))
            assert (await cart.get_action(a,s.session_id,action)).status=='pending'
            await sessions.release_turn(replacement)
        action=await cart.propose_action(a,s.session_id,'add',{'sku_id':skus[0].id,'quantity':1},view.version)
        await reject_stale(action.action_id,'确认加购','old-cart')
        assert (await cart.get_cart(a)).version==1 and not (await cart.get_cart(a)).items
        await cart.confirm(a,s.session_id,action.action_id,True,'button')
        preview=await service.checkout.preview(a,s.session_id,2)
        await reject_stale(preview.action_id,'确认结账','old-checkout')
        assert host.calls==0
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(CheckoutHandoff))==0
    asyncio.run(scenario())

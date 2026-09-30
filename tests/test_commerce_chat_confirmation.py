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

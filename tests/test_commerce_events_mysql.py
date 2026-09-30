"""有限页面事件去重及故障与购物事务隔离。"""
import asyncio
import pytest
from sqlalchemy import select,func
from sqlalchemy.exc import OperationalError
from commerce_events.service import CommerceEventService
from commerce_events.models import CommerceEvent
from tests.test_cart_confirmation_mysql import setup
from shopping_session.service import CommerceError
from db.base import new_id


def test_event_dedupe_and_failure_is_non_blocking(migrated_mysql_database):
    async def scenario():
        factory,sessions,cart,a,b,s,view,_,_,skus=await setup(migrated_mysql_database)
        p=await cart.propose_action(a,s.session_id,'add',{'sku_id':skus[0].id,'quantity':1},view.version)
        assert (await cart.confirm(a,s.session_id,p.action_id,True,'add')).status=='cart_updated'
        events=CommerceEventService(factory,sessions)
        eid=new_id()
        receipt=await events.record(a,s.session_id,eid,'request','impression',{'sku_id':skus[0].id,'position':0})
        duplicate=await events.record(a,s.session_id,eid,'request','impression',{'sku_id':skus[0].id,'position':0})
        assert receipt.accepted and not receipt.duplicate and duplicate.accepted and duplicate.duplicate
        with pytest.raises(CommerceError,match='not_found'):
            await events.record(b,s.session_id,eid,'request','impression',{})
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(CommerceEvent))==1
        class Broken:
            def __call__(self):
                raise OperationalError('query',{},Exception('offline'))
        events.factory=Broken()
        failed=await events.record(a,s.session_id,new_id(),'request','error',{'status':'unavailable'})
        assert not failed.accepted and failed.error_code=='event_unavailable'
        assert (await cart.get_cart(a)).items[0].quantity==1
    asyncio.run(scenario())

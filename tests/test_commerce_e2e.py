"""真实账号、两个类目、独立图与持久交接的购物准备闭环。"""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from sqlalchemy import select,update,func
from tests.test_auth_api import identity_key_files,identity_settings,build_runtime,build_app,http_client,_register,_login,bearer
from tests.test_commerce_facts_mysql import seed
from api.commerce import router
from api.commerce_demo import configure_commerce_demo
from shopping_session.service import ShoppingSessionService,CommerceError
from shopping_session.models import CommerceSession
from commerce_orchestrator.facts import CommerceFactsReader
from commerce_orchestrator.service import CommerceOrchestrator
from commerce_orchestrator.utils import request_hash
from commerce_orchestrator.model import SafeIntentModel,SafeCandidateReranker
from cart.service import CartService
from cart.models import PendingAction
from checkout.service import CheckoutService
from checkout.gateway import DemoHostCheckoutGateway
from commerce_events.service import CommerceEventService
from models.commerce import TurnRequest
from db.base import utcnow,new_id
from tests.conftest import downgrade_to,upgrade_to,read_schema


def test_commerce_migration_full_roundtrip(migrated_mysql_database):
    downgrade_to(migrated_mysql_database,'0004_identity')
    for revision in ['0005_commerce_sessions','0006_cart','0007_checkout','0008_commerce_events']:
        upgrade_to(migrated_mysql_database,revision)
    assert 'commerce_events' in read_schema(migrated_mysql_database)['tables']
    downgrade_to(migrated_mysql_database,'0004_identity')
    assert 'carts' not in read_schema(migrated_mysql_database)['tables']
    upgrade_to(migrated_mysql_database,'head')
    assert 'checkout_handoffs' in read_schema(migrated_mysql_database)['tables']


def test_two_category_purchase_preparation(migrated_mysql_database,clean_redis,identity_key_files):
    class Model:
        async def parse(self,*args):
            from commerce_orchestrator.schemas import IntentUpdate
            return IntentUpdate(soft_preferences=['便携'])
        async def rank(self,ids,facts,prefs):
            return ids
    class Host(DemoHostCheckoutGateway):
        def __init__(self):
            self.ids=[]
        async def create_handoff(self,snapshot,request_id):
            self.ids.append(request_id)
            if len(self.ids)==1:
                raise TimeoutError('timeout')
            return await super().create_handoff(snapshot,request_id)
    class ObservedGraph:
        def __init__(self,graph):
            self.graph=graph;self.paths=[]
        async def ainvoke(self,state):
            result=await self.graph.ainvoke(state);self.paths.append(result['trace']);return result
    async def scenario():
        factory,catalog,prices,stock,cats,products,skus=await seed(migrated_mysql_database)
        settings=identity_settings(identity_key_files,commerce_demo_enabled=True,commerce_demo_checkout_enabled=True)
        runtime=build_runtime(migrated_mysql_database,clean_redis,settings,client=None)
        runtime.catalog,runtime.pricing,runtime.inventory=catalog,prices,stock
        runtime.shopping_sessions=ShoppingSessionService(factory)
        runtime.cart=CartService(factory,runtime.shopping_sessions,CommerceFactsReader(catalog,prices,stock))
        host=Host();runtime.checkout=CheckoutService(factory,runtime.shopping_sessions,runtime.cart,host)
        runtime.commerce=CommerceOrchestrator(runtime.shopping_sessions,runtime.cart.facts,catalog)
        runtime.commerce.cart=runtime.cart;runtime.commerce.checkout=runtime.checkout
        runtime.commerce.intent_model=SafeIntentModel(Model());runtime.commerce.reranker=SafeCandidateReranker(Model())
        runtime.commerce.configure_graph();observed=ObservedGraph(runtime.commerce.graph);runtime.commerce.graph=observed
        runtime.commerce_events=CommerceEventService(factory,runtime.shopping_sessions)
        app=build_app(runtime,settings);app.include_router(router);configure_commerce_demo(app,settings)
        try:
            async with http_client(app) as a,http_client(app) as b:
                assert (await _register(a,'shopper-a@example.com')).status_code==201
                assert (await _register(b,'shopper-b@example.com')).status_code==201
                auth_a=(await _login(a,'shopper-a@example.com')).json();auth_b=(await _login(b,'shopper-b@example.com')).json()
                ha,hb=bearer(auth_a['access_token']),bearer(auth_b['access_token'])
                sid=(await a.post('/api/v1/commerce/sessions',headers=ha)).json()['session_id']
                other=(await b.post('/api/v1/commerce/sessions',headers=hb)).json()['session_id']
                path=f'/api/v1/commerce/sessions/{sid}'
                discovered=await a.post(path+'/turns',headers=ha,json={'turn_id':'shop','message':'预算8元','constraints':{'category_id':cats[0].id,'hard_conditions':[{'code':'wireless','value':True}]}})
                assert discovered.status_code==200,discovered.text
                shortlist=discovered.json();assert shortlist['status']=='shortlist' and len(shortlist['items'])==2
                product_ids=[i['product_id'] for i in shortlist['items']]
                comparison=(await a.post(path+'/turns',headers=ha,json={'turn_id':'compare','message':'比较','product_ids':product_ids})).json()
                assert comparison['status']=='compare' and len(comparison['comparison']['products'])==2
                selected=(await a.post(path+'/turns',headers=ha,json={'turn_id':'select','selected_product_id':products[0].id,'selected_sku_id':skus[0].id})).json()
                assert selected['items'][0]['sku_id']==skus[0].id
                proposed=(await a.post(path+'/turns',headers=ha,json={'turn_id':'add','message':'加入购物车','selected_sku_id':skus[0].id,'quantity':1,'expected_cart_version':1})).json()
                assert proposed['status']=='confirm_action'
                aid=proposed['proposed_action']['action_id'];calls=len(observed.paths)
                confirmed=await a.post(path+'/turns',headers=ha,json={'turn_id':'confirm','message':'确认加购','respond_to_action_id':aid,'idempotency_key':'confirm'})
                assert confirmed.status_code==200,confirmed.text
                assert confirmed.json()['status']=='cart_updated' and len(observed.paths)==calls
                cart=(await a.get(path+'/cart',headers=ha)).json();assert cart['items'][0]['quantity']==1
                preview=(await a.post(path+'/turns',headers=ha,json={'turn_id':'checkout','message':'结账','expected_cart_version':cart['version']})).json()['proposed_action']
                await prices.set_price(skus[0].id,150,1,'改价')
                changed=(await a.post(path+'/checkout-handoff',headers=ha,json={'action_id':preview['action_id'],'approved':True,'idempotency_key':'old-price'})).json()
                assert changed['error_code']=='reconfirmation_required'
                payload={'action_id':changed['preview']['action_id'],'approved':True,'idempotency_key':'handoff'}
                failed=(await a.post(path+'/checkout-handoff',headers=ha,json=payload)).json()
                assert failed['status']=='unavailable' and failed['destination'] is None
                ready=(await a.post(path+'/checkout-handoff',headers=ha,json=payload)).json()
                assert ready['status']=='handoff' and ready['destination']['demo']
                assert ready['handoff_id']==failed['handoff_id']==host.ids[0]==host.ids[1]
                assert (await b.get(path+'/cart',headers=hb)).status_code==404
                assert (await b.post(f'/api/v1/commerce/sessions/{other}/actions/{aid}/confirm',headers=hb,json={'approved':True,'idempotency_key':'attack'})).status_code==404
                assert (await b.get(path+f"/handoffs/{ready['handoff_id']}",headers=hb)).status_code==404
                bshort=(await b.post(f'/api/v1/commerce/sessions/{other}/turns',headers=hb,json={'turn_id':'headphones','constraints':{'category_id':cats[1].id}})).json()
                assert bshort['items'] and all(i['product_id'] in {products[n].id for n in [1,3,5,7]} for i in bshort['items'])
                principal=await runtime.identity.verify(auth_a['access_token'])
                resume=TurnRequest(turn_id='resume',message='加入购物车',selected_sku_id=skus[0].id,quantity=1,expected_cart_version=cart['version'])
                claim=await runtime.shopping_sessions.claim_turn(sid,principal,resume.turn_id,request_hash(resume.model_dump(mode='json')))
                crashed=await observed.ainvoke({'principal':principal,'claim':claim,'turn':resume})
                original=crashed['response'].proposed_action['action_id']
                async with factory() as db:
                    await db.execute(update(CommerceSession).where(CommerceSession.id==sid).values(turn_lease_expires_at=utcnow()-timedelta(seconds=1)));await db.commit()
                restored=await a.post(path+'/turns',headers=ha,json=resume.model_dump(mode='json'))
                assert restored.status_code==200,restored.text
                assert restored.json()['proposed_action']['action_id']==original
                async with factory() as db:
                    assert await db.scalar(select(func.count()).select_from(PendingAction).where(PendingAction.session_id==sid,PendingAction.source_turn_id=='resume'))==1
                event={'event_id':new_id(),'session_id':sid,'request_id':shortlist['request_id'],'kind':'impression','payload':{'product_id':products[0].id,'position':0}}
                assert (await a.post('/api/v1/commerce/events',headers=ha,json=event)).json()['accepted']
                assert (await a.post('/api/v1/commerce/events',headers=ha,json=event)).json()['duplicate']
                assert (await b.post('/api/v1/commerce/events',headers=hb,json=event)).status_code==404
                assert (await a.get('/demo/commerce')).status_code==200
                assert any('compare' in p for p in observed.paths) and any('preview_checkout' in p for p in observed.paths)
                assert all(p[-1]=='finalize' for p in observed.paths)
                assert all(body.get('status') not in {'paid','order_created','payment_success'} for body in [shortlist,selected,confirmed.json(),ready])
        finally:
            await runtime.redis.aclose()
    asyncio.run(scenario())


def test_demo_is_never_mounted_in_production():
    from fastapi import FastAPI
    from httpx import ASGITransport,AsyncClient
    async def scenario():
        app=FastAPI();configure_commerce_demo(app,SimpleNamespace(identity_environment='production',commerce_demo_enabled=True))
        async with AsyncClient(transport=ASGITransport(app),base_url='https://test') as client:
            assert (await client.get('/demo/commerce')).status_code==404
    asyncio.run(scenario())

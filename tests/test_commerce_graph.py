"""每种路由仅进入明确分支，并且不启用第二份持久图状态。"""
import asyncio
import pytest
from types import SimpleNamespace
from commerce_orchestrator.graph import build_commerce_graph,CommerceGraphDependencies
from commerce_orchestrator.schemas import ShoppingIntent
from shopping_session.schemas import TurnClaim
from identity.types import make_principal
from models.commerce import TurnRequest
from commerce_orchestrator.facts import CommerceDependencyError
from commerce_orchestrator.model import SafeIntentModel,SafeCandidateReranker
from cart.schemas import CartView
from db.base import utcnow


@pytest.mark.parametrize('message,expected', [('买东西','shortlist'),('看购物车','cart_updated'),('加入购物车','confirm_action'),('结账','confirm_action'),('查订单','unsupported'),('售后退款','unsupported'),('比较','compare'),('','clarify')])
def test_graph_routes_only_to_intended_branch(message,expected):
    class Discover:
        scan_limit=200
        async def discover(self,intent):
            return [],False
    class Cart:
        async def get_cart(self,principal):
            return CartView(cart_id='cart',version=1,current_subtotal=0,checkout_eligible=False,checked_at=utcnow())
        async def propose_action(self,principal,sid,kind,target,version,claim):
            assert claim.lease_token=='lease'
            from cart.schemas import PendingActionView
            return PendingActionView(action_id='action',kind=kind,status='pending',expected_cart_version=1,payload=target,summary={},expires_at=utcnow())
    class Comparison:
        async def compare(self,principal,sid,ids):
            from commerce_orchestrator.compare import CompareResponse
            return CompareResponse(products=[{'product_id':i} for i in ids])
    class Checkout:
        async def preview(self,principal,sid,version,claim):
            assert claim.lease_token=='lease'
            from checkout.schemas import CheckoutPreview
            return CheckoutPreview(action_id='action',checkout_eligible=True,current_subtotal=100,cart_version=1,checked_at=utcnow())
    async def scenario():
        deps=CommerceGraphDependencies(discovery=Discover(),facts=SimpleNamespace(),comparison=Comparison(),
            catalog=SimpleNamespace(),sessions=SimpleNamespace(),cart=Cart(),checkout=Checkout(),
            intent_model=SafeIntentModel(),reranker=SafeCandidateReranker())
        graph=build_commerce_graph(deps)
        p=make_principal('test','a')
        claim=TurnClaim(session_id='session',principal_key=p.principal_key,turn_id='t',lease_token='lease',session_version=1,state={})
        result=await graph.ainvoke({'principal':p,'claim':claim,'turn':TurnRequest(turn_id='t',message=message,
            constraints={'category_id':'category'} if message else None,product_ids=['p1','p2'] if message=='比较' else [],selected_sku_id='sku' if message=='加入购物车' else None,quantity=1,expected_cart_version=1)})
        assert result['response'].status==expected
        assert result['trace'][0:2]==['parse_intent','validate_route'] and result['trace'][-1]=='finalize'
        assert graph.checkpointer is None
        if expected=='shortlist':
            assert result['trace']==['parse_intent','validate_route','discover','quote_filter','rank_explain','finalize']
    asyncio.run(scenario())


def test_graph_uses_only_requoted_facts_and_no_empty_fallback():
    from catalog.schemas import SKUDetailView
    from pricing.schemas import PriceView
    from inventory.schemas import AvailabilityView
    from commerce_orchestrator.schemas import SKUQuote
    class Discovery:
        async def discover(self,intent):
            return [{'sku_id':'sku','product_id':'product','name':'商品','price':{'amount_minor':1}}],False
    class Facts:
        async def quote_many(self,ids):
            return {'sku':SKUQuote(sku_id='sku',sku=SKUDetailView(id='sku',product_id='product',sku_code='s',option_signature='default',status='active',product_status='active',version=1),
                price=PriceView(sku_id='sku',currency='CNY',amount_minor=100,version=1,effective_at=utcnow(),read_at=utcnow()),
                availability=AvailabilityView(sku_id='sku',status='available',available_quantity=1,read_at=utcnow()),purchasable=True,reason='purchasable',read_at=utcnow())}
    class BadModel:
        async def rank(self,*args):
            return ['invented']
    class Offline:
        async def quote_many(self,ids):
            raise CommerceDependencyError('offline')
    async def scenario():
        p=make_principal('test','a');claim=TurnClaim(session_id='session',principal_key=p.principal_key,turn_id='t',lease_token='lease',session_version=1)
        state={'principal':p,'claim':claim,'turn':TurnRequest(turn_id='t',constraints={'category_id':'category'})}
        deps=CommerceGraphDependencies(discovery=Discovery(),facts=Facts(),comparison=None,catalog=None,sessions=None,reranker=SafeCandidateReranker(BadModel()))
        response=(await build_commerce_graph(deps).ainvoke(state))['response']
        assert response.status=='shortlist' and response.items[0]['price']['amount_minor']==100
        assert [i['sku_id'] for i in response.items]==['sku']
        deps.facts=Offline()
        response=(await build_commerce_graph(deps).ainvoke(state))['response']
        assert response.status=='unavailable' and not response.items
        assert response.errors[0]['code']=='dependency_unavailable'
    asyncio.run(scenario())

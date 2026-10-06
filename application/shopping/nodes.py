"""异步受控节点；不读写 ORM、不确认车、不调用宿主。"""
import structlog
from sqlalchemy.exc import SQLAlchemyError

from application.shopping.contracts import TurnResponse
from application.shopping.facts import CommerceDependencyError
from application.shopping.intent import parse_intent, route
from kernel.errors import CommerceError
from kernel.intent import IntentUpdate, ShoppingIntent
from shared.ids import new_id

logger=structlog.get_logger()


class CommerceNodes:
    def __init__(self,deps):
        self.deps=deps

    def wrapped(self,name):
        method=getattr(self,name)
        async def node(state):
            trace=state.get('trace',[])+[name]
            logger.info('commerce.node',node=name,session_id=state['claim'].session_id,turn_id=state['claim'].turn_id)
            try:
                result=await method(state)
            except (CommerceDependencyError,SQLAlchemyError):
                response=state['response'].model_copy(deep=True)
                response.status='unavailable';response.errors=[{'code':'dependency_unavailable'}]
                result={'route':'unavailable','response':response,'candidates':[]}
            except CommerceError as exc:
                response=state['response'].model_copy(deep=True)
                response.status='clarify';response.errors=[{'code':exc.code}]
                response.question='请按当前展示的商品、数量和条件重新选择。'
                result={'route':'clarify','response':response,'candidates':[]}
            return dict(result,trace=trace)
        return node

    async def parse_intent(self,state):
        turn,claim=state['turn'],state['claim']
        current=ShoppingIntent.model_validate(claim.state)
        current=current.apply(parse_intent(turn.message,current))
        if turn.constraints is not None:
            current=current.apply(IntentUpdate.model_validate(turn.constraints.model_dump(exclude_unset=True)))
        intent=route(turn.message,turn)
        degraded=False
        if intent=='shop':
            update,degraded=await self.deps.intent_model.parse_result(turn.message,current)
            current=current.apply(update)
        response=TurnResponse(request_id=new_id(),session_id=claim.session_id,turn_id=claim.turn_id,intent=intent,status='clarify',constraints=current)
        return {'current':current,'claim':claim.model_copy(update={'state':current.model_dump(mode='json')}),
            'route':intent,'response':response,'model_degraded':degraded,'candidates':[],'facts':{}}

    async def validate_route(self,state):
        route_name=state['route'];turn=state['turn']
        if route_name in {'order_tracking','after_sales'}:
            route_name='unsupported'
        elif route_name=='shop' and not state['current'].category_id and not state['current'].query_text:
            route_name='clarify'
        elif route_name=='cart_mutation' and (not turn.selected_sku_id or turn.expected_cart_version is None or (turn.quantity is None and not any(w in turn.message for w in ['移除','删除']))):
            route_name='clarify'
        elif route_name=='checkout' and turn.expected_cart_version is None:
            route_name='clarify'
        return {'route':route_name}

    async def discover(self,state):
        if state['route']=='selection':
            t=state['turn']
            candidates,incomplete=await self.deps.discovery.select(state['principal'],state['claim'].session_id,t.selected_product_id,t.selected_sku_id,self.deps.sessions,state['current'])
        else:
            candidates,incomplete=await self.deps.discovery.discover(state['current'])
        response=state['response'].model_copy(deep=True);response.incomplete=incomplete
        return {'candidates':candidates,'candidate_ids':[i['sku_id'] for i in candidates],'response':response}

    async def quote_filter(self,state):
        candidates=state['candidates']
        quotes=await self.deps.facts.quote_many([i['sku_id'] for i in candidates]) if candidates else {}
        items=[];intent=state['current']
        for item in candidates:
            quote=quotes.get(item['sku_id'])
            if quote and quote.purchasable and quote.sku.product_id==item['product_id'] and (
                intent.budget_max_minor is None or quote.price.amount_minor<=intent.budget_max_minor):
                items.append(dict(item,**quote.model_dump(mode='json')))
        return {'candidates':items,'candidate_ids':[i['sku_id'] for i in items],
            'facts':{i['sku_id']:i for i in items}}

    async def rank_explain(self,state):
        ids,degraded=await self.deps.reranker.rank_result(state['candidate_ids'],state['facts'],state['current'].soft_preferences)
        response=state['response'].model_copy(deep=True)
        response.items=[dict(state['facts'][i],reason_text='符合已确认条件，价格与库存已核验') for i in ids]
        response.status='shortlist'
        return {'response':response,'model_degraded':state.get('model_degraded',False) or degraded}

    async def compare(self,state):
        response=state['response'].model_copy(deep=True)
        response.comparison=(await self.deps.comparison.compare(state['principal'],state['claim'].session_id,state['turn'].product_ids)).model_dump(mode='json')
        response.status='compare'
        return {'response':response}

    async def read_cart(self,state):
        response=state['response'].model_copy(deep=True)
        if self.deps.cart is None:
            return await self.unavailable(state)
        response.cart=(await self.deps.cart.get_cart(state['principal'])).model_dump(mode='json');response.status='cart_updated'
        return {'response':response}

    async def propose_cart_action(self,state):
        if self.deps.actions is None:
            return await self.unavailable(state)
        turn=state['turn'];response=state['response'].model_copy(deep=True)
        kind='remove' if any(w in turn.message for w in ['移除','删除']) else 'set_quantity' if '改成' in turn.message else 'add'
        target={'sku_id':turn.selected_sku_id}
        if kind!='remove':
            target['quantity']=turn.quantity
        response.proposed_action = (
            await self.deps.actions.propose_action(
                state["principal"],
                state["claim"].session_id,
                kind,
                target,
                turn.expected_cart_version,
                state["claim"],
            )
        ).model_dump(mode="json")
        response.status='confirm_action'
        return {'response':response}

    async def preview_checkout(self,state):
        if self.deps.actions is None or self.deps.actions.checkout is None:
            return await self.unavailable(state)
        response=state['response'].model_copy(deep=True)
        preview=await self.deps.actions.preview_checkout(
            state['principal'],state['claim'].session_id,
            state['turn'].expected_cart_version,state['claim'],
        )
        response.proposed_action=preview.model_dump(mode='json');response.errors=preview.errors
        response.status='confirm_action' if preview.checkout_eligible else 'clarify'
        return {'response':response}

    async def clarify(self,state):
        response=state['response'].model_copy(deep=True);response.status='clarify'
        if response.question is None:
            response.question='请明确商品类目、具体规格或当前操作所需的数量与版本。'
        return {'response':response}

    async def unsupported(self,state):
        response=state['response'].model_copy(deep=True);response.status='unsupported'
        response.question='当前支持选品、比较与购物准备；订单和售后暂未开放。'
        return {'response':response}

    async def unavailable(self,state):
        response=state['response'].model_copy(deep=True);response.status='unavailable'
        if not response.errors:
            response.errors=[{'code':'dependency_unavailable'}]
        return {'response':response}

    async def finalize(self,state):
        response=state['response'].model_copy(deep=True)
        if state.get('model_degraded'):
            response.errors.append({'code':'model_degraded'})
        return {'response':TurnResponse.model_validate(response)}

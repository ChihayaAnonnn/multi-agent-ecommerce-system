"""轮次入口：主体与租约、图外确认门禁、原子保存来源与响应。"""
from commerce_orchestrator.schemas import ShoppingIntent
from commerce_orchestrator.discovery import DiscoveryService
from commerce_orchestrator.compare import CompareService,CompareResponse
from commerce_orchestrator.facts import CommerceFactsReader,CommerceDependencyError
from commerce_orchestrator.graph import build_commerce_graph,CommerceGraphDependencies
from commerce_orchestrator.model import SafeIntentModel,SafeCandidateReranker
from commerce_orchestrator.utils import request_hash
from shopping_session.schemas import TurnReplay,TurnCompletion
from shopping_session.service import ShoppingSessionService,CommerceError
from identity.types import Principal
from catalog.service import CatalogService
from models.commerce import TurnRequest,TurnResponse
from db.base import new_id


class CommerceOrchestrator:
    def __init__(self,sessions: ShoppingSessionService,facts: CommerceFactsReader,catalog: CatalogService,scan_limit: int=200):
        self.sessions,self.facts,self.catalog=sessions,facts,catalog
        self.discovery=DiscoveryService(facts,catalog,scan_limit)
        self.comparison=CompareService(sessions,catalog,facts)
        self.cart=self.checkout=None
        self.intent_model=SafeIntentModel();self.reranker=SafeCandidateReranker()
        self.graph=None

    def configure_graph(self):
        self.graph=build_commerce_graph(CommerceGraphDependencies(discovery=self.discovery,facts=self.facts,
            comparison=self.comparison,catalog=self.catalog,sessions=self.sessions,cart=self.cart,checkout=self.checkout,
            intent_model=self.intent_model,reranker=self.reranker))

    async def compare(self,principal: Principal,session_id: str,product_ids: list[str]) -> CompareResponse:
        return await self.comparison.compare(principal,session_id,product_ids)

    async def chat_confirmation(self,principal: Principal,claim,turn: TurnRequest) -> TurnResponse:
        current=ShoppingIntent.model_validate(claim.state)
        response=TurnResponse(request_id=new_id(),session_id=claim.session_id,turn_id=turn.turn_id,
            intent='confirmation',status='clarify',constraints=current,question='请确认当前操作卡，或先提出明确的新修改。')
        if self.cart is None or not turn.respond_to_action_id or not turn.idempotency_key:
            return response
        action=await self.cart.get_action(principal,claim.session_id,turn.respond_to_action_id)
        expected={'add':'确认加购','set_quantity':'确认修改','remove':'确认移除','checkout_handoff':'确认结账'}[action.kind]
        phrase=turn.message.strip()
        if phrase not in {'确认','取消',expected}:
            return response
        if action.status=='pending' and not await self.cart.is_only_pending(principal,claim.session_id,action.id):
            return response
        approved=phrase!='取消'
        if action.kind=='checkout_handoff':
            if self.checkout is None:
                response.status='unavailable';response.errors=[{'code':'checkout_unavailable'}];return response
            result=await self.checkout.confirm_handoff(principal,claim.session_id,action.id,approved,turn.idempotency_key,source_turn=claim)
            response.handoff=result.model_dump(mode='json')
            response.status='handoff' if result.status=='handoff' else 'confirm_action' if result.preview else 'clarify' if result.status=='declined' else 'unavailable'
            if result.preview:
                response.proposed_action=result.preview.model_dump(mode='json')
            if result.error_code:
                response.errors=[{'code':result.error_code}]
        else:
            result=await self.cart.confirm(principal,claim.session_id,action.id,approved,turn.idempotency_key,source_turn=claim)
            response.cart=result.cart.model_dump(mode='json') if result.cart else None
            response.proposed_action=result.proposed_action.model_dump(mode='json') if result.proposed_action else None
            response.status='cart_updated' if result.status=='cart_updated' else 'confirm_action' if result.proposed_action else 'clarify'
            if result.error_code:
                response.errors=[{'code':result.error_code}]
        if result.status=='declined':
            response.action_result='declined'
        response.question=None if approved else '已取消，购物车未修改。'
        return response

    async def turn(self,principal: Principal,session_id: str,turn: TurnRequest) -> TurnResponse:
        normalized=turn.model_copy(update={'message':turn.message.strip()})
        claim=await self.sessions.claim_turn(session_id,principal,turn.turn_id,request_hash(normalized.model_dump(mode='json')))
        if isinstance(claim,TurnReplay):
            return claim.response
        if turn.respond_to_action_id or normalized.message in {'确认','确认加购','确认修改','确认移除','确认结账','取消'}:
            try:
                response=await self.chat_confirmation(principal,claim,normalized)
            except CommerceError as exc:
                if exc.status_code==404:
                    await self.sessions.release_turn(claim)
                    raise
                response=TurnResponse(request_id=new_id(),session_id=session_id,turn_id=turn.turn_id,
                    intent='confirmation',status='clarify',constraints=ShoppingIntent.model_validate(claim.state),
                    question='请核对当前待确认动作。',errors=[{'code':exc.code}])
            current=response.constraints
        else:
            if self.graph is None:
                self.configure_graph()
            result=await self.graph.ainvoke({'principal':principal,'claim':claim,'turn':normalized})
            response=TurnResponse.model_validate(result['response']);current=response.constraints
        await self.sessions.complete_turn(claim,TurnCompletion(response=response,state=current.model_dump(mode='json'),
            product_ids=[i['product_id'] for i in response.items],sku_ids=[i['sku_id'] for i in response.items]))
        return response

"""轮次入口：主体与租约、图外确认门禁、原子保存来源与响应。"""
from ecom.application.shopping.actions import CartActionService
from ecom.application.shopping.compare import CompareResponse, CompareService
from ecom.application.shopping.contracts import TurnRequest, TurnResponse
from ecom.application.shopping.ports import CommerceGraph
from ecom.kernel.errors import CommerceError
from ecom.kernel.intent import ShoppingIntent
from ecom.modules.sessions.schemas import (
    TurnCompletion,
    TurnReplay,
    validate_json_object,
)
from ecom.modules.sessions.service import ShoppingSessionService
from ecom.shared.hashing import request_hash
from ecom.shared.ids import new_id
from ecom.shared.principal import Principal


class CommerceOrchestrator:
    def __init__(
        self, sessions: ShoppingSessionService, graph: CommerceGraph,
        actions: CartActionService | None, comparison: CompareService,
    ) -> None:
        self.sessions, self.graph = sessions, graph
        self.actions, self.comparison = actions, comparison

    async def compare(self,principal: Principal,session_id: str,product_ids: list[str]) -> CompareResponse:
        return await self.comparison.compare(principal,session_id,product_ids)

    async def chat_confirmation(self,principal: Principal,claim,turn: TurnRequest) -> TurnResponse:
        current=ShoppingIntent.model_validate(claim.state)
        response=TurnResponse(request_id=new_id(),session_id=claim.session_id,turn_id=turn.turn_id,
            intent='confirmation',status='clarify',constraints=current,question='请确认当前操作卡，或先提出明确的新修改。')
        if (
            self.actions is None
            or not turn.respond_to_action_id
            or not turn.idempotency_key
        ):
            return response
        action = await self.actions.get_action(
            principal, claim.session_id, turn.respond_to_action_id
        )
        expected={'add':'确认加购','set_quantity':'确认修改','remove':'确认移除','checkout_handoff':'确认结账'}[action.kind]
        phrase=turn.message.strip()
        if phrase not in {'确认','取消',expected}:
            return response
        if action.status == "pending" and not await self.actions.is_only_pending(
            principal, claim.session_id, action.action_id
        ):
            return response
        approved=phrase!='取消'
        if action.kind=='checkout_handoff':
            if self.actions.checkout is None:
                response.status='unavailable';response.errors=[{'code':'checkout_unavailable'}];return response
            result = await self.actions.confirm_handoff(
                principal,
                claim.session_id,
                action.action_id,
                approved,
                turn.idempotency_key,
                source_turn=claim,
            )
            response.handoff=result.model_dump(mode='json')
            response.status='handoff' if result.status=='handoff' else 'confirm_action' if result.preview else 'clarify' if result.status=='declined' else 'unavailable'
            if result.preview:
                response.proposed_action=result.preview.model_dump(mode='json')
            if result.error_code:
                response.errors=[{'code':result.error_code}]
        else:
            result = await self.actions.confirm(
                principal,
                claim.session_id,
                action.action_id,
                approved,
                turn.idempotency_key,
                source_turn=claim,
            )
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
            replay = TurnReplay.model_validate(dict(claim))
            response = TurnResponse.model_validate(replay.response)
            if not (
                replay.session_id == response.session_id == session_id
                and replay.turn_id == response.turn_id == turn.turn_id
            ):
                raise CommerceError('invalid_completion')
            return response
        if turn.respond_to_action_id or normalized.message in {'确认','确认加购','确认修改','确认移除','确认结账','取消'}:
            try:
                response=await self.chat_confirmation(principal,claim,normalized)
            except CommerceError as exc:
                if exc.code == 'not_found':
                    await self.sessions.release_turn(claim)
                    raise
                response=TurnResponse(request_id=new_id(),session_id=session_id,turn_id=turn.turn_id,
                    intent='confirmation',status='clarify',constraints=ShoppingIntent.model_validate(claim.state),
                    question='请核对当前待确认动作。',errors=[{'code':exc.code}])
        else:
            result=await self.graph.ainvoke({'principal':principal,'claim':claim,'turn':normalized})
            response = result['response']
        response_data = (
            dict(response) if isinstance(response, TurnResponse) else response
        )
        if isinstance(response_data, dict):
            response_data = response_data.copy()
            constraints = response_data.get('constraints')
            if isinstance(constraints, ShoppingIntent):
                response_data['constraints'] = constraints.model_dump(
                    mode='python', warnings=False,
                )
        response_data = validate_json_object(response_data)
        response = TurnResponse.model_validate(response_data)
        if not (
            response.session_id == claim.session_id == session_id
            and response.turn_id == claim.turn_id == turn.turn_id
        ):
            raise CommerceError('invalid_completion')
        current = response.constraints
        completion = TurnCompletion(
            session_id=session_id, turn_id=turn.turn_id,
            response=response.model_dump(mode='json'),
            state=current.model_dump(mode='json'),
            product_ids=[item['product_id'] for item in response.items],
            sku_ids=[item['sku_id'] for item in response.items],
        )
        await self.sessions.complete_turn(claim, completion)
        return response

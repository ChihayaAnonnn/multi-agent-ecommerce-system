"""轮次入口：主体 → 租约 → 可信响应 → 原子保存来源。"""
import hashlib
import json
from commerce_orchestrator.schemas import ShoppingIntent, IntentUpdate
from commerce_orchestrator.intent import parse_intent, route
from commerce_orchestrator.discovery import DiscoveryService
from commerce_orchestrator.compare import CompareService, CompareResponse
from commerce_orchestrator.facts import CommerceFactsReader, CommerceDependencyError
from shopping_session.schemas import TurnReplay, TurnCompletion
from shopping_session.service import ShoppingSessionService, CommerceError
from identity.types import Principal
from catalog.service import CatalogService
from models.commerce import TurnRequest, TurnResponse
from db.base import new_id


from commerce_orchestrator.utils import request_hash

class CommerceOrchestrator:
    def __init__(self, sessions: ShoppingSessionService, facts: CommerceFactsReader, catalog: CatalogService, scan_limit: int = 200):
        self.sessions, self.facts, self.catalog = sessions, facts, catalog
        self.cart = None
        self.checkout = None
        self.discovery = DiscoveryService(facts, catalog, scan_limit)
        self.comparison = CompareService(sessions, catalog, facts)

    async def compare(self, principal: Principal, session_id: str, product_ids: list[str]) -> CompareResponse:
        return await self.comparison.compare(principal, session_id, product_ids)

    async def turn(self, principal: Principal, session_id: str, turn: TurnRequest) -> TurnResponse:
        normalized = turn.model_copy(update={'message':turn.message.strip()})
        claim = await self.sessions.claim_turn(session_id, principal, turn.turn_id, request_hash(normalized.model_dump(mode='json')))
        if isinstance(claim, TurnReplay):
            return claim.response
        current = ShoppingIntent.model_validate(claim.state)
        current = current.apply(parse_intent(normalized.message, current))
        if turn.constraints:
            current = current.apply(IntentUpdate.model_validate(turn.constraints.model_dump(exclude_unset=True)))
        claim = claim.model_copy(update={'state':current.model_dump(mode='json')})
        intent = route(normalized.message, turn)
        response = TurnResponse(request_id=new_id(), session_id=session_id, turn_id=turn.turn_id,
            intent=intent, status='clarify', constraints=current)
        try:
            if intent in {'order_tracking','after_sales'}:
                response.status = 'unsupported'
                response.question = '当前支持选品、比较和购物准备。'
            elif intent == 'cart_read' and self.cart is not None:
                response.cart = (await self.cart.get_cart(principal)).model_dump(mode='json')
                response.status = 'cart_updated'
            elif intent == 'cart_mutation' and self.cart is not None:
                kind = 'remove' if any(w in normalized.message for w in ['移除','删除']) else 'set_quantity' if '改成' in normalized.message else 'add'
                if not turn.selected_sku_id or (kind != 'remove' and turn.quantity is None):
                    response.question = '请选择具体规格并明确数量。'
                elif turn.expected_cart_version is None:
                    response.question = '请先读取购物车版本后再生成提案。'
                else:
                    target = {'sku_id':turn.selected_sku_id}
                    if kind != 'remove':
                        target['quantity'] = turn.quantity
                    response.proposed_action = (await self.cart.propose_action(principal,session_id,kind,target,turn.expected_cart_version,claim)).model_dump(mode='json')
                    response.status = 'confirm_action'
            elif intent == 'checkout' and self.checkout is not None:
                if turn.expected_cart_version is None:
                    response.question = '请先读取购物车版本。'
                else:
                    preview = await self.checkout.preview(principal,session_id,turn.expected_cart_version,claim)
                    response.proposed_action = preview.model_dump(mode='json')
                    response.status = 'confirm_action' if preview.checkout_eligible else 'clarify'
                    response.errors = preview.errors
            elif intent in {'cart_read','cart_mutation','checkout'}:
                response.status = 'unavailable'; response.errors = [{'code':'capability_unavailable'}]
            elif intent == 'compare':
                response.comparison = (await self.compare(principal,session_id,turn.product_ids)).model_dump(mode='json')
                response.status = 'compare'
            elif intent == 'selection':
                seen, _ = await self.sessions.seen(session_id,principal)
                products = await self.catalog.get_products([turn.selected_product_id] if turn.selected_product_id else [])
                product = products.get(turn.selected_product_id)
                if product is None or product.id not in seen or turn.selected_sku_id not in {s.id for s in product.skus}:
                    response.question = '请选择本会话已展示商品的实际规格。'
                else:
                    page = await self.facts.scan(current, self.discovery.scan_limit)
                    quotes = {q.sku_id:q for q in page.items}
                    q = quotes.get(turn.selected_sku_id)
                    if q is None:
                        response.question = '所选规格不符合当前条件或暂不可购买。'
                    else:
                        response.items = [dict(q.model_dump(mode='json'), product_id=product.id, name=product.name)]
                        response.status = 'shortlist'
            elif not current.category_id and not current.query_text:
                response.question = '想购买哪个类目或商品？'
            else:
                response.items, response.incomplete = await self.discovery.discover(current)
                response.status = 'shortlist'
        except CommerceDependencyError:
            response.status = 'unavailable'; response.errors = [{'code':'dependency_unavailable'}]
        except CommerceError as exc:
            response.status = 'clarify'; response.errors = [{'code':exc.code}]
            response.question = '请按当前展示的商品和条件重新选择。'
        await self.sessions.complete_turn(claim, TurnCompletion(response=response, state=current.model_dump(mode='json'),
            product_ids=[i['product_id'] for i in response.items], sku_ids=[i['sku_id'] for i in response.items]))
        return response

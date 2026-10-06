"""保守确定性解析；未确认文本只澄清，不推断硬约束。"""
import re

from ecom.kernel.intent import IntentUpdate, ShoppingIntent


def parse_intent(message: str, current: ShoppingIntent) -> IntentUpdate:
    match = re.search(r'(?:预算|改为|不超过|以内|最多)\s*(\d+)(?:\s*元)?', message)
    if match:
        return IntentUpdate(budget_max_minor=int(match[1])*100, sources={'budget_max_minor':'explicit'})
    return IntentUpdate()


def route(message: str, turn) -> str:
    if any(w in message for w in ['订单', '物流', '查单']):
        return 'order_tracking'
    if any(w in message for w in ['退货', '退款', '售后']):
        return 'after_sales'
    if any(w in message for w in ['结账', '去付款']):
        return 'checkout'
    if any(w in message for w in ['加入购物车', '加购', '改成', '移除', '删除']):
        return 'cart_mutation'
    if '购物车' in message:
        return 'cart_read'
    if turn.product_ids or any(w in message for w in ['比较', '对比']):
        return 'compare'
    if turn.selected_sku_id or turn.selected_product_id:
        return 'selection'
    return 'shop'

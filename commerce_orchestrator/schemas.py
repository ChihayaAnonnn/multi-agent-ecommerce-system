"""购物 DTO 与图状态的同对象兼容入口。"""

from commerce_orchestrator.state import CommerceGraphState
from kernel.intent import HardCondition, IntentUpdate, ShoppingIntent, Source
from kernel.quotes import CandidatePage, SKUQuote

__all__ = [
    'Source', 'HardCondition', 'ShoppingIntent', 'IntentUpdate',
    'SKUQuote', 'CandidatePage', 'CommerceGraphState',
]

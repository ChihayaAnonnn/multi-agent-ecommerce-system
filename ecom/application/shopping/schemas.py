"""购物 DTO 与图状态的同对象兼容入口。"""

from ecom.application.shopping.state import CommerceGraphState
from ecom.kernel.intent import HardCondition, IntentUpdate, ShoppingIntent, Source
from ecom.kernel.quotes import CandidatePage, SKUQuote

__all__ = [
    'Source', 'HardCondition', 'ShoppingIntent', 'IntentUpdate',
    'SKUQuote', 'CandidatePage', 'CommerceGraphState',
]

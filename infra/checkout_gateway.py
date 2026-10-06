"""受控宿主边界；真实宿主合同未落实前生产不可用。"""
from modules.checkout.ports import HostCheckoutGateway as HostCheckoutGateway
from modules.checkout.schemas import Destination, HandoffSnapshot
from modules.identity.types import DEMO_ENVIRONMENTS


class DemoHostCheckoutGateway:
    available=True
    def __init__(self,environment: str='test'):
        if environment not in DEMO_ENVIRONMENTS:
            raise ValueError('演示网关禁止在生产环境启用')

    async def create_handoff(self,snapshot: HandoffSnapshot,request_id: str) -> Destination:
        return Destination(kind='internal',ref=f'/demo/commerce/handoffs/{snapshot.handoff_id}',demo=True)


class UnavailableHostCheckoutGateway:
    available=False
    async def create_handoff(self,snapshot: HandoffSnapshot,request_id: str) -> Destination:
        raise RuntimeError('checkout_unavailable')

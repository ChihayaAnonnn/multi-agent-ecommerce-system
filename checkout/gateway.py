"""受控宿主边界；真实宿主合同未落实前生产不可用。"""
import re
from typing import Protocol
from urllib.parse import urlsplit
from checkout.schemas import HandoffSnapshot,Destination
from identity.types import DEMO_ENVIRONMENTS


class HostCheckoutGateway(Protocol):
    available: bool
    async def create_handoff(self,snapshot: HandoffSnapshot,request_id: str) -> Destination: ...


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


def validate_destination(destination: Destination,allowed_hosts: tuple[str,...]=(),allowed_path: str='/checkout/') -> Destination:
    if destination.kind=='internal':
        if not destination.demo or not re.fullmatch(r'/demo/commerce/handoffs/[0-9a-f-]{36}',destination.ref):
            raise ValueError('非法内部交接路由')
    else:
        url=urlsplit(destination.ref)
        if (url.scheme!='https' or url.hostname not in allowed_hosts or url.port not in (None,443)
            or url.username or url.password or url.query or url.fragment
            or not url.path.startswith(allowed_path) or '%' in url.path or '..' in url.path or '\\' in url.path):
            raise ValueError('非法宿主交接目的地')
    return destination

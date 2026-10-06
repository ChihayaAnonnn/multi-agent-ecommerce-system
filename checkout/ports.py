"""宿主结账交接的注入端口。"""

from typing import Protocol

from checkout.schemas import Destination, HandoffSnapshot


class HostCheckoutGateway(Protocol):
    available: bool

    async def create_handoff(
        self, snapshot: HandoffSnapshot, request_id: str
    ) -> Destination: ...


__all__ = [
    'HostCheckoutGateway',
]

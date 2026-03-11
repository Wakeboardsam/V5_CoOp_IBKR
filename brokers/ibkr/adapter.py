from typing import Optional, Callable
from brokers.base import BrokerBase, OrderResult


class IBKRAdapter(BrokerBase):
    def __init__(self, host: str, port: int, client_id: int, paper: bool):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.paper = paper

    async def connect(self) -> bool:
        raise NotImplementedError

    async def disconnect(self):
        raise NotImplementedError

    async def get_price(self, ticker: str) -> float:
        raise NotImplementedError

    async def get_bid_ask(self, ticker: str) -> tuple[float, float]:
        raise NotImplementedError

    async def place_bracket_order(
        self, ticker: str, action: str,
        qty: int, limit_price: float, profit_price: float,
        extended_hours: bool = True,
        on_fill: Optional[Callable] = None
    ) -> OrderResult:
        raise NotImplementedError

    async def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    async def get_open_orders(self) -> list[dict]:
        raise NotImplementedError

    async def get_positions(self) -> dict[str, int]:
        raise NotImplementedError

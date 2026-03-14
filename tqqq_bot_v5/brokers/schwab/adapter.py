from typing import Optional, Callable
from brokers.base import BrokerBase, OrderResult


class SchwabAdapter(BrokerBase):
    async def connect(self) -> bool:
        raise NotImplementedError

    async def disconnect(self):
        raise NotImplementedError

    async def is_connected(self) -> bool:
        raise NotImplementedError

    async def ensure_connected(self):
        raise NotImplementedError

    async def get_price(self, ticker: str) -> float:
        raise NotImplementedError

    async def get_bid_ask(self, ticker: str) -> tuple[float, float]:
        raise NotImplementedError

    async def get_wallet_balance(self) -> float:
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

    async def place_limit_order(
        self, ticker: str, action: str,
        qty: int, limit_price: float,
        extended_hours: bool = True,
        on_fill: Optional[Callable] = None
    ) -> OrderResult:
        raise NotImplementedError

    def subscribe_to_fill(self, order_id: str, callback: Callable):
        raise NotImplementedError

    async def get_positions(self) -> dict[str, int]:
        raise NotImplementedError

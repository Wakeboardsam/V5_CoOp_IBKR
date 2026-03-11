from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Callable


@dataclass
class OrderResult:
    order_id: str
    status: str          # 'submitted' | 'filled' | 'cancelled' | 'error'
    filled_price: Optional[float] = None
    filled_qty:   Optional[int]   = None
    error_msg:    Optional[str]   = None


class BrokerBase(ABC):

    @abstractmethod
    async def connect(self) -> bool: ...

    @abstractmethod
    async def disconnect(self): ...

    @abstractmethod
    async def get_price(self, ticker: str) -> float: ...

    @abstractmethod
    async def get_bid_ask(self, ticker: str) -> tuple[float, float]: ...

    @abstractmethod
    async def place_bracket_order(
        self, ticker: str, action: str,  # 'BUY' | 'SELL'
        qty: int, limit_price: float, profit_price: float,
        extended_hours: bool = True,
        on_fill: Optional[Callable] = None
    ) -> OrderResult: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def get_open_orders(self) -> list[dict]: ...

    @abstractmethod
    async def get_positions(self) -> dict[str, int]: ...

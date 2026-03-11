import asyncio
import logging
from typing import Optional, Callable
from ib_insync import IB, Stock, Order, Trade

from brokers.base import BrokerBase, OrderResult
from brokers.ibkr.connection import async_connect
from brokers.ibkr.order_builder import build_bracket_order

logger = logging.getLogger(__name__)

class IBKRAdapter(BrokerBase):
    def __init__(self, host: str, port: int, client_id: int, paper: bool):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.paper = paper
        self.ib = IB()
        self._on_fill_callbacks: dict[str, Callable] = {}

        # Subscribe to order status events
        self.ib.orderStatusEvent += self._on_order_status

    async def connect(self) -> bool:
        return await async_connect(self.ib, self.host, self.port, self.client_id)

    async def disconnect(self):
        self.ib.disconnect()

    async def get_price(self, ticker: str) -> float:
        # TQQQ contract is always Stock('TQQQ', 'SMART', 'USD') per instructions
        contract = Stock(ticker, 'SMART', 'USD')
        await self.ib.qualifyContractsAsync(contract)

        # Request market data
        ticker_data = self.ib.reqMktData(contract, '', False, False)

        try:
            # Wait for price to be available (briefly)
            for _ in range(50): # up to 5 seconds
                if ticker_data.last > 0:
                    return ticker_data.last
                await asyncio.sleep(0.1)

            # Fallback to close price if last is not available
            if ticker_data.close > 0:
                return ticker_data.close

            raise RuntimeError(f"Could not get price for {ticker}")
        finally:
            self.ib.cancelMktData(contract)

    async def get_bid_ask(self, ticker: str) -> tuple[float, float]:
        contract = Stock(ticker, 'SMART', 'USD')
        await self.ib.qualifyContractsAsync(contract)
        ticker_data = self.ib.reqMktData(contract, '', False, False)

        try:
            for _ in range(50):
                if ticker_data.bid > 0 and ticker_data.ask > 0:
                    return ticker_data.bid, ticker_data.ask
                await asyncio.sleep(0.1)

            raise RuntimeError(f"Could not get bid/ask for {ticker}")
        finally:
            self.ib.cancelMktData(contract)

    async def place_limit_order(self, ticker: str, action: str, qty: int, limit_price: float,
                                 on_fill: Optional[Callable] = None) -> OrderResult:
        """
        Implementation of place_limit_order as requested in the PR.
        This calls order_builder and submits via ib.placeOrder.
        For this bot, limit orders are always brackets. We use a 1% profit target as default
        if not otherwise specified, though usually place_bracket_order will be used.
        """
        profit_price = limit_price * 1.01 if action == 'BUY' else limit_price * 0.99
        return await self.place_bracket_order(ticker, action, qty, limit_price, profit_price, on_fill=on_fill)

    async def place_bracket_order(
        self, ticker: str, action: str,
        qty: int, limit_price: float, profit_price: float,
        extended_hours: bool = True,
        on_fill: Optional[Callable] = None
    ) -> OrderResult:
        contract, parent, take_profit = build_bracket_order(
            self.ib, ticker, action, qty, limit_price, profit_price
        )

        # Save callback for fills by orderId (both parent and TP)
        if on_fill:
            self._on_fill_callbacks[str(parent.orderId)] = on_fill
            self._on_fill_callbacks[str(take_profit.orderId)] = on_fill

        # Ensure contract is qualified
        await self.ib.qualifyContractsAsync(contract)

        # Place parent order
        self.ib.placeOrder(contract, parent)
        # Place child order
        self.ib.placeOrder(contract, take_profit)

        return OrderResult(
            order_id=f"{parent.orderId}|{take_profit.orderId}",
            status='submitted'
        )

    async def cancel_order(self, order_id: str) -> bool:
        # Find the order
        for trade in self.ib.trades():
            if str(trade.order.orderId) == order_id:
                self.ib.cancelOrder(trade.order)
                return True
        return False

    async def get_open_orders(self) -> list[dict]:
        orders = []
        for trade in self.ib.trades():
            if trade.isActive():
                orders.append({
                    'order_id': str(trade.order.orderId),
                    'ticker': trade.contract.symbol,
                    'action': trade.order.action,
                    'qty': trade.order.totalQuantity,
                    'limit_price': trade.order.lmtPrice,
                    'status': trade.orderStatus.status
                })
        return orders

    async def get_positions(self) -> dict[str, int]:
        positions = {}
        for pos in self.ib.positions():
            positions[pos.contract.symbol] = int(pos.position)
        return positions

    def _on_order_status(self, trade: Trade):
        if trade.orderStatus.status == 'Filled':
            order_id = str(trade.order.orderId)
            callback = self._on_fill_callbacks.get(order_id)
            if callback:
                # Basic fill details
                fill_details = {
                    'order_id': order_id,
                    'symbol': trade.contract.symbol,
                    'qty': trade.orderStatus.filled,
                    'price': trade.orderStatus.avgFillPrice
                }
                callback(fill_details)
                logger.info(f"Fill callback called for order {order_id}")
                # Optional: remove callback after fill if it's a one-time thing
                # del self._on_fill_callbacks[order_id]

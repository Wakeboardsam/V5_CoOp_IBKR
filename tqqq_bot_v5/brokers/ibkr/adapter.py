import asyncio
import logging
from typing import Optional, Callable
from ib_insync import IB, Stock, Order, Trade, LimitOrder

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
        self._selected_cash_tag: Optional[str] = None

        # Subscribe to order status events
        self.ib.orderStatusEvent += self._on_order_status

    async def connect(self) -> bool:
        connected = await async_connect(self.ib, self.host, self.port, self.client_id)
        if connected:
            self.ib.reqMarketDataType(3)
        return connected

    async def disconnect(self):
        self.ib.disconnect()

    async def is_connected(self) -> bool:
        return self.ib.isConnected()

    async def ensure_connected(self):
        if not await self.is_connected():
            logger.warning("IBKR disconnected. Watchdog attempting reconnection...")
            # Implement exponential backoff for reconnection as requested
            delay = 5
            max_attempts = 5
            for attempt in range(1, max_attempts + 1):
                logger.info(f"Reconnection attempt {attempt}/{max_attempts}...")
                try:
                    # Attempt connection with a timeout to avoid hanging indefinitely
                    await asyncio.wait_for(
                        self.ib.connectAsync(self.host, self.port, clientId=self.client_id),
                        timeout=30
                    )
                    if await self.is_connected():
                        logger.info("Watchdog successfully reconnected.")
                        self.ib.reqMarketDataType(3)
                        return
                except Exception as e:
                    logger.error(f"Reconnection attempt {attempt} failed: {e}")

                if attempt < max_attempts:
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    delay *= 2

            raise ConnectionError(f"Watchdog failed to reconnect after {max_attempts} attempts.")

    async def get_price(self, ticker: str) -> float:
        # TQQQ contract is always Stock('TQQQ', 'SMART', 'USD') per instructions
        contract = Stock(ticker, 'SMART', 'USD')
        await self.ib.qualifyContractsAsync(contract)

        try:
            # Request market data
            ticker_data = self.ib.reqMktData(contract, '', False, False)
            logger.info(f"Raw price response: {ticker_data}")

            # Wait for price to be available
            await asyncio.sleep(2)

            if ticker_data.last > 0:
                return ticker_data.last
            if ticker_data.close > 0:
                return ticker_data.close
            if ticker_data.delayedLast > 0:
                return ticker_data.delayedLast

            logger.error("API call returned empty — possible Gateway auth or subscription issue")
            raise RuntimeError(f"Could not get price for {ticker}")
        except Exception as e:
            if not isinstance(e, RuntimeError):
                logger.error(f"Error fetching price: {e}")
            raise
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

    async def get_wallet_balance(self) -> float:
        """
        Returns the USD balance from the selected conservative account tag.
        """
        try:
            account_values = self.ib.accountValues()
            if not account_values:
                logger.error("API call returned empty — possible Gateway auth or subscription issue")
                return 0.0

            # Filter for USD only
            usd_values = [v for v in account_values if v.currency == 'USD']

            if not self._selected_cash_tag:
                # 1. Search for "Settled" (case-insensitive)
                settled_tag = next((v.tag for v in usd_values if "settled" in v.tag.lower()), None)
                if settled_tag:
                    self._selected_cash_tag = settled_tag
                else:
                    # 2. Fallback to confirmed tags
                    for fallback in ["TotalCashValue", "TotalCashBalance"]:
                        if any(v.tag == fallback for v in usd_values):
                            self._selected_cash_tag = fallback
                            break

                if self._selected_cash_tag:
                    logger.info(f"Selected IBKR cash field: {self._selected_cash_tag}")
                else:
                    available_tags = [v.tag for v in usd_values]
                    logger.warning(f"No preferred conservative cash tags found. Available USD tags: {available_tags}")
                    return 0.0

            # Retrieve value for the selected tag
            balance_entry = next((v for v in usd_values if v.tag == self._selected_cash_tag), None)
            if balance_entry:
                return float(balance_entry.value)

            return 0.0
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return 0.0

    async def place_limit_order(
        self, ticker: str, action: str,
        qty: int, limit_price: float,
        extended_hours: bool = True,
        on_fill: Optional[Callable] = None
    ) -> OrderResult:
        from brokers.ibkr.order_builder import get_dynamic_exchange
        exchange = get_dynamic_exchange()
        contract = Stock(ticker, exchange, 'USD')
        await self.ib.qualifyContractsAsync(contract)

        order = LimitOrder(action, qty, limit_price)
        order.tif = 'GTC'
        order.outsideRth = True

        self.ib.placeOrder(contract, order)

        if on_fill:
            self._on_fill_callbacks[str(order.orderId)] = on_fill

        return OrderResult(
            order_id=str(order.orderId),
            status='submitted'
        )

    def subscribe_to_fill(self, order_id: str, callback: Callable):
        self._on_fill_callbacks[order_id] = callback

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
        status = trade.orderStatus.status
        if status == 'Filled':
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
        elif status in ('Cancelled', 'Inactive', 'Rejected'):
            reason = trade.orderStatus.whyHeld or "No reason provided"
            logger.warning(
                f"\n"
                f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                f"LOUD ALERT: ORDER {trade.order.orderId} {status.upper()}!\n"
                f"Ticker: {trade.contract.symbol}\n"
                f"Action: {trade.order.action}\n"
                f"Reason: {reason}\n"
                f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            )

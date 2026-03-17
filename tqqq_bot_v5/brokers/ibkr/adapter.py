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
        self._on_update_callbacks: dict[str, Callable] = {}
        self._selected_cash_tag: Optional[str] = None
        self._last_error: dict[int, tuple[int, str]] = {}  # reqId -> (errorCode, errorString)

        # Subscribe to order status events
        self.ib.orderStatusEvent += self._on_order_status
        self.ib.errorEvent += self._on_error

    def _on_error(self, reqId, errorCode, errorString, contract):
        logger.error(f"IBKR Error {errorCode}: {errorString}")
        self._last_error[reqId] = (errorCode, errorString)

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
        from brokers.ibkr.order_builder import get_dynamic_exchange
        exchange = get_dynamic_exchange()
        contract = Stock(ticker, exchange, 'USD', primaryExchange='NASDAQ')
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

            logger.error("API call returned empty — possible Gateway auth or subscription issue")
            raise RuntimeError(f"Could not get price for {ticker}")
        except Exception as e:
            if not isinstance(e, RuntimeError):
                logger.error(f"Error fetching price: {e}")
            raise
        finally:
            self.ib.cancelMktData(contract)

    async def get_bid_ask(self, ticker: str) -> tuple[float, float]:
        from brokers.ibkr.order_builder import get_dynamic_exchange
        exchange = get_dynamic_exchange()
        contract = Stock(ticker, exchange, 'USD', primaryExchange='NASDAQ')
        await self.ib.qualifyContractsAsync(contract)
        ticker_data = self.ib.reqMktData(contract, '', False, False)

        try:
            for _ in range(50):
                if ticker_data.bid > 0 and ticker_data.ask > 0:
                    return ticker_data.bid, ticker_data.ask
                await asyncio.sleep(0.1)

            # Fallback to last/close prices if bid/ask is unavailable
            fallback_price = None
            if ticker_data.last > 0:
                fallback_price = ticker_data.last
            elif ticker_data.close > 0:
                fallback_price = ticker_data.close

            if fallback_price is not None:
                logger.warning(f"Bid/Ask unavailable for {ticker}, falling back to last/close price: {fallback_price}")
                return fallback_price, fallback_price

            logger.error("API call returned empty — possible Gateway auth or subscription issue")
            raise RuntimeError(f"Could not get bid/ask for {ticker}")
        except Exception as e:
            if not isinstance(e, RuntimeError):
                logger.error(f"Error fetching bid/ask: {e}")
            raise
        finally:
            self.ib.cancelMktData(contract)

    async def get_next_order_id(self) -> str:
        """
        Returns the next available order ID from IBKR.
        """
        return str(self.ib.client.getReqId())

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
        on_update: Optional[Callable] = None,
        order_id: Optional[str] = None
    ) -> OrderResult:
        from brokers.ibkr.order_builder import get_dynamic_exchange, get_dynamic_tif
        exchange = get_dynamic_exchange()
        tif = get_dynamic_tif(exchange)
        logger.info(f"Session mode: {exchange} / {tif}")
        contract = Stock(ticker, exchange, 'USD', primaryExchange='NASDAQ')
        await self.ib.qualifyContractsAsync(contract)

        order = LimitOrder(action, qty, limit_price)
        order.tif = tif
        order.outsideRth = True

        if order_id:
            order.orderId = int(order_id)
        else:
            # If no ID provided, let ib_insync assign one or get it now
            order.orderId = self.ib.client.getReqId()

        final_order_id = str(order.orderId)

        if on_update:
            self._on_update_callbacks[final_order_id] = on_update

        trade = self.ib.placeOrder(contract, order)

        # Wait for status to be 'Submitted', 'PreSubmitted', or terminal
        while not trade.isDone() and trade.orderStatus.status not in ('Submitted', 'PreSubmitted'):
            await asyncio.sleep(0.1)
            if order.orderId in self._last_error:
                err_code, err_msg = self._last_error[order.orderId]
                # If it's a known terminal error for the order
                if err_code == 10329:
                    return OrderResult(
                        order_id=final_order_id,
                        status='error',
                        error_code=err_code,
                        error_msg=err_msg
                    )

        status = trade.orderStatus.status
        if status in ('Submitted', 'PreSubmitted'):
            return OrderResult(order_id=final_order_id, status='submitted')
        elif status == 'Filled':
            return OrderResult(
                order_id=final_order_id,
                status='filled',
                filled_price=trade.orderStatus.avgFillPrice,
                filled_qty=trade.orderStatus.filled
            )
        else:
            err_code = None
            err_msg = trade.orderStatus.whyHeld or f"Order failed with status: {status}"
            if order.orderId in self._last_error:
                err_code, err_msg = self._last_error[order.orderId]

            return OrderResult(
                order_id=final_order_id,
                status='error',
                error_code=err_code,
                error_msg=err_msg,
                reason=status
            )

    def subscribe_to_updates(self, order_id: str, on_update: Callable):
        self._on_update_callbacks[order_id] = on_update

    async def place_bracket_order(
        self, ticker: str, action: str,
        qty: int, limit_price: float, profit_price: float,
        extended_hours: bool = True,
        on_update: Optional[Callable] = None
    ) -> OrderResult:
        contract, parent, take_profit = build_bracket_order(
            self.ib, ticker, action, qty, limit_price, profit_price
        )

        # Save callback for fills by orderId (both parent and TP)
        if on_update:
            self._on_update_callbacks[str(parent.orderId)] = on_update
            self._on_update_callbacks[str(take_profit.orderId)] = on_update

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
        order_id = str(trade.order.orderId)
        callback = self._on_update_callbacks.get(order_id)

        unified_status = None
        if status in ('Submitted', 'PreSubmitted'):
            unified_status = 'submitted'
        elif status == 'Filled':
            unified_status = 'filled'
        elif status in ('Cancelled', 'Inactive', 'Rejected'):
            unified_status = 'cancelled' if status == 'Cancelled' else 'error'

            # LOUD ALERT for terminal failures
            reason = trade.orderStatus.whyHeld or "No reason provided"
            err_code = None
            if trade.order.orderId in self._last_error:
                err_code, err_msg = self._last_error[trade.order.orderId]
                reason = f"{err_msg} (Code: {err_code})"

            logger.warning(
                f"\n"
                f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                f"LOUD ALERT: ORDER {order_id} {status.upper()}!\n"
                f"Ticker: {trade.contract.symbol}\n"
                f"Action: {trade.order.action}\n"
                f"Reason: {reason}\n"
                f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            )

        if callback and unified_status:
            err_code = None
            err_msg = trade.orderStatus.whyHeld
            if trade.order.orderId in self._last_error:
                err_code, err_msg = self._last_error[trade.order.orderId]

            result = OrderResult(
                order_id=order_id,
                status=unified_status,
                filled_price=trade.orderStatus.avgFillPrice if status == 'Filled' else None,
                filled_qty=trade.orderStatus.filled if status == 'Filled' else None,
                error_msg=err_msg,
                error_code=err_code,
                reason=status
            )
            callback(result)
            logger.info(f"Update callback called for order {order_id} status {status}")

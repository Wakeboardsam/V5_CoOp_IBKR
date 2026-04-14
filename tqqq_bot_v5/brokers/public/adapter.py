from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal
from typing import Optional, Callable

from brokers.base import BrokerBase, OrderResult, PositionSnapshot

from public_api_sdk import (
    AsyncPublicApiClient,
    AsyncPublicApiClientConfiguration,
    ApiKeyAuthConfig,
    OrderRequest,
    OrderInstrument,
    InstrumentType,
    OrderSide,
    OrderType,
    OrderExpirationRequest,
    TimeInForce,
    EquityMarketSession,
    PreflightRequest,
)
import datetime
import zoneinfo
from public_api_sdk.exceptions import NotFoundError

logger = logging.getLogger(__name__)

class PublicAdapter(BrokerBase):
    def __init__(
        self,
        secret_key: str,
        account_id: str,
        preflight_enabled: bool = True,
        prefer_replace: bool = True,
    ):
        self.secret_key = secret_key
        self.account_id = account_id
        self.preflight_enabled = preflight_enabled
        self.prefer_replace = prefer_replace

        self.client: Optional[AsyncPublicApiClient] = None
        self._order_callbacks: dict[str, Callable] = {}
        self._execution_callbacks: list[Callable] = []
        self._connected = False

        # Cache for original order context to support cancel-and-replace fallback
        self._order_cache: dict[str, dict] = {}

    async def connect(self) -> bool:
        cfg = AsyncPublicApiClientConfiguration(default_account_number=self.account_id)
        self.client = AsyncPublicApiClient(
            auth_config=ApiKeyAuthConfig(api_secret_key=self.secret_key),
            config=cfg,
        )
        await self.client.__aenter__()
        self._connected = True
        return True

    async def disconnect(self):
        if self.client:
            await self.client.__aexit__(None, None, None)
        self._connected = False

    async def is_connected(self) -> bool:
        return self._connected and self.client is not None

    async def ensure_connected(self):
        if not await self.is_connected():
            await self.connect()

    async def get_price(self, ticker: str) -> float:
        quotes = await self.client.get_quotes([
            OrderInstrument(symbol=ticker, type=InstrumentType.EQUITY)
        ])
        q = quotes[0]
        return float(q.last)

    async def get_bid_ask(self, ticker: str) -> tuple[float, float]:
        quotes = await self.client.get_quotes([
            OrderInstrument(symbol=ticker, type=InstrumentType.EQUITY)
        ])
        q = quotes[0]
        return float(q.bid), float(q.ask)

    async def get_wallet_balance(self) -> float:
        portfolio = await self.client.get_portfolio(account_id=self.account_id)
        return float(portfolio.buying_power.cash_only_buying_power)

    async def get_net_liquidation_value(self) -> Optional[float]:
        portfolio = await self.client.get_portfolio(account_id=self.account_id)
        if hasattr(portfolio, "total_value"):
            return float(portfolio.total_value)

        # Fallback: manually sum equity value
        try:
            total_equity = 0.0
            if hasattr(portfolio, "equity") and portfolio.equity:
                for row in portfolio.equity:
                    total_equity += float(row.value)
            elif hasattr(portfolio, "positions"):
                for p in portfolio.positions:
                    total_equity += float(p.current_value)
                total_equity += float(portfolio.buying_power.cash_only_buying_power)

            return total_equity if total_equity > 0 else None
        except Exception as e:
            logger.warning(f"Failed to calculate net liquidation value: {e}")
            return None

    async def get_next_order_id(self) -> str:
        return str(uuid.uuid4())

    def _session_for_equity(self, extended_hours: bool) -> EquityMarketSession:
        """
        Map generic extended_hours flag to Public's CORE or EXTENDED session.

        Public's EXTENDED session:
        - Available: 4:00 a.m.–8:00 p.m. ET
        - Requires: DAY time-in-force only
        - Docs: https://public.com/api/docs/resources/order-placement/place-order
        """
        if extended_hours:
            now_et = datetime.datetime.now(zoneinfo.ZoneInfo("America/New_York"))
            current_time = now_et.time()

            # Only use EXTENDED if within Public's documented window
            if datetime.time(4, 0) <= current_time < datetime.time(20, 0):
                return EquityMarketSession.EXTENDED

        return EquityMarketSession.CORE

    async def _get_order_with_retry(self, order_id: str, max_retries: int = 3):
        """Retry reads during async visibility window (first ~500ms)."""
        for attempt in range(max_retries):
            try:
                return await self.client.get_order(order_id=order_id, account_id=self.account_id)
            except NotFoundError:
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.2)
                else:
                    raise

    async def place_limit_order(
        self,
        ticker: str,
        action: str,
        qty: int,
        limit_price: float,
        extended_hours: bool = True,
        on_update: Optional[Callable] = None,
        order_id: Optional[str] = None,
    ) -> OrderResult:
        oid = order_id or str(uuid.uuid4())

        req = OrderRequest(
            order_id=oid,
            instrument=OrderInstrument(symbol=ticker, type=InstrumentType.EQUITY),
            order_side=OrderSide.BUY if action.upper() == "BUY" else OrderSide.SELL,
            order_type=OrderType.LIMIT,
            expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
            quantity=Decimal(str(qty)),
            limit_price=Decimal(str(limit_price)),
            equity_market_session=self._session_for_equity(extended_hours),
        )

        if self.preflight_enabled:
            await self.client.perform_preflight_calculation(
                PreflightRequest(
                    instrument=req.instrument,
                    order_side=req.order_side,
                    order_type=req.order_type,
                    expiration=req.expiration,
                    quantity=req.quantity,
                    limit_price=req.limit_price,
                    equity_market_session=req.equity_market_session,
                    validate_order=True,
                )
            )

        order = await self.client.place_order(req, account_id=self.account_id)

        # Cache the order context for potential cancel-and-replace fallbacks
        self._order_cache[oid] = {
            "ticker": ticker,
            "action": action,
            "extended_hours": extended_hours
        }

        # Confirm the order was indexed during async visibility window
        await self._get_order_with_retry(oid)

        if on_update:
            self._order_callbacks[oid] = on_update
            await order.subscribe_updates(self._handle_order_update)

        return OrderResult(order_id=oid, status="submitted")

    async def place_bracket_order(
        self, ticker: str, action: str,
        qty: int, limit_price: float, profit_price: float,
        extended_hours: bool = True,
        on_update: Optional[Callable] = None
    ) -> OrderResult:
        # Currently unsupported by Public API directly in MVP format, but we'll raise an error or stub it
        raise NotImplementedError("Bracket orders are not natively supported by PublicAdapter MVP.")

    async def _handle_order_update(self, update):
        cb = self._order_callbacks.get(update.order_id)

        status = str(update.new_status)
        mapped = "submitted"

        if status in {"FILLED"}:
            mapped = "filled"
            # Trigger execution callbacks
            exec_id = f"EXEC-{update.order_id}-{uuid.uuid4().hex[:8]}"
            filled_qty = int(update.filled_quantity) if getattr(update, "filled_quantity", None) else 0
            filled_price = float(update.average_execution_price) if getattr(update, "average_execution_price", None) else 0.0

            # Fetch side from cache since it's not always in the update event
            action = "UNKNOWN"
            if update.order_id in self._order_cache:
                action = self._order_cache[update.order_id]["action"]

            exec_data = {
                "exec_id": exec_id,
                "order_id": update.order_id,
                "type": action,
                "filled_qty": filled_qty,
                "filled_price": filled_price,
            }
            for e_cb in self._execution_callbacks:
                e_cb(exec_data)

        elif status in {"CANCELLED", "QUEUED_CANCELLED"}:
            mapped = "cancelled"
        elif status in {"REJECTED", "EXPIRED"}:
            mapped = "error"

        if cb:
            result = OrderResult(
                order_id=update.order_id,
                status=mapped,
            )
            cb(result)

    async def cancel_order(self, order_id: str) -> bool:
        await self.client.cancel_order(order_id=order_id, account_id=self.account_id)

        # Verify it successfully cancelled by polling up to 5 times (1s)
        for _ in range(5):
            try:
                order = await self.client.get_order(order_id=order_id, account_id=self.account_id)
                if str(order.status) in {"CANCELLED", "QUEUED_CANCELLED", "REJECTED"}:
                    return True
            except NotFoundError:
                pass
            await asyncio.sleep(0.2)

        logger.warning(f"Order {order_id} cancel request sent but status not confirmed as CANCELLED within 1s.")
        return True

    async def get_open_orders(self) -> list[dict]:
        portfolio = await self.client.get_portfolio(account_id=self.account_id)
        open_statuses = {"NEW", "PARTIALLY_FILLED", "PENDING_CANCEL", "PENDING_REPLACE"}
        out = []
        for o in portfolio.orders:
            if str(o.status) in open_statuses:
                out.append({
                    "order_id": o.order_id,
                    "ticker": o.instrument.symbol,
                    "action": str(o.side),
                    "qty": int(Decimal(str(o.quantity))) if o.quantity is not None else 0,
                    "limit_price": float(o.limit_price) if o.limit_price is not None else 0.0,
                    "status": str(o.status),
                })
        return out

    async def get_positions(self) -> dict[str, int]:
        portfolio = await self.client.get_portfolio(account_id=self.account_id)
        return {
            p.instrument.symbol: int(Decimal(str(p.quantity)))
            for p in portfolio.positions
            if str(p.instrument.type) == "EQUITY"
        }

    async def get_position_snapshot(self) -> PositionSnapshot:
        return PositionSnapshot(is_ready=True, positions=await self.get_positions())

    async def get_portfolio_item(self, ticker: str) -> Optional[dict]:
        portfolio = await self.client.get_portfolio(account_id=self.account_id)
        for p in portfolio.positions:
            if p.instrument.symbol == ticker:
                return {
                    "position": float(p.quantity),
                    "marketPrice": float(p.last_price.last_price),
                    "marketValue": float(p.current_value),
                    "averageCost": float(p.cost_basis.unit_cost),
                }
        return None

    def subscribe_to_updates(self, order_id: str, on_update: Callable):
        self._order_callbacks[order_id] = on_update

    def subscribe_to_executions(self, on_execution: Callable):
        if on_execution not in self._execution_callbacks:
            self._execution_callbacks.append(on_execution)

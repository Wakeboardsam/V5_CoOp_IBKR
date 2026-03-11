import asyncio
import logging
import signal
from datetime import datetime
from typing import Optional

from brokers.base import BrokerBase, OrderResult
from config.schema import AppConfig
from engine.grid_state import GridState, GridLevel
from engine.order_manager import OrderManager
from engine.spread_guard import SpreadGuard
from sheets.interface import SheetInterface

logger = logging.getLogger(__name__)

TICKER = "TQQQ"

class GridEngine:
    def __init__(self, broker: BrokerBase, sheet: SheetInterface, config: AppConfig):
        self.broker = broker
        self.sheet = sheet
        self.config = config
        self.order_manager = OrderManager()
        self.spread_guard = SpreadGuard(config.max_spread_pct)
        self.grid_state: Optional[GridState] = None
        self._last_grid_refresh = datetime.min
        self._last_reconciliation = datetime.min
        self.last_price = 0.0
        self.last_fill_time: Optional[datetime] = None
        self._shutdown_event = asyncio.Event()

    async def run(self):
        logger.info("Starting GridEngine run loop")

        # Setup SIGTERM handler
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM,):
                loop.add_signal_handler(sig, self._handle_shutdown_signal)
        except (NotImplementedError, AttributeError):
            # signal handlers not supported (e.g. Windows)
            logger.warning("Signal handlers not supported in this environment.")

        await self.broker.connect()

        # Start periodic tasks
        health_task = asyncio.create_task(self._log_health_periodic())

        try:
            while not self._shutdown_event.is_set():
                try:
                    await self._tick()
                except Exception as e:
                    logger.error(f"Error in engine tick: {e}", exc_info=True)
                    await self.sheet.log_error(f"Engine tick error: {str(e)}")

                # Wait for poll interval or shutdown signal
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=self.config.poll_interval_seconds)
                except asyncio.TimeoutError:
                    pass

            logger.info("Exiting run loop. Starting cleanup...")
        finally:
            # 1. Cancel health task
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass

            # 2. Cancel all open GTC orders placed by this session
            await self._cancel_all_orders()

            # 3. Disconnect broker
            await self.broker.disconnect()
            logger.info("Graceful shutdown complete.")

    def _handle_shutdown_signal(self):
        logger.info("Shutdown signal received.")
        self._shutdown_event.set()

    async def _cancel_all_orders(self):
        tracked_ids = self.order_manager.get_tracked_order_ids()
        if tracked_ids:
            logger.info(f"Cancelling {len(tracked_ids)} tracked orders...")
            for oid in tracked_ids:
                success = await self.broker.cancel_order(oid)
                if success:
                    logger.info(f"Cancelled order: {oid}")
                else:
                    logger.warning(f"Failed to cancel order: {oid}")

    async def _log_health_periodic(self):
        while not self._shutdown_event.is_set():
            try:
                open_orders = await self.broker.get_open_orders()
                health_data = {
                    "last_price": self.last_price,
                    "open_orders_count": len(open_orders),
                    "last_fill_time": self.last_fill_time.strftime("%Y-%m-%d %H:%M:%S") if self.last_fill_time else "Never",
                    "status": "Running"
                }
                await self.sheet.log_health(health_data)
                logger.info("Health status logged to Google Sheets")
            except Exception as e:
                logger.error(f"Failed to log health status: {e}")

            # Wait 5 minutes or until shutdown
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                pass

    async def _tick(self):
        # 0. Watchdog: ensure connection
        await self.broker.ensure_connected()

        # 1. Refresh grid periodically
        if (datetime.now() - self._last_grid_refresh).total_seconds() > 900: # 15 mins
            logger.info("Refreshing grid state from sheet")
            self.grid_state = await self.sheet.fetch_grid()
            self._last_grid_refresh = datetime.now()

        if not self.grid_state:
            return

        # 2. Reconcile orders periodically
        if (datetime.now() - self._last_reconciliation).total_seconds() > 60: # 1 min
            await self._reconcile_orders()
            self._last_reconciliation = datetime.now()

        # 3. Get market data
        try:
            bid, ask = await self.broker.get_bid_ask(TICKER)
            price = (bid + ask) / 2 # Use mid-price for trigger check
            self.last_price = price
        except Exception as e:
            logger.warning(f"Failed to get market data: {e}")
            return

        # 4. Spread Guard check
        if self.spread_guard.is_too_wide(bid, ask):
            return

        # 5. Check triggers
        await self._check_triggers(price)

    async def _reconcile_orders(self):
        logger.debug("Reconciling orders with broker")
        try:
            open_orders = await self.broker.get_open_orders()
            broker_order_ids = {o['order_id'] for o in open_orders}
            tracked_order_ids = self.order_manager.get_tracked_order_ids()

            # 1. Clear tracked orders that no longer exist at broker
            for oid in tracked_order_ids:
                if oid not in broker_order_ids:
                    logger.warning(f"Order {oid} tracked but not found at broker. Marking cancelled for safety.")
                    self.order_manager.mark_cancelled(oid)

            # 2. Re-track orders found at broker that are NOT in OrderManager
            # This handles self-healing after bot restart.
            for order in open_orders:
                oid = order['order_id']
                if oid not in tracked_order_ids:
                    self._retrack_broker_order(order)

        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")

    def _retrack_broker_order(self, order: dict):
        """Attempts to match an orphan broker order to a grid level."""
        oid = order['order_id']
        price = order['limit_price']
        qty = order['qty']
        action = order['action']

        # Look for a matching level in grid_state
        levels = self.grid_state.buy_levels if action == 'BUY' else self.grid_state.sell_levels
        for level in levels:
            if level.limit_price == price and level.quantity == qty:
                # Found a match. Retrack it.
                # Note: This might be just one leg of a bracket, but it's enough to keep the level 'busy'.
                logger.info(f"Retracking orphan broker order {oid} to grid row {level.row_id}")
                self.order_manager.track(level.row_id, OrderResult(order_id=oid, status='submitted'), action)
                return

    async def _check_triggers(self, current_price: float):
        # Buy levels
        for level in self.grid_state.buy_levels:
            if current_price <= level.trigger_price and not self.order_manager.has_open_buy(level.row_id):
                # Calculate 1% profit target
                profit_price = round(level.limit_price * 1.01, 2)
                logger.info(f"Triggered BUY for row {level.row_id} at price {current_price} (trigger: {level.trigger_price})")
                result = await self.broker.place_bracket_order(
                    ticker=TICKER,
                    action='BUY',
                    qty=level.quantity,
                    limit_price=level.limit_price,
                    profit_price=profit_price,
                    on_fill=self._on_fill
                )
                if result.status == 'submitted':
                    self.order_manager.track(level.row_id, result, 'BUY')

        # Sell levels
        for level in self.grid_state.sell_levels:
            if current_price >= level.trigger_price and not self.order_manager.has_open_sell(level.row_id):
                # Calculate 1% profit target
                profit_price = round(level.limit_price * 0.99, 2)
                logger.info(f"Triggered SELL for row {level.row_id} at price {current_price} (trigger: {level.trigger_price})")
                result = await self.broker.place_bracket_order(
                    ticker=TICKER,
                    action='SELL',
                    qty=level.quantity,
                    limit_price=level.limit_price,
                    profit_price=profit_price,
                    on_fill=self._on_fill
                )
                if result.status == 'submitted':
                    self.order_manager.track(level.row_id, result, 'SELL')

    def _on_fill(self, fill_details: dict):
        self.last_fill_time = datetime.now()
        order_id = fill_details.get('order_id')
        row_id, action = self.order_manager.mark_filled(order_id)

        if row_id:
            # Prepare data for sheet logging
            log_data = {
                "row_id": row_id,
                "type": action,
                "filled_price": fill_details.get('price'),
                "filled_qty": fill_details.get('qty'),
                "order_id": order_id
            }
            # log_fill is async, create task to avoid blocking
            asyncio.create_task(self.sheet.log_fill(log_data))
            logger.info(f"Logged fill for row {row_id}, order {order_id}")
        else:
            logger.warning(f"Received fill for untracked order {order_id}")

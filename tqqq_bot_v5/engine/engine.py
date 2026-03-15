import asyncio
import logging
import signal
from datetime import datetime
from typing import Optional

from brokers.base import BrokerBase, OrderResult
from config.schema import AppConfig
from engine.grid_state import GridState, GridRow
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
        heartbeat_task = asyncio.create_task(self._heartbeat_periodic())

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
            # 1. Cancel periodic tasks
            health_task.cancel()
            heartbeat_task.cancel()
            try:
                await asyncio.gather(health_task, heartbeat_task, return_exceptions=True)
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

            # Wait for interval or until shutdown
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=self.config.health_log_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _heartbeat_periodic(self):
        while not self._shutdown_event.is_set():
            try:
                await self.sheet.write_heartbeat(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                logger.debug("Heartbeat logged to Google Sheets")
            except Exception as e:
                logger.error(f"Failed to log heartbeat: {e}")

            # Wait for interval or until shutdown
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=self.config.heartbeat_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _tick(self):
        # 0. Watchdog: ensure connection
        await self.broker.ensure_connected()

        # 0.1 Diagnostic: fetch balance and price
        try:
            balance = await self.broker.get_wallet_balance()
            price = await self.broker.get_price(TICKER)
            self.last_price = price
            if balance == 0 or price == 0:
                logger.error("API call returned empty — possible Gateway auth or subscription issue")
        except Exception as e:
            logger.error(f"Diagnostic API call failed: {e}")
            logger.error("API call returned empty — possible Gateway auth or subscription issue")

        # 1. Always Refresh grid from sheet
        self.grid_state = await self.sheet.fetch_grid()
        if not self.grid_state:
            return

        # 2. Circuit Breaker
        positions = await self.broker.get_positions()
        broker_shares = positions.get(TICKER, 0)
        sheet_shares = sum(row.shares for row in self.grid_state.rows.values() if row.has_y)

        if broker_shares != sheet_shares:
            msg = f"CIRCUIT BREAKER: Share discrepancy. Broker: {broker_shares}, Sheet: {sheet_shares}. Mode: {self.config.share_mismatch_mode}"
            if self.config.share_mismatch_mode == "halt":
                logger.critical(msg)
                await self.sheet.log_error(msg)
            else:
                logger.warning(msg)
            return

        # 3. Calculate Window
        distal_y = self.grid_state.distal_y_row
        window_start = max(7, distal_y - 3)
        window_end = max(7, distal_y + 3)
        window_range = range(window_start, window_end + 1)

        # 4. Get current open orders for evaluation
        open_orders = await self.broker.get_open_orders()
        broker_order_ids = {o['order_id'] for o in open_orders}

        # 5. Grid Evaluation
        for row in self.grid_state.rows.values():
            in_window = row.row_index in window_range

            # Parse existing status to check for current orders and historical IDs
            status_parts = row.status.split('|')
            active_order_id = None
            owned_id = None
            for part in status_parts:
                if part.startswith("WORKING_SELL:") or part.startswith("WORKING_BUY:"):
                    active_order_id = part.split(":")[1]
                elif part.startswith("OWNED:"):
                    owned_id = part.split(":")[1]

            # If an order is in Column C but not tracked, subscribe/track it
            if active_order_id and active_order_id in broker_order_ids:
                if not self.order_manager.is_tracked(active_order_id):
                    logger.info(f"Re-tracking order {active_order_id} from sheet status for row {row.row_index}")
                    action = 'SELL' if "WORKING_SELL" in row.status else 'BUY'
                    self.broker.subscribe_to_fill(active_order_id, self._on_fill)
                    self.order_manager.track(row.row_index, OrderResult(order_id=active_order_id, status='submitted'), action)

            if in_window:
                if row.has_y:
                    # Expect active SELL order
                    if not self.order_manager.has_open_sell(row.row_index):
                        logger.info(f"Placing missing SELL for owned row {row.row_index}")
                        result = await self.broker.place_limit_order(
                            ticker=TICKER, action='SELL', qty=row.shares,
                            limit_price=row.sell_price, on_fill=self._on_fill
                        )
                        if result.status == 'submitted':
                            self.order_manager.track(row.row_index, result, 'SELL')
                            new_status = f"WORKING_SELL:{result.order_id}"
                            if owned_id: new_status += f"|OWNED:{owned_id}"
                            await self.sheet.update_row_status(row.row_index, new_status)
                elif row.row_index > distal_y:
                    # Expect active BUY order
                    if not self.order_manager.has_open_buy(row.row_index):
                        logger.info(f"Placing missing BUY for empty row {row.row_index}")
                        result = await self.broker.place_limit_order(
                            ticker=TICKER, action='BUY', qty=row.shares,
                            limit_price=row.buy_price, on_fill=self._on_fill
                        )
                        if result.status == 'submitted':
                            self.order_manager.track(row.row_index, result, 'BUY')
                            await self.sheet.update_row_status(row.row_index, f"WORKING_BUY:{result.order_id}")
            else:
                # Outside window
                # Cancel any active orders for this row
                if row.row_index in self.order_manager._row_to_orders:
                    oids = list(self.order_manager._row_to_orders[row.row_index])
                    for oid in oids:
                        logger.info(f"Cancelling order {oid} for row {row.row_index} (outside window)")
                        await self.broker.cancel_order(oid)
                        self.order_manager.mark_cancelled(oid)

                # Update status
                if row.has_y:
                    new_status = f"OWNED:{owned_id}" if owned_id else "OWNED"
                    if row.status != new_status:
                        await self.sheet.update_row_status(row.row_index, new_status)
                else:
                    if row.status != "IDLE":
                        await self.sheet.update_row_status(row.row_index, "IDLE")

    def _on_fill(self, fill_details: dict):
        self.last_fill_time = datetime.now()
        order_id = fill_details.get('order_id')
        row_index, action = self.order_manager.mark_filled(order_id)

        if row_index:
            # Update status in sheet
            if action == 'BUY':
                new_status = f"OWNED:{order_id}"
            else: # SELL
                new_status = "IDLE"

            asyncio.create_task(self.sheet.update_row_status(row_index, new_status))

            # Prepare data for sheet logging in Fills tab
            log_data = {
                "row_id": str(row_index),
                "type": action,
                "filled_price": fill_details.get('price'),
                "filled_qty": fill_details.get('qty'),
                "order_id": order_id
            }
            asyncio.create_task(self.sheet.log_fill(log_data))
            logger.info(f"Logged fill for row {row_index}, order {order_id}")
        else:
            logger.warning(f"Received fill for untracked order {order_id}")

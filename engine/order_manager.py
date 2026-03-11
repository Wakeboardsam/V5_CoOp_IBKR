import logging
from typing import Dict, Set, Tuple, List, Optional
from brokers.base import OrderResult

logger = logging.getLogger(__name__)

class OrderManager:
    def __init__(self):
        # Mapping of row_id to set of active order_ids (parent and child)
        self._row_to_orders: Dict[str, Set[str]] = {}
        # Mapping of order_id to (row_id, action)
        self._order_map: Dict[str, Tuple[str, str]] = {}
        # Mapping of row_id to action ('BUY' or 'SELL')
        self._row_actions: Dict[str, str] = {}

    def track(self, row_id: str, order_result: OrderResult, action: str = None):
        """
        Track one or more orders for a grid row.
        order_result.order_id can be a single ID or multiple IDs separated by '|'.
        """
        # If action is not provided, try to infer it from the row_id prefix if we used one,
        # but better to pass it explicitly as we do in the engine.
        if action:
            self._row_actions[row_id] = action.upper()

        order_ids = order_result.order_id.split('|')

        if row_id not in self._row_to_orders:
            self._row_to_orders[row_id] = set()

        for oid in order_ids:
            self._row_to_orders[row_id].add(oid)
            self._order_map[oid] = (row_id, self._row_actions[row_id])

        logger.info(f"Tracking {self._row_actions[row_id]} row {row_id} with order(s): {order_ids}")

    def has_open_buy(self, row_id: str) -> bool:
        return row_id in self._row_to_orders and self._row_actions.get(row_id) == "BUY"

    def has_open_sell(self, row_id: str) -> bool:
        return row_id in self._row_to_orders and self._row_actions.get(row_id) == "SELL"

    def mark_filled(self, order_id: str) -> Tuple[Optional[str], Optional[str]]:
        return self._remove_order(order_id, "filled")

    def mark_cancelled(self, order_id: str) -> Tuple[Optional[str], Optional[str]]:
        return self._remove_order(order_id, "cancelled")

    def _remove_order(self, order_id: str, reason: str) -> Tuple[Optional[str], Optional[str]]:
        if order_id in self._order_map:
            row_id, action = self._order_map.pop(order_id)
            if row_id in self._row_to_orders:
                self._row_to_orders[row_id].discard(order_id)
                if not self._row_to_orders[row_id]:
                    # All orders for this row are gone (either filled or cancelled)
                    del self._row_to_orders[row_id]
                    logger.info(f"Row {row_id} is now clear (last order {order_id} was {reason})")
                else:
                    logger.info(f"Order {order_id} for row {row_id} was {reason}. Remaining orders for row: {self._row_to_orders[row_id]}")
            return row_id, action
        return None, None

    def get_tracked_order_ids(self) -> List[str]:
        return list(self._order_map.keys())

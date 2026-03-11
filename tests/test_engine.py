import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from engine.engine import GridEngine
from engine.grid_state import GridState, GridRow
from brokers.base import OrderResult
from config.schema import AppConfig

@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.connect = AsyncMock(return_value=True)
    broker.disconnect = AsyncMock()
    broker.ensure_connected = AsyncMock()
    # mid = 100.0, spread = 0.1%
    broker.get_bid_ask = AsyncMock(return_value=(99.95, 100.05))
    broker.place_bracket_order = AsyncMock(return_value=OrderResult(order_id="ORD-P|ORD-T", status="submitted"))
    broker.place_limit_order = AsyncMock(return_value=OrderResult(order_id="ORD-123", status="submitted"))
    broker.get_open_orders = AsyncMock(return_value=[])
    broker.get_positions = AsyncMock(return_value={"TQQQ": 10})
    broker.subscribe_to_fill = MagicMock()
    return broker

@pytest.fixture
def mock_sheet():
    sheet = AsyncMock()
    grid_state = GridState(
        rows={
            7: GridRow(row_index=7, status="OWNED:OLD-ID", has_y=True, sell_price=105.0, buy_price=100.0, shares=10),
            8: GridRow(row_index=8, status="IDLE", has_y=False, sell_price=110.0, buy_price=105.0, shares=10)
        }
    )
    sheet.fetch_grid = AsyncMock(return_value=grid_state)
    sheet.log_fill = AsyncMock(return_value=True)
    sheet.log_error = AsyncMock(return_value=True)
    sheet.log_health = AsyncMock(return_value=True)
    sheet.update_row_status = AsyncMock(return_value=True)
    return sheet

@pytest.fixture
def config():
    return AppConfig(
        google_sheet_id="test_sheet",
        google_credentials_json='{"test": "json"}',
        poll_interval_seconds=1,
        max_spread_pct=0.5
    )

@pytest.mark.asyncio
async def test_engine_places_sell_and_buy_limits(mock_broker, mock_sheet, config):
    engine = GridEngine(mock_broker, mock_sheet, config)
    # distal_y will be 7. Window [7, 10].
    # Row 7 is has_y -> should place SELL.
    # Row 8 is NOT has_y and 8 > 7 -> should place BUY.

    mock_broker.get_positions.return_value = {"TQQQ": 10} # Matches Row 7 shares

    await engine._tick()

    # Should have called place_limit_order twice
    assert mock_broker.place_limit_order.call_count == 2

    # Check SELL for row 7
    assert engine.order_manager.has_open_sell(7)
    # Status should preserve OLD-ID: WORKING_SELL:ORD-123|OWNED:OLD-ID
    mock_sheet.update_row_status.assert_any_call(7, "WORKING_SELL:ORD-123|OWNED:OLD-ID")

    # Check BUY for row 8
    assert engine.order_manager.has_open_buy(8)
    mock_sheet.update_row_status.assert_any_call(8, "WORKING_BUY:ORD-123")

@pytest.mark.asyncio
async def test_circuit_breaker_halts(mock_broker, mock_sheet, config):
    engine = GridEngine(mock_broker, mock_sheet, config)
    mock_broker.get_positions.return_value = {"TQQQ": 500} # Mismatch (should be 10)

    await engine._tick()

    # Should NOT place any orders
    assert mock_broker.place_limit_order.call_count == 0
    mock_sheet.log_error.assert_called()

@pytest.mark.asyncio
async def test_retrack_from_status(mock_broker, mock_sheet, config):
    # Mock row 8 as already having a working buy in status
    grid_state = GridState(
        rows={
            7: GridRow(row_index=7, status="OWNED", has_y=True, sell_price=105.0, buy_price=100.0, shares=10),
            8: GridRow(row_index=8, status="WORKING_BUY:ORD-EXISTING", has_y=False, sell_price=110.0, buy_price=105.0, shares=10)
        }
    )
    mock_sheet.fetch_grid.return_value = grid_state
    mock_broker.get_positions.return_value = {"TQQQ": 10}
    mock_broker.get_open_orders.return_value = [{'order_id': 'ORD-EXISTING', 'limit_price': 105.0, 'qty': 10, 'action': 'BUY'}]

    engine = GridEngine(mock_broker, mock_sheet, config)
    await engine._tick()

    # Should NOT place new order for row 8
    # But it should be tracked now
    assert engine.order_manager.is_tracked("ORD-EXISTING")
    assert engine.order_manager.has_open_buy(8)

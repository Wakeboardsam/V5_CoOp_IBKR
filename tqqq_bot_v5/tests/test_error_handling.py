import pytest
from unittest.mock import AsyncMock, patch
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
    broker.get_bid_ask = AsyncMock(return_value=(99.95, 100.05))
    broker.get_open_orders = AsyncMock(return_value=[])
    broker.get_positions = AsyncMock(return_value={"TQQQ": 0})
    broker.get_next_order_id = AsyncMock(return_value="ORD-FAILED")
    return broker

@pytest.fixture
def mock_sheet():
    sheet = AsyncMock()
    sheet.update_row_status = AsyncMock(return_value=True)
    sheet.write_cash_value = AsyncMock(return_value=True)
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
async def test_handle_error_10329(mock_broker, mock_sheet, config):
    grid_state = GridState(rows={
        10: GridRow(row_index=10, status="IDLE", has_y=False, sell_price=110.0, buy_price=105.0, shares=10)
    })
    mock_sheet.fetch_grid.return_value = grid_state

    # Simulate error 10329
    mock_broker.place_limit_order.return_value = OrderResult(
        order_id="ORD-FAILED",
        status="error",
        error_code=10329,
        error_msg="Margin violation"
    )

    engine = GridEngine(mock_broker, mock_sheet, config)
    # Row 10 is in window
    with patch.object(GridState, 'distal_y_row', 7):
        await engine._tick()

    # Should mark as FAILED
    mock_sheet.update_row_status.assert_called_with(10, "FAILED")
    # Should NOT be tracking the order (it should have been removed by mark_cancelled/mark_filled or similar)
    # Actually mark_cancelled is called when status is error
    assert not engine.order_manager.is_tracked("ORD-FAILED")

@pytest.mark.asyncio
async def test_skip_failed_level(mock_broker, mock_sheet, config):
    grid_state = GridState(rows={
        10: GridRow(row_index=10, status="FAILED", has_y=False, sell_price=110.0, buy_price=105.0, shares=10)
    })
    mock_sheet.fetch_grid.return_value = grid_state

    engine = GridEngine(mock_broker, mock_sheet, config)
    # Row 10 is in window
    with patch.object(GridState, 'distal_y_row', 7):
        await engine._tick()

    # Should NOT try to place any order for row 10
    mock_broker.place_limit_order.assert_not_called()

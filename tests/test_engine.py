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
    broker.get_open_orders = AsyncMock(return_value=[])
    return broker

@pytest.fixture
def mock_sheet():
    sheet = AsyncMock()
    grid_state = GridState(
        rows={
            7: GridRow(row_index=7, status="Ready", has_y=True, sell_price=105.0, buy_price=100.0, shares=10)
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
async def test_engine_places_buy_bracket(mock_broker, mock_sheet, config):
    engine = GridEngine(mock_broker, mock_sheet, config)
    engine.grid_state = await mock_sheet.fetch_grid()
    engine._last_grid_refresh = datetime.now()

    # mid = 99.9 <= 100.0, spread = 0.2% <= 0.5%
    mock_broker.get_bid_ask.return_value = (99.8, 100.0)

    await engine._tick()

    mock_broker.place_bracket_order.assert_called_once()
    # Should track both IDs
    assert engine.order_manager.has_open_buy(7)
    assert "ORD-P" in engine.order_manager.get_tracked_order_ids()
    assert "ORD-T" in engine.order_manager.get_tracked_order_ids()
    mock_sheet.update_row_status.assert_called_with(7, "Working")

@pytest.mark.asyncio
async def test_overtrading_prevention_after_fill(mock_broker, mock_sheet, config):
    engine = GridEngine(mock_broker, mock_sheet, config)
    engine.grid_state = await mock_sheet.fetch_grid()
    engine._last_grid_refresh = datetime.now()

    # mid = 99.9, spread = 0.2%
    mock_broker.get_bid_ask.return_value = (99.8, 100.0)

    # 1. Place order
    await engine._tick()
    assert mock_broker.place_bracket_order.call_count == 1

    # 2. Parent leg fills
    engine._on_fill({'order_id': 'ORD-P', 'price': 100.0, 'qty': 10})

    # 3. Check if level is still busy (due to ORD-T)
    assert engine.order_manager.has_open_buy(7)

    # 4. Next tick should NOT place new order
    await engine._tick()
    assert mock_broker.place_bracket_order.call_count == 1

    # 5. Take-profit leg fills
    engine._on_fill({'order_id': 'ORD-T', 'price': 101.0, 'qty': 10})

    # 6. Now level should be clear
    assert not engine.order_manager.has_open_buy(7)

    # 7. Next tick SHOULD place new order if price still in range
    await engine._tick()
    assert mock_broker.place_bracket_order.call_count == 2

@pytest.mark.asyncio
async def test_retrack_orphan_order(mock_broker, mock_sheet, config):
    engine = GridEngine(mock_broker, mock_sheet, config)
    engine.grid_state = await mock_sheet.fetch_grid()

    # Mock an orphan order at broker
    mock_broker.get_open_orders.return_value = [{
        'order_id': 'ORD-ORPHAN',
        'limit_price': 100.0,
        'qty': 10,
        'action': 'BUY'
    }]

    await engine._reconcile_orders()

    assert engine.order_manager.has_open_buy(7)
    assert "ORD-ORPHAN" in engine.order_manager.get_tracked_order_ids()

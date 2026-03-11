import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from engine.engine import GridEngine
from engine.grid_state import GridState, GridLevel
from brokers.base import OrderResult
from config.schema import AppConfig

@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.connect = AsyncMock(return_value=True)
    broker.disconnect = AsyncMock()
    broker.get_bid_ask = AsyncMock(return_value=(49.95, 50.05))
    broker.place_bracket_order = AsyncMock(return_value=OrderResult(order_id="ORD-P|ORD-T", status="submitted"))
    broker.get_open_orders = AsyncMock(return_value=[])
    return broker

@pytest.fixture
def mock_sheet():
    sheet = AsyncMock()
    grid_state = GridState(
        buy_levels=[GridLevel(row_id="BUY_1", trigger_price=50.0, limit_price=49.9, quantity=10)],
        sell_levels=[GridLevel(row_id="SELL_1", trigger_price=60.0, limit_price=60.1, quantity=10)]
    )
    sheet.fetch_grid = AsyncMock(return_value=grid_state)
    sheet.log_fill = AsyncMock(return_value=True)
    sheet.log_error = AsyncMock(return_value=True)
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

    mock_broker.get_bid_ask.return_value = (49.9, 50.0) # mid = 49.95

    await engine._tick()

    mock_broker.place_bracket_order.assert_called_once()
    # Should track both IDs
    assert engine.order_manager.has_open_buy("BUY_1")
    assert "ORD-P" in engine.order_manager.get_tracked_order_ids()
    assert "ORD-T" in engine.order_manager.get_tracked_order_ids()

@pytest.mark.asyncio
async def test_overtrading_prevention_after_fill(mock_broker, mock_sheet, config):
    engine = GridEngine(mock_broker, mock_sheet, config)
    engine.grid_state = await mock_sheet.fetch_grid()
    engine._last_grid_refresh = datetime.now()

    mock_broker.get_bid_ask.return_value = (49.9, 50.0)

    # 1. Place order
    await engine._tick()
    assert mock_broker.place_bracket_order.call_count == 1

    # 2. Parent leg fills
    engine._on_fill({'order_id': 'ORD-P', 'price': 49.9, 'qty': 10})

    # 3. Check if level is still busy (due to ORD-T)
    assert engine.order_manager.has_open_buy("BUY_1")

    # 4. Next tick should NOT place new order
    await engine._tick()
    assert mock_broker.place_bracket_order.call_count == 1

    # 5. Take-profit leg fills
    engine._on_fill({'order_id': 'ORD-T', 'price': 50.4, 'qty': 10})

    # 6. Now level should be clear
    assert not engine.order_manager.has_open_buy("BUY_1")

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
        'limit_price': 49.9,
        'qty': 10,
        'action': 'BUY'
    }]

    await engine._reconcile_orders()

    assert engine.order_manager.has_open_buy("BUY_1")
    assert "ORD-ORPHAN" in engine.order_manager.get_tracked_order_ids()

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from engine.engine import GridEngine
from engine.grid_state import GridState, GridRow
from brokers.base import OrderResult
from config.schema import AppConfig
import zoneinfo

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
    mock_broker.get_wallet_balance.return_value = 50000.0

    await engine._tick()

    # Should have updated cash balance
    mock_sheet.write_cash_value.assert_called_with(50000.0)

    # Should have called place_limit_order twice
    assert mock_broker.place_limit_order.call_count == 2

    # Check SELL for row 7
    assert engine.order_manager.has_open_sell(7)
    # Status should NOT preserve OLD-ID per strict requirements in PR 6
    mock_sheet.update_row_status.assert_any_call(7, "WORKING_SELL:ORD-123")

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

@pytest.mark.asyncio
async def test_share_mismatch_warn(mock_broker, mock_sheet, config):
    config.share_mismatch_mode = "warn"
    mock_broker.get_positions.return_value = {"TQQQ": 500} # Mismatch
    mock_broker.get_price.return_value = 100.0
    engine = GridEngine(mock_broker, mock_sheet, config)

    await engine._tick()

    # Should have called log_error (new in PR 5)
    mock_sheet.log_error.assert_called()

    # Should HAVE called place_limit_order for SELL (row 7) but NOT for BUY (row 8)
    # Row 7 is has_y=True in the mock_sheet fixture
    assert mock_broker.place_limit_order.call_count == 1
    buy_calls = [call for call in mock_broker.place_limit_order.call_args_list if call.kwargs.get('action') == 'BUY']
    assert len(buy_calls) == 0

@pytest.mark.asyncio
async def test_heartbeat_periodic(mock_broker, mock_sheet, config):
    config.heartbeat_interval_seconds = 0.01
    engine = GridEngine(mock_broker, mock_sheet, config)

    # Run heartbeat task for a short time
    task = asyncio.create_task(engine._heartbeat_periodic())
    await asyncio.sleep(0.05)
    engine._shutdown_event.set()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.TimeoutError:
        task.cancel()

    assert mock_sheet.write_heartbeat.call_count >= 1

@pytest.mark.asyncio
async def test_anchor_acquisition(mock_broker, mock_sheet, config):
    # distal_y == 0 condition
    grid_state = GridState(
        rows={
            7: GridRow(row_index=7, status="IDLE", has_y=False, sell_price=105.0, buy_price=100.0, shares=10),
            8: GridRow(row_index=8, status="IDLE", has_y=False, sell_price=110.0, buy_price=105.0, shares=10)
        }
    )
    mock_sheet.fetch_grid.return_value = grid_state
    mock_broker.get_positions.return_value = {"TQQQ": 0}
    mock_broker.get_wallet_balance.return_value = 50000.0
    mock_broker.get_bid_ask.return_value = (99.9, 100.0)
    config.anchor_buy_offset = 0.05

    engine = GridEngine(mock_broker, mock_sheet, config)
    await engine._tick()

    # Should write anchor ask to G7
    mock_sheet.write_anchor_ask.assert_called_with(100.0)

    # Should place buy order at ask + offset (100.0 + 0.05 = 100.05)
    mock_broker.place_limit_order.assert_any_call(
        ticker="TQQQ", action="BUY", qty=10, limit_price=100.05, on_fill=engine._on_fill
    )

@pytest.mark.asyncio
async def test_no_anchor_write_if_owned(mock_broker, mock_sheet, config):
    # distal_y > 0 condition
    grid_state = GridState(
        rows={
            7: GridRow(row_index=7, status="OWNED", has_y=True, sell_price=105.0, buy_price=100.0, shares=10),
            8: GridRow(row_index=8, status="IDLE", has_y=False, sell_price=110.0, buy_price=105.0, shares=10)
        }
    )
    mock_sheet.fetch_grid.return_value = grid_state
    mock_broker.get_positions.return_value = {"TQQQ": 10}
    mock_broker.get_wallet_balance.return_value = 50000.0

    engine = GridEngine(mock_broker, mock_sheet, config)
    await engine._tick()

    # Should NOT write anchor ask to G7
    mock_sheet.write_anchor_ask.assert_not_called()

@pytest.mark.asyncio
async def test_engine_boundary_regeneration(mock_broker, mock_sheet, config):
    engine = GridEngine(mock_broker, mock_sheet, config)

    # 1. Start session normally
    tz = zoneinfo.ZoneInfo("America/New_York")

    # Track a dummy order
    engine.order_manager.track(10, OrderResult(order_id="TEST-1", status="submitted"), "BUY")
    mock_broker.cancel_order = AsyncMock(return_value=True)

    # Mock time to cross 4:00 PM ET on a Wednesday
    # Wednesday is weekday 2
    wed_16_01 = datetime(2023, 10, 11, 16, 1, 0, tzinfo=tz)

    with patch('engine.engine.datetime') as mock_dt:
        mock_dt.now.return_value = wed_16_01
        mock_dt.combine = datetime.combine
        await engine._check_daily_grid_regeneration()

    # Verify cancel_all_orders was triggered (which calls broker.cancel_order)
    mock_broker.cancel_order.assert_called_with("TEST-1")
    # Verify order manager was reset
    assert not engine.order_manager.is_tracked("TEST-1")

    # 2. Track another order and cross 8:00 PM ET
    engine.order_manager.track(11, OrderResult(order_id="TEST-2", status="submitted"), "SELL")
    mock_broker.cancel_order.reset_mock()

    wed_20_01 = datetime(2023, 10, 11, 20, 1, 0, tzinfo=tz)

    with patch('engine.engine.datetime') as mock_dt:
        mock_dt.now.return_value = wed_20_01
        mock_dt.combine = datetime.combine
        await engine._check_daily_grid_regeneration()

    mock_broker.cancel_order.assert_called_with("TEST-2")
    assert not engine.order_manager.is_tracked("TEST-2")
    assert engine._is_weekend_gap is False

    # 3. Test Weekend Skip: Friday 4:01 PM ET
    # Friday is weekday 4
    engine.order_manager.track(12, OrderResult(order_id="TEST-3", status="submitted"), "BUY")
    mock_broker.cancel_order.reset_mock()

    fri_16_01 = datetime(2023, 10, 13, 16, 1, 0, tzinfo=tz)

    with patch('engine.engine.datetime') as mock_dt:
        mock_dt.now.return_value = fri_16_01
        mock_dt.combine = datetime.combine
        await engine._check_daily_grid_regeneration()

    # It should still cancel and reset the previous day's orders,
    # but it should also set _is_weekend_gap = True
    mock_broker.cancel_order.assert_called_with("TEST-3")
    assert not engine.order_manager.is_tracked("TEST-3")
    assert engine._is_weekend_gap is True


@pytest.mark.asyncio
async def test_no_anchor_write_if_already_working(mock_broker, mock_sheet, config):
    # Row 7 already has a WORKING_BUY in status, even if distal_y is 0
    grid_state = GridState(
        rows={
            7: GridRow(row_index=7, status="WORKING_BUY:ORD-1", has_y=False, sell_price=105.0, buy_price=100.0, shares=10),
            8: GridRow(row_index=8, status="IDLE", has_y=False, sell_price=110.0, buy_price=105.0, shares=10)
        }
    )
    mock_sheet.fetch_grid.return_value = grid_state
    mock_broker.get_positions.return_value = {"TQQQ": 0}
    mock_broker.get_wallet_balance.return_value = 50000.0
    mock_broker.get_open_orders.return_value = [{'order_id': 'ORD-1', 'limit_price': 100.0, 'qty': 10, 'action': 'BUY'}]

    engine = GridEngine(mock_broker, mock_sheet, config)
    await engine._tick()

    # Should NOT write anchor ask to G7
    mock_sheet.write_anchor_ask.assert_not_called()
    # Should NOT place a new order
    assert mock_broker.place_limit_order.call_count == 0

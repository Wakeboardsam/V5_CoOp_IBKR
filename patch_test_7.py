import re

def apply_patch(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Need a test where share count is identical but price changed (regression test for same share count logic)
    test_block = """
@pytest.mark.asyncio
async def test_full_sell_cycle_same_shares(mock_broker, mock_sheet, config):
    # Regression: even if share count is identical, it uses the recalculated values on the NEXT tick,
    # rather than failing to recognize that it changed. Since we implemented a deterministic tick skip,
    # it naturally works without relying on integer changes.
    grid_state = GridState(
        rows={
            7: GridRow(row_index=7, status="OWNED:ORD-123", has_y=True, sell_price=105.0, buy_price=100.0, shares=10),
        }
    )
    mock_sheet.fetch_grid.return_value = grid_state
    mock_broker.get_position_snapshot.return_value = PositionSnapshot(is_ready=True, positions={"TQQQ": 0})
    mock_broker.get_wallet_balance.return_value = 50000.0
    mock_broker.get_bid_ask.return_value = (101.9, 102.0)

    engine = GridEngine(mock_broker, mock_sheet, config)
    engine.last_broker_shares = 10

    # First tick triggers anchor reset
    await engine._tick()
    mock_sheet.write_anchor_ask.assert_called_with(102.0)
    mock_broker.place_limit_order.assert_not_called()

    # Next tick: same share count (10), but new price (102.0)
    grid_state_next = GridState(
        rows={
            7: GridRow(row_index=7, status="IDLE", has_y=False, sell_price=107.0, buy_price=102.0, shares=10),
        }
    )
    mock_sheet.fetch_grid.return_value = grid_state_next

    await engine._tick()

    # Buy is placed with new price and same shares!
    mock_broker.place_limit_order.assert_called_once_with(
        ticker="TQQQ", action="BUY", qty=10, limit_price=102.0, on_update=engine._handle_order_update, order_id=mock_broker.get_next_order_id.return_value
    )
"""
    if "test_full_sell_cycle_same_shares" not in content:
        with open(filepath, 'a') as f:
            f.write(test_block)
        print("Added full sell cycle same shares regression test.")

apply_patch('tqqq_bot_v5/tests/test_engine.py')

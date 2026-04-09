import re

def apply_patch(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Add the test for full sell cycle updating G7 and exiting without placing orders
    test_block = """
@pytest.mark.asyncio
async def test_full_sell_cycle_halts_trading_evaluation(mock_broker, mock_sheet, config):
    # Setup row 7 with owned
    grid_state = GridState(
        rows={
            7: GridRow(row_index=7, status="OWNED:ORD-123", has_y=True, sell_price=105.0, buy_price=100.0, shares=10),
        }
    )
    mock_sheet.fetch_grid.return_value = grid_state

    # 0 shares returned meaning we just sold
    mock_broker.get_position_snapshot.return_value = PositionSnapshot(is_ready=True, positions={"TQQQ": 0})
    mock_broker.get_wallet_balance.return_value = 50000.0
    mock_broker.get_bid_ask.return_value = (99.9, 100.0)

    engine = GridEngine(mock_broker, mock_sheet, config)
    # Set previous shares to 10 so it triggers full sell cycle
    engine.last_broker_shares = 10

    await engine._tick()

    # Verify G7 is updated
    mock_sheet.write_anchor_ask.assert_called_with(100.0)

    # Verify no orders are placed in this tick
    mock_broker.place_limit_order.assert_not_called()

    # Next tick:
    # 1. Update engine.last_broker_shares (which would be 0 now)
    # 2. Update sheet state to simulate sheet recalulating and row 7 being IDLE
    grid_state_next = GridState(
        rows={
            7: GridRow(row_index=7, status="IDLE", has_y=False, sell_price=105.0, buy_price=100.0, shares=10),
        }
    )
    mock_sheet.fetch_grid.return_value = grid_state_next

    await engine._tick()

    # Verify anchor buy is placed in the NEXT tick
    mock_broker.place_limit_order.assert_called_once()
"""
    if "test_full_sell_cycle_halts_trading_evaluation" not in content:
        with open(filepath, 'a') as f:
            f.write(test_block)
        print("Added full sell cycle halt test.")

apply_patch('tqqq_bot_v5/tests/test_engine.py')

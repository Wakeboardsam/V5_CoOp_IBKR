import re

def apply_patch(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    search_block = """    # Setup row 7 with working order, but sheet shares mismatch live order
    grid_state = GridState(
        rows={
            7: GridRow(row_index=7, status="WORKING_BUY:ORD-123", has_y=False, sell_price=105.0, buy_price=100.0, shares=15),
            8: GridRow(row_index=8, status="IDLE", has_y=False, sell_price=110.0, buy_price=105.0, shares=10)
        }
    )"""

    replace_block = """    # Setup row 7 with working order, but sheet shares mismatch live order
    # Row 7 is distal_y (has_y=False, shares=15), Row 8 will try to place if distal_y logic permits it
    # But wait, in the loop: 'if row.row_index > distal_y' triggers BUY logic.
    # distal_y here is 0 since no row has `has_y=True`.
    grid_state = GridState(
        rows={
            7: GridRow(row_index=7, status="WORKING_BUY:ORD-123", has_y=False, sell_price=105.0, buy_price=100.0, shares=15),
            8: GridRow(row_index=8, status="IDLE", has_y=False, sell_price=110.0, buy_price=105.0, shares=10)
        }
    )"""

    if search_block in content:
        content = content.replace(search_block, replace_block)
        with open(filepath, 'w') as f:
            f.write(content)
        print("Test patch 4 applied.")
    else:
        print("Test patch 4 failed. Block not found.")

apply_patch('tqqq_bot_v5/tests/test_engine.py')

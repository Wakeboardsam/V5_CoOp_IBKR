import re

def apply_patch(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    search_block = """    mock_broker.place_limit_order.assert_any_call(
        ticker="TQQQ", action="BUY", qty=10, limit_price=105.0, on_update=engine._handle_order_update, order_id="ORD-123"
    )"""

    replace_block = """    # `ORD-123` was already used by row 7 in the test setup. Row 8 will get a new order_id from get_next_order_id
    mock_broker.place_limit_order.assert_any_call(
        ticker="TQQQ", action="BUY", qty=10, limit_price=105.0, on_update=engine._handle_order_update, order_id=mock_broker.get_next_order_id.return_value
    )"""

    if search_block in content:
        content = content.replace(search_block, replace_block)
        with open(filepath, 'w') as f:
            f.write(content)
        print("Test patch applied.")
    else:
        print("Test patch failed. Block not found.")

apply_patch('tqqq_bot_v5/tests/test_engine.py')

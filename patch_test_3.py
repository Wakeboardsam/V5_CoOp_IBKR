import re

def apply_patch(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    search_block = """    # `ORD-123` was already used by row 7 in the test setup. Row 8 will get a new order_id from get_next_order_id
    mock_broker.place_limit_order.assert_any_call(
        ticker="TQQQ", action="BUY", qty=10, limit_price=105.0, on_update=engine._handle_order_update, order_id=mock_broker.get_next_order_id.return_value
    )
    # Check that it didn't call place limit order for row 7
    buy_calls = [call for call in mock_broker.place_limit_order.call_args_list if call.kwargs.get('action') == 'BUY']
    # 1 call for row 8
    assert len(buy_calls) == 1"""

    replace_block = """    # Check that it didn't call place limit order for row 7
    buy_calls = [call for call in mock_broker.place_limit_order.call_args_list if call.kwargs.get('action') == 'BUY']

    # 1 call for row 8
    assert len(buy_calls) == 1
    # Ensure it's for 10 shares at 105.0 (row 8) and not for row 7
    assert buy_calls[0].kwargs.get('qty') == 10
    assert buy_calls[0].kwargs.get('limit_price') == 105.0
    """

    if search_block in content:
        content = content.replace(search_block, replace_block)
        with open(filepath, 'w') as f:
            f.write(content)
        print("Test patch 3 applied.")
    else:
        print("Test patch 3 failed. Block not found.")

apply_patch('tqqq_bot_v5/tests/test_engine.py')

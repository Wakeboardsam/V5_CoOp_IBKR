import pytest
import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from ib_insync import IB, Stock, LimitOrder, OrderStatus, Trade
from brokers.ibkr.adapter import IBKRAdapter
from brokers.ibkr.order_builder import get_dynamic_exchange

@pytest.fixture
def mock_ib():
    ib = MagicMock(spec=IB)
    ib.client = MagicMock()
    ib.client.getReqId.return_value = 123
    # Mock bracketOrder to return some Order objects
    def mock_bracket(action, qty, lmt, takeProfitPrice, stopLossPrice):
        parent = LimitOrder(action, qty, lmt)
        parent.orderId = 100
        tp = LimitOrder('SELL' if action == 'BUY' else 'BUY', qty, takeProfitPrice)
        tp.orderId = 101
        sl = LimitOrder('SELL' if action == 'BUY' else 'BUY', qty, stopLossPrice)
        sl.orderId = 102
        return [parent, tp, sl]

    ib.bracketOrder.side_effect = mock_bracket
    ib.qualifyContractsAsync = AsyncMock()
    ib.placeOrder = MagicMock()
    ib.trades.return_value = []
    return ib

@pytest.mark.asyncio
async def test_place_bracket_order_rth_gtc(mock_ib):
    # We need to patch the IB constructor inside IBKRAdapter or just replace the instance
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    with patch('brokers.ibkr.adapter.build_bracket_order') as mock_build:
        # Create real-ish order objects to check attributes
        parent = LimitOrder('BUY', 10, 50.0)
        parent.orderId = 100
        tp = LimitOrder('SELL', 10, 55.0)
        tp.orderId = 101
        contract = Stock('TQQQ', 'SMART', 'USD')

        mock_build.return_value = (contract, parent, tp)

        await adapter.place_bracket_order('TQQQ', 'BUY', 10, 50.0, 55.0)

        # Verify parent and tp had their attributes set by builder (or we can check builder tests)
        # But we must ensure adapter calls it correctly.

        # Check that builder was called
        mock_build.assert_called_once()

        # Actually we should test the builder specifically for outsideRth and tif
        from brokers.ibkr.order_builder import build_bracket_order
        # Mock the dynamic exchange and TIF so we know what they evaluate to
        with patch('brokers.ibkr.order_builder.get_dynamic_exchange', return_value='OVERNIGHT'):
            with patch('brokers.ibkr.order_builder.get_dynamic_tif', return_value='OND'):
                c, p, t = build_bracket_order(mock_ib, 'TQQQ', 'BUY', 10, 50.0, 55.0)

        assert p.outsideRth is True
        assert p.tif == 'OND'
        assert t.outsideRth is True
        assert t.tif == 'OND'

@pytest.mark.parametrize("weekday,current_time,expected_exchange", [
    (0, datetime.time(10, 0), "SMART"),      # Mon 10 AM ET -> SMART
    (0, datetime.time(21, 0), "OVERNIGHT"),  # Mon 9 PM ET -> OVERNIGHT
    (1, datetime.time(2, 0), "OVERNIGHT"),   # Tue 2 AM ET -> OVERNIGHT
    (2, datetime.time(3, 49), "OVERNIGHT"),  # Wed 3:49 AM ET -> OVERNIGHT
    (3, datetime.time(3, 50), "SMART"),      # Thu 3:50 AM ET -> SMART
    (4, datetime.time(20, 0), "SMART"),      # Fri 8:00 PM ET -> SMART (weekend skip)
    (4, datetime.time(20, 1), "SMART"),      # Fri 8:01 PM ET -> SMART (weekend skip)
    (5, datetime.time(2, 0), "SMART"),       # Sat 2:00 AM ET -> SMART (weekend skip)
    (6, datetime.time(19, 59), "SMART"),     # Sun 7:59 PM ET -> SMART (weekend skip)
    (6, datetime.time(20, 1), "OVERNIGHT"),  # Sun 8:01 PM ET -> OVERNIGHT (market open)
])
def test_dynamic_exchange_logic(weekday, current_time, expected_exchange):
    with patch('brokers.ibkr.order_builder.datetime') as mock_datetime:
        # Mock now().time() to return current_time and now().weekday() to return weekday
        mock_now = MagicMock()
        mock_now.time.return_value = current_time
        mock_now.weekday.return_value = weekday
        mock_datetime.datetime.now.return_value = mock_now
        mock_datetime.time = datetime.time

        assert get_dynamic_exchange() == expected_exchange

@pytest.mark.asyncio
async def test_handle_order_update_callback(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    mock_callback1 = MagicMock()
    mock_callback2 = MagicMock()

    # Place two orders
    adapter._on_update_callbacks['100'] = mock_callback1
    adapter._on_update_callbacks['200'] = mock_callback2

    # Create mock Trades
    trade1 = MagicMock()
    trade1.order.orderId = 100
    trade1.contract.symbol = 'TQQQ'
    trade1.orderStatus.status = 'Filled'
    trade1.orderStatus.filled = 10
    trade1.orderStatus.avgFillPrice = 50.5
    trade1.orderStatus.whyHeld = None

    trade2 = MagicMock()
    trade2.order.orderId = 200
    trade2.contract.symbol = 'TQQQ'
    trade2.orderStatus.status = 'Filled'
    trade2.orderStatus.filled = 5
    trade2.orderStatus.avgFillPrice = 51.0
    trade2.orderStatus.whyHeld = None

    # Manually trigger callbacks
    adapter._on_order_status(trade1)
    adapter._on_order_status(trade2)

    from brokers.base import OrderResult
    mock_callback1.assert_called_once_with(OrderResult(
        order_id='100',
        status='filled',
        filled_qty=10,
        filled_price=50.5,
        reason='Filled'
    ))
    mock_callback2.assert_called_once_with(OrderResult(
        order_id='200',
        status='filled',
        filled_qty=5,
        filled_price=51.0,
        reason='Filled'
    ))

@pytest.mark.asyncio
async def test_market_data_cancellation(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    # Mock reqMktData to return a ticker with last price
    mock_ticker = MagicMock()
    mock_ticker.last = 50.0
    mock_ticker.close = 0.0
    mock_ticker.delayedLast = 0.0
    mock_ib.reqMktData.return_value = mock_ticker
    mock_ib.cancelMktData = MagicMock()

    price = await adapter.get_price('TQQQ')

    assert price == 50.0
    mock_ib.reqMktData.assert_called_once()
    mock_ib.cancelMktData.assert_called_once()

@pytest.mark.asyncio
async def test_get_price_fallbacks(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    # Test close fallback
    mock_ticker = MagicMock()
    mock_ticker.last = 0.0
    mock_ticker.close = 51.0
    mock_ticker.delayedLast = 0.0
    mock_ib.reqMktData.return_value = mock_ticker

    price = await adapter.get_price('TQQQ')
    assert price == 51.0

@pytest.mark.asyncio
async def test_get_net_liquidation_value_prefers_usd(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    v_base = MagicMock(tag='NetLiquidation', value='900.0', currency='BASE')
    v_usd = MagicMock(tag='NetLiquidation', value='1000.0', currency='USD')
    mock_ib.accountValues.return_value = [v_base, v_usd]

    nlv = await adapter.get_net_liquidation_value()
    assert nlv == 1000.0

@pytest.mark.asyncio
async def test_get_net_liquidation_value_empty(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib
    mock_ib.accountValues.return_value = []

    nlv = await adapter.get_net_liquidation_value()
    assert nlv is None

@pytest.mark.asyncio
async def test_get_wallet_balance_selection_settled(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    # Mock accountValues: SettledCash should win
    v1 = MagicMock(tag='NetLiquidation', value='1000.0', currency='USD')
    v2 = MagicMock(tag='TotalCashValue', value='500.0', currency='USD')
    v3 = MagicMock(tag='SettledCash', value='400.0', currency='USD')
    v4 = MagicMock(tag='SettledCash', value='300.0', currency='EUR')

    mock_ib.accountValues.return_value = [v1, v2, v3, v4]

    balance = await adapter.get_wallet_balance()
    assert balance == 400.0
    assert adapter._selected_cash_tag == 'SettledCash'

@pytest.mark.asyncio
async def test_get_wallet_balance_selection_fallback_total(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    # No settled tag, TotalCashValue should win
    v1 = MagicMock(tag='NetLiquidation', value='1000.0', currency='USD')
    v2 = MagicMock(tag='TotalCashValue', value='500.0', currency='USD')
    v3 = MagicMock(tag='TotalCashBalance', value='450.0', currency='USD')

    mock_ib.accountValues.return_value = [v1, v2, v3]

    balance = await adapter.get_wallet_balance()
    assert balance == 500.0
    assert adapter._selected_cash_tag == 'TotalCashValue'

@pytest.mark.asyncio
async def test_get_wallet_balance_selection_fallback_balance(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    # Only TotalCashBalance available
    v1 = MagicMock(tag='NetLiquidation', value='1000.0', currency='USD')
    v2 = MagicMock(tag='TotalCashBalance', value='450.0', currency='USD')

    mock_ib.accountValues.return_value = [v1, v2]

    balance = await adapter.get_wallet_balance()
    assert balance == 450.0
    assert adapter._selected_cash_tag == 'TotalCashBalance'

@pytest.mark.asyncio
async def test_get_wallet_balance_no_match(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    # No preferred tags
    v1 = MagicMock(tag='NetLiquidation', value='1000.0', currency='USD')
    v2 = MagicMock(tag='BuyingPower', value='2000.0', currency='USD')

    mock_ib.accountValues.return_value = [v1, v2]

    balance = await adapter.get_wallet_balance()
    assert balance == 0.0
    assert adapter._selected_cash_tag is None

@pytest.mark.asyncio
async def test_place_limit_order_outside_rth(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    with patch('brokers.ibkr.order_builder.get_dynamic_exchange', return_value='SMART'):
        with patch('brokers.ibkr.order_builder.get_dynamic_tif', return_value='GTC'):
            await adapter.place_limit_order('TQQQ', 'BUY', 10, 50.0, order_id="123")

            # Get the order passed to placeOrder
            args, kwargs = mock_ib.placeOrder.call_args
            order = args[1]

            assert order.outsideRth is True
            assert order.tif == 'GTC'
            assert order.orderId == 123

@pytest.mark.asyncio
async def test_get_price_contract_routing(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    # Mock ticker data
    mock_ticker = MagicMock()
    mock_ticker.last = 50.0
    mock_ib.reqMktData.return_value = mock_ticker

    # Test SMART
    with patch('brokers.ibkr.order_builder.get_dynamic_exchange', return_value='SMART'):
        await adapter.get_price('TQQQ')
        contract_arg = mock_ib.reqMktData.call_args[0][0]
        assert contract_arg.symbol == 'TQQQ'
        assert contract_arg.exchange == 'SMART'
        assert contract_arg.primaryExchange == 'NASDAQ'
        assert contract_arg.currency == 'USD'
        assert contract_arg.secType == 'STK'

    # Test OVERNIGHT
    with patch('brokers.ibkr.order_builder.get_dynamic_exchange', return_value='OVERNIGHT'):
        await adapter.get_price('TQQQ')
        contract_arg = mock_ib.reqMktData.call_args[0][0]
        assert contract_arg.symbol == 'TQQQ'
        assert contract_arg.exchange == 'OVERNIGHT'
        assert contract_arg.primaryExchange == 'NASDAQ'
        assert contract_arg.currency == 'USD'
        assert contract_arg.secType == 'STK'

@pytest.mark.asyncio
async def test_get_bid_ask_contract_routing(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    # Mock ticker data
    mock_ticker = MagicMock()
    mock_ticker.bid = 49.9
    mock_ticker.ask = 50.1
    mock_ib.reqMktData.return_value = mock_ticker

    # Test SMART
    with patch('brokers.ibkr.order_builder.get_dynamic_exchange', return_value='SMART'):
        await adapter.get_bid_ask('TQQQ')
        contract_arg = mock_ib.reqMktData.call_args[0][0]
        assert contract_arg.symbol == 'TQQQ'
        assert contract_arg.exchange == 'SMART'
        assert contract_arg.primaryExchange == 'NASDAQ'

    # Test OVERNIGHT
    with patch('brokers.ibkr.order_builder.get_dynamic_exchange', return_value='OVERNIGHT'):
        await adapter.get_bid_ask('TQQQ')
        contract_arg = mock_ib.reqMktData.call_args[0][0]
        assert contract_arg.symbol == 'TQQQ'
        assert contract_arg.exchange == 'OVERNIGHT'
        assert contract_arg.primaryExchange == 'NASDAQ'

@pytest.mark.asyncio
async def test_place_limit_order_contract_routing(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    with patch('brokers.ibkr.order_builder.get_dynamic_exchange', return_value='OVERNIGHT'):
        with patch('brokers.ibkr.order_builder.get_dynamic_tif', return_value='OND'):
            await adapter.place_limit_order('TQQQ', 'BUY', 10, 50.0, order_id="123")

            # Check the contract passed to placeOrder
            contract_arg, order_arg = mock_ib.placeOrder.call_args[0]
            assert contract_arg.symbol == 'TQQQ'
            assert contract_arg.exchange == 'OVERNIGHT'
            assert contract_arg.primaryExchange == 'NASDAQ'
            assert contract_arg.secType == 'STK'

def test_build_bracket_order_contract_routing(mock_ib):
    from brokers.ibkr.order_builder import build_bracket_order

    with patch('brokers.ibkr.order_builder.get_dynamic_exchange', return_value='OVERNIGHT'):
        with patch('brokers.ibkr.order_builder.get_dynamic_tif', return_value='OND'):
            c, p, t = build_bracket_order(mock_ib, 'TQQQ', 'BUY', 10, 50.0, 55.0)
            assert c.symbol == 'TQQQ'
            assert c.exchange == 'OVERNIGHT'
            assert c.primaryExchange == 'NASDAQ'

@pytest.mark.asyncio
async def test_get_bid_ask_fallback(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    mock_ticker = MagicMock()
    mock_ticker.bid = 0.0
    mock_ticker.ask = 0.0
    mock_ticker.last = 50.5
    mock_ticker.close = 50.0

    mock_ib.reqMktData.return_value = mock_ticker

    with patch('brokers.ibkr.order_builder.get_dynamic_exchange', return_value='OVERNIGHT'):
        bid, ask = await adapter.get_bid_ask('TQQQ')
        assert bid == 50.5
        assert ask == 50.5

@pytest.mark.asyncio
async def test_ibkr_reconnect_clears_state_and_readiness():
    adapter = IBKRAdapter("127.0.0.1", 7497, 1, False)
    # Simulate being connected and READY
    adapter.ib.isConnected = MagicMock(return_value=True)
    adapter._broker_state_ready = True

    # Mock account values with some dummy data to simulate being ready
    from ib_insync import AccountValue
    dummy_val = AccountValue(account='dummy', tag='dummy', value='dummy', currency='dummy', modelCode='dummy')
    # Using tuple of attributes as key per ib_insync wrapper implementation, or just any key to prove it clears
    adapter.ib.wrapper.accountValues[('dummy', 'dummy', 'dummy', 'dummy')] = dummy_val

    # Simulate a disconnect happening
    adapter.ib.isConnected = MagicMock(return_value=False)

    # Mock connectAsync for Stage 1 reconnect
    adapter.ib.connectAsync = AsyncMock()
    # Mock reqMarketDataType
    adapter.ib.reqMarketDataType = MagicMock()

    # During Stage 1, it calls isConnected to verify. We'll make it return True
    # AFTER the reconnect attempt.
    adapter.ib.isConnected.side_effect = [False, True]

    # Call ensure_connected which triggers Stage 1
    await adapter.ensure_connected()

    # Readiness should be explicitly reset to False
    assert adapter._broker_state_ready is False

    # The wrapper's cached accountValues should be cleared so they don't immediately
    # flip readiness back to True
    assert len(adapter.ib.wrapper.accountValues) == 0

    # Test that get_position_snapshot now correctly returns is_ready=False
    snapshot = await adapter.get_position_snapshot()
    assert snapshot.is_ready is False

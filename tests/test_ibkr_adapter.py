import pytest
import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from ib_insync import IB, Stock, LimitOrder, OrderStatus, Trade
from brokers.ibkr.adapter import IBKRAdapter
from brokers.ibkr.order_builder import get_dynamic_exchange

@pytest.fixture
def mock_ib():
    ib = MagicMock(spec=IB)
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
        c, p, t = build_bracket_order(mock_ib, 'TQQQ', 'BUY', 10, 50.0, 55.0)

        assert p.outsideRth is True
        assert p.tif == 'GTC'
        assert t.outsideRth is True
        assert t.tif == 'GTC'

@pytest.mark.parametrize("current_time,expected_exchange", [
    (datetime.time(10, 0), "SMART"),      # 10 AM ET -> SMART
    (datetime.time(21, 0), "OVERNIGHT"),  # 9 PM ET -> OVERNIGHT
    (datetime.time(2, 0), "OVERNIGHT"),   # 2 AM ET -> OVERNIGHT
    (datetime.time(3, 49), "OVERNIGHT"),  # 3:49 AM ET -> OVERNIGHT
    (datetime.time(3, 50), "SMART"),      # 3:50 AM ET -> SMART
    (datetime.time(20, 0), "OVERNIGHT"),  # 8:00 PM ET -> OVERNIGHT
    (datetime.time(19, 59), "SMART"),     # 7:59 PM ET -> SMART
])
def test_dynamic_exchange_logic(current_time, expected_exchange):
    with patch('brokers.ibkr.order_builder.datetime') as mock_datetime:
        # Mock now().time() to return current_time
        # In the implementation: now_et = datetime.datetime.now(tz)
        #                        current_time = now_et.time()
        mock_now = MagicMock()
        mock_now.time.return_value = current_time
        mock_datetime.datetime.now.return_value = mock_now
        mock_datetime.time = datetime.time

        assert get_dynamic_exchange() == expected_exchange

@pytest.mark.asyncio
async def test_on_fill_callback(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    mock_callback1 = MagicMock()
    mock_callback2 = MagicMock()

    # Place two orders
    adapter._on_fill_callbacks['100'] = mock_callback1
    adapter._on_fill_callbacks['200'] = mock_callback2

    # Create mock Trades
    trade1 = MagicMock()
    trade1.order.orderId = 100
    trade1.contract.symbol = 'TQQQ'
    trade1.orderStatus.status = 'Filled'
    trade1.orderStatus.filled = 10
    trade1.orderStatus.avgFillPrice = 50.5

    trade2 = MagicMock()
    trade2.order.orderId = 200
    trade2.contract.symbol = 'TQQQ'
    trade2.orderStatus.status = 'Filled'
    trade2.orderStatus.filled = 5
    trade2.orderStatus.avgFillPrice = 51.0

    # Manually trigger callbacks
    adapter._on_order_status(trade1)
    adapter._on_order_status(trade2)

    mock_callback1.assert_called_once_with({
        'order_id': '100',
        'symbol': 'TQQQ',
        'qty': 10,
        'price': 50.5
    })
    mock_callback2.assert_called_once_with({
        'order_id': '200',
        'symbol': 'TQQQ',
        'qty': 5,
        'price': 51.0
    })

@pytest.mark.asyncio
async def test_market_data_cancellation(mock_ib):
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = mock_ib

    # Mock reqMktData to return a ticker with last price
    mock_ticker = MagicMock()
    mock_ticker.last = 50.0
    mock_ib.reqMktData.return_value = mock_ticker
    mock_ib.cancelMktData = MagicMock()

    price = await adapter.get_price('TQQQ')

    assert price == 50.0
    mock_ib.reqMktData.assert_called_once()
    mock_ib.cancelMktData.assert_called_once()

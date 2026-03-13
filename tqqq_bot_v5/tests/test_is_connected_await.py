import pytest
from unittest.mock import MagicMock, AsyncMock
from brokers.ibkr.adapter import IBKRAdapter

@pytest.mark.asyncio
async def test_ensure_connected_awaits_is_connected():
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = MagicMock()

    # Mock is_connected to return True immediately to avoid the retry loop
    adapter.is_connected = AsyncMock(return_value=True)

    await adapter.ensure_connected()

    # If it wasn't awaited, this would fail or throw a warning
    adapter.is_connected.assert_awaited()

@pytest.mark.asyncio
async def test_ensure_connected_reconnects_and_awaits():
    adapter = IBKRAdapter(host='localhost', port=7497, client_id=1, paper=True)
    adapter.ib = MagicMock()

    # First call False, second call True
    adapter.is_connected = AsyncMock(side_effect=[False, True])
    adapter.ib.connectAsync = AsyncMock()

    await adapter.ensure_connected()

    assert adapter.is_connected.await_count == 2
    adapter.ib.connectAsync.assert_awaited_once()

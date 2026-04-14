import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from decimal import Decimal
from brokers.public.adapter import PublicAdapter
from brokers.base import OrderResult

@pytest.fixture
def public_adapter():
    return PublicAdapter(
        secret_key="test_secret",
        account_id="test_account",
        preflight_enabled=False,
        prefer_replace=False
    )

@pytest.mark.asyncio
async def test_connect_disconnect(public_adapter):
    with patch('brokers.public.adapter.AsyncPublicApiClient') as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        # Test connect
        assert await public_adapter.connect() is True
        assert await public_adapter.is_connected()
        assert public_adapter.client is not None
        mock_client.__aenter__.assert_called_once()

        # Test disconnect
        await public_adapter.disconnect()
        assert await public_adapter.is_connected() is False
        mock_client.__aexit__.assert_called_once()

@pytest.mark.asyncio
async def test_place_limit_order(public_adapter):
    with patch('brokers.public.adapter.AsyncPublicApiClient') as mock_client_cls:
        mock_client = AsyncMock()
        public_adapter.client = mock_client
        public_adapter._connected = True

        # Mock order placement
        mock_order = AsyncMock()
        mock_client.place_order.return_value = mock_order

        # Mock get_order with retry
        public_adapter._get_order_with_retry = AsyncMock(return_value=True)

        on_update_mock = MagicMock()
        result = await public_adapter.place_limit_order(
            ticker="TQQQ",
            action="BUY",
            qty=10,
            limit_price=100.5,
            extended_hours=True,
            on_update=on_update_mock,
            order_id="cf05e09e-6d40-4da2-befe-a303ebf0b2d7"
        )

        assert result.order_id == "cf05e09e-6d40-4da2-befe-a303ebf0b2d7"
        assert result.status == "submitted"
        assert "cf05e09e-6d40-4da2-befe-a303ebf0b2d7" in public_adapter._order_cache
        mock_client.place_order.assert_called_once()
        mock_order.subscribe_updates.assert_called_once()

@pytest.mark.asyncio
async def test_cancel_order(public_adapter):
    with patch('brokers.public.adapter.AsyncPublicApiClient') as mock_client_cls:
        mock_client = AsyncMock()
        public_adapter.client = mock_client
        public_adapter._connected = True

        mock_order = MagicMock()
        mock_order.status = "CANCELLED"
        mock_client.get_order.return_value = mock_order

        result = await public_adapter.cancel_order("cf05e09e-6d40-4da2-befe-a303ebf0b2d7")
        assert result is True
        mock_client.cancel_order.assert_called_once_with(order_id="cf05e09e-6d40-4da2-befe-a303ebf0b2d7", account_id="test_account")
        mock_client.get_order.assert_called()

@pytest.mark.asyncio
async def test_execution_callback(public_adapter):
    # Setup cache
    public_adapter._order_cache["cf05e09e-6d40-4da2-befe-a303ebf0b2d7"] = {"ticker": "TQQQ", "action": "BUY", "extended_hours": True}

    # Setup execution callback
    exec_callback = MagicMock()
    public_adapter.subscribe_to_executions(exec_callback)

    # Create fake update event
    mock_update = MagicMock()
    mock_update.order_id = "cf05e09e-6d40-4da2-befe-a303ebf0b2d7"
    mock_update.new_status = "FILLED"
    mock_update.filled_quantity = Decimal("10")
    mock_update.average_execution_price = Decimal("100.5")

    # Trigger handle update
    await public_adapter._handle_order_update(mock_update)

    # Verify execution callback was fired
    exec_callback.assert_called_once()
    called_arg = exec_callback.call_args[0][0]
    assert called_arg["order_id"] == "cf05e09e-6d40-4da2-befe-a303ebf0b2d7"
    assert called_arg["type"] == "BUY"
    assert called_arg["filled_qty"] == 10
    assert called_arg["filled_price"] == 100.5

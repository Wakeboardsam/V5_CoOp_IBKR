import unittest
from unittest.mock import MagicMock, patch
import json
import asyncio
from config.schema import AppConfig
from sheets.interface import SheetInterface
from sheets.schema import (
    COL_STATUS, COL_STRATEGY, COL_SELL_PRICE, COL_BUY_PRICE, COL_SHARES
)

class TestSheetInterface(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = AppConfig(
            google_sheet_id="fake_id",
            google_credentials_json=json.dumps({"project_id": "test", "private_key": "fake", "client_email": "test@example.com"})
        )
        # Mocking gspread.authorize and google.oauth2.service_account.Credentials.from_service_account_info
        self.patcher_creds = patch('google.oauth2.service_account.Credentials.from_service_account_info')
        self.patcher_gspread = patch('gspread.authorize')

        self.mock_creds = self.patcher_creds.start()
        self.mock_gspread = self.patcher_gspread.start()

        self.mock_client = MagicMock()
        self.mock_gspread.return_value = self.mock_client
        self.mock_sheet = MagicMock()
        self.mock_client.open_by_key.return_value = self.mock_sheet

        self.interface = SheetInterface(self.config)

    def tearDown(self):
        self.patcher_creds.stop()
        self.patcher_gspread.stop()

    async def test_fetch_grid_success(self):
        mock_worksheet = MagicMock()
        self.mock_sheet.worksheet.return_value = mock_worksheet

        # data for range C7:H100
        # Columns: C(Status), D(Strategy), E(Empty), F(Sell), G(Buy), H(Shares)
        mock_worksheet.get_values.return_value = [
            ["Ready", "Y", "", "105.0", "100.0", "10"],
            ["Filled", "N", "", "115.0", "110.0", "15"],
            ["Ready", "Y", "", "125.0", "120.0", "20"],
        ]

        grid_state = await self.interface.fetch_grid()

        self.assertEqual(len(grid_state.rows), 3)
        self.assertEqual(grid_state.rows[7].status, "Ready")
        self.assertTrue(grid_state.rows[7].has_y)
        self.assertEqual(grid_state.rows[7].sell_price, 105.0)
        self.assertEqual(grid_state.rows[7].buy_price, 100.0)
        self.assertEqual(grid_state.rows[7].shares, 10)

        self.assertFalse(grid_state.rows[8].has_y)
        self.assertEqual(grid_state.distal_y_row, 9)

    async def test_update_row_status(self):
        mock_worksheet = MagicMock()
        self.mock_sheet.worksheet.return_value = mock_worksheet

        await self.interface.update_row_status(10, "Working")

        mock_worksheet.update_cell.assert_called_once_with(10, COL_STATUS, "Working")

    async def test_log_fill_success(self):
        mock_worksheet = MagicMock()
        self.mock_sheet.worksheet.return_value = mock_worksheet

        fill_data = {
            "row_id": "7",
            "type": "BUY",
            "filled_price": 99.5,
            "filled_qty": 10,
            "order_id": "ORDER-123"
        }

        result = await self.interface.log_fill(fill_data)

        self.assertTrue(result)
        mock_worksheet.append_row.assert_called_once()
        # Verify first value is timestamp, others match fill_data
        args = mock_worksheet.append_row.call_args[0][0]
        self.assertEqual(args[1], "7")
        self.assertEqual(args[3], 99.5)

    async def test_log_error_success(self):
        mock_worksheet = MagicMock()
        self.mock_sheet.worksheet.return_value = mock_worksheet

        result = await self.interface.log_error("Test error")

        self.assertTrue(result)
        mock_worksheet.append_row.assert_called_once()
        args = mock_worksheet.append_row.call_args[0][0]
        self.assertEqual(args[1], "Test error")

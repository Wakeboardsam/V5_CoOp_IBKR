import unittest
from unittest.mock import MagicMock, patch
import json
import asyncio
from config.schema import AppConfig
from sheets.interface import SheetInterface
from sheets.schema import (
    COL_ROW_ID, COL_TYPE, COL_TRIGGER_PRICE, COL_LIMIT_PRICE, COL_QUANTITY, COL_ACTIVE
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

    async def test_fetch_grid_filters_inactive(self):
        mock_worksheet = MagicMock()
        self.mock_sheet.worksheet.return_value = mock_worksheet

        mock_worksheet.get_all_records.return_value = [
            {COL_ROW_ID: "BUY_1", COL_TYPE: "BUY", COL_TRIGGER_PRICE: "100", COL_LIMIT_PRICE: "99", COL_QUANTITY: "10", COL_ACTIVE: "TRUE"},
            {COL_ROW_ID: "BUY_2", COL_TYPE: "BUY", COL_TRIGGER_PRICE: "90", COL_LIMIT_PRICE: "89", COL_QUANTITY: "10", COL_ACTIVE: "FALSE"},
            {COL_ROW_ID: "SELL_1", COL_TYPE: "SELL", COL_TRIGGER_PRICE: "110", COL_LIMIT_PRICE: "111", COL_QUANTITY: "10", COL_ACTIVE: "TRUE"},
        ]

        grid_state = await self.interface.fetch_grid()

        self.assertEqual(len(grid_state.buy_levels), 1)
        self.assertEqual(len(grid_state.sell_levels), 1)
        self.assertEqual(grid_state.buy_levels[0].row_id, "BUY_1")
        self.assertEqual(grid_state.sell_levels[0].row_id, "SELL_1")

    async def test_log_fill_success(self):
        mock_worksheet = MagicMock()
        self.mock_sheet.worksheet.return_value = mock_worksheet

        fill_data = {
            "row_id": "BUY_1",
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
        self.assertEqual(args[1], "BUY_1")
        self.assertEqual(args[3], 99.5)

    async def test_log_error_success(self):
        mock_worksheet = MagicMock()
        self.mock_sheet.worksheet.return_value = mock_worksheet

        result = await self.interface.log_error("Test error")

        self.assertTrue(result)
        mock_worksheet.append_row.assert_called_once()
        args = mock_worksheet.append_row.call_args[0][0]
        self.assertEqual(args[1], "Test error")

    async def test_log_error_worksheet_not_found(self):
        import gspread
        self.mock_sheet.worksheet.side_effect = gspread.exceptions.WorksheetNotFound

        result = await self.interface.log_error("Test error")
        self.assertFalse(result)

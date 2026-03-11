import gspread
import asyncio
import json
import logging
from datetime import datetime
from google.oauth2.service_account import Credentials
from config.schema import AppConfig
from engine.grid_state import GridState, GridRow
from sheets.schema import (
    GRID_TAB_NAME, FILLS_TAB_NAME, HEALTH_TAB_NAME,
    COL_STATUS, COL_STRATEGY, COL_SELL_PRICE, COL_BUY_PRICE, COL_SHARES
)

logger = logging.getLogger(__name__)

class SheetInterface:
    def __init__(self, config: AppConfig):
        self.config = config
        self.scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        self._creds = Credentials.from_service_account_info(
            json.loads(config.google_credentials_json),
            scopes=self.scopes
        )
        self._client = gspread.authorize(self._creds)
        self._sheet = self._client.open_by_key(config.google_sheet_id)

    async def fetch_grid(self) -> GridState:
        """Reads cols C through H (rows 7 to 100) and returns GridState."""
        data = await asyncio.to_thread(self._get_grid_range)

        rows = {}
        # Start row in sheet is 7. Data starts at row index 0.
        for i, row_values in enumerate(data):
            row_index = 7 + i
            if len(row_values) < 6: # C, D, E, F, G, H
                continue

            try:
                status = str(row_values[0]).strip()
                has_y = str(row_values[1]).strip().upper() == "Y"
                # row_values[2] is Column E (empty or notes in legacy)
                sell_price = float(row_values[3]) if row_values[3] else 0.0
                buy_price = float(row_values[4]) if row_values[4] else 0.0
                shares = int(row_values[5]) if row_values[5] else 0

                rows[row_index] = GridRow(
                    row_index=row_index,
                    status=status,
                    has_y=has_y,
                    sell_price=sell_price,
                    buy_price=buy_price,
                    shares=shares
                )
            except (ValueError, TypeError) as e:
                logger.debug(f"Skipping malformed row {row_index}: {e}")
                continue

        return GridState(rows=rows)

    def _get_grid_range(self):
        worksheet = self._sheet.worksheet(GRID_TAB_NAME)
        # C7:H100 range. get_values is 0-indexed for the result, but gspread range is 1-indexed.
        # Column C is 3, Column H is 8.
        return worksheet.get_values("C7:H100")

    async def update_row_status(self, row_index: int, status: str):
        """Writes exclusively to Column C for the given row index."""
        await asyncio.to_thread(self._update_cell, row_index, COL_STATUS, status)

    def _update_cell(self, row: int, col: int, value: str):
        worksheet = self._sheet.worksheet(GRID_TAB_NAME)
        worksheet.update_cell(row, col, value)

    async def log_fill(self, fill_data: dict) -> bool:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # TIMESTAMP, ROW_ID, TYPE, FILLED_PRICE, FILLED_QTY, ORDER_ID
        row = [
            timestamp,
            fill_data.get("row_id"),
            fill_data.get("type"),
            fill_data.get("filled_price"),
            fill_data.get("filled_qty"),
            fill_data.get("order_id")
        ]

        try:
            await asyncio.to_thread(self._append_fill_row, row)
            return True
        except Exception:
            return False

    def _append_fill_row(self, row: list):
        worksheet = self._sheet.worksheet(FILLS_TAB_NAME)
        worksheet.append_row(row)

    async def log_error(self, error_msg: str) -> bool:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            await asyncio.to_thread(self._append_error_row, [timestamp, error_msg])
            return True
        except Exception:
            return False

    def _append_error_row(self, row: list):
        try:
            worksheet = self._sheet.worksheet("Errors")
            worksheet.append_row(row)
        except gspread.exceptions.WorksheetNotFound:
            raise

    async def log_health(self, health_data: dict) -> bool:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # TIMESTAMP, LAST_PRICE, OPEN_ORDERS_COUNT, LAST_FILL_TIME, STATUS
        row = [
            timestamp,
            health_data.get("last_price"),
            health_data.get("open_orders_count"),
            health_data.get("last_fill_time"),
            health_data.get("status")
        ]

        try:
            await asyncio.to_thread(self._append_health_row, row)
            return True
        except Exception:
            return False

    def _append_health_row(self, row: list):
        try:
            worksheet = self._sheet.worksheet(HEALTH_TAB_NAME)
            worksheet.append_row(row)
        except gspread.exceptions.WorksheetNotFound:
            raise

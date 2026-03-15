import gspread
import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from google.oauth2.service_account import Credentials
from config.schema import AppConfig
from engine.grid_state import GridState, GridRow
from sheets.schema import (
    GRID_TAB_NAME, FILLS_TAB_NAME, HEALTH_TAB_NAME, ERRORS_TAB_NAME,
    COL_STATUS, COL_STRATEGY, COL_SELL_PRICE, COL_BUY_PRICE, COL_SHARES,
    ROW_HEARTBEAT, COL_HEARTBEAT, ROW_CASH, COL_CASH, ROW_ANCHOR_ASK, COL_ANCHOR_ASK,
    GRID_START_ROW, GRID_END_ROW
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
        await asyncio.to_thread(self._update_cell_with_guard, GRID_TAB_NAME, row_index, COL_STATUS, status)

    async def write_heartbeat(self, value: str):
        """Writes heartbeat to C1."""
        await asyncio.to_thread(self._update_cell_with_guard, GRID_TAB_NAME, ROW_HEARTBEAT, COL_HEARTBEAT, value)

    async def write_cash_value(self, value: float):
        """Writes cash value to C2."""
        await asyncio.to_thread(self._update_cell_with_guard, GRID_TAB_NAME, ROW_CASH, COL_CASH, value)

    async def write_anchor_ask(self, value: float):
        """Writes anchor ask price to G7."""
        await asyncio.to_thread(self._update_cell_with_guard, GRID_TAB_NAME, ROW_ANCHOR_ASK, COL_ANCHOR_ASK, value)

    def _update_cell_with_guard(self, worksheet_name: str, row: int, col: int, value: Any):
        """Guarded write method to ensure only approved cells/tabs are modified."""
        if worksheet_name == GRID_TAB_NAME:
            # Check special cells
            is_special = (
                (row == ROW_HEARTBEAT and col == COL_HEARTBEAT) or
                (row == ROW_CASH and col == COL_CASH) or
                (row == ROW_ANCHOR_ASK and col == COL_ANCHOR_ASK)
            )
            # Check grid status column
            is_grid_status = (GRID_START_ROW <= row <= GRID_END_ROW and col == COL_STATUS)

            if not (is_special or is_grid_status):
                raise ValueError(f"Unauthorized write attempt to {worksheet_name} at cell ({row}, {col})")
        elif worksheet_name in [FILLS_TAB_NAME, HEALTH_TAB_NAME, ERRORS_TAB_NAME]:
            raise ValueError(f"Use append_row for {worksheet_name}, not update_cell")
        else:
            raise ValueError(f"Unauthorized worksheet: {worksheet_name}")

        worksheet = self._sheet.worksheet(worksheet_name)
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
            await asyncio.to_thread(self._append_row_with_guard, FILLS_TAB_NAME, row)
            return True
        except Exception as e:
            logger.error(f"Failed to log fill: {e}")
            return False

    async def log_error(self, error_msg: str) -> bool:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            await asyncio.to_thread(self._append_row_with_guard, ERRORS_TAB_NAME, [timestamp, error_msg])
            return True
        except Exception as e:
            logger.error(f"Failed to log error to sheet: {e}")
            return False

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
            await asyncio.to_thread(self._append_row_with_guard, HEALTH_TAB_NAME, row)
            return True
        except Exception as e:
            logger.error(f"Failed to log health status: {e}")
            return False

    def _append_row_with_guard(self, worksheet_name: str, row_data: list):
        """Guarded append method to ensure only approved tabs are appended to."""
        if worksheet_name not in [FILLS_TAB_NAME, HEALTH_TAB_NAME, ERRORS_TAB_NAME]:
            raise ValueError(f"Unauthorized append attempt to {worksheet_name}")

        try:
            worksheet = self._sheet.worksheet(worksheet_name)
            worksheet.append_row(row_data)
        except gspread.exceptions.WorksheetNotFound:
            logger.error(f"Worksheet '{worksheet_name}' not found in the spreadsheet.")
            raise

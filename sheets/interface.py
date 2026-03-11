import gspread
import asyncio
import json
from datetime import datetime
from google.oauth2.service_account import Credentials
from config.schema import AppConfig
from engine.grid_state import GridState, GridLevel
from sheets.schema import (
    GRID_TAB_NAME, FILLS_TAB_NAME,
    COL_ROW_ID, COL_TYPE, COL_TRIGGER_PRICE, COL_LIMIT_PRICE, COL_QUANTITY, COL_ACTIVE
)

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
        records = await asyncio.to_thread(self._get_all_grid_records)

        buy_levels = []
        sell_levels = []

        for row in records:
            # Check if row is active. Using .get() for safety and strip/upper for robustness.
            active_val = str(row.get(COL_ACTIVE, "")).strip().upper()
            if active_val != "TRUE":
                continue

            try:
                level = GridLevel(
                    row_id=str(row.get(COL_ROW_ID)),
                    trigger_price=float(row.get(COL_TRIGGER_PRICE)),
                    limit_price=float(row.get(COL_LIMIT_PRICE)),
                    quantity=int(row.get(COL_QUANTITY))
                )

                type_val = str(row.get(COL_TYPE, "")).strip().upper()
                if type_val == "BUY":
                    buy_levels.append(level)
                elif type_val == "SELL":
                    sell_levels.append(level)
            except (ValueError, TypeError):
                # Skip malformed rows
                continue

        return GridState(buy_levels=buy_levels, sell_levels=sell_levels)

    def _get_all_grid_records(self):
        worksheet = self._sheet.worksheet(GRID_TAB_NAME)
        return worksheet.get_all_records()

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
            # Fallback or create? For now just re-raise as per plan to not assume too much.
            raise

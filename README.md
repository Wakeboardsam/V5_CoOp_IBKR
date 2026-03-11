# TQQQ Grid Trading Bot

An automated grid trading bot for TQQQ, designed for 24/7 unattended operation with IBKR (Interactive Brokers) support and Google Sheets as the control interface.

## Setup Instructions

1.  **Google Sheets Setup**:
    *   Create a new Google Sheet.
    *   Create four tabs: `Grid`, `Fills`, `Errors`, and `Health`.
    *   Set up headers for each tab as described in the [Schema](#google-sheet-schema) section.
    *   Create a Google Cloud Project, enable the Google Sheets and Drive APIs, and create a Service Account.
    *   Download the Service Account JSON credentials.
    *   Share the Google Sheet with the Service Account's email address (with Editor permissions).

2.  **Configuration**:
    *   Create a file named `options.json` (or use the Home Assistant Addon configuration).
    *   Populate it with your broker details, Google Sheet ID, and the content of your Service Account JSON.

3.  **Broker Setup (IBKR)**:
    *   Ensure IB Gateway or TWS is running and the API is enabled.
    *   Default port for paper trading is `7497`, and live trading is `7496`.

4.  **Running the Bot**:
    ```bash
    pip install -r requirements.txt
    python3 main.py
    ```

## Google Sheet Schema

### `Grid` Tab
The bot reads its configuration from this tab.

| Column | Description |
| :--- | :--- |
| `ROW_ID` | Unique identifier for the grid level. |
| `TYPE` | `BUY` or `SELL`. |
| `TRIGGER_PRICE` | The price at which the order should be placed. |
| `LIMIT_PRICE` | The limit price for the order. |
| `QUANTITY` | Number of shares to trade. |
| `ACTIVE` | `TRUE` to enable this level, anything else to ignore. |
| `NOTES` | Optional notes. |

### `Fills` Tab
Logs executed trades.

| Column | Description |
| :--- | :--- |
| `TIMESTAMP` | Time of the fill. |
| `ROW_ID` | The ID from the Grid tab. |
| `TYPE` | `BUY` or `SELL`. |
| `FILLED_PRICE` | The actual execution price. |
| `FILLED_QTY` | The number of shares filled. |
| `ORDER_ID` | The broker's order ID. |

### `Health` Tab
Logs periodic heartbeat and status (every 5 minutes).

| Column | Description |
| :--- | :--- |
| `TIMESTAMP` | Time of the health check. |
| `LAST_PRICE` | Last observed market price. |
| `OPEN_ORDERS_COUNT` | Number of active orders at the broker. |
| `LAST_FILL_TIME` | Timestamp of the last successful fill. |
| `STATUS` | Bot status (e.g., `Running`). |

### `Errors` Tab
Logs any runtime errors.

| Column | Description |
| :--- | :--- |
| `TIMESTAMP` | Time of the error. |
| `ERROR_MSG` | Detailed error message. |

## options.json Reference

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `active_broker` | string | `ibkr` | Currently supported: `ibkr`. |
| `paper_trading` | boolean | `true` | Use paper trading account if true. |
| `ibkr_host` | string | `127.0.0.1` | Host for IB Gateway/TWS. |
| `ibkr_port` | integer | `7497` | Port for IB Gateway/TWS. |
| `ibkr_client_id` | integer | `1` | API Client ID. |
| `poll_interval_seconds` | integer | `10` | How often to check for triggers. |
| `max_spread_pct` | float | `0.5` | Max allowed bid-ask spread % to trade. |
| `google_sheet_id` | string | (Required) | The ID from the Google Sheet URL. |
| `google_credentials_json`| string | (Required) | The full JSON content of the service account key. |

## Paper-to-Live Promotion Checklist

- [ ] Verify all grid levels in the `Grid` tab are correct for live trading.
- [ ] Update `google_sheet_id` if using a separate sheet for live.
- [ ] Set `paper_trading` to `false` in `options.json`.
- [ ] Update `ibkr_port` to the live port (usually `7496`).
- [ ] Ensure the live account has sufficient permissions and market data subscriptions for TQQQ.
- [ ] Test the connection with a single small order first.

## Broker Swap Instructions

To swap brokers (e.g., when Schwab support is fully implemented):
1.  Update `active_broker` in `options.json` to the new broker name (e.g., `schwab`).
2.  Add any broker-specific configuration to `options.json`.
3.  Ensure the new adapter is properly implemented in `brokers/` and follows the `BrokerBase` interface.
4.  Restart the bot.

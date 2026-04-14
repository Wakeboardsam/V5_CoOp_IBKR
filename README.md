# TQQQ Grid Trading Bot v5

An automated grid trading bot for TQQQ, designed for 24/7 unattended operation with IBKR (Interactive Brokers) support and Google Sheets as the control interface. V5 implements an asynchronous engine with a 7-level sliding window and strict state management.

## Setup Instructions

1.  **Google Sheets Setup**:
    *   Create a new Google Sheet.
    *   Create four tabs: `TQQQ_Tracker`, `Fills`, `Errors`, and `Health`.
    *   Set up the `TQQQ_Tracker` tab using the legacy v4 layout (Data starting at Row 7).
    *   Create a Google Cloud Project, enable the Google Sheets and Drive APIs, and create a Service Account.
    *   Download the Service Account JSON credentials.
    *   Share the Google Sheet with the Service Account's email address (with Editor permissions).

2.  **Configuration**:
    *   Create a file named `options.json` (or use the Home Assistant Addon configuration).
    *   Populate it with your broker details, Google Sheet ID, and the content of your Service Account JSON.
    *   **Security Best Practice:** Ensure `options.json` is never committed to Git. In production or containerized deployments, use a secret manager, mount secrets as volumes, or utilize Home Assistant's built-in secrets handling.

3.  **Broker Setup (IBKR)**:
    *   Ensure IB Gateway or TWS is running and the API is enabled.
    *   Default port for paper trading is `7497`, and live trading is `7496`.
    *   The bot uses delayed market data (Type 3) by default.

4.  **Running the Bot**:
    ```bash
    pip install -r tqqq_bot_v5/requirements.txt
    PYTHONPATH=tqqq_bot_v5 python3 tqqq_bot_v5/main.py
    ```

## Core Features (v5)

*   **7-Level Sliding Window**: Trading is constrained to a 7-level window centered on the highest owned row (`distal_y_row +/- 3`), with a minimum index of Row 7.
*   **Share Mismatch Circuit Breaker**: Compares broker positions against the grid sheet. Modes: `halt` (stops the engine) or `warn` (logs error but continues housekeeping, skipping new BUY orders).
*   **Anchor Acquisition**: Automated acquisition of the first level (Row 7) using market `ask` price plus an optional offset when no shares are owned.
*   **Spread Guard**: Prevents trading when the bid-ask spread exceeds a configurable percentage.
*   **Graceful Shutdown**: Cancels all tracked GTC orders on SIGTERM before disconnecting.

## Google Sheet Schema

### `TQQQ_Tracker` Tab (Legacy Layout)
The bot reads its state from this tab and writes status updates. Data begins at **Row 7**.

| Column | Name | Description | Bot Access |
| :--- | :--- | :--- | :--- |
| **C** | `Status` | `WORKING_BUY:ID`, `WORKING_SELL:ID`, `OWNED:ID`, or `IDLE`. | **Read/Write** |
| **D** | `Strategy` | User formula; must return `Y` if the level is owned. | **Read Only** |
| **F** | `Sell Price` | Target price to sell shares for this level. | **Read Only** |
| **G** | `Buy Price` | Target price to buy shares for this level. | **Read Only** |
| **H** | `Shares` | Number of shares for this level. | **Read Only** |

**Approved Write Surface (TQQQ_Tracker):**
*   `C1`: Heartbeat timestamp.
*   `C2`: Current wallet balance (USD).
*   `G7`: Anchor ask price (written only during anchor acquisition).
*   `C7:C100`: Order status tracking.

### `Fills` Tab (Append Only)
Logs executed trades.
Columns: `TIMESTAMP`, `ROW_ID`, `TYPE`, `FILLED_PRICE`, `FILLED_QTY`, `ORDER_ID`.

### `Health` Tab (Append Only)
Logs periodic health snapshots.
Columns **A:J**: `TIMESTAMP`, `LAST_PRICE`, `OPEN_ORDERS_COUNT`, `LAST_FILL_TIME`, `STATUS`, `POSITION`, `MARKET_PRICE`, `MARKET_VALUE`, `AVG_COST`, `NET_LIQUIDATION_VALUE`.

*Note: `NET_LIQUIDATION_VALUE` is read from IBKR account values (`NetLiquidation`, preferred `USD`).*

### `Errors` Tab (Append Only)
Logs runtime errors and circuit breaker events.
Columns: `TIMESTAMP`, `ERROR_MSG`.

## options.json Reference

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `active_broker` | string | `ibkr` | Currently supported: `ibkr`, `schwab`, `public`. |
| `paper_trading` | boolean | `true` | Use paper trading account if true. |
| `public_secret_key` | string | `null` | Public.com API secret key (Required if active_broker=public). |
| `public_account_id` | string | `null` | Public.com Account ID (Required if active_broker=public). |
| `public_preflight_enabled` | boolean | `true` | Enable Preflight checks on Public orders. |
| `public_prefer_replace` | boolean | `true` | Use cancel-and-replace for Public instead of cancel-and-place. |
| `ibkr_host` | string | `127.0.0.1` | Host for IB Gateway/TWS. |
| `ibkr_port` | integer | `7497` | Port for IB Gateway/TWS. |
| `ibkr_client_id` | integer | `1` | API Client ID. |
| `poll_interval_seconds` | integer | `60` | Main engine tick interval. |
| `heartbeat_interval_seconds`| integer | `60` | Frequency of `C1` heartbeat updates. |
| `health_log_interval_seconds`| integer | `300` | Frequency of `Health` tab appends. |
| `anchor_buy_offset` | float | `0.0` | Offset added to `ask` for anchor acquisition. |
| `share_mismatch_mode` | string | `halt` | `halt` or `warn` on position discrepancy. |
| `max_spread_pct` | float | `0.5` | Max allowed bid-ask spread % to trade. |
| `google_sheet_id` | string | (Required) | The ID from the Google Sheet URL. |
| `google_credentials_json`| string | (Required) | The full JSON content of the service account key. |

## Paper-to-Live Promotion Checklist

- [ ] Verify all grid levels in `TQQQ_Tracker` are correct for live trading.
- [ ] Update `google_sheet_id` if using a separate sheet for live.
- [ ] Set `paper_trading` to `false` in `options.json`.
- [ ] Update `ibkr_port` to the live port (usually `7496`).
- [ ] Ensure the live account has sufficient permissions and market data subscriptions for TQQQ.
- [ ] Test the connection with a single small order first.

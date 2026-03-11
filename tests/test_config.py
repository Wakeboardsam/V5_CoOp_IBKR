import pytest
import json
import os
from pydantic import ValidationError
from config.loader import load_config
from config.schema import AppConfig


def test_default_values(tmp_path):
    config_file = tmp_path / "options.json"
    config_data = {
        "google_sheet_id": "test_sheet_id",
        "google_credentials_json": "{}"
    }
    with open(config_file, "w") as f:
        json.dump(config_data, f)

    config = load_config(str(config_file))

    assert config.active_broker == "ibkr"
    assert config.paper_trading is True
    assert config.ibkr_host == "127.0.0.1"
    assert config.ibkr_port == 7497
    assert config.ibkr_client_id == 1
    assert config.poll_interval_seconds == 10
    assert config.max_spread_pct == 0.5
    assert config.google_sheet_id == "test_sheet_id"
    assert config.google_credentials_json == "{}"


def test_missing_google_sheet_id(tmp_path):
    config_file = tmp_path / "options.json"
    config_data = {
        "google_credentials_json": "{}"
    }
    with open(config_file, "w") as f:
        json.dump(config_data, f)

    with pytest.raises(SystemExit) as e:
        load_config(str(config_file))

    assert e.type == SystemExit
    assert e.value.code == 1

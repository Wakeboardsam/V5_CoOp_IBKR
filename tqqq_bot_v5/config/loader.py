import json
import sys
from pydantic import ValidationError
from config.schema import AppConfig


def validate_public_settings(config: AppConfig) -> list[str]:
    """
    Validates that public_secret_key and public_account_id are provided
    when active_broker is 'public'.
    """
    warnings = []
    if config.active_broker == "public":
        if not config.public_secret_key:
            warnings.append(
                "active_broker is 'public' but public_secret_key is missing. "
                "Obtain it from your Public.com API settings."
            )
        if not config.public_account_id:
            warnings.append(
                "active_broker is 'public' but public_account_id is missing. "
                "Get it from your account details or portfolio endpoint."
            )
    return warnings


def validate_ibkr_settings(config: AppConfig) -> list[str]:
    """
    Validates that paper_trading and ibkr_port are consistent with IBKR defaults.
    Returns a list of warning messages.
    """
    warnings = []
    if config.active_broker == "ibkr":
        if config.paper_trading:
            if config.ibkr_port != 7497:
                warnings.append(
                    f"Inconsistency detected: paper_trading=True but ibkr_port={config.ibkr_port}. "
                    "IBKR default paper port is 7497."
                )
        else:
            if config.ibkr_port != 7496:
                warnings.append(
                    f"Inconsistency detected: paper_trading=False (LIVE) but ibkr_port={config.ibkr_port}. "
                    "IBKR default live port is 7496."
                )
    return warnings


def load_config(path: str = "/data/options.json") -> AppConfig:
    try:
        with open(path, "r") as f:
            data = json.load(f)
        config = AppConfig(**data)

        # Fail fast if public settings are invalid
        public_warnings = validate_public_settings(config)
        if public_warnings:
            for warning in public_warnings:
                print(f"Error: {warning}", file=sys.stderr)
            sys.exit(1)

        return config
    except FileNotFoundError:
        print(f"Error: Configuration file not found at {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {path}", file=sys.stderr)
        sys.exit(1)
    except ValidationError as e:
        print("Error: Missing or invalid required configuration fields:", file=sys.stderr)
        print(e, file=sys.stderr)
        sys.exit(1)

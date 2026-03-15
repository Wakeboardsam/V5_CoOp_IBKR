from typing import Optional
from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    active_broker: str = Field(default="ibkr")
    paper_trading: bool = Field(default=True)
    ibkr_host: str = Field(default="127.0.0.1")
    ibkr_port: int = Field(default=7497)
    ibkr_client_id: int = Field(default=1)
    ibkr_username: Optional[str] = Field(default=None)
    ibkr_password: Optional[str] = Field(default=None)
    poll_interval_seconds: int = Field(default=60)
    heartbeat_interval_seconds: int = Field(default=60)
    health_log_interval_seconds: int = Field(default=300)
    anchor_buy_offset: float = Field(default=0.0)
    share_mismatch_mode: str = Field(default="halt")
    max_spread_pct: float = Field(default=0.5)
    google_sheet_id: str
    google_credentials_json: str

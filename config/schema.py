from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    active_broker: str = Field(default="ibkr")
    paper_trading: bool = Field(default=True)
    ibkr_host: str = Field(default="127.0.0.1")
    ibkr_port: int = Field(default=7497)
    ibkr_client_id: int = Field(default=1)
    poll_interval_seconds: int = Field(default=10)
    max_spread_pct: float = Field(default=0.5)
    google_sheet_id: str
    google_credentials_json: str

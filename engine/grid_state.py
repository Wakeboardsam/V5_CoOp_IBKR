from dataclasses import dataclass

@dataclass
class GridLevel:
    row_id: str
    trigger_price: float
    limit_price: float
    quantity: int

@dataclass
class GridState:
    buy_levels: list[GridLevel]
    sell_levels: list[GridLevel]

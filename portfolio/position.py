from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Position:
    symbol: str
    size: int
    entry_price: float
    entry_time: datetime = field(default_factory=datetime.now)

    peak_price: float = field(init=False)
    trailing_stop_price: float = field(default=0.0)

    def __post_init__(self):
        self.peak_price = self.entry_price

    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.size

    def unrealized_pnl_pct(self, current_price: float) -> float:
        return (current_price - self.entry_price) / self.entry_price

    def update_peak(self, current_price: float) -> None:
        if current_price > self.peak_price:
            self.peak_price = current_price

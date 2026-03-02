import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings_loader import get_strategy, StrategyConfig
from strategy.base_strategy import BaseStrategy
from utils.trade_logger import log_bar, log_signal


class EMACrossoverStrategy(BaseStrategy):
    """
    Dual EMA crossover (Scalper_EMA2).
    BUY  → fast EMA crosses above slow EMA
    SELL → fast EMA crosses below slow EMA
    """

    def __init__(
        self,
        strategy_name: str = "Scalper_EMA2",
        settings_path: str = "settings.json",
    ):
        cfg: StrategyConfig = get_strategy(strategy_name, settings_path)
        p = cfg.parameters

        self.short_span: int = p["short_span"]
        self.long_span: int = p["long_span"]
        self.period_type: str = p.get("period_type", "day")
        self.period: int = p.get("period", 2)
        self.frequency_type: str = p.get("frequency_type", "minute")
        self.frequency: int = p.get("frequency", 1)

        self.symbols: dict[str, int] = {
            sym.name: sym.position_size for sym in cfg.symbols
        }

        assert self.short_span < self.long_span, (
            f"short_span ({self.short_span}) must be < long_span ({self.long_span})"
        )

    # ── Indicators ─────────────────────────────────────────────────────────────

    def compute_emas(self, prices: pd.Series) -> tuple[pd.Series, pd.Series]:
        fast = prices.ewm(span=self.short_span, adjust=False).mean()
        slow = prices.ewm(span=self.long_span, adjust=False).mean()
        return fast, slow

    # ── Signal ─────────────────────────────────────────────────────────────────

    def evaluate(self, prices: pd.Series, symbol: str = "") -> str:
        """
        Args:
            prices: pd.Series of closing prices, most recent last
            symbol: optional symbol name for logging
        Returns:
            'BUY', 'SELL', or 'HOLD'
        """
        if len(prices) < self.long_span + 1:
            return "HOLD"

        fast, slow = self.compute_emas(prices)

        if symbol:
            log_bar(symbol, float(prices.iloc[-1]),
                    float(fast.iloc[-1]), float(slow.iloc[-1]))

        prev_diff = float(fast.iloc[-2]) - float(slow.iloc[-2])
        curr_diff = float(fast.iloc[-1]) - float(slow.iloc[-1])

        if prev_diff < 0 and curr_diff > 0:
            signal = "BUY"
        elif prev_diff > 0 and curr_diff < 0:
            signal = "SELL"
        else:
            signal = "HOLD"

        if signal != "HOLD" and symbol:
            log_signal(symbol, signal, float(fast.iloc[-1]), float(slow.iloc[-1]))

        return signal

    def position_size(self, symbol: str) -> int:
        """Return configured position size for a symbol (0 if not configured)."""
        return self.symbols.get(symbol, 0)

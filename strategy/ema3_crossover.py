import sys
from pathlib import Path
import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings_loader import get_strategy, StrategyConfig
from strategy.base_strategy import BaseStrategy
from utils.trade_logger import log_signal


class EMA3CrossoverStrategy(BaseStrategy):
    """
    Triple EMA crossover (Scalper_EMA3).

    Uses three EMAs:
      fast   (short_span)  — reacts quickly, generates the crossover signal
      medium (medium_span) — intermediate; fast/medium crossover triggers entry/exit
      slow   (long_span)   — trend filter; signals only taken in its direction

    Signal rules:
      BUY  → fast crosses above medium AND last close is above slow EMA (uptrend)
      SELL → fast crosses below medium AND last close is below slow EMA (downtrend)
      HOLD → crossover occurs but against the slow-EMA trend, or no crossover
    """

    def __init__(
        self,
        strategy_name: str = "Scalper_EMA3",
        settings_path: str = "settings.json",
    ):
        cfg: StrategyConfig = get_strategy(strategy_name, settings_path)
        p = cfg.parameters

        self.short_span: int = p["short_span"]
        self.medium_span: int = p["medium_span"]
        self.long_span: int = p["long_span"]
        self.period_type: str = p.get("period_type", "day")
        self.period: int = p.get("period", 2)
        self.frequency_type: str = p.get("frequency_type", "minute")
        self.frequency: int = p.get("frequency", 1)

        self.symbols: dict[str, int] = {
            sym.name: sym.position_size for sym in cfg.symbols
        }

        assert self.short_span < self.medium_span < self.long_span, (
            f"EMA spans must satisfy short < medium < long "
            f"(got {self.short_span}/{self.medium_span}/{self.long_span})"
        )

    # ── Indicators ─────────────────────────────────────────────────────────────

    def compute_emas(
        self, prices: pd.Series
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        fast   = prices.ewm(span=self.short_span,  adjust=False).mean()
        medium = prices.ewm(span=self.medium_span, adjust=False).mean()
        slow   = prices.ewm(span=self.long_span,   adjust=False).mean()
        return fast, medium, slow

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

        fast, medium, slow = self.compute_emas(prices)

        f_curr = float(fast.iloc[-1])
        f_prev = float(fast.iloc[-2])
        m_curr = float(medium.iloc[-1])
        m_prev = float(medium.iloc[-2])
        s_curr = float(slow.iloc[-1])
        close  = float(prices.iloc[-1])

        if symbol:
            logger.trace(
                "[BAR3] {symbol} | close={close:.4f} "
                "| fast={fast:.4f} | medium={med:.4f} | slow={slow:.4f} "
                "| f-m={fm:+.4f} | trend={'UP' if close > s_curr else 'DOWN'}",
                symbol=symbol, close=close,
                fast=f_curr, med=m_curr, slow=s_curr,
                fm=f_curr - m_curr,
            )

        # Fast/medium crossover detection
        prev_fast_above_medium = f_prev > m_prev
        curr_fast_above_medium = f_curr > m_curr

        # Slow EMA trend filter: only trade in direction of slow EMA
        in_uptrend   = close > s_curr
        in_downtrend = close < s_curr

        if not prev_fast_above_medium and curr_fast_above_medium and in_uptrend:
            signal = "BUY"
        elif prev_fast_above_medium and not curr_fast_above_medium and in_downtrend:
            signal = "SELL"
        else:
            signal = "HOLD"

        if signal != "HOLD" and symbol:
            log_signal(symbol, signal, f_curr, m_curr)

        return signal

    def position_size(self, symbol: str) -> int:
        """Return configured position size for a symbol (0 if not configured)."""
        return self.symbols.get(symbol, 0)

"""
backtest/engine.py — Walk-forward backtesting engine.

Two data modes (--mode):

  "daily"    — 1 year  of daily bars  (~252 candles/symbol)
               period_type="year", period=1, frequency_type="daily", frequency=1

  "intraday" — 10 days of 1-min bars  (~3,900 candles/symbol)
               period_type="day",  period=10, frequency_type="minute", frequency=1

Custom date range (--start / --end, format YYYY-MM-DD):
  When both dates are provided the period param is replaced by start_date/end_date
  (milliseconds since epoch). The mode still controls the bar frequency:
    --mode daily    → daily   bars for the given range
    --mode intraday → 1-minute bars for the given range (max ~10 days of minute data)

Fill model  : BUY/SELL signals are filled at the NEXT bar's open price,
              avoiding look-ahead bias.

Risk checks : On each bar, intra-bar stop-loss (bar low) and profit-target
              (bar high) are evaluated before the signal check, matching the
              live bot's PositionMonitor behaviour.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings_loader import load_settings


# ── Mode configuration ────────────────────────────────────────────────────────

MODES: dict[str, dict] = {
    "daily": {
        "period_type":    "year",
        "period":         1,
        "frequency_type": "daily",
        "frequency":      1,
        "label":          "1 year of daily bars",
        "date_fmt":       "%Y-%m-%d",
    },
    "intraday": {
        "period_type":    "day",
        "period":         10,
        "frequency_type": "minute",
        "frequency":      1,
        "label":          "10 days of 1-minute bars",
        "date_fmt":       "%Y-%m-%d %H:%M",
    },
}

_DATE_INPUT_FMT = "%Y-%m-%d"


def _to_epoch_ms(date_str: str) -> int:
    """Convert 'YYYY-MM-DD' string to milliseconds since epoch (Schwab API format)."""
    dt = datetime.strptime(date_str, _DATE_INPUT_FMT)
    return int(dt.timestamp() * 1000)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    symbol:      str
    size:        int
    entry_bar:   int
    entry_price: float
    entry_date:  datetime
    exit_bar:    int                = 0
    exit_price:  float             = 0.0
    exit_date:   Optional[datetime] = None
    exit_reason: str               = ""

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.size

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price

    @property
    def hold_days(self) -> int:
        if self.entry_date and self.exit_date:
            return (self.exit_date - self.entry_date).days
        return 0

    @property
    def hold_mins(self) -> float:
        if self.entry_date and self.exit_date:
            return (self.exit_date - self.entry_date).total_seconds() / 60
        return 0.0


@dataclass
class BacktestResult:
    symbol:       str
    mode:         str         = "daily"
    date_range:   str         = ""        # e.g. "2025-01-01 → 2025-12-31" or ""
    trades:       list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    n_bars:       int         = 0


# ── Engine ────────────────────────────────────────────────────────────────────

class BacktestEngine:
    """
    Replays Schwab price history through a strategy.

    Usage:
        # Standard modes
        engine = BacktestEngine(client, strategy, mode="daily")
        engine = BacktestEngine(client, strategy, mode="intraday")

        # Custom date range (YYYY-MM-DD strings)
        engine = BacktestEngine(client, strategy, mode="daily",
                                start_date="2025-01-01", end_date="2025-06-30")
        engine = BacktestEngine(client, strategy, mode="intraday",
                                start_date="2026-02-17", end_date="2026-02-28")

        results = engine.run()   # dict[symbol → BacktestResult]
    """

    def __init__(
        self,
        client,
        strategy,
        settings_path: str = "settings.json",
        mode: str = "daily",
        start_date: Optional[str] = None,
        end_date:   Optional[str] = None,
    ):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {list(MODES)}; got '{mode}'")
        self.client     = client
        self.strategy   = strategy
        self.mode       = mode
        self._cfg       = MODES[mode]
        self.start_date = start_date   # "YYYY-MM-DD" or None
        self.end_date   = end_date     # "YYYY-MM-DD" or None
        cfg = load_settings(settings_path).global_settings
        self.profit_target_pct = cfg.profit_target_pct
        self.stop_loss_pct     = cfg.stop_loss_pct

    # ── Data fetching ─────────────────────────────────────────────────────────

    def fetch_history(self, symbol: str) -> pd.DataFrame:
        """
        Fetch OHLCV candles from Schwab.

        - No dates:  uses mode's period_type + period (standard window).
        - With dates: uses start_date/end_date in ms; omits period so Schwab
          returns all bars within that range at the mode's frequency.
        """
        cfg = self._cfg
        kwargs: dict = {
            "symbol":        symbol,
            "period_type":   cfg["period_type"],
            "frequency_type": cfg["frequency_type"],
            "frequency":     cfg["frequency"],
        }

        using_dates = self.start_date and self.end_date
        if using_dates:
            kwargs["start_date"] = _to_epoch_ms(self.start_date)
            kwargs["end_date"]   = _to_epoch_ms(self.end_date)
            # period is intentionally omitted — dates take precedence
        else:
            kwargs["period"] = cfg["period"]

        raw = self.client.get_price_history(**kwargs)
        candles = raw.get("candles", [])
        if not candles:
            raise ValueError(f"No candles returned for {symbol}")

        rows = []
        for c in candles:
            rows.append({
                "date":   datetime.fromtimestamp(c["datetime"] / 1000),
                "open":   float(c["open"]),
                "high":   float(c["high"]),
                "low":    float(c["low"]),
                "close":  float(c["close"]),
                "volume": int(c.get("volume", 0)),
            })

        df  = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        fmt = cfg["date_fmt"]

        if using_dates:
            range_str = f"{self.start_date} → {self.end_date}"
        else:
            range_str = cfg["label"]

        logger.info(
            "[BACKTEST] {} — {} bars  ({})  {} → {}",
            symbol, len(df), range_str,
            df["date"].iloc[0].strftime(fmt),
            df["date"].iloc[-1].strftime(fmt),
        )
        return df, range_str

    # ── Single-symbol backtest ────────────────────────────────────────────────

    def run_symbol(self, symbol: str, size: int) -> BacktestResult:
        df, range_str = self.fetch_history(symbol)
        closes = df["close"]
        result = BacktestResult(
            symbol=symbol, mode=self.mode,
            date_range=range_str, n_bars=len(df),
        )

        open_trade: Optional[Trade] = None
        cumulative_pnl = 0.0
        fmt = self._cfg["date_fmt"]

        for i in range(len(df)):
            result.equity_curve.append(cumulative_pnl)

            bar = df.iloc[i]

            # ── Intra-bar risk checks ─────────────────────────────────────────
            if open_trade is not None:
                pnl_pct_high = (bar["high"] - open_trade.entry_price) / open_trade.entry_price
                pnl_pct_low  = (bar["low"]  - open_trade.entry_price) / open_trade.entry_price

                if pnl_pct_low <= -self.stop_loss_pct:
                    exit_price = round(open_trade.entry_price * (1 - self.stop_loss_pct), 4)
                    open_trade = self._close(open_trade, i, exit_price, bar["date"], "stop-loss")
                    cumulative_pnl += open_trade.pnl
                    result.trades.append(open_trade)
                    open_trade = None
                    continue

                elif pnl_pct_high >= self.profit_target_pct:
                    exit_price = round(open_trade.entry_price * (1 + self.profit_target_pct), 4)
                    open_trade = self._close(open_trade, i, exit_price, bar["date"], "profit-target")
                    cumulative_pnl += open_trade.pnl
                    result.trades.append(open_trade)
                    open_trade = None
                    continue

            # ── Strategy signal ───────────────────────────────────────────────
            prices_so_far = closes.iloc[: i + 1]
            signal = self.strategy.evaluate(prices_so_far, symbol="")

            if i + 1 >= len(df):
                break

            next_open = float(df["open"].iloc[i + 1])
            next_date = df["date"].iloc[i + 1]

            if signal == "BUY" and open_trade is None:
                open_trade = Trade(
                    symbol=symbol, size=size,
                    entry_bar=i + 1, entry_price=next_open, entry_date=next_date,
                )
                logger.debug("[BACKTEST] {} BUY  bar={} @ {:.4f}  ({})",
                             symbol, i + 1, next_open, next_date.strftime(fmt))

            elif signal == "SELL" and open_trade is not None:
                open_trade = self._close(open_trade, i + 1, next_open, next_date, "signal")
                cumulative_pnl += open_trade.pnl
                result.trades.append(open_trade)
                logger.debug("[BACKTEST] {} SELL bar={} @ {:.4f}  pnl={:+.2f}  ({})",
                             symbol, i + 1, next_open, open_trade.pnl, next_date.strftime(fmt))
                open_trade = None

        # Force-close any position still open at last bar
        if open_trade is not None:
            last = df.iloc[-1]
            open_trade = self._close(
                open_trade, len(df) - 1,
                float(last["close"]), last["date"], "end-of-backtest"
            )
            cumulative_pnl += open_trade.pnl
            result.trades.append(open_trade)

        logger.info("[BACKTEST] {} complete — {} trade(s)  total_pnl={:+.2f}",
                    symbol, len(result.trades), cumulative_pnl)
        return result

    # ── Multi-symbol runner ───────────────────────────────────────────────────

    def run(self) -> dict[str, BacktestResult]:
        results = {}
        for symbol, size in self.strategy.symbols.items():
            logger.info("[BACKTEST] Starting {}  mode={}  dates={}",
                        symbol, self.mode,
                        f"{self.start_date}→{self.end_date}" if self.start_date else "default")
            try:
                results[symbol] = self.run_symbol(symbol, size)
            except Exception as exc:
                logger.error("[BACKTEST] {} failed: {}", symbol, exc)
        return results

    # ── Helper ────────────────────────────────────────────────────────────────

    @staticmethod
    def _close(trade: Trade, bar: int, price: float,
               date: datetime, reason: str) -> Trade:
        trade.exit_bar    = bar
        trade.exit_price  = price
        trade.exit_date   = date
        trade.exit_reason = reason
        return trade

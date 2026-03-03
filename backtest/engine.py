"""
backtest/engine.py — Walk-forward backtesting engine.

Data source : Schwab API get_price_history()
              period_type="year", period=1, frequency_type="daily", frequency=1
              → ~252 daily OHLCV candles per symbol.

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


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    symbol:      str
    size:        int
    entry_bar:   int
    entry_price: float
    entry_date:  datetime
    exit_bar:    int           = 0
    exit_price:  float         = 0.0
    exit_date:   Optional[datetime] = None
    exit_reason: str           = ""

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


@dataclass
class BacktestResult:
    symbol:       str
    trades:       list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)  # cumulative P&L per bar
    n_bars:       int         = 0


# ── Engine ────────────────────────────────────────────────────────────────────

class BacktestEngine:
    """
    Replays 1 year of daily Schwab price history through a strategy.

    Usage:
        engine  = BacktestEngine(client, strategy)
        results = engine.run()           # dict[symbol → BacktestResult]
    """

    def __init__(self, client, strategy, settings_path: str = "settings.json"):
        self.client   = client
        self.strategy = strategy
        cfg = load_settings(settings_path).global_settings
        self.profit_target_pct = cfg.profit_target_pct   # e.g. 0.02
        self.stop_loss_pct     = cfg.stop_loss_pct       # e.g. 0.01

    # ── Data fetching ─────────────────────────────────────────────────────────

    def fetch_history(self, symbol: str) -> pd.DataFrame:
        """
        Fetch 1 year of daily OHLCV candles from Schwab.
        Returns a DataFrame with columns: date, open, high, low, close, volume.
        Rows are sorted oldest → newest.
        """
        raw = self.client.get_price_history(
            symbol=symbol,
            period_type="year",
            period=1,
            frequency_type="daily",
            frequency=1,
        )
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

        df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        logger.info(
            "[BACKTEST] {} — {} daily bars  ({} → {})",
            symbol, len(df),
            df["date"].iloc[0].strftime("%Y-%m-%d"),
            df["date"].iloc[-1].strftime("%Y-%m-%d"),
        )
        return df

    # ── Single-symbol backtest ────────────────────────────────────────────────

    def run_symbol(self, symbol: str, size: int) -> BacktestResult:
        """
        Walk forward through every bar, evaluate strategy, simulate fills.
        Returns a BacktestResult with the full trade list and equity curve.
        """
        df     = self.fetch_history(symbol)
        closes = df["close"]
        result = BacktestResult(symbol=symbol, n_bars=len(df))

        open_trade: Optional[Trade] = None
        cumulative_pnl = 0.0

        for i in range(len(df)):
            result.equity_curve.append(cumulative_pnl)

            bar = df.iloc[i]

            # ── Intra-bar risk checks on open position ────────────────────────
            if open_trade is not None:
                pnl_pct_high = (bar["high"] - open_trade.entry_price) / open_trade.entry_price
                pnl_pct_low  = (bar["low"]  - open_trade.entry_price) / open_trade.entry_price

                # Stop-loss — assume filled at stop price (not bar low)
                if pnl_pct_low <= -self.stop_loss_pct:
                    exit_price = round(open_trade.entry_price * (1 - self.stop_loss_pct), 4)
                    open_trade = self._close(
                        open_trade, i, exit_price, bar["date"], "stop-loss"
                    )
                    cumulative_pnl += open_trade.pnl
                    result.trades.append(open_trade)
                    open_trade = None
                    continue

                # Profit target — assume filled at target price (not bar high)
                elif pnl_pct_high >= self.profit_target_pct:
                    exit_price = round(open_trade.entry_price * (1 + self.profit_target_pct), 4)
                    open_trade = self._close(
                        open_trade, i, exit_price, bar["date"], "profit-target"
                    )
                    cumulative_pnl += open_trade.pnl
                    result.trades.append(open_trade)
                    open_trade = None
                    continue

            # ── Strategy signal on prices up to and including bar i ───────────
            prices_so_far = closes.iloc[: i + 1]
            signal = self.strategy.evaluate(prices_so_far, symbol="")

            # Need a next bar to fill on
            if i + 1 >= len(df):
                break

            next_open = float(df["open"].iloc[i + 1])
            next_date = df["date"].iloc[i + 1]

            if signal == "BUY" and open_trade is None:
                open_trade = Trade(
                    symbol=symbol,
                    size=size,
                    entry_bar=i + 1,
                    entry_price=next_open,
                    entry_date=next_date,
                )
                logger.debug(
                    "[BACKTEST] {} BUY  bar={} @ {:.4f}  ({})",
                    symbol, i + 1, next_open,
                    next_date.strftime("%Y-%m-%d"),
                )

            elif signal == "SELL" and open_trade is not None:
                open_trade = self._close(open_trade, i + 1, next_open, next_date, "signal")
                cumulative_pnl += open_trade.pnl
                result.trades.append(open_trade)
                logger.debug(
                    "[BACKTEST] {} SELL bar={} @ {:.4f}  pnl={:+.2f}  ({})",
                    symbol, i + 1, next_open, open_trade.pnl,
                    next_date.strftime("%Y-%m-%d"),
                )
                open_trade = None

        # Force-close any position still open at last bar's close
        if open_trade is not None:
            last = df.iloc[-1]
            open_trade = self._close(
                open_trade, len(df) - 1,
                float(last["close"]), last["date"], "end-of-backtest"
            )
            cumulative_pnl += open_trade.pnl
            result.trades.append(open_trade)

        logger.info(
            "[BACKTEST] {} complete — {} trade(s)  total_pnl={:+.2f}",
            symbol, len(result.trades), cumulative_pnl,
        )
        return result

    # ── Multi-symbol runner ───────────────────────────────────────────────────

    def run(self) -> dict[str, BacktestResult]:
        """
        Run backtest for every symbol in strategy.symbols.
        Returns dict[symbol → BacktestResult].
        """
        results = {}
        for symbol, size in self.strategy.symbols.items():
            logger.info("[BACKTEST] Starting {} (size={})", symbol, size)
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

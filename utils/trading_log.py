"""
trading_log.py — Daily trade recorder + EOD summary.

Records every BUY/SELL fill to logs/trades_YYYY-MM-DD.csv and computes
round-trip P&L.  Call print_summary() at shutdown (Ctrl-C or EOD flatten)
to get a per-symbol breakdown printed to the console and written to the log.

CSV columns:
    time, symbol, side, qty, fill_price, entry_price, pnl, pnl_pct
    (entry_price / pnl / pnl_pct are blank for BUY rows)
"""

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

ET = ZoneInfo("America/New_York")


@dataclass
class _TradeRecord:
    time:        datetime
    symbol:      str
    side:        str          # "BUY" or "SELL"
    qty:         int
    fill_price:  float
    entry_price: float | None = None   # set on SELL from matched BUY
    pnl:         float | None = None
    pnl_pct:     float | None = None


@dataclass
class _SymbolStats:
    trades:    int   = 0
    wins:      int   = 0
    losses:    int   = 0
    total_pnl: float = 0.0
    best:      float | None = None
    worst:     float | None = None

    def record_pnl(self, pnl: float) -> None:
        self.trades += 1
        self.total_pnl += pnl
        if pnl > 0:
            self.wins += 1
        elif pnl < 0:
            self.losses += 1
        self.best  = pnl if self.best  is None else max(self.best,  pnl)
        self.worst = pnl if self.worst is None else min(self.worst, pnl)


class TradingLog:
    """
    Thread-safe daily trade recorder.

    Usage:
        log = TradingLog(strategy_name="Scalper_EMA2")
        log.record("AAPL", "BUY",  100, 264.60)
        log.record("AAPL", "SELL", 100, 265.50)
        log.print_summary()
    """

    def __init__(self, strategy_name: str, log_dir: str = "logs"):
        self._strategy = strategy_name
        self._date     = datetime.now(ET).date()
        self._path     = Path(log_dir) / f"trades_{self._date}.csv"
        self._records: list[_TradeRecord] = []
        # Track open entry price per symbol for round-trip P&L
        self._open_entry: dict[str, float] = {}
        self._open_qty:   dict[str, int]   = {}

        Path(log_dir).mkdir(exist_ok=True)
        self._ensure_header()

    # ── Public API ─────────────────────────────────────────────────────────────

    def record(self, symbol: str, side: str, qty: int, fill_price: float) -> None:
        """Record a fill and append to the daily CSV."""
        side = side.upper()
        now  = datetime.now(ET)

        entry_price = pnl = pnl_pct = None

        if side == "BUY":
            self._open_entry[symbol] = fill_price
            self._open_qty[symbol]   = qty

        elif side == "SELL" and symbol in self._open_entry:
            entry_price = self._open_entry.pop(symbol)
            open_qty    = self._open_qty.pop(symbol, qty)
            pnl         = (fill_price - entry_price) * open_qty
            pnl_pct     = (fill_price - entry_price) / entry_price * 100

        rec = _TradeRecord(
            time=now, symbol=symbol, side=side, qty=qty,
            fill_price=fill_price, entry_price=entry_price,
            pnl=pnl, pnl_pct=pnl_pct,
        )
        self._records.append(rec)
        self._append_csv(rec)

        if pnl is not None:
            logger.info(
                "[TRADE LOG] {} {} {} @ {:.4f} | entry={:.4f} pnl={:+.2f} ({:+.2f}%)",
                side, qty, symbol, fill_price, entry_price, pnl, pnl_pct,
            )
        else:
            logger.info(
                "[TRADE LOG] {} {} {} @ {:.4f}",
                side, qty, symbol, fill_price,
            )

    def print_summary(self) -> None:
        """Print and log the full-day trading summary."""
        summary = self._build_summary()
        # Print to console (bypasses logger formatting for clean table)
        print(summary)
        # Also write to the main log file
        for line in summary.splitlines():
            logger.info(line)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _ensure_header(self) -> None:
        if not self._path.exists():
            with open(self._path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "time", "symbol", "side", "qty",
                    "fill_price", "entry_price", "pnl", "pnl_pct",
                ])

    def _append_csv(self, rec: _TradeRecord) -> None:
        with open(self._path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                rec.time.strftime("%Y-%m-%d %H:%M:%S"),
                rec.symbol,
                rec.side,
                rec.qty,
                f"{rec.fill_price:.4f}",
                f"{rec.entry_price:.4f}" if rec.entry_price is not None else "",
                f"{rec.pnl:+.2f}"        if rec.pnl        is not None else "",
                f"{rec.pnl_pct:+.2f}%"  if rec.pnl_pct    is not None else "",
            ])

    def _build_summary(self) -> str:
        # Aggregate closed round-trips per symbol
        stats: dict[str, _SymbolStats] = {}
        buy_count:  dict[str, int] = {}
        sell_count: dict[str, int] = {}

        for rec in self._records:
            stats.setdefault(rec.symbol, _SymbolStats())
            if rec.side == "BUY":
                buy_count[rec.symbol] = buy_count.get(rec.symbol, 0) + 1
            elif rec.side == "SELL":
                sell_count[rec.symbol] = sell_count.get(rec.symbol, 0) + 1
                if rec.pnl is not None:
                    stats[rec.symbol].record_pnl(rec.pnl)

        total_pnl    = sum(s.total_pnl for s in stats.values())
        total_trades = sum(
            buy_count.get(sym, 0) + sell_count.get(sym, 0) for sym in stats
        )
        total_wins   = sum(s.wins   for s in stats.values())
        total_losses = sum(s.losses for s in stats.values())

        W = 56
        lines = [
            "═" * W,
            f"  DAY TRADING SUMMARY — {self._date}",
            f"  Strategy : {self._strategy}",
            f"  Log file : {self._path}",
            "═" * W,
        ]

        if not stats:
            lines += ["  No trades executed today.", "═" * W]
            return "\n".join(lines)

        # Header row
        lines.append(
            f"  {'Symbol':<6}  {'Buys':>4}  {'Sells':>5}  "
            f"{'Wins':>4}  {'Loss':>4}  {'P&L':>10}  {'Best':>9}  {'Worst':>9}"
        )
        lines.append("  " + "─" * (W - 2))

        for sym in sorted(stats):
            s  = stats[sym]
            bc = buy_count.get(sym, 0)
            sc = sell_count.get(sym, 0)
            best  = f"{s.best:+.2f}"  if s.best  is not None else "  —"
            worst = f"{s.worst:+.2f}" if s.worst is not None else "  —"
            lines.append(
                f"  {sym:<6}  {bc:>4}  {sc:>5}  "
                f"{s.wins:>4}  {s.losses:>4}  "
                f"{s.total_pnl:>+10.2f}  {best:>9}  {worst:>9}"
            )

        lines += [
            "  " + "─" * (W - 2),
            f"  {'TOTAL':<6}  {sum(buy_count.values()):>4}  "
            f"{sum(sell_count.values()):>5}  "
            f"{total_wins:>4}  {total_losses:>4}  "
            f"{total_pnl:>+10.2f}",
            "═" * W,
        ]
        return "\n".join(lines)

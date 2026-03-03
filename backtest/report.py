"""
backtest/report.py — Performance statistics and trade-log printer.

Metrics computed:
  - Total P&L ($)
  - Win rate (%)
  - Average win / average loss ($)
  - Profit factor (gross wins / gross losses)
  - Sharpe ratio (annualised, trade-return series)
  - Max drawdown ($) from the equity curve
  - Exit reason breakdown
  - Per-trade log
"""

from __future__ import annotations

import math
from backtest.engine import BacktestResult, Trade


# ── Stat helpers ──────────────────────────────────────────────────────────────

def _sharpe(returns: list[float], risk_free: float = 0.0) -> float:
    """Annualised Sharpe ratio from a list of per-trade return fractions."""
    if len(returns) < 2:
        return 0.0
    n    = len(returns)
    mean = sum(returns) / n
    var  = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std  = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 0.0
    # Annualise using sqrt(252) — standard assumption for daily returns
    return (mean - risk_free) / std * math.sqrt(252)


def _max_drawdown(equity_curve: list[float]) -> float:
    """Maximum peak-to-trough drawdown in absolute dollar terms."""
    peak   = float("-inf")
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    return max_dd


# ── Per-symbol report ─────────────────────────────────────────────────────────

def print_report(symbol: str, result: BacktestResult) -> dict:
    """
    Print a formatted report for one symbol and return a stats dict.
    Returns {} if no trades were generated.
    """
    trades = result.trades
    n      = len(trades)

    print(f"\n{'=' * 62}")
    print(f"  BACKTEST REPORT — {symbol}  ({result.n_bars} daily bars, 1 year)")
    print(f"{'=' * 62}")

    if n == 0:
        print("  No trades generated — insufficient signal or data.")
        print(f"{'=' * 62}")
        return {}

    wins   = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    total_pnl     = sum(t.pnl for t in trades)
    win_rate      = len(wins) / n * 100
    avg_win       = sum(t.pnl for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss      = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
    gross_wins    = sum(t.pnl for t in wins)
    gross_losses  = abs(sum(t.pnl for t in losses))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    sharpe        = _sharpe([t.pnl_pct for t in trades])
    max_dd        = _max_drawdown(result.equity_curve)
    avg_hold      = sum(t.hold_days for t in trades) / n

    exit_counts: dict[str, int] = {}
    for t in trades:
        exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1

    print(f"  Total trades    : {n}")
    print(f"  Win / Loss      : {len(wins)}W  {len(losses)}L  ({win_rate:.1f}% win rate)")
    print(f"  Total P&L       : ${total_pnl:>+,.2f}")
    print(f"  Avg win         : ${avg_win:>+,.2f}")
    print(f"  Avg loss        : ${avg_loss:>+,.2f}")
    print(f"  Profit factor   : {profit_factor:.2f}")
    print(f"  Sharpe ratio    : {sharpe:.2f}  (annualised)")
    print(f"  Max drawdown    : ${max_dd:>,.2f}")
    print(f"  Avg hold (days) : {avg_hold:.1f}")
    print(f"  Exit breakdown  : {exit_counts}")
    print(f"{'─' * 62}")

    # Trade log
    hdr = f"  {'Entry date':<12} {'Exit date':<12} {'Entry':>8} {'Exit':>8} {'P&L':>10}  Reason"
    print(hdr)
    print(f"  {'─' * 58}")
    for t in trades:
        entry_d = t.entry_date.strftime("%Y-%m-%d") if t.entry_date else "?"
        exit_d  = t.exit_date.strftime("%Y-%m-%d")  if t.exit_date  else "?"
        print(
            f"  {entry_d:<12} {exit_d:<12} "
            f"{t.entry_price:>8.4f} {t.exit_price:>8.4f} "
            f"${t.pnl:>+9.2f}  {t.exit_reason}"
        )

    print(f"{'=' * 62}")

    return {
        "symbol":         symbol,
        "n_trades":       n,
        "win_rate_pct":   round(win_rate, 2),
        "total_pnl":      round(total_pnl, 2),
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "profit_factor":  round(profit_factor, 4),
        "sharpe":         round(sharpe, 4),
        "max_drawdown":   round(max_dd, 2),
        "avg_hold_days":  round(avg_hold, 1),
        "exit_breakdown": exit_counts,
    }


# ── Cross-symbol summary ──────────────────────────────────────────────────────

def print_summary(all_stats: dict[str, dict]) -> None:
    """Print a combined one-line-per-symbol summary table."""
    if not all_stats:
        return

    total_pnl = sum(r.get("total_pnl", 0.0) for r in all_stats.values())

    print(f"\n{'=' * 72}")
    print(f"  COMBINED SUMMARY")
    print(f"{'─' * 72}")
    print(f"  {'Symbol':<8} {'Trades':>6} {'Win%':>6} {'Total P&L':>12} "
          f"{'Profit F':>9} {'Sharpe':>7} {'MaxDD':>10}")
    print(f"  {'─' * 66}")

    for sym, r in all_stats.items():
        pf_str = f"{r.get('profit_factor', 0):.2f}" if r.get("profit_factor") != float("inf") else "  inf"
        print(
            f"  {sym:<8} "
            f"{r.get('n_trades', 0):>6} "
            f"{r.get('win_rate_pct', 0):>5.1f}% "
            f"${r.get('total_pnl', 0):>+11,.2f} "
            f"{pf_str:>9} "
            f"{r.get('sharpe', 0):>7.2f} "
            f"${r.get('max_drawdown', 0):>9,.2f}"
        )

    print(f"  {'─' * 66}")
    print(f"  {'TOTAL':<8} {'':>6} {'':>6} ${total_pnl:>+11,.2f}")
    print(f"{'=' * 72}\n")

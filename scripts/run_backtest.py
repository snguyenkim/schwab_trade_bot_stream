#!/usr/bin/env python3
"""
scripts/run_backtest.py — Run a strategy backtest against Schwab historical data.

Modes (--mode):
  daily     (default) 1 year  of daily bars    — ~252 candles/symbol
  intraday            10 days of 1-minute bars — ~3,900 candles/symbol

Custom date range (--start / --end, format YYYY-MM-DD):
  Overrides the default period window. Mode still controls bar frequency.
  Schwab limits: minute bars are only available for the past ~30 days.

Usage examples:
    # Standard modes
    python scripts/run_backtest.py
    python scripts/run_backtest.py --strategy Scalper_EMA2 --mode daily
    python scripts/run_backtest.py --strategy Scalper_EMA3 --mode intraday

    # Custom date range — daily bars
    python scripts/run_backtest.py --start 2025-01-01 --end 2025-06-30
    python scripts/run_backtest.py --strategy Scalper_EMA3 --start 2025-06-01 --end 2025-12-31

    # Custom date range — intraday (1-minute) bars
    python scripts/run_backtest.py --mode intraday --start 2026-02-17 --end 2026-02-28
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.logger import setup_logger
from auth.schwab_auth import get_client
from config.settings_loader import load_settings
from strategy.ema_crossover import EMACrossoverStrategy
from strategy.ema3_crossover import EMA3CrossoverStrategy
from backtest.engine import BacktestEngine, MODES
from backtest.report import print_report, print_summary

STRATEGY_CLASSES = {
    "Scalper_EMA2": EMACrossoverStrategy,
    "Scalper_EMA3": EMA3CrossoverStrategy,
}

_DATE_FMT = "%Y-%m-%d"


def _validate_date(s: str) -> str:
    try:
        datetime.strptime(s, _DATE_FMT)
        return s
    except ValueError:
        raise argparse.ArgumentTypeError(f"Date must be YYYY-MM-DD, got '{s}'")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest a strategy against Schwab price history."
    )
    parser.add_argument(
        "--strategy", default=None,
        help="Scalper_EMA2 or Scalper_EMA3. Defaults to settings.json.",
    )
    parser.add_argument(
        "--mode", default="daily", choices=list(MODES),
        help=(
            "'daily' = 1 year of daily bars (default); "
            "'intraday' = 10 days of 1-minute bars."
        ),
    )
    parser.add_argument(
        "--start", default=None, type=_validate_date, metavar="YYYY-MM-DD",
        help="Backtest start date. Overrides the default period window.",
    )
    parser.add_argument(
        "--end", default=None, type=_validate_date, metavar="YYYY-MM-DD",
        help="Backtest end date. Overrides the default period window.",
    )
    args = parser.parse_args()

    # Validate: both --start and --end must be given together
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must both be provided, or neither.")

    if args.start and args.end and args.start >= args.end:
        parser.error(f"--start ({args.start}) must be before --end ({args.end}).")

    # ── Settings ────────────────────────────────────────────────────────────
    settings      = load_settings("settings.json")
    strategy_name = args.strategy or settings.global_settings.strategy

    if strategy_name not in STRATEGY_CLASSES:
        print(f"ERROR: Unknown strategy '{strategy_name}'. "
              f"Available: {list(STRATEGY_CLASSES.keys())}")
        sys.exit(1)

    log_tag = f"backtest_{strategy_name}_{args.mode}"
    if args.start:
        log_tag += f"_{args.start}_{args.end}"
    setup_logger(log_dir="logs", strategy_name=log_tag)

    # ── Auth ────────────────────────────────────────────────────────────────
    print(f"\nAuthenticating with Schwab API...")
    try:
        client, _ = get_client()
    except RuntimeError as exc:
        print(f"Auth failed: {exc}")
        sys.exit(1)

    # ── Strategy ────────────────────────────────────────────────────────────
    strategy = STRATEGY_CLASSES[strategy_name](
        strategy_name=strategy_name, settings_path="settings.json"
    )

    spans = "/".join(
        str(getattr(strategy, s))
        for s in ("short_span", "medium_span", "long_span")
        if hasattr(strategy, s)
    )

    if args.start:
        data_desc = f"{args.start} → {args.end}  ({MODES[args.mode]['frequency_type']} bars)"
    else:
        data_desc = MODES[args.mode]["label"]

    print(f"\n  Strategy : {strategy_name}  (EMA {spans})")
    print(f"  Symbols  : {list(strategy.symbols.keys())}")
    print(f"  Data     : {data_desc}")
    print(f"  Risk     : stop={settings.global_settings.stop_loss_pct:.1%}  "
          f"target={settings.global_settings.profit_target_pct:.1%}")
    print()

    # ── Run backtest ────────────────────────────────────────────────────────
    engine = BacktestEngine(
        client, strategy,
        settings_path="settings.json",
        mode=args.mode,
        start_date=args.start,
        end_date=args.end,
    )
    results = engine.run()

    # ── Report ──────────────────────────────────────────────────────────────
    all_stats: dict[str, dict] = {}
    for symbol, result in results.items():
        stats = print_report(symbol, result)
        if stats:
            all_stats[symbol] = stats

    if len(all_stats) > 1:
        print_summary(all_stats)


if __name__ == "__main__":
    main()

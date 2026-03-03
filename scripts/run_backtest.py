#!/usr/bin/env python3
"""
scripts/run_backtest.py — Run a strategy backtest against Schwab historical data.

Modes:
  --mode daily     (default) 1 year  of daily bars     — ~252 candles/symbol
  --mode intraday             10 days of 1-minute bars  — ~3,900 candles/symbol

Usage:
    python scripts/run_backtest.py
    python scripts/run_backtest.py --strategy Scalper_EMA2 --mode daily
    python scripts/run_backtest.py --strategy Scalper_EMA3 --mode intraday
"""

import argparse
import sys
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest a strategy against Schwab price history."
    )
    parser.add_argument(
        "--strategy", default=None,
        help=(
            "Strategy to backtest: Scalper_EMA2 or Scalper_EMA3. "
            "Defaults to the strategy set in settings.json."
        ),
    )
    parser.add_argument(
        "--mode", default="daily", choices=list(MODES),
        help=(
            "Data mode: "
            "'daily' = 1 year of daily bars (default); "
            "'intraday' = 10 days of 1-minute bars."
        ),
    )
    args = parser.parse_args()

    # ── Settings ────────────────────────────────────────────────────────────
    settings      = load_settings("settings.json")
    strategy_name = args.strategy or settings.global_settings.strategy

    if strategy_name not in STRATEGY_CLASSES:
        print(
            f"ERROR: Unknown strategy '{strategy_name}'. "
            f"Available: {list(STRATEGY_CLASSES.keys())}"
        )
        sys.exit(1)

    setup_logger(log_dir="logs", strategy_name=f"backtest_{strategy_name}_{args.mode}")

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

    print(f"\n  Strategy : {strategy_name}  (EMA {spans})")
    print(f"  Symbols  : {list(strategy.symbols.keys())}")
    print(f"  Mode     : {args.mode}  —  {MODES[args.mode]['label']}")
    print(f"  Risk     : stop={settings.global_settings.stop_loss_pct:.1%}  "
          f"target={settings.global_settings.profit_target_pct:.1%}")
    print()

    # ── Run backtest ────────────────────────────────────────────────────────
    engine  = BacktestEngine(client, strategy, settings_path="settings.json", mode=args.mode)
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

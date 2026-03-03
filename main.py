#!/usr/bin/env python3
"""
main.py — Day Trading Bot entry point.

Starts the Scalper_EMA2 agent loop:
  1. Authenticate via CredentialManager (SQLite)
  2. Load strategy + settings
  3. Spin up position monitor in background thread
  4. Loop: fetch prices → evaluate signal → risk check → execute order
"""

import os
import signal
import sys
import time
import threading
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

PID_FILE = Path("bot.pid")

# ── Project imports ────────────────────────────────────────────────────────────
from utils.logger import setup_logger
from utils.trade_logger import log_kill_switch
from auth.schwab_auth import get_client
from config.settings_loader import load_settings
from data.market_data import MarketData
from strategy.ema_crossover import EMACrossoverStrategy
from strategy.ema3_crossover import EMA3CrossoverStrategy
from risk.risk_manager import RiskManager

STRATEGY_CLASSES = {
    "Scalper_EMA2": EMACrossoverStrategy,
    "Scalper_EMA3": EMA3CrossoverStrategy,
}
from execution.order_manager import OrderManager
from portfolio.position_monitor import PositionMonitor

ET = ZoneInfo("America/New_York")
MARKET_OPEN = dt_time(10, 0)
MARKET_CLOSE = dt_time(16, 0)


def market_is_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:          # Saturday / Sunday
        return False
    return MARKET_OPEN <= now.time() < MARKET_CLOSE


def main():
    # ── Settings — load first so strategy name is available for logging ─────────
    settings = load_settings("settings.json")
    strategy_name = settings.global_settings.strategy

    # ── Logging ────────────────────────────────────────────────────────────────
    setup_logger(log_dir="logs", strategy_name=strategy_name)
    logger.info("[MAIN] Starting {} bot", strategy_name)

    # ── PID file — lets force_flatten.py find this process ─────────────────────
    PID_FILE.write_text(str(os.getpid()))
    logger.info("[MAIN] PID {} written to {}", os.getpid(), PID_FILE)

    # ── Auth ───────────────────────────────────────────────────────────────────
    try:
        client, cm = get_client()
    except RuntimeError as exc:
        logger.critical("[MAIN] Auth failed: {}", exc)
        sys.exit(1)

    # ── Account hash ───────────────────────────────────────────────────────────
    account_numbers = client.get_account_numbers()
    if not account_numbers.accounts:
        logger.critical("[MAIN] No accounts found on this credential set")
        sys.exit(1)
    account_hash = account_numbers.accounts[0].hash_value
    logger.info("[MAIN] Trading account hash: {}", account_hash)

    # ── Strategy — resolve class from name in settings.json ────────────────────
    if strategy_name not in STRATEGY_CLASSES:
        logger.critical(
            "[MAIN] Unknown strategy '{}'. Available: {}",
            strategy_name, list(STRATEGY_CLASSES.keys()),
        )
        sys.exit(1)

    strategy = STRATEGY_CLASSES[strategy_name](
        strategy_name=strategy_name, settings_path="settings.json"
    )
    logger.info(
        "[MAIN] Strategy: {} | spans: {} | data: {} {}(s) @ {} {} | symbols: {}",
        strategy_name,
        "/".join(
            str(getattr(strategy, s))
            for s in ("short_span", "medium_span", "long_span")
            if hasattr(strategy, s)
        ),
        strategy.period, strategy.period_type,
        strategy.frequency, strategy.frequency_type,
        list(strategy.symbols.keys()),
    )

    # ── Sub-components ─────────────────────────────────────────────────────────
    market_data = MarketData(client)
    risk_manager = RiskManager(settings_path="settings.json")
    order_manager = OrderManager(client, account_hash)
    monitor = PositionMonitor(client, order_manager, settings_path="settings.json")

    # ── SIGUSR1 handler — force EOD flatten from outside the process ───────────
    def _handle_sigusr1(signum, frame):
        logger.critical("[MAIN] SIGUSR1 received — forcing EOD flatten")
        monitor._flatten_all("manual force-flatten via SIGUSR1")

    signal.signal(signal.SIGUSR1, _handle_sigusr1)
    logger.info("[MAIN] SIGUSR1 handler registered (send to PID {} to force-flatten)", os.getpid())

    # ── Position monitor — background daemon thread ────────────────────────────
    monitor_thread = threading.Thread(
        target=monitor.run,
        kwargs={"poll_interval_sec": 5},
        daemon=True,
        name="PositionMonitor",
    )
    monitor_thread.start()
    logger.info("[MAIN] Position monitor started (daemon thread)")

    # ── Main agent loop ────────────────────────────────────────────────────────
    tick_interval = strategy.frequency * 60   # convert minutes → seconds

    logger.info("[MAIN] Waiting for market to open...")
    while not market_is_open():
        time.sleep(30)

    logger.info("[MAIN] Market open — entering signal loop")

    try:
        while market_is_open():
            for symbol, size in strategy.symbols.items():
                try:
                    prices = market_data.get_price_series(
                        symbol,
                        period_type=strategy.period_type,
                        period=strategy.period,
                        frequency_type=strategy.frequency_type,
                        frequency=strategy.frequency,
                    )

                    if prices.empty:
                        continue

                    signal = strategy.evaluate(prices, symbol=symbol)

                    if signal == "BUY":
                        if risk_manager.approve(symbol, "BUY", size):
                            fill = order_manager.execute(symbol, "BUY", quantity=size)
                            if fill > 0:
                                monitor.add_position(symbol, size, fill)
                                risk_manager.record_fill(symbol, "BUY", size, fill)

                    elif signal == "SELL":
                        if risk_manager.approve(symbol, "SELL", size):
                            fill = order_manager.execute(symbol, "SELL", quantity=size)
                            if fill > 0:
                                entry = order_manager.entry_price(symbol)
                                risk_manager.record_fill(
                                    symbol, "SELL", size, fill, entry
                                )
                                monitor.remove_position(symbol)

                except Exception as exc:
                    logger.error(
                        "[MAIN] Error processing {symbol}: {exc}",
                        symbol=symbol, exc=exc,
                    )

            # Daily loss kill switch check
            if risk_manager.daily_pnl <= -abs(settings.global_settings.max_daily_loss_usd):
                log_kill_switch("daily loss limit breached", risk_manager.daily_pnl)
                logger.critical("[MAIN] Kill switch activated — halting bot")
                break

            time.sleep(tick_interval)

    except KeyboardInterrupt:
        logger.info("[MAIN] Interrupted by user — initiating EOD cleanup")

    # ── EOD cleanup ────────────────────────────────────────────────────────────
    PID_FILE.unlink(missing_ok=True)
    logger.info(
        "[MAIN] Session complete | realized_pnl={pnl:+.2f}",
        pnl=risk_manager.daily_pnl,
    )


if __name__ == "__main__":
    main()

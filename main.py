#!/usr/bin/env python3
"""
main.py — Day Trading Bot entry point (streaming edition).

Flow:
  1. Authenticate via CredentialManager (SQLite)
  2. Load strategy + settings
  3. Fetch user preferences → get StreamerInfo
  4. Spin up PositionMonitor in a background daemon thread
  5. Connect SchwabStreamer WebSocket
  6. StreamFeed seeds historical buffers, then drives strategy evaluation
     on every new CHART_EQUITY bar — no more polling loop
"""

import asyncio
import os
import signal
import sys
import threading
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

PID_FILE = Path("bot.pid")

# ── Project imports ─────────────────────────────────────────────────────────
from utils.logger import setup_logger
from utils.trade_logger import log_kill_switch
from utils.trading_log import TradingLog
from auth.schwab_auth import get_client
from config.settings_loader import load_settings
from data.market_data import MarketData
from data.stream_feed import StreamFeed, SSLSchwabStreamer
from strategy.ema_crossover import EMACrossoverStrategy
from strategy.ema3_crossover import EMA3CrossoverStrategy
from risk.risk_manager import RiskManager
from execution.order_manager import OrderManager
from portfolio.position_monitor import PositionMonitor

from schwab.streaming import QOSLevel

STRATEGY_CLASSES = {
    "Scalper_EMA2": EMACrossoverStrategy,
    "Scalper_EMA3": EMA3CrossoverStrategy,
}

ET = ZoneInfo("America/New_York")
MARKET_OPEN  = dt_time(10, 0)
MARKET_CLOSE = dt_time(16, 0)
EOD_FLATTEN_TIME = dt_time(15, 30)


def market_is_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() < MARKET_CLOSE


async def run_bot() -> None:
    # ── Settings ───────────────────────────────────────────────────────────────
    settings = load_settings("settings.json")
    strategy_name = settings.global_settings.strategy

    # ── Logging ────────────────────────────────────────────────────────────────
    setup_logger(log_dir="logs", strategy_name=strategy_name)
    logger.info("[MAIN] Starting {} bot (streaming mode)", strategy_name)

    # ── PID file ───────────────────────────────────────────────────────────────
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

    # ── Strategy ───────────────────────────────────────────────────────────────
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
    market_data   = MarketData(client)
    risk_manager  = RiskManager(settings_path="settings.json")
    trading_log   = TradingLog(strategy_name=strategy_name, log_dir="logs")
    order_manager = OrderManager(client, account_hash, trading_log=trading_log)
    monitor = PositionMonitor(client, order_manager, settings_path="settings.json")

    # ── Streamer setup ─────────────────────────────────────────────────────────
    logger.info("[MAIN] Fetching user preferences for streaming...")
    user_prefs = client.get_user_preferences()
    if not user_prefs.streamer_info:
        logger.critical("[MAIN] No streamer_info returned from user preferences")
        sys.exit(1)

    streamer = SSLSchwabStreamer(client.auth, user_prefs.streamer_info[0])

    # ── StreamFeed — wires streamer → strategy → orders ────────────────────────
    feed = StreamFeed(
        streamer=streamer,
        market_data=market_data,
        strategy=strategy,
        risk_manager=risk_manager,
        order_manager=order_manager,
        monitor=monitor,
        settings=settings,
    )

    # Share the live price cache with PositionMonitor so it reads
    # streamed prices instead of polling the REST endpoint
    monitor._price_cache = feed.latest_prices

    # ── SIGUSR1 handler — force EOD flatten from outside the process ───────────
    def _handle_sigusr1(signum, frame):
        logger.critical("[MAIN] SIGUSR1 received — forcing EOD flatten")
        monitor._flatten_all("manual force-flatten via SIGUSR1")

    signal.signal(signal.SIGUSR1, _handle_sigusr1)
    logger.info("[MAIN] SIGUSR1 handler registered (PID {})", os.getpid())

    # ── PositionMonitor — background daemon thread ─────────────────────────────
    monitor_thread = threading.Thread(
        target=monitor.run,
        kwargs={"poll_interval_sec": 5},
        daemon=True,
        name="PositionMonitor",
    )
    monitor_thread.start()
    logger.info("[MAIN] Position monitor started (daemon thread)")

    # ── Wait for market open ───────────────────────────────────────────────────
    logger.info("[MAIN] Waiting for market to open...")
    while not market_is_open():
        await asyncio.sleep(30)

    logger.info("[MAIN] Market open — connecting streamer")

    # ── Connect WebSocket ──────────────────────────────────────────────────────
    await streamer.connect()
    await streamer.set_qos(QOSLevel.FAST)
    logger.info("[MAIN] Streamer connected")

    # ── Run until market close, kill switch, or Ctrl+C ────────────────────────
    try:
        await feed.run()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("[MAIN] Disconnecting streamer...")
        await streamer.disconnect()

    # ── EOD cleanup ────────────────────────────────────────────────────────────
    PID_FILE.unlink(missing_ok=True)
    logger.info(
        "[MAIN] Session complete | realized_pnl={pnl:+.2f}",
        pnl=risk_manager.daily_pnl,
    )
    trading_log.print_summary()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("[MAIN] Interrupted by user — shutting down")


if __name__ == "__main__":
    main()

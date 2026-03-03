"""
stream_feed.py — Event-driven market data via Schwab WebSocket streaming.

Replaces the polling loop in main.py:
  - CHART_EQUITY     → new 1-min OHLCV bar close → evaluate EMA strategy → execute order
  - LEVELONE_EQUITIES → live last/mark price     → update shared price cache for position monitor

Usage:
    feed = StreamFeed(streamer, market_data, strategy, risk_manager, order_manager, monitor, settings)
    asyncio.run(feed.run())
"""

import asyncio
import ssl
from collections import deque
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import certifi
import pandas as pd
import websockets
from loguru import logger

from schwab.streaming import SchwabStreamer, ChartEquityFields, LevelOneEquityFields
from utils.trade_logger import log_kill_switch

ET = ZoneInfo("America/New_York")
EOD_FLATTEN_TIME = dt_time(15, 30)


class SSLSchwabStreamer(SchwabStreamer):
    """
    SchwabStreamer with a certifi SSL context.

    The base library calls ``websockets.connect(url)`` without an SSL context,
    which fails on macOS Python 3.14 when the server chain contains a
    self-signed intermediate certificate.  This subclass overrides ``connect()``
    to inject ``ssl=certifi_ctx`` so the handshake succeeds.
    """

    async def connect(self) -> None:
        if self.is_connected:
            return
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        self.websocket = await websockets.connect(
            self.streamer_info.streamer_socket_url, ssl=ssl_ctx
        )
        await self._login()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._receive_task = asyncio.create_task(self._receive_loop())
        self.is_connected = True


class StreamFeed:
    """
    Seeds historical price buffers then drives strategy evaluation from
    streamed CHART_EQUITY bars. Maintains a live price cache from
    LEVELONE_EQUITIES ticks, shared with PositionMonitor.
    """

    def __init__(
        self,
        streamer: SchwabStreamer,
        market_data,
        strategy,
        risk_manager,
        order_manager,
        monitor,
        settings,
        max_bars: int = 500,
    ):
        self.streamer = streamer
        self.market_data = market_data
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.order_manager = order_manager
        self.monitor = monitor
        self.settings = settings
        self._running = True

        self._price_buffers: dict[str, deque] = {
            sym: deque(maxlen=max_bars) for sym in strategy.symbols
        }

        # Shared with PositionMonitor — updated on every Level 1 tick
        self.latest_prices: dict[str, float] = {}

    # ── Buffer seeding ─────────────────────────────────────────────────────────

    def _seed_buffers(self) -> None:
        """Fetch historical candles to warm up EMA buffers before streaming starts."""
        for symbol in self.strategy.symbols:
            try:
                series = self.market_data.get_price_series(
                    symbol,
                    period_type=self.strategy.period_type,
                    period=self.strategy.period,
                    frequency_type=self.strategy.frequency_type,
                    frequency=self.strategy.frequency,
                )
                if not series.empty:
                    self._price_buffers[symbol].extend(series.tolist())
                    logger.info(
                        "[STREAM] Seeded {} bars for {}", len(series), symbol
                    )
                else:
                    logger.warning("[STREAM] No seed data returned for {}", symbol)
            except Exception as exc:
                logger.error("[STREAM] Seed failed for {}: {}", symbol, exc)

    # ── Streaming callbacks (synchronous — called by SchwabStreamer) ───────────

    def _on_chart_bar(self, service: str, content: list) -> None:
        """Handle CHART_EQUITY data: append new bar close, evaluate strategy."""
        close_field = str(ChartEquityFields.CLOSE_PRICE.value)

        for item in content:
            symbol = item.get("key", "")
            close_raw = item.get(close_field)
            if not symbol or close_raw is None:
                continue

            close = float(close_raw)
            self._price_buffers[symbol].append(close)
            self.latest_prices[symbol] = close

            bar_time = item.get(str(ChartEquityFields.CHART_TIME.value))
            ts = (
                datetime.fromtimestamp(bar_time / 1000, tz=ET).strftime("%H:%M")
                if bar_time else "?"
            )
            logger.debug("[STREAM] BAR {} @ {} close={:.4f}", symbol, ts, close)

            # EOD guard — stop processing new signals after flatten time
            if datetime.now(ET).time() >= EOD_FLATTEN_TIME:
                logger.info("[STREAM] Past EOD flatten time, ignoring bar for {}", symbol)
                continue

            self._process_signal(symbol)

    def _on_level1_quote(self, service: str, content: list) -> None:
        """Handle LEVELONE_EQUITIES ticks: update live price cache only."""
        last_field = str(LevelOneEquityFields.LAST_PRICE.value)
        mark_field = str(LevelOneEquityFields.MARK.value)
        ask_field = str(LevelOneEquityFields.ASK_PRICE.value)

        for item in content:
            symbol = item.get("key", "")
            price = (
                item.get(last_field)
                or item.get(mark_field)
                or item.get(ask_field)
            )
            if symbol and price is not None:
                self.latest_prices[symbol] = float(price)

    # ── Signal processing ──────────────────────────────────────────────────────

    def _process_signal(self, symbol: str) -> None:
        """Evaluate strategy on current buffer and execute order if signalled."""
        buf = self._price_buffers.get(symbol)
        if not buf:
            return

        prices = pd.Series(list(buf), dtype=float)
        size = self.strategy.symbols[symbol]

        try:
            signal = self.strategy.evaluate(prices, symbol=symbol)
        except Exception as exc:
            logger.error("[STREAM] Strategy error for {}: {}", symbol, exc)
            return

        if signal == "BUY":
            if self.risk_manager.approve(symbol, "BUY", size):
                fill = self.order_manager.execute(symbol, "BUY", quantity=size)
                if fill > 0:
                    self.monitor.add_position(symbol, size, fill)
                    self.risk_manager.record_fill(symbol, "BUY", size, fill)

        elif signal == "SELL":
            if self.risk_manager.approve(symbol, "SELL", size):
                fill = self.order_manager.execute(symbol, "SELL", quantity=size)
                if fill > 0:
                    entry = self.order_manager.entry_price(symbol)
                    self.risk_manager.record_fill(symbol, "SELL", size, fill, entry)
                    self.monitor.remove_position(symbol)

        # Daily kill switch
        max_loss = abs(self.settings.global_settings.max_daily_loss_usd)
        if self.risk_manager.daily_pnl <= -max_loss:
            log_kill_switch("daily loss limit breached", self.risk_manager.daily_pnl)
            logger.critical("[STREAM] Kill switch activated — stopping stream")
            self._running = False

    # ── Main async entry point ─────────────────────────────────────────────────

    async def run(self) -> None:
        """
        1. Seed historical buffers via REST.
        2. Subscribe to CHART_EQUITY (bar-close signals) and LEVELONE_EQUITIES (live price).
        3. Yield to the event loop until kill switch or KeyboardInterrupt.
        """
        logger.info("[STREAM] Seeding historical price buffers...")
        self._seed_buffers()

        symbols = list(self.strategy.symbols.keys())

        logger.info("[STREAM] Subscribing to CHART_EQUITY for {}", symbols)
        await self.streamer.subscribe_chart_equity(
            symbols=symbols,
            callback=self._on_chart_bar,
        )

        logger.info("[STREAM] Subscribing to LEVELONE_EQUITIES for {}", symbols)
        await self.streamer.subscribe_level_one_equity(
            symbols=symbols,
            fields=[
                LevelOneEquityFields.LAST_PRICE.value,
                LevelOneEquityFields.MARK.value,
                LevelOneEquityFields.ASK_PRICE.value,
            ],
            callback=self._on_level1_quote,
        )

        logger.info("[STREAM] Active — waiting for market data (Ctrl+C to stop)")

        # Idle loop — callbacks drive all work; we just keep the event loop alive
        while self._running:
            await asyncio.sleep(1)

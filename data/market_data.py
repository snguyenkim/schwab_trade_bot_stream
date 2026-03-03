from collections import deque
from datetime import datetime
import pandas as pd
from loguru import logger


class MarketData:
    """
    Fetches OHLCV price history from the Schwab API and maintains
    a rolling in-memory buffer of closing prices per symbol.
    """

    def __init__(self, client, max_bars: int = 500):
        self.client = client
        self.max_bars = max_bars
        # symbol → deque of closing prices
        self._price_buffers: dict[str, deque] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_price_series(
        self,
        symbol: str,
        period_type: str = "day",
        period: int = 2,
        frequency_type: str = "minute",
        frequency: int = 1,
    ) -> pd.Series:
        """
        Fetch price history from Schwab and return a pd.Series of closing prices
        (oldest first, most recent last). Also updates the in-memory buffer.
        """
        try:
            raw = self.client.get_price_history(
                symbol=symbol,
                period_type=period_type,
                period=period,
                frequency_type=frequency_type,
                frequency=frequency,
            )
        except Exception as exc:
            logger.error("[DATA] get_price_history failed | {symbol} | {exc}",
                         symbol=symbol, exc=exc)
            raise

        candles = raw.get("candles", [])
        if not candles:
            logger.warning("[DATA] No candles returned for {symbol}", symbol=symbol)
            return pd.Series(dtype=float)

        closes = [c["close"] for c in candles]
        series = pd.Series(closes, dtype=float)

        # Update rolling buffer
        buf = self._price_buffers.setdefault(symbol, deque(maxlen=self.max_bars))
        buf.extend(closes)

        logger.debug(
            "[DATA] {symbol} | {n} candles | last_close={last:.4f}",
            symbol=symbol, n=len(closes), last=closes[-1],
        )
        return series

    def get_latest_price(self, symbol: str) -> float:
        """Fetch the latest trade price for a single symbol via quotes endpoint."""
        try:
            response = self.client.get_quotes(symbol)
            obj = response.root.get(symbol)
            if obj is None:
                raise ValueError(f"No quote data returned for {symbol}")
            inner = obj.root
            quote = getattr(inner, "quote", None)
            if quote is None:
                raise ValueError(f"Quote field missing for {symbol}")
            price = (getattr(quote, "last_price", None)
                     or getattr(quote, "mark", None)
                     or getattr(quote, "ask_price", None))
            if price is None:
                raise ValueError(f"All price fields None for {symbol}")
            return float(price)
        except Exception as exc:
            logger.error("[DATA] get_latest_price failed | {symbol} | {exc}",
                         symbol=symbol, exc=exc)
            raise

    def get_buffer(self, symbol: str) -> pd.Series:
        """Return the in-memory rolling buffer as a pd.Series (may be empty)."""
        buf = self._price_buffers.get(symbol, deque())
        return pd.Series(list(buf), dtype=float)

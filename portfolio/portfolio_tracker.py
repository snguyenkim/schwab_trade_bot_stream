import sys
from pathlib import Path
from datetime import datetime
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from portfolio.position import Position
from utils.trade_logger import log_position_opened, log_position_closed


class PortfolioTracker:
    """
    Tracks open positions and realized P&L for the session.
    Single source of truth for position state across the bot.
    """

    def __init__(self):
        self.positions: dict[str, Position] = {}
        self.realized_pnl: float = 0.0
        self.trades: list[dict] = []

    # ── Position management ────────────────────────────────────────────────────

    def open_position(self, symbol: str, size: int, entry_price: float) -> None:
        if symbol in self.positions:
            logger.warning(
                "[PORTFOLIO] Already tracking {symbol} — ignoring duplicate open",
                symbol=symbol,
            )
            return
        self.positions[symbol] = Position(
            symbol=symbol, size=size, entry_price=entry_price
        )
        log_position_opened(symbol, "LONG", size, entry_price)

    def close_position(self, symbol: str, exit_price: float) -> float:
        """
        Mark a position as closed at exit_price.
        Returns realized P&L for this trade.
        """
        pos = self.positions.pop(symbol, None)
        if pos is None:
            logger.warning(
                "[PORTFOLIO] close_position called but no position for {symbol}",
                symbol=symbol,
            )
            return 0.0

        pnl = pos.unrealized_pnl(exit_price)
        self.realized_pnl += pnl

        self.trades.append({
            "symbol": symbol,
            "size": pos.size,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "entry_time": pos.entry_time,
            "exit_time": datetime.now(),
        })

        log_position_closed(symbol, pos.size, pos.entry_price, exit_price)
        return pnl

    # ── Queries ────────────────────────────────────────────────────────────────

    def is_open(self, symbol: str) -> bool:
        return symbol in self.positions

    def snapshot(self, price_fn=None) -> list[dict]:
        """
        Return a list of position dicts with optional live P&L.
        price_fn: callable(symbol) → float, or None to skip live pricing.
        """
        result = []
        for symbol, pos in self.positions.items():
            entry = {
                "symbol": symbol,
                "size": pos.size,
                "entry_price": pos.entry_price,
                "entry_time": pos.entry_time.isoformat(),
            }
            if price_fn:
                try:
                    price = price_fn(symbol)
                    entry["current_price"] = price
                    entry["unrealized_pnl"] = round(pos.unrealized_pnl(price), 2)
                    entry["pnl_pct"] = round(pos.unrealized_pnl_pct(price) * 100, 2)
                    entry["peak_price"] = round(pos.peak_price, 4)
                except Exception:
                    pass
            result.append(entry)
        return result

    def print_summary(self) -> None:
        logger.info(
            "[PORTFOLIO] Open={open} | Realized P&L={pnl:+.2f} | Trades={trades}",
            open=len(self.positions),
            pnl=self.realized_pnl,
            trades=len(self.trades),
        )

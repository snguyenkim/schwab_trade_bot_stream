import sys
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from loguru import logger

sys.path.insert(0, str(Path := __import__("pathlib").Path(__file__).parent.parent))
from config.settings_loader import load_settings
from portfolio.position import Position
from utils.trade_logger import (
    log_bar, log_position_closed, log_kill_switch, log_risk_block,
)

ET = ZoneInfo("America/New_York")
EOD_FLATTEN_TIME = dt_time(15, 30)


class PositionMonitor:
    """
    Background monitor for all open positions.
    Checks stop-loss, profit-target, trailing-stop, time-stop,
    EOD flatten, and the daily loss kill switch.
    """

    def __init__(self, client, order_manager, settings_path: str = "settings.json"):
        cfg = load_settings(settings_path)
        g = cfg.global_settings

        self.client = client
        self.order_manager = order_manager
        self.profit_target_pct: float = g.profit_target_pct
        self.stop_loss_pct: float = g.stop_loss_pct
        self.trailing_stop_pct: float = g.stop_loss_pct
        self.max_daily_loss: float = g.max_daily_loss_usd
        self.max_hold_minutes: int = g.max_hold_minutes

        self.positions: dict[str, Position] = {}
        self.realized_pnl: float = 0.0

    # ── Position registry ──────────────────────────────────────────────────────

    def add_position(self, symbol: str, size: int, entry_price: float) -> None:
        self.positions[symbol] = Position(
            symbol=symbol, size=size, entry_price=entry_price
        )
        logger.info(
            "[MONITOR] TRACKING | {symbol} size={size} entry={entry:.4f}",
            symbol=symbol, size=size, entry=entry_price,
        )

    def remove_position(self, symbol: str) -> None:
        self.positions.pop(symbol, None)

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self, poll_interval_sec: int = 5) -> None:
        """
        Blocking loop — run in a daemon thread.
        Polls positions every poll_interval_sec seconds.
        """
        logger.info("[MONITOR] Started — poll_interval={}s", poll_interval_sec)
        while True:
            now_et = datetime.now(ET)

            if now_et.time() >= EOD_FLATTEN_TIME:
                self._flatten_all("EOD flatten — market close approaching")
                logger.info("[MONITOR] EOD complete. Stopping monitor.")
                break

            self._check_all_positions(now_et)

            if self.realized_pnl <= -abs(self.max_daily_loss):
                self._flatten_all("daily loss limit breached")
                log_kill_switch("daily loss limit breached", self.realized_pnl)
                break

            time.sleep(poll_interval_sec)

    # ── Per-position checks ────────────────────────────────────────────────────

    def _check_all_positions(self, now: datetime) -> None:
        for symbol, pos in list(self.positions.items()):
            try:
                current_price = self._get_price(symbol)
            except Exception as exc:
                logger.error(
                    "[MONITOR] Price fetch failed | {symbol} | {exc}",
                    symbol=symbol, exc=exc,
                )
                continue

            pos.update_peak(current_price)
            pnl = pos.unrealized_pnl(current_price)
            pnl_pct = pos.unrealized_pnl_pct(current_price)

            logger.debug(
                "[MONITOR] {symbol} | price={price:.4f} | pnl={pnl:+.2f} ({pct:+.2%}) | peak={peak:.4f}",
                symbol=symbol, price=current_price,
                pnl=pnl, pct=pnl_pct, peak=pos.peak_price,
            )

            reason = self._exit_reason(pos, current_price, now)
            if reason:
                self._close_position(pos, current_price, reason)

    def _exit_reason(
        self, pos: Position, price: float, now: datetime
    ) -> str | None:
        pnl_pct = pos.unrealized_pnl_pct(price)

        entry_time = pos.entry_time
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=ET)
        hold_minutes = (now - entry_time).total_seconds() / 60

        if pnl_pct <= -self.stop_loss_pct:
            return f"stop-loss hit ({pnl_pct:+.2%} <= -{self.stop_loss_pct:.2%})"

        if pnl_pct >= self.profit_target_pct:
            return f"profit target hit ({pnl_pct:+.2%} >= +{self.profit_target_pct:.2%})"

        drawdown_from_peak = (price - pos.peak_price) / pos.peak_price
        if (pos.peak_price > pos.entry_price and
                drawdown_from_peak <= -self.trailing_stop_pct):
            return (
                f"trailing stop hit (peak={pos.peak_price:.4f}, "
                f"drawdown={drawdown_from_peak:+.2%})"
            )

        if hold_minutes >= self.max_hold_minutes:
            return f"time-stop ({hold_minutes:.0f} min >= {self.max_hold_minutes} min)"

        return None

    # ── Execution helpers ──────────────────────────────────────────────────────

    def _close_position(
        self, pos: Position, exit_price: float, reason: str
    ) -> None:
        logger.warning(
            "[MONITOR] EXIT TRIGGERED | {symbol} | reason={reason}",
            symbol=pos.symbol, reason=reason,
        )
        log_risk_block(pos.symbol, reason)
        self.order_manager.execute(pos.symbol, "SELL", quantity=pos.size)
        pnl = pos.unrealized_pnl(exit_price)
        self.realized_pnl += pnl
        log_position_closed(pos.symbol, pos.size, pos.entry_price, exit_price)
        self.remove_position(pos.symbol)

    def _flatten_all(self, reason: str) -> None:
        logger.critical("[MONITOR] FLATTEN ALL | reason={}", reason)
        for symbol, pos in list(self.positions.items()):
            try:
                price = self._get_price(symbol)
            except Exception:
                price = pos.entry_price  # fallback
            self._close_position(pos, price, reason)

    def _get_price(self, symbol: str) -> float:
        quote = self.client.get_quote(symbol)
        # Handle both nested and flat quote shapes
        if symbol in quote:
            inner = quote[symbol]
            price = (inner.get("lastPrice") or inner.get("last")
                     or inner.get("mark") or inner.get("bidPrice"))
        else:
            price = (quote.get("lastPrice") or quote.get("last")
                     or quote.get("mark"))
        return float(price)

    # ── Status ─────────────────────────────────────────────────────────────────

    def snapshot(self) -> list[dict]:
        result = []
        for symbol, pos in self.positions.items():
            try:
                price = self._get_price(symbol)
                result.append({
                    "symbol": symbol,
                    "size": pos.size,
                    "entry_price": pos.entry_price,
                    "current_price": price,
                    "unrealized_pnl": round(pos.unrealized_pnl(price), 2),
                    "pnl_pct": round(pos.unrealized_pnl_pct(price) * 100, 2),
                    "peak_price": round(pos.peak_price, 4),
                })
            except Exception:
                pass
        return result

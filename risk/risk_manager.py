import sys
from pathlib import Path
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings_loader import load_settings
from utils.trade_logger import log_risk_block


class RiskManager:
    """
    Pre-trade risk checks: position limits, daily loss cap, PDT guard.
    All limits are loaded from settings.json global_settings.
    """

    MAX_DAY_TRADES = 3          # PDT rule: max same-day round-trips in 5 days
    PDT_LOOKBACK_DAYS = 5

    def __init__(self, settings_path: str = "settings.json"):
        cfg = load_settings(settings_path).global_settings
        self.max_daily_loss_usd: float = cfg.max_daily_loss_usd
        self.stop_loss_pct: float = cfg.stop_loss_pct
        self.profit_target_pct: float = cfg.profit_target_pct

        self.realized_pnl: float = 0.0
        self._day_trade_log: list = []      # timestamps of completed day trades
        self._open_positions: set[str] = set()  # symbols with open positions

    # ── Main approve gate ──────────────────────────────────────────────────────

    def approve(self, symbol: str, signal: str, quantity: int) -> bool:
        """
        Return True if the signal passes all risk checks.
        Logs and blocks if any check fails.
        """
        # 1. Daily loss kill switch
        if self.realized_pnl <= -abs(self.max_daily_loss_usd):
            log_risk_block(symbol, f"daily loss limit reached ({self.realized_pnl:+.2f})")
            return False

        # 2. PDT guard (< $25k accounts: max 3 day trades per 5 days)
        if signal == "BUY" and self._pdt_limit_reached():
            log_risk_block(symbol, "PDT limit: 3 day-trades in 5 days would be exceeded")
            return False

        # 3. No duplicate long positions
        if signal == "BUY" and symbol in self._open_positions:
            log_risk_block(symbol, "already holding position — skipping BUY")
            return False

        # 4. No selling without an open position
        if signal == "SELL" and symbol not in self._open_positions:
            log_risk_block(symbol, "no open position to SELL")
            return False

        # 5. Quantity sanity
        if quantity <= 0:
            log_risk_block(symbol, f"invalid quantity={quantity}")
            return False

        return True

    # ── State updates ──────────────────────────────────────────────────────────

    def record_fill(self, symbol: str, side: str, qty: int,
                    fill_price: float, entry_price: float = 0.0) -> None:
        """Update internal state after a confirmed fill."""
        if side == "BUY":
            self._open_positions.add(symbol)
        elif side == "SELL":
            self._open_positions.discard(symbol)
            pnl = (fill_price - entry_price) * qty
            self.realized_pnl += pnl
            self._day_trade_log.append(__import__("datetime").datetime.now())
            logger.debug(
                "[RISK] Trade recorded | {symbol} pnl={pnl:+.2f} | daily_pnl={daily:+.2f}",
                symbol=symbol, pnl=pnl, daily=self.realized_pnl,
            )

    def reset_daily(self) -> None:
        """Call at start of each trading day."""
        self.realized_pnl = 0.0
        self._open_positions.clear()
        logger.info("[RISK] Daily state reset")

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _pdt_limit_reached(self) -> bool:
        import datetime
        cutoff = datetime.datetime.now() - datetime.timedelta(days=self.PDT_LOOKBACK_DAYS)
        recent_trades = [t for t in self._day_trade_log if t >= cutoff]
        return len(recent_trades) >= self.MAX_DAY_TRADES

    @property
    def daily_pnl(self) -> float:
        return self.realized_pnl

    @property
    def open_positions(self) -> set[str]:
        return self._open_positions.copy()

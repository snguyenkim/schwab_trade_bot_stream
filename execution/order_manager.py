import sys
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.trade_logger import (
    log_order_submitted, log_order_filled,
    log_order_rejected, log_order_cancelled,
)
from utils.trading_log import TradingLog

# Schwab order model imports
from schwab.models.generated.trading_models import (
    Order, OrderType, Session as OrderSession,
    Duration as OrderDuration, OrderStrategyType,
    OrderLeg, OrderLegType, Instruction as OrderInstruction,
)


def _build_market_order(symbol: str, side: str, quantity: int) -> Order:
    """Build a simple market order dict compatible with the Schwab API."""
    instruction = (
        OrderInstruction.BUY if side.upper() == "BUY" else OrderInstruction.SELL
    )
    return Order(
        order_type=OrderType.MARKET,
        session=OrderSession.NORMAL,
        duration=OrderDuration.DAY,
        order_strategy_type=OrderStrategyType.SINGLE,
        order_leg_collection=[
            OrderLeg(
                order_leg_type=OrderLegType.EQUITY,
                instruction=instruction,
                quantity=quantity,
                instrument={"symbol": symbol, "assetType": "EQUITY"},
            )
        ],
    )


class OrderManager:
    """
    Translates BUY/SELL signals into logged paper trades.
    No real orders are submitted to the Schwab API.
    """

    PAPER_TRADING = True  # Set to False to enable live order submission

    def __init__(self, client, account_hash: str, trading_log: TradingLog | None = None):
        """
        Args:
            client: authenticated SchwabClient instance
            account_hash: encrypted account hash from get_account_numbers()
            trading_log: optional TradingLog instance for daily CSV recording
        """
        self.client = client
        self.account_hash = account_hash
        self._entry_prices: dict[str, float] = {}   # symbol → entry fill price
        self._order_counter: int = 0
        self._trading_log = trading_log

    def execute(self, symbol: str, side: str, quantity: int) -> float:
        """
        Paper-trade: log the order instead of submitting it to Schwab.
        Returns the simulated fill price (latest quote).
        """
        if quantity <= 0:
            logger.warning(
                "[ORDER] Skipping zero-quantity order | {symbol} {side}", symbol=symbol, side=side
            )
            return 0.0

        self._order_counter += 1
        order_id = f"PAPER-{symbol}-{side.upper()}-{self._order_counter}"

        fill_price = self._fetch_price(symbol)

        logger.info(
            "[PAPER] {side} {qty} {symbol} @ {price:.4f} | order_id={oid}",
            side=side.upper(), qty=quantity, symbol=symbol,
            price=fill_price, oid=order_id,
        )
        log_order_submitted(symbol, side.upper(), quantity, order_id)
        log_order_filled(symbol, side.upper(), quantity, fill_price, order_id)

        if side.upper() == "BUY":
            self._entry_prices[symbol] = fill_price
        else:
            self._entry_prices.pop(symbol, None)

        if self._trading_log:
            self._trading_log.record(symbol, side.upper(), quantity, fill_price)

        return fill_price

    def cancel(self, order_id: int, symbol: str = "") -> None:
        logger.info(
            "[PAPER] CANCEL order_id={oid} | {symbol} (no-op in paper mode)",
            oid=order_id, symbol=symbol,
        )
        log_order_cancelled(symbol, str(order_id))

    def entry_price(self, symbol: str) -> float:
        return self._entry_prices.get(symbol, 0.0)

    # ── Broker queries ─────────────────────────────────────────────────────────

    def get_open_orders(self, days_back: int = 1) -> list[dict]:
        """
        Fetch all working (open/pending) orders from the Schwab API.
        Returns a list of dicts with order details.
        """
        try:
            raw_orders = self.client.get_orders(
                account_number=self.account_hash,
                from_entered_time=datetime.now() - timedelta(days=days_back),
                to_entered_time=datetime.now(),
                status="WORKING",
            )
            orders = []
            for o in raw_orders:
                symbol = ""
                instruction = ""
                if getattr(o, "order_leg_collection", None):
                    leg = o.order_leg_collection[0]
                    symbol = getattr(getattr(leg, "instrument", None), "symbol", "")
                    instruction = str(getattr(leg, "instruction", ""))
                orders.append({
                    "order_id":    getattr(o, "order_id", ""),
                    "symbol":      symbol,
                    "instruction": instruction,
                    "quantity":    getattr(o, "quantity", 0),
                    "filled":      getattr(o, "filled_quantity", 0),
                    "status":      str(getattr(o, "status", "")),
                    "order_type":  str(getattr(o, "order_type", "")),
                    "entered_time": str(getattr(o, "entered_time", "")),
                })
            logger.info("[ORDER] get_open_orders → {} working order(s)", len(orders))
            return orders
        except Exception as exc:
            logger.error("[ORDER] get_open_orders failed: {}", exc)
            return []

    # Asset types included by get_positions()
    POSITION_ASSET_TYPES = {"EQUITY", "COLLECTIVE_INVESTMENT"}

    def get_positions(self) -> list[dict]:
        """
        Fetch current positions from the Schwab API.
        Includes EQUITY and COLLECTIVE_INVESTMENT (mutual funds, ETFs, UITs).
        Returns a list of dicts with position details, each including 'asset_type'.
        """
        try:
            account = self.client.get_account(
                self.account_hash, include_positions=True
            )
            sec = account.securities_account
            raw_positions = getattr(sec, "positions", []) or []
            positions = []
            for p in raw_positions:
                inst = getattr(p, "instrument", None)
                if not inst:
                    continue
                asset_type = str(getattr(inst, "asset_type", "")).upper()
                if asset_type not in self.POSITION_ASSET_TYPES:
                    continue
                symbol       = getattr(inst, "symbol", "")
                long_qty     = float(getattr(p, "long_quantity", 0) or 0)
                short_qty    = float(getattr(p, "short_quantity", 0) or 0)
                avg_price    = float(getattr(p, "average_price", 0) or 0)
                market_value = float(getattr(p, "market_value", 0) or 0)
                positions.append({
                    "symbol":       symbol,
                    "asset_type":   asset_type,
                    "long_qty":     long_qty,
                    "short_qty":    short_qty,
                    "net_qty":      long_qty - short_qty,
                    "avg_price":    round(avg_price, 4),
                    "market_value": round(market_value, 2),
                })
            by_type = {}
            for pos in positions:
                by_type[pos["asset_type"]] = by_type.get(pos["asset_type"], 0) + 1
            logger.info(
                "[ORDER] get_positions → {} position(s) {}",
                len(positions), by_type,
            )
            return positions
        except Exception as exc:
            logger.error("[ORDER] get_positions failed: {}", exc)
            return []

    def _fetch_price(self, symbol: str) -> float:
        try:
            response = self.client.get_quotes(symbol)
            obj = response.root.get(symbol)
            if obj is None:
                logger.warning("[ORDER] No quote data returned for {}", symbol)
                return 0.0
            inner = obj.root          # EquityResponse (or similar)
            quote = getattr(inner, "quote", None)
            if quote is None:
                logger.warning("[ORDER] Quote field missing for {}", symbol)
                return 0.0
            price = (getattr(quote, "last_price", None)
                     or getattr(quote, "mark", None)
                     or getattr(quote, "ask_price", None))
            if price is None:
                logger.warning("[ORDER] All price fields None for {}", symbol)
                return 0.0
            return float(price)
        except Exception as exc:
            logger.error("[ORDER] _fetch_price failed for {}: {}", symbol, exc)
            return 0.0

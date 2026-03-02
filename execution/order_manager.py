import sys
from pathlib import Path
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.trade_logger import (
    log_order_submitted, log_order_filled,
    log_order_rejected, log_order_cancelled,
)

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
    Translates BUY/SELL signals into Schwab API orders.
    Tracks last fill price per symbol for P&L reporting.
    """

    def __init__(self, client, account_hash: str):
        """
        Args:
            client: authenticated SchwabClient instance
            account_hash: encrypted account hash from get_account_numbers()
        """
        self.client = client
        self.account_hash = account_hash
        self._entry_prices: dict[str, float] = {}   # symbol → entry fill price

    def execute(self, symbol: str, side: str, quantity: int) -> float:
        """
        Submit a market order and return the estimated fill price.

        In production the fill price comes from an order status poll or
        streaming event — here we use the last quote as a proxy.

        Returns:
            Estimated fill price (0.0 on failure).
        """
        if quantity <= 0:
            logger.warning(
                "[ORDER] Skipping zero-quantity order | {symbol} {side}", symbol=symbol, side=side
            )
            return 0.0

        order = _build_market_order(symbol, side.upper(), quantity)
        order_id = "PENDING"

        try:
            self.client.place_order(self.account_hash, order)
            # Schwab place_order returns None on success; derive a local order_id
            order_id = f"{symbol}-{side.upper()}-{quantity}"
            log_order_submitted(symbol, side.upper(), quantity, order_id)
        except Exception as exc:
            log_order_rejected(symbol, str(exc), order_id)
            logger.error(
                "[ORDER] place_order failed | {symbol} {side} qty={qty} | {exc}",
                symbol=symbol, side=side, qty=quantity, exc=exc,
            )
            return 0.0

        # Approximate fill price from latest quote
        fill_price = self._fetch_price(symbol)
        log_order_filled(symbol, side.upper(), quantity, fill_price, order_id)

        if side.upper() == "BUY":
            self._entry_prices[symbol] = fill_price
        else:
            self._entry_prices.pop(symbol, None)

        return fill_price

    def cancel(self, order_id: int, symbol: str = "") -> None:
        try:
            self.client.cancel_order(self.account_hash, order_id)
            log_order_cancelled(symbol, str(order_id))
        except Exception as exc:
            logger.error(
                "[ORDER] cancel_order failed | order_id={oid} | {exc}",
                oid=order_id, exc=exc,
            )

    def entry_price(self, symbol: str) -> float:
        return self._entry_prices.get(symbol, 0.0)

    def _fetch_price(self, symbol: str) -> float:
        try:
            quote = self.client.get_quote(symbol)
            if symbol in quote:
                inner = quote[symbol]
                price = (inner.get("lastPrice") or inner.get("last")
                         or inner.get("mark") or inner.get("askPrice"))
            else:
                price = quote.get("lastPrice") or quote.get("last") or quote.get("mark")
            return float(price)
        except Exception:
            return 0.0

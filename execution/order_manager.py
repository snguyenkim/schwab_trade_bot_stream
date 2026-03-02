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
    Translates BUY/SELL signals into logged paper trades.
    No real orders are submitted to the Schwab API.
    """

    PAPER_TRADING = True  # Set to False to enable live order submission

    def __init__(self, client, account_hash: str):
        """
        Args:
            client: authenticated SchwabClient instance
            account_hash: encrypted account hash from get_account_numbers()
        """
        self.client = client
        self.account_hash = account_hash
        self._entry_prices: dict[str, float] = {}   # symbol → entry fill price
        self._order_counter: int = 0

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

        return fill_price

    def cancel(self, order_id: int, symbol: str = "") -> None:
        logger.info(
            "[PAPER] CANCEL order_id={oid} | {symbol} (no-op in paper mode)",
            oid=order_id, symbol=symbol,
        )
        log_order_cancelled(symbol, str(order_id))

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

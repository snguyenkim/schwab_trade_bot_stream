from loguru import logger


# ── Market Data ────────────────────────────────────────────────────────────────

def log_bar(symbol: str, close: float, fast_ema: float, slow_ema: float) -> None:
    logger.trace(
        "[BAR] {symbol} | close={close:.4f} | fast_ema={fast:.4f} | slow_ema={slow:.4f} | diff={diff:+.4f}",
        symbol=symbol, close=close, fast=fast_ema, slow=slow_ema,
        diff=fast_ema - slow_ema,
    )


# ── Signal ─────────────────────────────────────────────────────────────────────

def log_signal(symbol: str, signal: str, fast_ema: float, slow_ema: float) -> None:
    logger.debug(
        "[SIGNAL] {symbol} → {signal} | fast_ema={fast:.4f} | slow_ema={slow:.4f}",
        symbol=symbol, signal=signal, fast=fast_ema, slow=slow_ema,
    )


# ── Risk ───────────────────────────────────────────────────────────────────────

def log_risk_block(symbol: str, reason: str) -> None:
    logger.warning("[RISK BLOCK] {symbol} | reason={reason}", symbol=symbol, reason=reason)


# ── Orders ─────────────────────────────────────────────────────────────────────

def log_order_submitted(symbol: str, side: str, qty: int, order_id: str) -> None:
    logger.info(
        "[ORDER] SUBMITTED | {symbol} {side} qty={qty} | order_id={order_id}",
        symbol=symbol, side=side, qty=qty, order_id=order_id,
    )


def log_order_filled(symbol: str, side: str, qty: int, fill_price: float, order_id: str) -> None:
    logger.info(
        "[ORDER] FILLED    | {symbol} {side} qty={qty} @ {fill_price:.4f} | order_id={order_id}",
        symbol=symbol, side=side, qty=qty, fill_price=fill_price, order_id=order_id,
    )


def log_order_rejected(symbol: str, reason: str, order_id: str) -> None:
    logger.error(
        "[ORDER] REJECTED  | {symbol} | reason={reason} | order_id={order_id}",
        symbol=symbol, reason=reason, order_id=order_id,
    )


def log_order_cancelled(symbol: str, order_id: str) -> None:
    logger.warning(
        "[ORDER] CANCELLED | {symbol} | order_id={order_id}",
        symbol=symbol, order_id=order_id,
    )


# ── Position / P&L ─────────────────────────────────────────────────────────────

def log_position_opened(symbol: str, side: str, qty: int, entry_price: float) -> None:
    logger.info(
        "[POSITION] OPENED | {symbol} {side} qty={qty} entry={entry:.4f}",
        symbol=symbol, side=side, qty=qty, entry=entry_price,
    )


def log_position_closed(symbol: str, qty: int, entry: float, exit_price: float) -> None:
    pnl = (exit_price - entry) * qty
    pnl_pct = (exit_price - entry) / entry * 100
    logger.info(
        "[POSITION] CLOSED | {symbol} qty={qty} entry={entry:.4f} exit={exit:.4f} "
        "pnl={pnl:+.2f} ({pnl_pct:+.2f}%)",
        symbol=symbol, qty=qty, entry=entry, exit=exit_price,
        pnl=pnl, pnl_pct=pnl_pct,
    )


# ── Kill Switch ────────────────────────────────────────────────────────────────

def log_kill_switch(reason: str, daily_pnl: float) -> None:
    logger.critical(
        "[KILL SWITCH] ACTIVATED | reason={reason} | daily_pnl={daily_pnl:+.2f}",
        reason=reason, daily_pnl=daily_pnl,
    )

#!/usr/bin/env python3
"""
scripts/force_flatten.py — Emergency standalone force-flatten.

Works even when main.py is hung or unresponsive.

Strategy:
  1. Try a soft signal (SIGUSR1) so the running bot can flush cleanly.
  2. Wait up to 5 seconds for the bot to respond.
  3. If the bot is still alive (hung), SIGKILL it.
  4. Read the last-known open positions from state/positions.json.
  5. Re-authenticate via SQLite credentials and paper-sell each position.
  6. Clean up bot.pid and state/positions.json.

Usage:
    cd /Users/sonnguyen/Desktop/_tradingbot/Agent/Test_1
    python scripts/force_flatten.py
"""

import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent
PID_FILE = ROOT / "bot.pid"
STATE_FILE = ROOT / "state" / "positions.json"
ET = ZoneInfo("America/New_York")


# ── Step 1: soft signal ────────────────────────────────────────────────────────

def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    raw = PID_FILE.read_text().strip()
    return int(raw) if raw.isdigit() else None


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _try_soft_signal(pid: int) -> bool:
    """Send SIGUSR1 and wait up to 5s for the process to exit cleanly."""
    print(f"[INFO] Sending SIGUSR1 to PID {pid} (soft flatten request)...")
    try:
        os.kill(pid, signal.SIGUSR1)
    except ProcessLookupError:
        print("[INFO] Process already gone.")
        return True

    for _ in range(10):          # 10 × 0.5s = 5s
        time.sleep(0.5)
        if not _process_alive(pid):
            print("[INFO] Bot exited cleanly after SIGUSR1.")
            return True

    print("[WARN] Bot did not exit after 5s — proceeding with force kill.")
    return False


def _force_kill(pid: int) -> None:
    print(f"[INFO] Sending SIGKILL to PID {pid}...")
    try:
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
    except ProcessLookupError:
        pass
    print("[INFO] Process killed.")


# ── Step 2: read position state ────────────────────────────────────────────────

def _load_positions() -> list[dict]:
    if not STATE_FILE.exists():
        print(f"[WARN] No state file found at {STATE_FILE}. Nothing to flatten.")
        return []
    data = json.loads(STATE_FILE.read_text())
    positions = data.get("positions", [])
    updated = data.get("updated_at", "unknown")
    print(f"[INFO] State file last updated: {updated}")
    print(f"[INFO] Found {len(positions)} open position(s): "
          f"{[p['symbol'] for p in positions]}")
    return positions


# ── Step 3: auth + paper-sell ─────────────────────────────────────────────────

def _get_price(client, symbol: str) -> float:
    try:
        quote = client.get_quote(symbol)
        if symbol in quote:
            inner = quote[symbol]
            price = (inner.get("lastPrice") or inner.get("last")
                     or inner.get("mark") or inner.get("bidPrice"))
        else:
            price = quote.get("lastPrice") or quote.get("last") or quote.get("mark")
        return float(price)
    except Exception:
        return 0.0


def _paper_sell(client, pos: dict, counter: int) -> None:
    symbol = pos["symbol"]
    size = pos["size"]
    entry = pos["entry_price"]

    price = _get_price(client, symbol)
    order_id = f"FORCE-FLATTEN-{symbol}-SELL-{counter}"
    pnl = (price - entry) * size if price else 0.0
    pnl_pct = ((price - entry) / entry * 100) if price and entry else 0.0

    ts = datetime.now(ET).strftime("%H:%M:%S")
    print(
        f"[{ts}] [PAPER SELL] {symbol} qty={size} "
        f"entry={entry:.4f} exit={price:.4f} "
        f"pnl={pnl:+.2f} ({pnl_pct:+.2f}%) | {order_id}"
    )


def _flatten_via_api(positions: list[dict]) -> None:
    sys.path.insert(0, str(ROOT))
    try:
        from auth.schwab_auth import get_client
    except ImportError as exc:
        print(f"[ERROR] Cannot import auth module: {exc}")
        print("[INFO]  Positions logged above without price confirmation.")
        for i, pos in enumerate(positions, 1):
            print(f"  FORCE SELL: {pos['symbol']} qty={pos['size']} "
                  f"entry={pos['entry_price']:.4f}")
        return

    print("[INFO] Authenticating via SQLite credentials...")
    try:
        client, _ = get_client(ROOT / "schwab_trader.db")
    except RuntimeError as exc:
        print(f"[ERROR] Auth failed: {exc}")
        print("[INFO]  Positions that need manual closing:")
        for pos in positions:
            print(f"  SELL {pos['size']} {pos['symbol']} (entry={pos['entry_price']:.4f})")
        return

    print(f"[INFO] Authenticated. Paper-selling {len(positions)} position(s)...\n")
    for i, pos in enumerate(positions, 1):
        _paper_sell(client, pos, i)


# ── Cleanup ────────────────────────────────────────────────────────────────────

def _cleanup() -> None:
    PID_FILE.unlink(missing_ok=True)
    STATE_FILE.unlink(missing_ok=True)
    print("\n[INFO] Cleaned up bot.pid and state/positions.json.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  FORCE EOD FLATTEN — Emergency Position Closer")
    print("=" * 60)

    pid = _read_pid()

    if pid and _process_alive(pid):
        soft_ok = _try_soft_signal(pid)
        if not soft_ok and _process_alive(pid):
            _force_kill(pid)
    elif pid:
        print(f"[INFO] PID {pid} in bot.pid but process is not running.")
    else:
        print("[INFO] No bot.pid found — bot may already be stopped.")

    positions = _load_positions()

    if positions:
        _flatten_via_api(positions)
    else:
        print("[INFO] No open positions to close.")

    _cleanup()
    print("\n[DONE] Force flatten complete.")


if __name__ == "__main__":
    main()

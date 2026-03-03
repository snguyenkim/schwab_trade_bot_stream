# claude.md — WAT Template: Day Trading Bot (Python + schwab-trader)

---

## 🧭 WORKFLOW

### Overview
Build a Python-based day trading bot using the `schwab-trader` library. The bot will authenticate with the Schwab API, monitor real-time market data, apply a trading strategy, and execute buy/sell orders automatically.

### Phase 1 — Setup & Authentication
1. Clone and install `schwab-trader`:
   ```bash
   git clone https://github.com/ibouazizi/schwab-trader
   cd schwab-trader
   pip install -e .
   ```
2. Configure credentials (API key, secret, redirect URI) via environment variables or a `.env` file.
3. Run `examples/account_overview.py` as the baseline smoke test to confirm auth + account access.

### Phase 2 — Market Data Collection
1. Subscribe to real-time quotes for target symbols (e.g., SPY, QQQ, individual equities).
2. Store tick/OHLCV data in a rolling in-memory buffer (e.g., `deque` or `pandas DataFrame`).
3. Optionally persist to SQLite or CSV for backtesting.

### Phase 3 — Strategy Engine
1. Define entry/exit signals (e.g., moving average crossover, RSI, VWAP deviation).
2. Compute indicators on each new bar/tick.
3. Generate BUY / SELL / HOLD signals with position sizing logic.

### Phase 4 — Order Execution
1. Translate signals into Schwab API orders (market, limit, stop).
2. Implement pre-trade risk checks (max position size, max daily loss, PDT rule guard).
3. Submit orders via `schwab-trader` order endpoints.
4. Track open positions and P&L in real time.

### Phase 5 — Monitoring & Logging
1. Log all events (signals, orders, fills, errors) to a structured log file.
2. Optionally expose a simple dashboard (CLI or web) showing live P&L and positions.
3. Implement a kill switch / emergency flatten-all-positions function.

---

## 🤖 AGENT

### Agent Role
**Day Trading Execution Agent** — an autonomous loop that ingests market data, evaluates strategy rules, manages risk, and executes orders without manual intervention during market hours.

### Agent Loop (Pseudo-code)
```
while market_is_open():
    data = fetch_latest_quotes(symbols)
    update_indicator_state(data)
    signals = strategy.evaluate(indicator_state)
    for signal in signals:
        if risk_manager.approve(signal, portfolio):
            order = build_order(signal)
            broker.submit(order)
            portfolio.update(order)
    log_state(portfolio, signals)
    sleep(tick_interval)
end_of_day_cleanup()
```

### Agent Responsibilities
| Responsibility        | Description                                              |
|-----------------------|----------------------------------------------------------|
| Data Ingestion        | Poll or stream real-time quotes from Schwab API          |
| Signal Generation     | Apply technical indicators and strategy rules            |
| Risk Management       | Enforce position limits, stop-losses, max daily loss     |
| Order Management      | Submit, monitor, and cancel orders                       |
| State Persistence     | Track positions, P&L, and trade history                  |
| Error Recovery        | Handle API errors, reconnects, and unexpected fills      |

### Key Agent Constraints
- **PDT Rule**: Track day trades; halt if approaching 3 day-trades in 5 days (for accounts < $25k).
- **Market Hours**: Only trade between 10:00–16:00 ET. All positions flattened at 15:30 ET.
- **Kill Switch**: Immediately flatten all positions and halt on critical error or drawdown breach.

---

## 🛠 TOOLS

### Core Library
| Tool | Purpose | Source |
|------|---------|--------|
| `schwab-trader` | Schwab API wrapper (auth, quotes, orders, accounts) | `github.com/ibouazizi/schwab-trader` |

### Starting Point
```python
# examples/account_overview.py — baseline reference
# Authenticates and prints account balances/positions
# Use this to verify credentials and understand the API response schema
```

### Python Dependencies
```txt
schwab-trader @ git+https://github.com/ibouazizi/schwab-trader
pandas
numpy
ta          # Technical Analysis library (RSI, MACD, BB, etc.)
python-dotenv
loguru      # Structured logging
schedule    # Optional: cron-style task scheduling
```

### Project File Structure
```
day-trading-bot/
├── claude.md                  # This file
├── .env                       # API credentials (never commit)
├── requirements.txt
├── main.py                    # Entry point — starts the agent loop
├── config.py                  # Strategy params, symbols, risk limits
├── auth/
│   └── schwab_auth.py         # OAuth2 token management (wraps CredentialManager)
├── data/
│   └── market_data.py         # Quote fetching + OHLCV aggregation
├── strategy/
│   ├── base_strategy.py       # Abstract strategy interface
│   ├── ma_crossover.py        # Example: Moving Average Crossover
│   └── ema_crossover.py       # EMA dual-window crossover strategy
├── risk/
│   └── risk_manager.py        # Position sizing, loss limits, PDT guard
├── execution/
│   └── order_manager.py       # Order building, submission, tracking
├── portfolio/
│   └── portfolio_tracker.py   # Real-time P&L and position state
├── utils/
│   └── logger.py              # Loguru setup
└── cresential/
    └── credential_manager.py  # Copied from schwab-trader for reference
```

### Schwab API Key Endpoints (via schwab-trader)
| Action | Method |
|--------|--------|
| Get account balances | `client.get_account()` |
| Get quotes | `client.get_quote(symbol)` |
| Place order | `client.place_order(account_id, order)` |
| Get orders | `client.get_orders(account_id)` |
| Cancel order | `client.cancel_order(account_id, order_id)` |
| Get positions | `client.get_positions(account_id)` |

### Credential Storage — SQLite via `CredentialManager`
Credentials and OAuth tokens are stored in a **SQLite database** (`schwab_trader.db`)
using `cresential/credential_manager.py` from the `schwab-trader` library.
**Do NOT use a `.env` file** for tokens — tokens rotate every 30 minutes and only
SQLite can persist the refreshed values automatically.

| Table | Stores |
|-------|--------|
| `credentials` | `trading_client_id`, `trading_client_secret`, `redirect_uri`, optional `market_data_client_id/secret` |
| `tokens` | `access_token`, `refresh_token`, `expiry`, `api_type` (`trading` or `market_data`) |

#### One-time setup — save your credentials
```python
from cresential.credential_manager import CredentialManager

cm = CredentialManager()          # creates schwab_trader.db if not exists

# Save trading credentials (required)
cm.save_all_credentials(
    trading_client_id="YOUR_CLIENT_ID",
    trading_client_secret="YOUR_CLIENT_SECRET",
    redirect_uri="https://127.0.0.1",
    # optionally add market data creds:
    market_data_client_id="YOUR_MD_CLIENT_ID",
    market_data_client_secret="YOUR_MD_CLIENT_SECRET",
)
```

#### Load credentials + tokens at runtime
```python
from cresential.credential_manager import CredentialManager

cm = CredentialManager()

# Check if we already have a valid non-expired token
if cm.has_valid_auth(api_type="trading"):
    params = cm.get_auth_params(api_type="trading")
    # params keys: client_id, client_secret, redirect_uri,
    #              access_token, refresh_token, token_expiry
else:
    # Trigger OAuth flow, then persist the new tokens:
    # cm.save_tokens(access_token, refresh_token, expires_in=1800)
    pass

# After a token refresh, always persist back to DB:
cm.save_tokens(
    access_token=new_access_token,
    refresh_token=new_refresh_token,
    expires_in=1800,          # 30 min — Schwab default
    api_type="trading",
)
```

#### Key methods
| Method | Purpose |
|--------|---------|
| `save_all_credentials(...)` | One-time save of all API keys |
| `get_credentials(api_type)` | Load `client_id / secret / redirect_uri` |
| `save_tokens(access, refresh, expires_in)` | Persist refreshed tokens |
| `get_tokens(api_type)` | Load tokens + check `is_valid`, `expires_in` |
| `has_valid_auth(api_type)` | Quick boolean — creds + non-expired token exist |
| `get_auth_params(api_type)` | All-in-one dict for client init |
| `clear_all()` | Wipe DB (use for re-auth or testing) |

---

## 🚀 Quick Start

```bash
# 1. Clone schwab-trader and install
git clone https://github.com/ibouazizi/schwab-trader
pip install -e schwab-trader

# 2. Save credentials to SQLite (one-time only)
python - << 'PYEOF'
from cresential.credential_manager import CredentialManager
cm = CredentialManager()
cm.save_all_credentials(
    trading_client_id="YOUR_CLIENT_ID",
    trading_client_secret="YOUR_CLIENT_SECRET",
    redirect_uri="https://127.0.0.1",
)
print("Credentials saved to schwab_trader.db")
'PYEOF'

# 4. Run the bot
python main.py
```

---

## 📈 EMA Crossover Strategy

### Concept
Uses **two Exponential Moving Averages** with different windows (fast & slow). A crossover between them generates trade signals:
- **BUY** → fast EMA crosses **above** slow EMA (bullish momentum)
- **SELL** → fast EMA crosses **below** slow EMA (bearish momentum)

### Default Windows
| Window | Default | Description |
|--------|---------|-------------|
| Fast EMA | 9 bars | Reacts quickly to price changes |
| Slow EMA | 21 bars | Smoother, filters out noise |

Both windows are configurable in `config.py`.

### `strategy/ema_crossover.py`
```python
import pandas as pd
from strategy.base_strategy import BaseStrategy


class EMACrossoverStrategy(BaseStrategy):
    """
    Dual EMA crossover strategy.
    Generates BUY when fast EMA crosses above slow EMA,
    SELL when fast EMA crosses below slow EMA.
    """

    def __init__(self, fast_window: int = 9, slow_window: int = 21):
        assert fast_window < slow_window, "fast_window must be < slow_window"
        self.fast_window = fast_window
        self.slow_window = slow_window

    def compute_emas(self, prices: pd.Series) -> tuple[pd.Series, pd.Series]:
        fast = prices.ewm(span=self.fast_window, adjust=False).mean()
        slow = prices.ewm(span=self.slow_window, adjust=False).mean()
        return fast, slow

    def evaluate(self, prices: pd.Series) -> str:
        """
        Args:
            prices: pd.Series of closing prices (most recent last)
        Returns:
            'BUY', 'SELL', or 'HOLD'
        """
        if len(prices) < self.slow_window + 1:
            return "HOLD"  # Not enough data yet

        fast, slow = self.compute_emas(prices)

        # Current and previous bar crossover detection
        prev_diff = fast.iloc[-2] - slow.iloc[-2]
        curr_diff = fast.iloc[-1] - slow.iloc[-1]

        if prev_diff < 0 and curr_diff > 0:
            return "BUY"
        elif prev_diff > 0 and curr_diff < 0:
            return "SELL"
        else:
            return "HOLD"
```

### `strategy/base_strategy.py`
```python
from abc import ABC, abstractmethod
import pandas as pd


class BaseStrategy(ABC):
    @abstractmethod
    def evaluate(self, prices: pd.Series) -> str:
        """Return 'BUY', 'SELL', or 'HOLD'."""
        pass
```

### `config.py` — EMA Settings
```python
# EMA Strategy Configuration
EMA_FAST_WINDOW = 9    # Fast EMA period (bars)
EMA_SLOW_WINDOW = 21   # Slow EMA period (bars)

# Symbols to trade
SYMBOLS = ["SPY", "QQQ"]

# Risk limits
MAX_POSITION_SIZE = 100     # Max shares per position
MAX_DAILY_LOSS_USD = 500    # Kill switch threshold
```

### Integration in `main.py`
```python
from config import EMA_FAST_WINDOW, EMA_SLOW_WINDOW, SYMBOLS
from strategy.ema_crossover import EMACrossoverStrategy
from execution.order_manager import OrderManager

strategy = EMACrossoverStrategy(fast_window=EMA_FAST_WINDOW, slow_window=EMA_SLOW_WINDOW)

while market_is_open():
    for symbol in SYMBOLS:
        prices = get_price_series(symbol)       # pd.Series of closes
        signal = strategy.evaluate(prices)
        if signal in ("BUY", "SELL"):
            order_manager.execute(symbol, signal)
    sleep(60)  # evaluate every 1 minute
```

---

## ⚠️ Disclaimers
- **Paper trade first.** Never run with real capital until the strategy is validated.
- This bot is for **educational purposes**. Day trading carries significant financial risk.
- Ensure compliance with Schwab's API terms of service and FINRA/SEC regulations.
---

## 🔐 Authentication — `CredentialManager` (SQLite)

### Why SQLite instead of `.env`
Schwab OAuth access tokens **expire every 30 minutes**. A `.env` file is static —
it cannot update itself when a token refreshes. `CredentialManager` solves this by
writing refreshed tokens back to `schwab_trader.db` automatically after each OAuth cycle.

### SQLite Schema
```
schwab_trader.db
├── credentials
│   ├── trading_client_id      ← your Schwab app Client ID
│   ├── trading_client_secret  ← your Schwab app Client Secret
│   ├── redirect_uri           ← https://127.0.0.1
│   ├── market_data_client_id  ← optional, if using Market Data API
│   └── market_data_client_secret
└── tokens
    ├── api_type               ← "trading" or "market_data"
    ├── access_token           ← expires in 30 min
    ├── refresh_token          ← used to get new access_token
    └── expiry                 ← ISO timestamp, checked by has_valid_auth()
```

### `auth/schwab_auth.py` — wrapper around CredentialManager
```python
from pathlib import Path
from cresential.credential_manager import CredentialManager
from schwab import SchwabClient
from loguru import logger


DB_PATH = Path("schwab_trader.db")


def get_client() -> SchwabClient:
    """
    Return an authenticated SchwabClient.
    Loads credentials + tokens from SQLite via CredentialManager.
    Raises RuntimeError if no valid auth exists.
    """
    cm = CredentialManager(db_path=DB_PATH)

    if not cm.has_valid_auth(api_type="trading"):
        raise RuntimeError(
            "No valid trading auth found in schwab_trader.db. "
            "Run credential setup first:
"
            "  python scripts/setup_credentials.py"
        )

    params = cm.get_auth_params(api_type="trading")
    logger.info(
        "[AUTH] Loaded credentials | token_expiry={expiry} | expires_in={sec}s",
        expiry=params["token_expiry"],
        sec=int((params["token_expiry"] - __import__("datetime").datetime.now()).total_seconds()),
    )

    client = SchwabClient(api_key=params["client_id"])
    return client, cm


def refresh_and_save(cm: CredentialManager, new_access: str,
                     new_refresh: str, expires_in: int = 1800) -> None:
    """Call this after every successful token refresh."""
    cm.save_tokens(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=expires_in,
        api_type="trading",
    )
    logger.info("[AUTH] Token refreshed and saved to DB | expires_in={}s", expires_in)
```

### `scripts/setup_credentials.py` — one-time credential setup
```python
"""
Run once before first bot launch to store your Schwab API credentials.
Usage: python scripts/setup_credentials.py
"""
from cresential.credential_manager import CredentialManager

cm = CredentialManager()

print("=== Schwab Trader — Credential Setup ===")
client_id     = input("Trading Client ID     : ").strip()
client_secret = input("Trading Client Secret : ").strip()
redirect_uri  = input("Redirect URI [https://127.0.0.1]: ").strip() or "https://127.0.0.1"

ok = cm.save_all_credentials(
    trading_client_id=client_id,
    trading_client_secret=client_secret,
    redirect_uri=redirect_uri,
)

if ok:
    print(f"✅ Credentials saved to {cm.db_path}")
else:
    print("❌ Failed to save credentials")
```

### Token lifecycle in the bot
```
startup
  └── get_client()
        └── cm.has_valid_auth()  → True?
              ├── YES → load token, init SchwabClient
              └── NO  → trigger OAuth flow → save_tokens() → init SchwabClient

every API call
  └── SchwabClient auto-refreshes access_token internally
        └── on refresh → call refresh_and_save(cm, new_access, new_refresh)

EOD / restart
  └── cm.get_tokens() → is_valid=True (refresh_token still valid for ~7 days)
        └── resume trading without re-authenticating
```

### Updated File Structure
```
day-trading-bot/
├── schwab_trader.db           # ← SQLite credential + token store (git-ignored!)
├── scripts/
│   └── setup_credentials.py  # ← one-time credential setup CLI
├── auth/
│   └── schwab_auth.py        # ← get_client() + refresh_and_save()
└── cresential/
    └── credential_manager.py # ← copied from schwab-trader repo
```

> ⚠️ **Always add `schwab_trader.db` to `.gitignore`** — it contains plaintext secrets.


---

## ⚙️ Settings & Configuration Loading

### `settings.json`
Place this file in the project root. It drives all strategy parameters and symbol lists — no code changes needed to tune the bot.

```json
{
  "global_settings": {
    "profit_target_pct": 0.02,
    "stop_loss_pct": 0.01
  },
  "strategies": [
    {
      "name": "Scalper_EMA2",
      "parameters": {
        "short_span": 9,
        "long_span": 21,
        "period_type": "day",
        "period": 2,
        "frequency_type": "minute",
        "frequency": 1
      },
      "symbols": [
        { "name": "AAPL", "position_size": 100 },
        { "name": "MSFT", "position_size": 200 },
        { "name": "TSLA", "position_size": 300 }
      ]
    },
    {
      "name": "Scalper_EMA3",
      "parameters": {
        "short_span": 5,
        "medium_span": 13,
        "long_span": 50,
        "frequency": 1
      },
      "symbols": [
        { "name": "QQQ",  "position_size": 100 },
        { "name": "AMZN", "position_size": 100 },
        { "name": "NVDA", "position_size": 100 }
      ]
    }
  ]
}
```

### `config/settings_loader.py`
Loads `settings.json` and returns the `Scalper_EMA2` strategy config (or any strategy by name).

```python
import json
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class SymbolConfig:
    name: str
    position_size: int


@dataclass
class StrategyConfig:
    name: str
    parameters: dict
    symbols: list[SymbolConfig]


@dataclass
class GlobalSettings:
    profit_target_pct: float
    stop_loss_pct: float


@dataclass
class AppSettings:
    global_settings: GlobalSettings
    strategies: dict[str, StrategyConfig]   # keyed by strategy name


def load_settings(path: str = "settings.json") -> AppSettings:
    raw = json.loads(Path(path).read_text())

    global_cfg = GlobalSettings(**raw["global_settings"])

    strategies = {}
    for s in raw["strategies"]:
        symbols = [SymbolConfig(**sym) for sym in s["symbols"]]
        strategies[s["name"]] = StrategyConfig(
            name=s["name"],
            parameters=s["parameters"],
            symbols=symbols,
        )

    return AppSettings(global_settings=global_cfg, strategies=strategies)


def get_strategy(name: str, path: str = "settings.json") -> StrategyConfig:
    settings = load_settings(path)
    if name not in settings.strategies:
        raise KeyError(f"Strategy '{name}' not found in {path}. "
                       f"Available: {list(settings.strategies.keys())}")
    return settings.strategies[name]
```

### `strategy/ema_crossover.py` — updated to load from settings
```python
import pandas as pd
from config.settings_loader import get_strategy, StrategyConfig
from strategy.base_strategy import BaseStrategy


class EMACrossoverStrategy(BaseStrategy):
    """
    Dual EMA crossover (Scalper_EMA2).
    Reads short_span, long_span, period_type, period,
    frequency_type, and frequency from settings.json.
    """

    def __init__(self, strategy_name: str = "Scalper_EMA2",
                 settings_path: str = "settings.json"):
        cfg: StrategyConfig = get_strategy(strategy_name, settings_path)
        p = cfg.parameters

        self.short_span:     int = p["short_span"]       # fast EMA window
        self.long_span:      int = p["long_span"]        # slow EMA window
        self.period_type:    str = p.get("period_type", "day")
        self.period:         int = p.get("period", 2)
        self.frequency_type: str = p.get("frequency_type", "minute")
        self.frequency:      int = p.get("frequency", 1)

        # Symbol → position size mapping
        self.symbols: dict[str, int] = {
            sym.name: sym.position_size for sym in cfg.symbols
        }

        assert self.short_span < self.long_span, \
            f"short_span ({self.short_span}) must be < long_span ({self.long_span})"

    # ------------------------------------------------------------------
    def compute_emas(self, prices: pd.Series) -> tuple[pd.Series, pd.Series]:
        fast = prices.ewm(span=self.short_span, adjust=False).mean()
        slow = prices.ewm(span=self.long_span,  adjust=False).mean()
        return fast, slow

    def evaluate(self, prices: pd.Series) -> str:
        """
        Args:
            prices: pd.Series of closing prices, most recent last
        Returns:
            'BUY', 'SELL', or 'HOLD'
        """
        if len(prices) < self.long_span + 1:
            return "HOLD"   # insufficient history

        fast, slow = self.compute_emas(prices)

        prev_diff = fast.iloc[-2] - slow.iloc[-2]
        curr_diff = fast.iloc[-1] - slow.iloc[-1]

        if prev_diff < 0 and curr_diff > 0:
            return "BUY"
        elif prev_diff > 0 and curr_diff < 0:
            return "SELL"
        return "HOLD"

    def position_size(self, symbol: str) -> int:
        """Return configured position size for a symbol."""
        return self.symbols.get(symbol, 0)
```

### Integration in `main.py`
```python
from config.settings_loader import load_settings
from strategy.ema_crossover import EMACrossoverStrategy

# Load credentials from SQLite
from cresential.credential_manager import CredentialManager
cm = CredentialManager()
if not cm.has_valid_auth():
    raise RuntimeError("No valid auth — run credential setup first")
auth = cm.get_auth_params()   # {client_id, client_secret, access_token, ...}

# Load full settings
settings = load_settings("settings.json")
profit_target = settings.global_settings.profit_target_pct  # 0.02
stop_loss     = settings.global_settings.stop_loss_pct      # 0.01

# Boot Scalper_EMA2
strategy = EMACrossoverStrategy(strategy_name="Scalper_EMA2")

print(f"Strategy : {strategy.short_span}/{strategy.long_span} EMA")
print(f"Data     : {strategy.period} {strategy.period_type}(s) "
      f"@ {strategy.frequency} {strategy.frequency_type}")
print(f"Symbols  : {strategy.symbols}")
# → Strategy : 9/21 EMA
# → Data     : 2 day(s) @ 1 minute
# → Symbols  : {'AAPL': 100, 'MSFT': 200, 'TSLA': 300}

while market_is_open():
    for symbol, size in strategy.symbols.items():
        prices = get_price_series(
            symbol,
            period_type=strategy.period_type,
            period=strategy.period,
            frequency_type=strategy.frequency_type,
            frequency=strategy.frequency,
        )
        signal = strategy.evaluate(prices)
        if signal in ("BUY", "SELL"):
            order_manager.execute(symbol, signal, quantity=size)
    sleep(strategy.frequency * 60)
```

### Updated File Structure
```
day-trading-bot/
├── settings.json              # ← All strategy params + symbol lists
├── config/
│   ├── __init__.py
│   └── settings_loader.py     # ← Loads + parses settings.json
├── strategy/
│   ├── base_strategy.py
│   ├── ema_crossover.py       # ← Scalper_EMA2 (short/long span)
│   └── ...
└── ...
```


---

## 📋 Trade Logging

### Overview
Uses `loguru` for structured, leveled logging. Every lifecycle event — signal, order submission, fill, rejection, risk block, P&L — gets a timestamped trace line written to both console and a rotating log file.

### Log Levels Used
| Level | Purpose |
|-------|---------|
| `TRACE` | Raw EMA values, every bar evaluation |
| `DEBUG` | Signal generation details |
| `INFO`  | Order submitted, position opened/closed |
| `WARNING` | Risk checks triggered, partial fills |
| `ERROR` | API failures, order rejections |
| `CRITICAL` | Kill switch activated, daily loss breached |

---

### `utils/logger.py`
```python
import sys
from pathlib import Path
from loguru import logger


def setup_logger(log_dir: str = "logs", strategy_name: str = "bot") -> None:
    """
    Configure loguru sinks:
      - Console: INFO and above, colorized
      - File:    TRACE and above, rotating daily, retained 30 days
    """
    Path(log_dir).mkdir(exist_ok=True)

    # Remove default sink
    logger.remove()

    # Console — INFO+
    logger.add(
        sys.stdout,
        level="INFO",
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    )

    # File — TRACE+ with daily rotation
    logger.add(
        f"{log_dir}/{strategy_name}_{{time:YYYY-MM-DD}}.log",
        level="TRACE",
        rotation="00:00",        # new file each day
        retention="30 days",
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
        enqueue=True,            # thread-safe async logging
    )
```

---

### `utils/trade_logger.py`
Typed helper functions so every component logs in a consistent, searchable format.

```python
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
    logger.warning("[ORDER] CANCELLED | {symbol} | order_id={order_id}",
                   symbol=symbol, order_id=order_id)


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
```

---

### Integration across modules

**`main.py`** — initialise once at startup:
```python
from utils.logger import setup_logger
from utils.trade_logger import log_kill_switch

setup_logger(log_dir="logs", strategy_name="Scalper_EMA2")

# ... agent loop ...
# On breach:
# log_kill_switch("daily loss limit reached", daily_pnl=-520.00)
```

**`strategy/ema_crossover.py`** — log every bar and every signal:
```python
from utils.trade_logger import log_bar, log_signal

def evaluate(self, prices: pd.Series, symbol: str = "") -> str:
    ...
    fast, slow = self.compute_emas(prices)
    log_bar(symbol, prices.iloc[-1], fast.iloc[-1], slow.iloc[-1])

    ...signal logic...

    if signal != "HOLD":
        log_signal(symbol, signal, fast.iloc[-1], slow.iloc[-1])
    return signal
```

**`execution/order_manager.py`** — log every order lifecycle event:
```python
from utils.trade_logger import (
    log_order_submitted, log_order_filled,
    log_order_rejected, log_order_cancelled,
)
```

**`risk/risk_manager.py`** — log every block:
```python
from utils.trade_logger import log_risk_block

# e.g.
log_risk_block("TSLA", "position size exceeds MAX_POSITION_SIZE=300")
```

---

### Sample log output
```
# Console (INFO+)
09:31:02 | INFO     | order_manager - [ORDER] SUBMITTED | AAPL BUY qty=100 | order_id=ORD-0042
09:31:03 | INFO     | order_manager - [ORDER] FILLED    | AAPL BUY qty=100 @ 189.4200 | order_id=ORD-0042
09:45:17 | INFO     | order_manager - [ORDER] FILLED    | AAPL SELL qty=100 @ 191.2600 | order_id=ORD-0043
09:45:17 | INFO     | portfolio     - [POSITION] CLOSED | AAPL qty=100 entry=189.4200 exit=191.2600 pnl=+184.00 (+0.97%)

# File (TRACE+)
2025-03-02 09:31:01.204 | TRACE    | ema_crossover:evaluate:48 - [BAR] AAPL | close=189.4200 | fast_ema=189.1832 | slow_ema=188.9451 | diff=+0.2381
2025-03-02 09:31:01.205 | DEBUG    | ema_crossover:evaluate:55 - [SIGNAL] AAPL → BUY | fast_ema=189.1832 | slow_ema=188.9451
```

---

### Updated File Structure
```
day-trading-bot/
├── utils/
│   ├── logger.py              # ← Loguru setup (console + rotating file)
│   └── trade_logger.py        # ← Typed trace helpers for all trade events
├── logs/
│   └── Scalper_EMA2_2025-03-02.log   # ← Auto-created, rotates daily
└── ...
```

---

## 🔍 Position Monitor (Real-Time Risk Management)

### Overview
A dedicated monitoring loop runs concurrently with the signal loop. Every tick it fetches the latest price for each open position, computes unrealized P&L, and triggers exits when stop-loss, profit-target, trailing-stop, time-stop, or EOD conditions are breached.

### Position Lifecycle
```
ENTRY → [monitor loop] → STOP-LOSS exit
                       → PROFIT-TARGET exit
                       → TRAILING-STOP exit
                       → TIME-STOP exit (max hold)
                       → EOD FLATTEN (15:30 ET)
                       → KILL SWITCH (daily loss breach)
```

---

### `portfolio/position.py`
Data model for a single open position.

```python
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Position:
    symbol:       str
    size:         int
    entry_price:  float
    entry_time:   datetime = field(default_factory=datetime.now)

    # Trailing stop state
    peak_price:   float = field(init=False)
    trailing_stop_price: float = field(default=0.0)

    def __post_init__(self):
        self.peak_price = self.entry_price

    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.size

    def unrealized_pnl_pct(self, current_price: float) -> float:
        return (current_price - self.entry_price) / self.entry_price

    def update_peak(self, current_price: float) -> None:
        if current_price > self.peak_price:
            self.peak_price = current_price
```

---

### `portfolio/position_monitor.py`

```python
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from loguru import logger

from portfolio.position import Position
from config.settings_loader import load_settings
from utils.trade_logger import (
    log_bar, log_position_closed, log_kill_switch, log_risk_block
)

ET = ZoneInfo("America/New_York")
EOD_FLATTEN_TIME = dt_time(15, 30)   # force-close all by 15:30 ET


class PositionMonitor:
    """
    Monitors all open positions in real time.
    Checks stop-loss, profit-target, trailing-stop,
    time-stop, EOD flatten, and daily loss kill switch.
    """

    def __init__(self, client, order_manager,
                 settings_path: str = "settings.json"):
        cfg = load_settings(settings_path)
        g   = cfg.global_settings

        self.client              = client
        self.order_manager       = order_manager
        self.profit_target_pct   = g.profit_target_pct       # e.g. 0.02
        self.stop_loss_pct       = g.stop_loss_pct           # e.g. 0.01
        self.trailing_stop_pct   = g.stop_loss_pct           # same by default
        self.max_daily_loss      = g.get("max_daily_loss_usd", 500)
        self.max_hold_minutes    = g.get("max_hold_minutes", 60)

        self.positions:   dict[str, Position] = {}   # symbol → Position
        self.realized_pnl: float = 0.0

    # ── Position registry ─────────────────────────────────────────────────────

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

    # ── Main monitor loop ─────────────────────────────────────────────────────

    def run(self, poll_interval_sec: int = 5) -> None:
        """
        Call this in a background thread or asyncio task.
        Polls every poll_interval_sec seconds.
        """
        logger.info("[MONITOR] Started — poll interval={}s", poll_interval_sec)
        while True:
            now_et = datetime.now(ET)

            # EOD flatten — close everything before market close
            if now_et.time() >= EOD_FLATTEN_TIME:
                self._flatten_all("EOD flatten — market close approaching")
                logger.info("[MONITOR] EOD complete. Stopping monitor.")
                break

            self._check_all_positions(now_et)

            # Daily loss kill switch
            if self.realized_pnl <= -abs(self.max_daily_loss):
                self._flatten_all("daily loss limit breached")
                log_kill_switch("daily loss limit breached", self.realized_pnl)
                break

            time.sleep(poll_interval_sec)

    # ── Per-position checks ───────────────────────────────────────────────────

    def _check_all_positions(self, now: datetime) -> None:
        for symbol, pos in list(self.positions.items()):
            try:
                current_price = self._get_price(symbol)
            except Exception as e:
                logger.error("[MONITOR] Price fetch failed | {symbol} | {e}",
                             symbol=symbol, e=e)
                continue

            pos.update_peak(current_price)
            pnl     = pos.unrealized_pnl(current_price)
            pnl_pct = pos.unrealized_pnl_pct(current_price)

            log_bar(symbol, current_price,
                    fast_ema=0.0, slow_ema=0.0)   # EMA not needed here
            logger.debug(
                "[MONITOR] {symbol} | price={price:.4f} | pnl={pnl:+.2f} ({pct:+.2%}) "
                "| peak={peak:.4f}",
                symbol=symbol, price=current_price,
                pnl=pnl, pct=pnl_pct, peak=pos.peak_price,
            )

            exit_reason = self._exit_reason(pos, current_price, now)
            if exit_reason:
                self._close_position(pos, current_price, exit_reason)

    def _exit_reason(self, pos: Position, price: float,
                     now: datetime) -> str | None:
        pnl_pct      = pos.unrealized_pnl_pct(price)
        hold_minutes = (now - pos.entry_time.replace(tzinfo=ET)).seconds / 60

        # 1. Stop-loss
        if pnl_pct <= -self.stop_loss_pct:
            return f"stop-loss hit ({pnl_pct:+.2%} <= -{self.stop_loss_pct:.2%})"

        # 2. Profit target
        if pnl_pct >= self.profit_target_pct:
            return f"profit target hit ({pnl_pct:+.2%} >= +{self.profit_target_pct:.2%})"

        # 3. Trailing stop — only activates once in profit > trailing_stop_pct
        drawdown_from_peak = (price - pos.peak_price) / pos.peak_price
        if (pos.peak_price > pos.entry_price and
                drawdown_from_peak <= -self.trailing_stop_pct):
            return (f"trailing stop hit (peak={pos.peak_price:.4f}, "
                    f"drawdown={drawdown_from_peak:+.2%})")

        # 4. Time-stop — position held too long
        if hold_minutes >= self.max_hold_minutes:
            return f"time-stop ({hold_minutes:.0f} min >= {self.max_hold_minutes} min)"

        return None

    # ── Execution helpers ─────────────────────────────────────────────────────

    def _close_position(self, pos: Position, exit_price: float,
                        reason: str) -> None:
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
            price = self._get_price(symbol)
            self._close_position(pos, price, reason)

    def _get_price(self, symbol: str) -> float:
        quote = self.client.get_quote(symbol)
        return float(quote["lastPrice"])

    # ── Status snapshot ───────────────────────────────────────────────────────

    def snapshot(self) -> list[dict]:
        """Return current open positions with live P&L for display/logging."""
        result = []
        for symbol, pos in self.positions.items():
            try:
                price = self._get_price(symbol)
                result.append({
                    "symbol":       symbol,
                    "size":         pos.size,
                    "entry_price":  pos.entry_price,
                    "current_price": price,
                    "unrealized_pnl": round(pos.unrealized_pnl(price), 2),
                    "pnl_pct":      round(pos.unrealized_pnl_pct(price) * 100, 2),
                    "peak_price":   round(pos.peak_price, 4),
                })
            except Exception:
                pass
        return result
```

---

### `settings.json` — add monitor fields to `global_settings`
```json
{
  "global_settings": {
    "profit_target_pct": 0.02,
    "stop_loss_pct": 0.01,
    "max_daily_loss_usd": 500,
    "max_hold_minutes": 60
  },
  ...
}
```

---

### Integration in `main.py`
```python
import threading
from portfolio.position_monitor import PositionMonitor
from utils.logger import setup_logger

setup_logger(log_dir="logs", strategy_name="Scalper_EMA2")

monitor = PositionMonitor(client, order_manager, settings_path="settings.json")

# Pre-load existing positions (e.g. from JSON / recovery)
for p in existing_positions:
    monitor.add_position(p["symbol"], p["size"], p["entry_price"])

# Run monitor in background thread
monitor_thread = threading.Thread(target=monitor.run,
                                  kwargs={"poll_interval_sec": 5},
                                  daemon=True)
monitor_thread.start()

# Signal loop — register new positions after fills
while market_is_open():
    for symbol, size in strategy.symbols.items():
        prices = get_price_series(symbol, ...)
        signal = strategy.evaluate(prices, symbol=symbol)
        if signal == "BUY" and symbol not in monitor.positions:
            fill_price = order_manager.execute(symbol, "BUY", quantity=size)
            monitor.add_position(symbol, size, fill_price)
    sleep(strategy.frequency * 60)
```

---

### Sample monitor log output
```
# Console
09:31:05 | INFO     | position_monitor - [MONITOR] TRACKING  | AAPL size=10 entry=182.3500
09:31:05 | INFO     | position_monitor - [MONITOR] TRACKING  | TQQQ size=5  entry=82.3500
09:31:05 | INFO     | position_monitor - [MONITOR] TRACKING  | AMZN size=3  entry=670.1000
09:31:10 | WARNING  | position_monitor - [MONITOR] EXIT TRIGGERED | TQQQ | reason=stop-loss hit (-1.03% <= -1.00%)
09:31:10 | INFO     | portfolio        - [POSITION] CLOSED   | TQQQ qty=5 entry=82.3500 exit=81.5000 pnl=-4.25 (-1.03%)
09:45:17 | INFO     | portfolio        - [POSITION] CLOSED   | AAPL qty=10 entry=182.3500 exit=185.8900 pnl=+35.40 (+1.94%)
15:30:00 | CRITICAL | position_monitor - [MONITOR] FLATTEN ALL | reason=EOD flatten — market close approaching
15:30:01 | INFO     | portfolio        - [POSITION] CLOSED   | AMZN qty=3 entry=670.1000 exit=672.5000 pnl=+7.20 (+0.36%)

# File (TRACE — every 5s tick)
2025-03-02 09:31:10.042 | DEBUG | position_monitor:_check_all_positions:91 - [MONITOR] TQQQ | price=81.5000 | pnl=-4.25 (-1.03%) | peak=82.6100
2025-03-02 09:31:10.043 | DEBUG | position_monitor:_check_all_positions:91 - [MONITOR] AMZN | price=671.8000 | pnl=+5.10 (+0.25%) | peak=672.4500
```

---

### Exit Rules Summary
| Rule | Trigger | Source |
|------|---------|--------|
| Stop-loss | `pnl_pct <= -stop_loss_pct` | `settings.json` |
| Profit target | `pnl_pct >= profit_target_pct` | `settings.json` |
| Trailing stop | drawdown from peak `<= -trailing_stop_pct` | `settings.json` |
| Time-stop | held `>= max_hold_minutes` | `settings.json` |
| EOD flatten | current ET time `>= 15:30` | hardcoded (safe default) |
| Kill switch | `realized_pnl <= -max_daily_loss_usd` | `settings.json` |

---

### Updated File Structure
```
day-trading-bot/
├── portfolio/
│   ├── position.py            # ← Position dataclass with peak tracking
│   ├── position_monitor.py    # ← Real-time monitor loop + all exit rules
│   └── portfolio_tracker.py   # ← Aggregate P&L and position state
└── ...
```

---

## 📈 Triple EMA Crossover Strategy (Scalper_EMA3)

### Concept
Uses **three Exponential Moving Averages** — fast, medium, and slow. The fast/medium pair generates entry/exit signals; the slow EMA acts as a trend filter so signals are only taken in the prevailing direction.

- **BUY**  → fast EMA crosses **above** medium EMA, AND last close is **above** slow EMA (uptrend confirmed)
- **SELL** → fast EMA crosses **below** medium EMA, AND last close is **below** slow EMA (downtrend confirmed)
- **HOLD** → crossover occurs but against the slow EMA direction, or no crossover

### Default Windows (from `settings.json`)
| EMA | Span | Role |
|-----|------|------|
| Fast | 5 | Signal crossover trigger |
| Medium | 13 | Signal crossover counterpart |
| Slow | 50 | Trend filter — only trade in its direction |

### `strategy/ema3_crossover.py`
```python
class EMA3CrossoverStrategy(BaseStrategy):
    def __init__(self, strategy_name="Scalper_EMA3", settings_path="settings.json"):
        cfg = get_strategy(strategy_name, settings_path)
        p = cfg.parameters
        self.short_span  = p["short_span"]    # 5
        self.medium_span = p["medium_span"]   # 13
        self.long_span   = p["long_span"]     # 50
        self.frequency   = p.get("frequency", 1)
        self.symbols     = {sym.name: sym.position_size for sym in cfg.symbols}

    def compute_emas(self, prices):
        fast   = prices.ewm(span=self.short_span,  adjust=False).mean()
        medium = prices.ewm(span=self.medium_span, adjust=False).mean()
        slow   = prices.ewm(span=self.long_span,   adjust=False).mean()
        return fast, medium, slow

    def evaluate(self, prices, symbol=""):
        if len(prices) < self.long_span + 1:
            return "HOLD"
        fast, medium, slow = self.compute_emas(prices)
        prev_fast_above = fast.iloc[-2] > medium.iloc[-2]
        curr_fast_above = fast.iloc[-1] > medium.iloc[-1]
        in_uptrend   = prices.iloc[-1] > slow.iloc[-1]
        in_downtrend = prices.iloc[-1] < slow.iloc[-1]
        if not prev_fast_above and curr_fast_above and in_uptrend:
            return "BUY"
        if prev_fast_above and not curr_fast_above and in_downtrend:
            return "SELL"
        return "HOLD"
```

### `settings.json` — Scalper_EMA3 block
```json
{
  "name": "Scalper_EMA3",
  "parameters": {
    "short_span": 5,
    "medium_span": 13,
    "long_span": 50,
    "period_type": "day",
    "period": 2,
    "frequency_type": "minute",
    "frequency": 1
  },
  "symbols": [
    { "name": "QQQ",  "position_size": 100 },
    { "name": "AMZN", "position_size": 100 },
    { "name": "NVDA", "position_size": 100 }
  ]
}
```

### Logging
Every bar emits a `TRACE`-level `[BAR3]` line with all three EMA values and the current trend direction:
```
[BAR3] QQQ | close=484.2100 | fast=484.0320 | medium=483.7410 | slow=481.9900 | f-m=+0.2910 | trend=UP
```
Signals use the same `log_signal()` helper as EMA2 (`[SIGNAL] QQQ → BUY | fast_ema=... | slow_ema=...`), where `slow_ema` refers to the medium EMA (the crossover counterpart).

### Switching between strategies
Change one line in `settings.json` — **no code changes needed**:
```json
"global_settings": {
  "strategy": "Scalper_EMA2",   ← change to "Scalper_EMA3" to switch
  ...
}
```
`main.py` reads `global_settings.strategy` at startup, looks up the class in `STRATEGY_CLASSES`, and instantiates it. All other components (risk manager, order manager, position monitor) are strategy-agnostic.

### File Structure
```
strategy/
├── base_strategy.py       # Abstract BaseStrategy interface
├── ema_crossover.py       # Scalper_EMA2 — dual EMA (short/long)
└── ema3_crossover.py      # Scalper_EMA3 — triple EMA (short/medium/long)
```

---

## 📝 Implementation Changes (vs. Template)

The following changes were made during Phase 1 implementation:

### Folder rename
- `examples/` renamed to `cresential/` — contains `credential_manager.py`
- All `sys.path.insert` references updated in `auth/schwab_auth.py` and `scripts/setup_credentials.py`

### Trading hours
| Setting | Template | Actual |
|---------|----------|--------|
| Market open | 09:30 ET | **10:00 ET** (`main.py`) |
| EOD flatten | 15:45 ET | **15:30 ET** (`portfolio/position_monitor.py`) |

### Paper trading mode
`execution/order_manager.py` runs in paper trading mode — **no real orders are submitted**.
All BUY/SELL signals are logged with a `PAPER-` prefixed order ID and the current quote price.
Set `OrderManager.PAPER_TRADING = False` to re-enable live order submission.

### Bug fix — CredentialManager
`cresential/credential_manager.py` `get_credentials()` was patched to use
`sqlite3.Row` column-name access instead of hardcoded positional indices,
which caused `None` to be returned on freshly created databases.

### GitHub repository
[github.com/snguyenkim/schwab_trade_bot](https://github.com/snguyenkim/schwab_trade_bot) (private)

### Strategy selector via `settings.json`
`global_settings.strategy` controls which strategy `main.py` runs — no code changes needed:

```json
"global_settings": {
  "strategy": "Scalper_EMA2"
}
```

| Value | Class | File |
|-------|-------|------|
| `"Scalper_EMA2"` | `EMACrossoverStrategy` | `strategy/ema_crossover.py` |
| `"Scalper_EMA3"` | `EMA3CrossoverStrategy` | `strategy/ema3_crossover.py` |

`main.py` resolves the class at startup via `STRATEGY_CLASSES` dict. Unknown names print a clear error and exit. `GlobalSettings.strategy` defaults to `"Scalper_EMA2"` if the key is omitted from `settings.json`.

### Force EOD flatten

Two mechanisms were added to close all positions outside the automatic 15:30 ET trigger:

#### 1. SIGUSR1 signal handler (`main.py`)
- On startup, `main.py` writes its PID to `bot.pid`
- A `SIGUSR1` handler is registered that calls `monitor._flatten_all()` immediately
- `bot.pid` is deleted on clean exit
- Trigger with: `kill -USR1 $(cat bot.pid)`

#### 2. Standalone emergency script (`scripts/force_flatten.py`)
Works even when `main.py` is completely hung:

1. **Soft signal** — sends `SIGUSR1`, waits 5s for the bot to exit cleanly
2. **Force kill** — if still alive, sends `SIGKILL`
3. **Read state** — loads open positions from `state/positions.json`
4. **Re-auth** — independently authenticates via `schwab_trader.db`
5. **Paper-sell** — logs forced SELL for each position with current price + P&L
6. **Cleanup** — removes `bot.pid` and `state/positions.json`

Run with:
```bash
python scripts/force_flatten.py
```

#### 3. Position state file (`state/positions.json`)
`PositionMonitor` writes `state/positions.json` on every `add_position`, `remove_position`, and after each 5-second poll cycle. This ensures `force_flatten.py` always has an up-to-date position snapshot even after a crash.

Both `bot.pid` and `state/` are added to `.gitignore`.

### Broker query methods (`OrderManager`)

Two methods added to `execution/order_manager.py` for fetching live data from the Schwab API:

#### `get_open_orders(days_back=1) → list[dict]`
Calls `client.get_orders(status="WORKING")`. Returns:
```python
[{
    "order_id", "symbol", "instruction",   # BUY / SELL
    "quantity", "filled", "status",
    "order_type", "entered_time"
}]
```

#### `get_positions() → list[dict]`
Calls `client.get_account(include_positions=True)`. Filters to `POSITION_ASSET_TYPES`:

| Asset type | Covers |
|------------|--------|
| `EQUITY` | Individual stocks |
| `COLLECTIVE_INVESTMENT` | Mutual funds, ETFs (booked as collective), UITs |

Returns:
```python
[{
    "symbol", "asset_type",
    "long_qty", "short_qty", "net_qty",
    "avg_price", "market_value"
}]
```

To add more asset types, extend the class-level set in `order_manager.py`:
```python
POSITION_ASSET_TYPES = {"EQUITY", "COLLECTIVE_INVESTMENT", "FIXED_INCOME"}
```

---

## 🗂 Git Workflow

### Repository Setup (one-time)

```bash
cd /Users/sonnguyen/Desktop/_tradingbot/Agent/Test_1
git init
git add .
git commit -m "Initial commit — day trading bot"
```

### .gitignore — critical exclusions

The `.gitignore` already covers all sensitive and runtime files:

```
schwab_trader.db   # OAuth tokens + API credentials — NEVER commit
bot.pid            # Runtime PID file
state/             # Live position state written every 5s
logs/              # Daily rotating log files
__pycache__/
*.pyc
.env
```

> ⚠️ Always verify `.gitignore` is applied **before** `git add .` — run `git status` and confirm `schwab_trader.db` is not listed.

### Typical commit workflow

```bash
# Check what changed
git status
git diff

# Stage specific files (preferred over git add -A)
git add settings.json strategy/ema3_crossover.py

# Commit
git commit -m "Add Scalper_EMA3 strategy with triple EMA crossover"
```

### What to commit vs. what to exclude

| File / Dir | Commit? | Reason |
|------------|---------|--------|
| `*.py` source files | Yes | Core bot logic |
| `settings.json` | Yes | Strategy config (no secrets) |
| `CLAUDE.md`, `info.md` | Yes | Project docs |
| `requirements.txt` | Yes | Reproducible installs |
| `schwab_trader.db` | **No** | Contains plaintext API keys + OAuth tokens |
| `bot.pid` | **No** | Runtime artifact |
| `state/positions.json` | **No** | Runtime artifact |
| `logs/` | **No** | Large, auto-generated |
| `.env` | **No** | Secrets |

Both methods are safe to call at any time — they log errors and return `[]` on failure so the bot loop is never interrupted.

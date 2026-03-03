# Schwab Day Trading Bot

A Python-based automated day trading bot using the [schwab-trader](https://github.com/ibouazizi/schwab-trader) library. It authenticates with the Schwab API, monitors real-time 1-minute bars, applies an EMA crossover strategy, manages risk, and executes orders automatically.

> **Paper trading mode is active by default.** No real orders are submitted until `OrderManager.PAPER_TRADING = False`.

---

## Project Structure

```
Test_1/
├── main.py                    # Entry point — starts the agent loop
├── settings.json              # All strategy params, symbols, risk limits
├── requirements.txt
├── auth/
│   └── schwab_auth.py         # get_client() — loads creds + tokens from SQLite
├── config/
│   └── settings_loader.py     # Parses settings.json into dataclasses
├── data/
│   └── market_data.py         # get_price_series(), get_latest_price()
├── strategy/
│   ├── base_strategy.py       # Abstract BaseStrategy interface
│   ├── ema_crossover.py       # Scalper_EMA2 — dual EMA (9/21)
│   └── ema3_crossover.py      # Scalper_EMA3 — triple EMA (5/13/50)
├── risk/
│   └── risk_manager.py        # PDT guard, daily loss cap, duplicate position check
├── execution/
│   └── order_manager.py       # execute() — paper or live order submission
├── portfolio/
│   ├── position.py            # Position dataclass with peak tracking
│   ├── position_monitor.py    # Background thread: stop-loss, profit-target, EOD flatten
│   └── portfolio_tracker.py   # Aggregate P&L and position state
├── backtest/
│   ├── engine.py              # Walk-forward backtest engine
│   └── report.py              # Performance stats + trade log printer
├── scripts/
│   ├── setup_credentials.py   # One-time credential setup
│   ├── run_backtest.py        # Backtest CLI entry point
│   └── force_flatten.py       # Emergency flatten — works even if main.py is hung
├── utils/
│   ├── logger.py              # Loguru setup (console + rotating file)
│   └── trade_logger.py        # Typed log helpers for all trade events
├── cresential/
│   └── credential_manager.py  # SQLite credential + token manager
├── logs/                      # Auto-created, rotates daily (git-ignored)
├── state/                     # Live position state written every 5s (git-ignored)
└── schwab_trader.db           # OAuth tokens + API keys (git-ignored — never commit)
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Save credentials (one-time)

```bash
python3 scripts/setup_credentials.py
```

Prompts for your Schwab app Client ID, Client Secret, and redirect URI. Stored in `schwab_trader.db` — **never committed to git**.

### 3. Authenticate (first run)

On first launch, a browser window opens for OAuth2 login. After approving, tokens are saved to `schwab_trader.db` and auto-refreshed every 30 minutes.

---

## Running the Bot

```bash
python3 main.py
```

**Trading hours:** 10:00 – 16:00 ET. Bot waits if market is closed.
**EOD flatten:** All positions closed at 15:30 ET automatically.

### Force-flatten positions

```bash
# Soft signal (SIGUSR1) — bot exits cleanly
kill -USR1 $(cat bot.pid)

# Emergency script — works even if bot is hung
python3 scripts/force_flatten.py
```

---

## Configuration (`settings.json`)

All parameters are set in `settings.json` — no code changes needed.

```json
{
  "global_settings": {
    "strategy": "Scalper_EMA2",
    "profit_target_pct": 0.02,
    "stop_loss_pct": 0.01,
    "max_daily_loss_usd": 500,
    "max_hold_minutes": 60
  }
}
```

Switch strategies by changing `"strategy"` to `"Scalper_EMA2"` or `"Scalper_EMA3"`.

---

## Strategies

### Scalper_EMA2 — Dual EMA Crossover

- **Signal:** Fast (9) crosses above Slow (21) → **BUY**; crosses below → **SELL**
- **Symbols:** AAPL (100 shares), MSFT (200), TSLA (300)
- **Data:** 1-minute bars, 2 days of history

### Scalper_EMA3 — Triple EMA Crossover

- **Signal:** Fast (5) crosses above Medium (13) AND price above Slow (50) → **BUY**; opposite → **SELL**
- **Slow EMA acts as a trend filter** — signals only taken in its direction
- **Symbols:** QQQ (100 shares), AMZN (100), NVDA (100)
- **Data:** 1-minute bars, 2 days of history

---

## Risk Management

| Rule                   | Setting                  | Default   |
| ---------------------- | ------------------------ | --------- |
| Stop-loss              | `stop_loss_pct`          | 1%        |
| Profit target          | `profit_target_pct`      | 2%        |
| Trailing stop          | same as stop-loss        | 1%        |
| Max hold time          | `max_hold_minutes`       | 60 min    |
| Daily loss kill switch | `max_daily_loss_usd`     | $500      |
| PDT guard              | < 3 day-trades in 5 days | always on |
| EOD flatten            | hardcoded                | 15:30 ET  |

---

## Agent Loop

```
while market_is_open():
    for each symbol:
        prices = fetch_latest_1min_bars()
        trade_signal = strategy.evaluate(prices)
        if signal == BUY and no open position:
            risk_manager.approve() → order_manager.execute()
            monitor.add_position()
        elif signal == SELL and position open:
            order_manager.execute() → monitor.remove_position()
    sleep(60s)

# Background thread (PositionMonitor):
    every 5s: check stop-loss, profit-target, trailing-stop, time-stop, EOD
```

---

## Backtesting

```bash
# 1 year of daily bars (default)
python3 scripts/run_backtest.py --strategy Scalper_EMA2

# 10 days of 1-minute bars
python3 scripts/run_backtest.py --strategy Scalper_EMA3 --mode intraday

# Custom date range — daily bars
python3 scripts/run_backtest.py --start 2025-01-01 --end 2025-06-30

# Custom date range — 1-minute bars
python3 scripts/run_backtest.py --mode intraday --start 2026-02-17 --end 2026-02-28
```

**Fill model:** Signal evaluated at bar close → fill at next bar's open (no look-ahead bias).
**Intra-bar checks:** Stop-loss and profit-target tested against bar low/high before signal.

**Metrics reported:** Total P&L, win rate, avg win/loss, profit factor, Sharpe ratio (annualised), max drawdown, avg hold time, exit breakdown.

---

## Logging

Logs are written to `logs/` and rotate daily. Retained for 30 days.

| Level      | Events                            |
| ---------- | --------------------------------- |
| `TRACE`    | Raw EMA values, every bar         |
| `DEBUG`    | Signal generation details         |
| `INFO`     | Orders, position opens/closes     |
| `WARNING`  | Risk checks triggered, stop exits |
| `ERROR`    | API failures                      |
| `CRITICAL` | Kill switch activated             |

---

## Security Notes

- `schwab_trader.db` contains plaintext API keys and OAuth tokens — **always in `.gitignore`**
- Never commit `.env`, `bot.pid`, `state/`, or `logs/`
- Paper trading is the default — verify results before enabling live orders

---

## Disclaimer

This bot is for **educational purposes**. Day trading carries significant financial risk. Paper trade and backtest thoroughly before using real capital. Ensure compliance with Schwab's API terms and FINRA/SEC regulations.

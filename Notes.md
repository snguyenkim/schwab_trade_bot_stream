# Development Notes

## 2026-03-03 — Replace polling loop with WebSocket streaming

### Problem
The original bot used a blocking `while market_is_open()` loop in `main.py` that:
- Called `client.get_price_history()` via REST on every tick (every `frequency` minutes)
- `PositionMonitor` separately polled `client.get_quotes()` every 5 seconds per open position

This is inefficient and introduces latency relative to actual market events.

---

### Changes

#### `data/stream_feed.py` *(new file)*
New `StreamFeed` class that drives the entire trading loop from WebSocket events:

1. **Seed** — on startup, fetches historical candles via REST once to warm up EMA price buffers
2. **`CHART_EQUITY` subscription** — callback fires on every new 1-min bar close:
   - Appends close price to the symbol's rolling buffer
   - Updates `latest_prices` cache
   - Evaluates the EMA strategy
   - Executes BUY/SELL via `OrderManager` if signal + risk approved
   - Checks daily kill switch
3. **`LEVELONE_EQUITIES` subscription** — callback fires on every live tick:
   - Updates `latest_prices[symbol]` with the latest last/mark/ask price
   - This dict is shared with `PositionMonitor` to avoid REST polling

#### `portfolio/position_monitor.py` *(modified)*
- `__init__` now accepts an optional `price_cache: dict | None = None` parameter
- `_get_price(symbol)` checks `self._price_cache` first (streamed price, zero extra API calls)
- Falls back to REST `client.get_quotes()` if the cache is empty (maintains backwards compatibility)

#### `main.py` *(rewritten)*
- Converted from sync `main()` to `async def run_bot()` / `asyncio.run()`
- Fetches `user_prefs = client.get_user_preferences()` to get `StreamerInfo`
- Instantiates `SchwabStreamer` and calls `await streamer.connect()`
- Passes `feed.latest_prices` to `PositionMonitor` so both share the same live price dict
- Replaces `time.sleep(tick_interval)` polling loop with `await feed.run()`

---

### Architecture: Before vs After

**Before (poll-based):**
```
main loop (every N minutes):
  REST → get_price_history()   ← blocking HTTP call
  strategy.evaluate()
  order_manager.execute()

position monitor thread (every 5s):
  REST → get_quotes()          ← blocking HTTP call per position
  check stop/target/trailing
```

**After (stream-based):**
```
WebSocket → CHART_EQUITY bar close
  buffer.append(close)
  strategy.evaluate()          ← event-driven, no polling
  order_manager.execute()

WebSocket → LEVELONE_EQUITIES tick
  latest_prices[symbol] = price  ← shared dict, zero REST calls

position monitor thread (every 5s):
  latest_prices[symbol]        ← reads from shared cache, no HTTP
  check stop/target/trailing
```

---

### Key files
| File | Role |
|------|------|
| `data/stream_feed.py` | StreamFeed — WebSocket event handler + strategy trigger |
| `data/market_data.py` | MarketData — REST API, used only for initial buffer seeding |
| `portfolio/position_monitor.py` | PositionMonitor — uses streamed price cache |
| `main.py` | Entry point — async, connects streamer, starts feed |
| `schwab/streaming.py` | SchwabStreamer — WebSocket client (library) |

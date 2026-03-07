# Partial Updates Architecture Plan

## What (Current vs Target)

### Current Architecture
```
Delta WS tick → ticker_store.update() → every 200ms timer fires
→ rebuild FULL chain (all 80 strikes) → send 50KB JSON to browser
→ React replaces entire state → all 80 rows checked for re-render
```

### Target Architecture
```
Delta WS tick for symbol X → recompute only symbol X leg
→ send 200 byte patch { symbol, strike, type, mark, bid, ask, greeks }
→ frontend Map.set(strike, updatedRow) → only that 1 row re-renders
```

---

## Why

| Metric | Full Update (current) | Partial Update (target) |
|---|---|---|
| Data per push | ~50KB | ~200 bytes |
| Push frequency | Every 200ms always | Only on price change |
| Frontend re-renders | All 80 rows | 1 row |
| Latency | Up to 200ms behind | ~5ms behind |
| Bandwidth | ~250KB/sec | ~2KB/sec |

- Delta pushes ~5-10 option updates per second that actually changed
- Full update sends redundant data for 150+ options that did NOT change
- Partial update sends only what changed — same as Bloomberg, Zerodha Kite

---

## How — Implementation Plan

### Phase 1: Backend Changes

#### 1. New WS message types
```python
# Initial full chain load (sent once on connect)
{ "type": "snapshot", "expiry": "2026-03-08", "chain": [...all rows...] }

# Incremental tick update (sent per symbol change)
{ "type": "tick", "strike": 68000, "option_type": "call",
  "symbol": "C-BTC-68000-080326",
  "mark_price": 320.5, "bid": 315.0, "ask": 326.0,
  "iv_pct": 32.8, "delta": 0.43, "gamma": 0.00035,
  "theta": -241.7, "vega": 13.2, "oi_usd": 6844.0, "volume_usd": 500.0 }
```

#### 2. Backend WS endpoint (`api/ws.py`)
```python
@router.websocket("/ws/chain")
async def chain_ws(websocket: WebSocket, expiry: str = Query(...)):
    await websocket.accept()
    expiry_date = date.fromisoformat(expiry)

    # Step 1: Send full snapshot once
    chain = await get_option_chain_from_store(expiry_date)
    await websocket.send_text(json.dumps({"type": "snapshot", ...chain}))

    # Step 2: Subscribe to ticker_store changes for this expiry
    # Use asyncio.Queue — delta_ws_client puts updates, we consume them
    queue = asyncio.Queue()
    ticker_store.subscribe(expiry_date, queue)

    try:
        while True:
            tick = await queue.get()  # blocks until Delta pushes something
            await websocket.send_text(json.dumps({"type": "tick", **tick}))
    except WebSocketDisconnect:
        ticker_store.unsubscribe(expiry_date, queue)
```

#### 3. ticker_store.py — add pub/sub
```python
# Subscribers: expiry_date -> list of asyncio.Queue
_subscribers: dict[date, list[asyncio.Queue]] = {}

def subscribe(expiry: date, queue: asyncio.Queue):
    _subscribers.setdefault(expiry, []).append(queue)

def unsubscribe(expiry: date, queue: asyncio.Queue):
    if expiry in _subscribers:
        _subscribers[expiry].discard(queue)

def update_ticker(symbol: str, data: dict) -> None:
    _tickers[symbol] = data
    # Notify subscribers for this symbol's expiry
    expiry = _get_expiry_from_symbol(symbol)  # parse from symbol name
    for queue in _subscribers.get(expiry, []):
        queue.put_nowait(build_tick_payload(symbol, data))
```

#### 4. Recompute only changed leg
```python
def build_tick_payload(symbol: str, ticker: dict) -> dict:
    # Parse strike and type from symbol e.g. C-BTC-68000-080326
    parts = symbol.split("-")
    opt_type = "call" if parts[0] == "C" else "put"
    strike = float(parts[2])
    # Recompute greeks for just this one leg
    spot = get_spot()
    T = _years_to_expiry(...)
    iv = float(ticker.get("quotes", {}).get("mark_iv") or 0.5)
    g = compute_greeks(spot, strike, T, r, iv, opt_type)
    return {
        "symbol": symbol, "strike": strike, "option_type": opt_type,
        "mark_price": ..., "bid": ..., "ask": ...,
        "delta": g.delta, "gamma": g.gamma, ...
    }
```

---

### Phase 2: Frontend Changes

#### 1. State as Map instead of array
```typescript
// Instead of:
const [data, setData] = useState<OptionChainResponse | null>(null)

// Use:
const [snapshot, setSnapshot] = useState<OptionChainResponse | null>(null)
const chainMap = useRef<Map<number, ChainRow>>(new Map())
// key = strike price, value = ChainRow
```

#### 2. Handle snapshot vs tick messages
```typescript
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data)

  if (msg.type === 'snapshot') {
    // Initial load — populate map
    setSnapshot(msg)
    msg.chain.forEach((row: ChainRow) => chainMap.current.set(row.strike, row))
    setRows([...chainMap.current.values()])
  }

  if (msg.type === 'tick') {
    // Patch only the changed leg
    const row = chainMap.current.get(msg.strike) ?? { strike: msg.strike }
    const updatedRow = {
      ...row,
      [msg.option_type === 'call' ? 'call' : 'put']: {
        mark_price: msg.mark_price, bid: msg.bid, ask: msg.ask,
        delta: msg.delta, gamma: msg.gamma, ...
      }
    }
    chainMap.current.set(msg.strike, updatedRow)
    // Trigger re-render for only this row using row-level state
    setRows(prev => prev.map(r => r.strike === msg.strike ? updatedRow : r))
  }
}
```

#### 3. Row-level memoization
```typescript
// Memoize each row so unchanged rows don't re-render
const ChainRowComponent = React.memo(({ row }: { row: ChainRow }) => {
  return <tr>...</tr>
}, (prev, next) => prev.row === next.row)  // reference equality check
```

---

## Symbol Parsing (C-BTC-68000-080326)
```
C       → call (P = put)
BTC     → underlying
68000   → strike price
080326  → expiry date (DDMMYY = 08 March 2026)
```

---

## What to Keep from Current Architecture
- Black-Scholes greeks computation (verified match with Delta)
- ticker_store in-memory store
- Products list in-memory (no Redis hits)
- India endpoint (api.india.delta.exchange)
- Single uvicorn worker

## What Changes
- Remove 200ms polling timer in ws.py
- Add pub/sub to ticker_store
- New message format (snapshot + tick)
- Frontend Map state instead of full replace

---

## Priority
Implement this when:
- Multiple users connected simultaneously (bandwidth matters)
- UI feels laggy or flickery
- Moving to production deployment

For single-user local dev, current architecture is acceptable.

# Claude Instructions — BTC Options Platform

## Endpoints
- Delta REST: `https://api.india.delta.exchange`
- Delta WebSocket: `wss://socket.india.delta.exchange`
- Always use India region endpoints — matches user's trading account

## Architecture Rules
- Backend runs with `--workers 1` (single uvicorn worker) — required for in-memory ticker_store
- Never increase workers without switching to Redis-backed ticker store
- Frontend is Vite dev server on port 3000 (not containerised)

## Branch Strategy
- `main` — full featured (IV chart, Premium chart)
- `feature/chain-only-no-charts` — clean chain-only for testing/new development

## After Any Code Change
1. Rebuild backend: `cd docker && docker compose up --build -d backend`
2. Restart frontend: `fuser -k 3000/tcp && cd frontend && npm run dev`
3. Commit and push to current branch

## Key Facts
- Greeks computed with Black-Scholes server-side (verified match with Delta's live greeks)
- OI and Volume displayed in USD using Delta's `oi_value_usd` and `turnover_usd` fields
- WS product list refreshed every 1 hour (new expiries added weekly)
- Settlement time: 5:30 PM IST = 12:00 UTC
- Contract size: 0.001 BTC per contract

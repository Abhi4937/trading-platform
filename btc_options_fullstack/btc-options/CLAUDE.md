# Claude Instructions — BTC Options Platform

## RULE #1 — Always Ask Before Making Changes
Before editing ANY file (code, config, docs, anything):
1. Describe what you plan to change and why
2. Ask "shall I proceed?" and wait for confirmation
Do NOT assume a question about behavior is a request to fix it.

## Session Start Checklist (do this first, every session)
1. Read `HANDOFF.md` — who worked last, what changed, what's pending
2. Read `docs/memories/current_state.md` — active tasks and open issues
3. Read `docs/memories/work_log_gemini.md` — what Gemini did (avoid conflicts)
4. Then ask the user what they want to work on

## Handoff Protocol (do this at end of every session)
1. Update `HANDOFF.md` — fill in: who worked, what files changed, what's pending
2. Update `docs/memories/work_log_claude.md` — append what was done
3. Update `docs/memories/current_state.md` — if anything changed
4. Tell the user: "Ready to hand off to Gemini — HANDOFF.md is updated"

## AI Collaboration
- Claude and Gemini take turns on this codebase
- Gemini reads the same `HANDOFF.md` and `docs/memories/` files
- Never overwrite a file Gemini touched without reading it first
- Check `git status` before starting work to see Gemini's uncommitted changes


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
- **Frontend change:** `fuser -k 3000/tcp && cd frontend && npm run dev`
- **Backend change:** `cd docker && docker compose up --build -d backend` then restart frontend
- Always restart/rebuild immediately after making changes — do not wait for user to ask
- Once the user confirms the change works, commit and push to current branch immediately

## Key Facts
- Greeks computed with Black-Scholes server-side (verified match with Delta's live greeks)
- OI and Volume displayed in USD using Delta's `oi_value_usd` and `turnover_usd` fields
- WS product list refreshed every 1 hour (new expiries added weekly)
- Settlement time: 5:30 PM IST = 12:00 UTC
- Contract size: 0.001 BTC per contract

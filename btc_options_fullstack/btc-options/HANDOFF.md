# Handoff Log

## Last Session
**Who:** Claude
**Date:** 2026-03-22
**Branch:** `mainbranch-gemini`
**GitHub:** Branch is UP TO DATE with origin — all committed work is pushed

---

## What Was Done (since last handoff 2026-03-13)

### Committed (pushed to GitHub)
- `2576a9c` — style: compact historical dashboard top bar (merge spot+search inline, reduce padding)
- `2baaf30` — style: maximize chart real estate (flush axes, dynamic height, subtle crosshair)
- `3eaccd9` — **feat: Strategy Builder** with MTM P&L chart for historical simulation
- `b7bb854` — fix: zero out Greeks when mark price is 0 (no more phantom Greeks)
- `0f6125e` — **feat: portfolio margin engine** + live margin display in Strategy Builder
- `502bdea` — fix: skip zero-IV legs in margin engine (was fabricating 50% IV, now excluded)
- `d0a2904` — style: clarify lot size in strategy builder (show BTC equivalent per leg)

### Uncommitted Changes (working tree dirty — NOT yet committed)
- `backend/app/api/historical.py` — removed stale in-memory caches for `_cached_data_range` and `_cached_latest_data` (they were causing stale data issues; filesystem scan is fast enough without them)
- `frontend/src/pages/HistoricalDashboard.tsx` — UTC timezone fix: use `simDate + 'T00:00:00Z'` to avoid local-timezone date shifts when generating expiry dates
- `frontend/src/components/historical/StrategyPanel.tsx` — minor tweak (likely lot-size display or margin display polish)
- `frontend/src/utils/marginEngine.ts` — minor tweak (likely related to zero-IV leg skip display)

**These 4 files need to be reviewed and committed.**

---

## Current Architecture State
- **Strategy Builder** is live: add legs (strike, expiry, type, qty), see live margin + MTM P&L chart
- **Margin Engine** (`marginEngine.ts`): SPAN-style portfolio margin, skips legs with IV=0
- **Historical simulation**: date picker, expiry selector, time scrubber, option chain, MTM P&L chart
- **Backend** single uvicorn worker (no Redis, in-memory ticker_store)

---

## Pending / Next Up
- [ ] **Commit the 4 uncommitted files** — review diffs, write commit message
- [ ] Partial updates implementation (Gemini's plan in `docs/partial-updates-plan.md`)
  - `ticker_store.py` — add pub/sub (subscribe/unsubscribe per expiry)
  - `ws.py` — send snapshot once, then tick diffs only
  - `useOptionChain.ts` — Map<strike, row> state + React.memo per row
- [ ] Historical auto-play (play button with setInterval)
- [ ] Spot price via WS (add BTCUSDT to existing Delta WS subscription)

---

## Key Decisions Made
- Partial updates plan approved in principle — implement when ready
- Single worker constraint stays (in-memory ticker_store, no Redis)
- India region endpoints only
- Zero-IV legs are EXCLUDED from margin calc (not faked with 50% IV) — UI shows warning count

---

## Note for Gemini
- Strategy Builder is new — see `frontend/src/components/historical/StrategyPanel.tsx` and `frontend/src/utils/marginEngine.ts`
- Uncommitted changes in 4 files above — check `git diff` before touching them
- UTC timezone fix in HistoricalDashboard.tsx is uncommitted — don't revert it

---

## Quick Commands
```bash
# Check what's uncommitted
git diff --stat

# Rebuild backend after any backend change
cd docker && docker compose up --build -d backend

# Restart frontend
fuser -k 3000/tcp && cd frontend && npm run dev
```

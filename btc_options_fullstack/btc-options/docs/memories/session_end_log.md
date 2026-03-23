# Session End Snapshots

Auto-updated by PreCompact hook when context nears limit.

---
## Snapshot — 2026-03-22 18:31 (branch: mainbranch-gemini)

### Recent Commits (last 10)
```
d0a2904 style: clarify lot size in strategy builder — show BTC equivalent per leg
502bdea fix: skip zero-IV legs in margin engine instead of fabricating with 50% IV
0f6125e feat: portfolio margin engine + live margin display in Strategy Builder
b7bb854 fix: zero out Greeks when mark price is 0 — no more phantom Greeks
3eaccd9 feat: Strategy Builder with MTM P&L chart for historical simulation
2baaf30 style: maximize chart real estate — flush axes, dynamic height, subtle crosshair
2576a9c style: compact historical dashboard top bar — merge spot+search inline, reduce padding
cc63bc2 chore: update HANDOFF.md with latest commits
3ef2d87 feat: historical chart UX improvements
2898c66 fix: corrupted variable names in option_chain_service + parallelize IV solver
```

### Uncommitted Changes (git diff --stat)
```
 btc_options_fullstack/btc-options/HANDOFF.md       | 76 ++++++++++++++++------
 .../btc-options/backend/app/api/historical.py      | 17 +----
 .../src/components/historical/StrategyPanel.tsx    |  6 +-
 .../frontend/src/pages/HistoricalDashboard.tsx     | 61 +++++++++++++----
 .../btc-options/frontend/src/utils/marginEngine.ts |  4 +-
 5 files changed, 111 insertions(+), 53 deletions(-)
```

### Git Status
```
 M HANDOFF.md
 M backend/app/api/historical.py
 M frontend/src/components/historical/StrategyPanel.tsx
 M frontend/src/pages/HistoricalDashboard.tsx
 M frontend/src/utils/marginEngine.ts
```

---
## Snapshot — 2026-03-22 23:16 (branch: mainbranch-gemini)

### Recent Commits (last 10)
```
a6059e6 feat: responsive UI, lot stepper, B/S fixes, date picker max fix
10c31a0 fix: clear MTM chart when legs change — prevents stale P&L after adding/removing legs
27d8014 feat: MTM chart hover tooltip — P&L value + time (IST) on crosshair move
80d6670 fix: download uses final MTM price per leg instead of live simulation price
b37f8b3 feat: Download ▾ dropdown on Strategy Builder — exports CSV (2 files) or Excel (2 sheets)
3d9b899 fix: fillna(0) on chart-data OHLC to prevent NaN JSON serialization crash (500 → CORS error)
85ccb5f feat: MTM timeframe selector (1m/5m/15m/30m/1h) + end date/time range picker
ad1ebd8 feat: show B/S badge on strike cell — left for CE leg, right for PE leg
2258dfc feat: show B/S buttons on hover only + highlight rows with active strategy legs
2c9b525 fix: center chain both vertically (ATM row) and horizontally (Strike column) on load/tab switch
```

### Uncommitted Changes (git diff --stat)
```
 .../frontend/src/pages/HistoricalDashboard.tsx     | 110 +++++++++++----------
 1 file changed, 58 insertions(+), 52 deletions(-)
```

### Git Status
```
 M frontend/src/pages/HistoricalDashboard.tsx
?? "UI ss/"
```

---
## Snapshot — 2026-03-22 23:16 (branch: mainbranch-gemini)

### Recent Commits (last 10)
```
a6059e6 feat: responsive UI, lot stepper, B/S fixes, date picker max fix
10c31a0 fix: clear MTM chart when legs change — prevents stale P&L after adding/removing legs
27d8014 feat: MTM chart hover tooltip — P&L value + time (IST) on crosshair move
80d6670 fix: download uses final MTM price per leg instead of live simulation price
b37f8b3 feat: Download ▾ dropdown on Strategy Builder — exports CSV (2 files) or Excel (2 sheets)
3d9b899 fix: fillna(0) on chart-data OHLC to prevent NaN JSON serialization crash (500 → CORS error)
85ccb5f feat: MTM timeframe selector (1m/5m/15m/30m/1h) + end date/time range picker
ad1ebd8 feat: show B/S badge on strike cell — left for CE leg, right for PE leg
2258dfc feat: show B/S buttons on hover only + highlight rows with active strategy legs
2c9b525 fix: center chain both vertically (ATM row) and horizontally (Strike column) on load/tab switch
```

### Uncommitted Changes (git diff --stat)
```
 .../btc-options/docs/memories/session_end_log.md   |  29 ++++++
 .../frontend/src/pages/HistoricalDashboard.tsx     | 110 +++++++++++----------
 2 files changed, 87 insertions(+), 52 deletions(-)
```

### Git Status
```
 M docs/memories/session_end_log.md
 M frontend/src/pages/HistoricalDashboard.tsx
?? "UI ss/"
```

---
## Snapshot — 2026-03-23 00:52 (branch: mainbranch-gemini_claude_compare)

### Recent Commits (last 10)
```
da644eb feat: Compare button — per-leg MTM curves on shared chart
499fa12 docs: add RULE #2 — commit and push before starting new work
1d4965b feat: multi-expiry strategy support — live P&L for all legs across expiries
110bfde style: tighten fonts/spacing + fix multi-expiry current price bug + add expiry column
6bf6ce3 style: increase chart/strategy builder panel width, shrink chain panel
5d5ffcd feat: add Greeks columns to strategy table + wider panel
a6059e6 feat: responsive UI, lot stepper, B/S fixes, date picker max fix
10c31a0 fix: clear MTM chart when legs change — prevents stale P&L after adding/removing legs
27d8014 feat: MTM chart hover tooltip — P&L value + time (IST) on crosshair move
80d6670 fix: download uses final MTM price per leg instead of live simulation price
```

### Uncommitted Changes (git diff --stat)
```
 .../src/components/historical/StrategyPanel.tsx    | 520 +++++++++------------
 .../frontend/src/pages/HistoricalDashboard.tsx     | 101 ++--
 .../btc-options/frontend/src/types/strategy.ts     |   6 +
 3 files changed, 292 insertions(+), 335 deletions(-)
```

### Git Status
```
 M frontend/src/components/historical/StrategyPanel.tsx
 M frontend/src/pages/HistoricalDashboard.tsx
 M frontend/src/types/strategy.ts
?? "UI ss/"
```

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

---
## Snapshot — 2026-03-25 04:55 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
d646676 feat: MTM statistics panel — max/min P&L, max drawdown, all drawdown periods
c9d67bf feat: fix Greeks scaling + add Gamma to strategy table and net portfolio summary
8e8ba0f refactor: separate build and compare modes — clean single-strategy builder + dedicated compare view
5571411 feat: multi-strategy compare — add/switch strategies, MTM curves per strategy on shared chart
da644eb feat: Compare button — per-leg MTM curves on shared chart
499fa12 docs: add RULE #2 — commit and push before starting new work
1d4965b feat: multi-expiry strategy support — live P&L for all legs across expiries
110bfde style: tighten fonts/spacing + fix multi-expiry current price bug + add expiry column
6bf6ce3 style: increase chart/strategy builder panel width, shrink chain panel
5d5ffcd feat: add Greeks columns to strategy table + wider panel
```

### Uncommitted Changes (git diff --stat)
```
 .../src/components/historical/StrategyPanel.tsx    | 23 ++++++++++++++++++++-
 .../frontend/src/pages/HistoricalDashboard.tsx     | 24 ++++++++++++++--------
 2 files changed, 38 insertions(+), 9 deletions(-)
```

### Git Status
```
 M frontend/src/components/historical/StrategyPanel.tsx
 M frontend/src/pages/HistoricalDashboard.tsx
?? "UI ss/"
```

---
## Snapshot — 2026-04-27 11:10 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
d1c184d feat: HV chart, IV-HV spread, slippage model + entry-spot context
03289e4 fix: IV pane shrink on Delta toggle + hide replay bar on maximize
18669c3 fix: compute Greeks T from bucket END not bucket START in chart-data-with-greeks
4ea3a65 docs: clarify restart rules — backend change requires both backend rebuild AND frontend restart
73dbc4d feat: unified MultiPaneChart (MTM + IV + Delta) + chartsOnly focus mode
c0c38a3 feat: Greeks time series in MTM download — per-leg + net portfolio
04fcea8 feat: draggable panel divider + zero reference line on compare chart
06637b8 feat: compare mode collapsible legs + draggable chart resize + DD sort by time
251fab5 feat: maximize strategy builder + fix expiry after settlement
d646676 feat: MTM statistics panel — max/min P&L, max drawdown, all drawdown periods
```

### Uncommitted Changes (git diff --stat)
```
 .../btc-options/backend/app/api/historical.py      |  53 ++++---
 .../src/components/historical/MultiPaneChart.tsx   | 130 ++++++++--------
 .../src/components/historical/StrategyPanel.tsx    | 110 +++++++------
 .../frontend/src/services/historical_api.ts        |   4 +-
 .../btc-options/frontend/src/types/historical.ts   |   2 +-
 .../btc-options/frontend/src/utils/slippage.ts     | 173 ++++++++++-----------
 6 files changed, 245 insertions(+), 227 deletions(-)
```

### Git Status
```
 M backend/app/api/historical.py
 M frontend/src/components/historical/MultiPaneChart.tsx
 M frontend/src/components/historical/StrategyPanel.tsx
 M frontend/src/services/historical_api.ts
 M frontend/src/types/historical.ts
 M frontend/src/utils/slippage.ts
?? "Slipage calculation SS/"
?? "UI ss/"
?? margin-calculator.jsx
?? modify-codebase-for-deployment.md
?? "../../new platform arch/"
```

---
## Snapshot — 2026-04-27 11:29 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
730b35d feat: HV → RV (daily-return realized vol, GVOL/Delta convention)
d1c184d feat: HV chart, IV-HV spread, slippage model + entry-spot context
03289e4 fix: IV pane shrink on Delta toggle + hide replay bar on maximize
18669c3 fix: compute Greeks T from bucket END not bucket START in chart-data-with-greeks
4ea3a65 docs: clarify restart rules — backend change requires both backend rebuild AND frontend restart
73dbc4d feat: unified MultiPaneChart (MTM + IV + Delta) + chartsOnly focus mode
c0c38a3 feat: Greeks time series in MTM download — per-leg + net portfolio
04fcea8 feat: draggable panel divider + zero reference line on compare chart
06637b8 feat: compare mode collapsible legs + draggable chart resize + DD sort by time
251fab5 feat: maximize strategy builder + fix expiry after settlement
```

### Uncommitted Changes (git diff --stat)
```
 .../btc-options/docs/memories/session_end_log.md   |  43 +++++
 .../src/components/historical/StrategyPanel.tsx    | 116 ++++++++++----
 .../btc-options/frontend/src/utils/slippage.ts     | 173 ++++++++++-----------
 3 files changed, 217 insertions(+), 115 deletions(-)
```

### Git Status
```
 M docs/memories/session_end_log.md
 M frontend/src/components/historical/StrategyPanel.tsx
 M frontend/src/utils/slippage.ts
?? "Slipage calculation SS/"
?? "UI ss/"
?? margin-calculator.jsx
?? modify-codebase-for-deployment.md
?? "../../new platform arch/"
```

---
## Snapshot — 2026-04-27 13:49 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
2362b15 fix: revert RV default to 7d (30d was too large for weekly contracts)
48dee87 feat: entry/exit slippage split + brokerage model + peak exit P&L
ed70ad9 feat: RV — intraday rolling window + full contract lifetime for IV/RV panes
730b35d feat: HV → RV (daily-return realized vol, GVOL/Delta convention)
d1c184d feat: HV chart, IV-HV spread, slippage model + entry-spot context
03289e4 fix: IV pane shrink on Delta toggle + hide replay bar on maximize
18669c3 fix: compute Greeks T from bucket END not bucket START in chart-data-with-greeks
4ea3a65 docs: clarify restart rules — backend change requires both backend rebuild AND frontend restart
73dbc4d feat: unified MultiPaneChart (MTM + IV + Delta) + chartsOnly focus mode
c0c38a3 feat: Greeks time series in MTM download — per-leg + net portfolio
```

### Uncommitted Changes (git diff --stat)
```
 .../btc-options/docs/memories/session_end_log.md   |  80 +++++++++
 .../src/components/historical/MultiPaneChart.tsx   |  13 ++
 .../btc-options/frontend/src/utils/marginEngine.ts | 200 +++++++++++++++------
 3 files changed, 234 insertions(+), 59 deletions(-)
```

### Git Status
```
 M docs/memories/session_end_log.md
 M frontend/src/components/historical/MultiPaneChart.tsx
 M frontend/src/utils/marginEngine.ts
?? "Slipage calculation SS/"
?? "UI ss/"
?? margin-calculator.jsx
?? margin_check.py
?? modify-codebase-for-deployment.md
?? "../../new platform arch/"
```

---
## Snapshot — 2026-04-27 19:23 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
2362b15 fix: revert RV default to 7d (30d was too large for weekly contracts)
48dee87 feat: entry/exit slippage split + brokerage model + peak exit P&L
ed70ad9 feat: RV — intraday rolling window + full contract lifetime for IV/RV panes
730b35d feat: HV → RV (daily-return realized vol, GVOL/Delta convention)
d1c184d feat: HV chart, IV-HV spread, slippage model + entry-spot context
03289e4 fix: IV pane shrink on Delta toggle + hide replay bar on maximize
18669c3 fix: compute Greeks T from bucket END not bucket START in chart-data-with-greeks
4ea3a65 docs: clarify restart rules — backend change requires both backend rebuild AND frontend restart
73dbc4d feat: unified MultiPaneChart (MTM + IV + Delta) + chartsOnly focus mode
c0c38a3 feat: Greeks time series in MTM download — per-leg + net portfolio
```

### Uncommitted Changes (git diff --stat)
```
 .../btc-options/docs/memories/session_end_log.md   | 118 ++++++++
 .../btc-options/frontend/src/App.css               |   5 +
 .../src/components/historical/MultiPaneChart.tsx   |  13 +
 .../src/components/historical/StrategyPanel.tsx    | 138 +++++----
 .../btc-options/frontend/src/utils/marginEngine.ts | 333 ++++++++++++++++-----
 5 files changed, 481 insertions(+), 126 deletions(-)
```

### Git Status
```
 M docs/memories/session_end_log.md
 M frontend/src/App.css
 M frontend/src/components/historical/MultiPaneChart.tsx
 M frontend/src/components/historical/StrategyPanel.tsx
 M frontend/src/utils/marginEngine.ts
?? "Slipage calculation SS/"
?? "UI ss/"
?? frontend/src/components/historical/PayoffGraph.tsx
?? margin-calculator.jsx
?? margin_check.py
?? modify-codebase-for-deployment.md
?? scripts/
?? "../../new platform arch/"
```

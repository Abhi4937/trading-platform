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

---
## Snapshot — 2026-04-30 09:41 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
bb5adab feat: per-leg margin lookup, compare-mode margin, payoff slider-only, ATM IV fallback
2362b15 fix: revert RV default to 7d (30d was too large for weekly contracts)
48dee87 feat: entry/exit slippage split + brokerage model + peak exit P&L
ed70ad9 feat: RV — intraday rolling window + full contract lifetime for IV/RV panes
730b35d feat: HV → RV (daily-return realized vol, GVOL/Delta convention)
d1c184d feat: HV chart, IV-HV spread, slippage model + entry-spot context
03289e4 fix: IV pane shrink on Delta toggle + hide replay bar on maximize
18669c3 fix: compute Greeks T from bucket END not bucket START in chart-data-with-greeks
4ea3a65 docs: clarify restart rules — backend change requires both backend rebuild AND frontend restart
73dbc4d feat: unified MultiPaneChart (MTM + IV + Delta) + chartsOnly focus mode
```

### Uncommitted Changes (git diff --stat)
```

```

### Git Status
```
?? "Slipage calculation SS/"
?? "UI ss/"
?? margin-calculator.jsx
?? margin_check.py
?? modify-codebase-for-deployment.md
?? scripts/
?? "../../new platform arch/"
```

---
## Snapshot — 2026-04-30 09:51 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
bb5adab feat: per-leg margin lookup, compare-mode margin, payoff slider-only, ATM IV fallback
2362b15 fix: revert RV default to 7d (30d was too large for weekly contracts)
48dee87 feat: entry/exit slippage split + brokerage model + peak exit P&L
ed70ad9 feat: RV — intraday rolling window + full contract lifetime for IV/RV panes
730b35d feat: HV → RV (daily-return realized vol, GVOL/Delta convention)
d1c184d feat: HV chart, IV-HV spread, slippage model + entry-spot context
03289e4 fix: IV pane shrink on Delta toggle + hide replay bar on maximize
18669c3 fix: compute Greeks T from bucket END not bucket START in chart-data-with-greeks
4ea3a65 docs: clarify restart rules — backend change requires both backend rebuild AND frontend restart
73dbc4d feat: unified MultiPaneChart (MTM + IV + Delta) + chartsOnly focus mode
```

### Uncommitted Changes (git diff --stat)
```
 .../btc-options/docs/memories/session_end_log.md   | 33 ++++++++++++++++++++++
 1 file changed, 33 insertions(+)
```

### Git Status
```
 M docs/memories/session_end_log.md
?? "Slipage calculation SS/"
?? "UI ss/"
?? margin-calculator.jsx
?? margin_check.py
?? modify-codebase-for-deployment.md
?? scripts/
?? "../../new platform arch/"
```

---
## Snapshot — 2026-04-30 13:34 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
bb5adab feat: per-leg margin lookup, compare-mode margin, payoff slider-only, ATM IV fallback
2362b15 fix: revert RV default to 7d (30d was too large for weekly contracts)
48dee87 feat: entry/exit slippage split + brokerage model + peak exit P&L
ed70ad9 feat: RV — intraday rolling window + full contract lifetime for IV/RV panes
730b35d feat: HV → RV (daily-return realized vol, GVOL/Delta convention)
d1c184d feat: HV chart, IV-HV spread, slippage model + entry-spot context
03289e4 fix: IV pane shrink on Delta toggle + hide replay bar on maximize
18669c3 fix: compute Greeks T from bucket END not bucket START in chart-data-with-greeks
4ea3a65 docs: clarify restart rules — backend change requires both backend rebuild AND frontend restart
73dbc4d feat: unified MultiPaneChart (MTM + IV + Delta) + chartsOnly focus mode
```

### Uncommitted Changes (git diff --stat)
```
 .../btc-options/docs/memories/session_end_log.md   | 68 ++++++++++++++++++++++
 .../src/components/historical/StrategyPanel.tsx    | 67 +++++++++++++++++++++
 2 files changed, 135 insertions(+)
```

### Git Status
```
 M docs/memories/session_end_log.md
 M frontend/src/components/historical/StrategyPanel.tsx
?? "Slipage calculation SS/"
?? "UI ss/"
?? backend/app/api/backtest.py
?? backend/app/services/backtest.py
?? backend/app/services/backtest_jobs.py
?? backend/app/services/costs.py
?? backend/app/services/margin_v2.py
?? backend/app/services/option_data.py
?? frontend/src/utils/slippage_v2.ts
?? margin-calculator.jsx
?? margin_check.py
?? modify-codebase-for-deployment.md
?? scripts/
?? "../../new platform arch/"
```

---
## Snapshot — 2026-04-30 18:44 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
bb5adab feat: per-leg margin lookup, compare-mode margin, payoff slider-only, ATM IV fallback
2362b15 fix: revert RV default to 7d (30d was too large for weekly contracts)
48dee87 feat: entry/exit slippage split + brokerage model + peak exit P&L
ed70ad9 feat: RV — intraday rolling window + full contract lifetime for IV/RV panes
730b35d feat: HV → RV (daily-return realized vol, GVOL/Delta convention)
d1c184d feat: HV chart, IV-HV spread, slippage model + entry-spot context
03289e4 fix: IV pane shrink on Delta toggle + hide replay bar on maximize
18669c3 fix: compute Greeks T from bucket END not bucket START in chart-data-with-greeks
4ea3a65 docs: clarify restart rules — backend change requires both backend rebuild AND frontend restart
73dbc4d feat: unified MultiPaneChart (MTM + IV + Delta) + chartsOnly focus mode
```

### Uncommitted Changes (git diff --stat)
```
 .../btc-options/backend/app/main.py                |   3 +-
 .../btc-options/docs/memories/session_end_log.md   | 112 ++++++++++++++++++
 .../btc-options/frontend/src/App.tsx               |  41 +++++--
 .../src/components/historical/StrategyPanel.tsx    |  67 +++++++++++
 .../frontend/src/pages/HistoricalDashboard.tsx     | 129 ++++++++++++++++++++-
 5 files changed, 337 insertions(+), 15 deletions(-)
```

### Git Status
```
 M backend/app/main.py
 M docs/memories/session_end_log.md
 M frontend/src/App.tsx
 M frontend/src/components/historical/StrategyPanel.tsx
 M frontend/src/pages/HistoricalDashboard.tsx
?? "Slipage calculation SS/"
?? "UI ss/"
?? backend/app/api/backtest.py
?? backend/app/services/backtest.py
?? backend/app/services/backtest_jobs.py
?? backend/app/services/costs.py
?? backend/app/services/margin_engine_v2.py
?? backend/app/services/margin_engine_v2_constants.json
?? backend/app/services/margin_v2.py
?? backend/app/services/option_data.py
?? frontend/src/components/backtest/
?? frontend/src/hooks/usePersistedState.ts
?? frontend/src/pages/BacktestDashboard.tsx
?? frontend/src/services/backtest_api.ts
?? frontend/src/types/backtest.ts
?? frontend/src/utils/slippage_v2.ts
?? margin-calculator.jsx
?? margin_check.py
?? modify-codebase-for-deployment.md
?? scripts/
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-01 06:04 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
d79686c feat: multi-day backtester + persistence layer + slippage parity fix
bb5adab feat: per-leg margin lookup, compare-mode margin, payoff slider-only, ATM IV fallback
2362b15 fix: revert RV default to 7d (30d was too large for weekly contracts)
48dee87 feat: entry/exit slippage split + brokerage model + peak exit P&L
ed70ad9 feat: RV — intraday rolling window + full contract lifetime for IV/RV panes
730b35d feat: HV → RV (daily-return realized vol, GVOL/Delta convention)
d1c184d feat: HV chart, IV-HV spread, slippage model + entry-spot context
03289e4 fix: IV pane shrink on Delta toggle + hide replay bar on maximize
18669c3 fix: compute Greeks T from bucket END not bucket START in chart-data-with-greeks
4ea3a65 docs: clarify restart rules — backend change requires both backend rebuild AND frontend restart
```

### Uncommitted Changes (git diff --stat)
```
 btc_options_fullstack/btc-options/CLAUDE.md        |  23 ++
 btc_options_fullstack/btc-options/HANDOFF.md       | 101 ++++++++-
 .../btc-options/backend/app/api/backtest.py        |   6 +
 .../btc-options/backend/app/services/backtest.py   | 233 +++++++++++++++++----
 .../btc-options/docs/memories/current_state.md     |  31 +++
 .../btc-options/docs/memories/work_log_claude.md   |  49 +++++
 .../src/components/backtest/BacktestForm.tsx       |  40 ++++
 .../src/components/backtest/BacktestStatsPanel.tsx |  40 +++-
 .../components/backtest/BacktestTradeLogTable.tsx  |  60 +++++-
 .../src/components/historical/StrategyPanel.tsx    | 210 ++++++++++++++-----
 .../btc-options/frontend/src/types/backtest.ts     |  44 +++-
 11 files changed, 737 insertions(+), 100 deletions(-)
```

### Git Status
```
 M CLAUDE.md
 M HANDOFF.md
 M backend/app/api/backtest.py
 M backend/app/services/backtest.py
 M docs/memories/current_state.md
 M docs/memories/work_log_claude.md
 M frontend/src/components/backtest/BacktestForm.tsx
 M frontend/src/components/backtest/BacktestStatsPanel.tsx
 M frontend/src/components/backtest/BacktestTradeLogTable.tsx
 M frontend/src/components/historical/StrategyPanel.tsx
 M frontend/src/types/backtest.ts
?? "Slipage calculation SS/"
?? "UI ss/"
?? frontend/src/utils/slippage_v2.ts
?? margin-calculator.jsx
?? margin_check.py
?? modify-codebase-for-deployment.md
?? scripts/
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-02 12:10 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
1a48d4f docs: add UI ss/ — strangle spec + reference screenshots
0bd3863 feat: Module 1 — spot enrichment pipeline (analytics layer)
d79686c feat: multi-day backtester + persistence layer + slippage parity fix
bb5adab feat: per-leg margin lookup, compare-mode margin, payoff slider-only, ATM IV fallback
2362b15 fix: revert RV default to 7d (30d was too large for weekly contracts)
48dee87 feat: entry/exit slippage split + brokerage model + peak exit P&L
ed70ad9 feat: RV — intraday rolling window + full contract lifetime for IV/RV panes
730b35d feat: HV → RV (daily-return realized vol, GVOL/Delta convention)
d1c184d feat: HV chart, IV-HV spread, slippage model + entry-spot context
03289e4 fix: IV pane shrink on Delta toggle + hide replay bar on maximize
```

### Uncommitted Changes (git diff --stat)
```
 btc_options_fullstack/btc-options/CLAUDE.md        | 161 +++++++++
 btc_options_fullstack/btc-options/HANDOFF.md       |  86 ++++-
 .../btc-options/backend/app/api/backtest.py        |   8 +
 .../btc-options/backend/app/api/historical.py      | 241 +++++++++++-
 .../btc-options/backend/app/services/backtest.py   | 402 +++++++++++++++++++--
 .../backend/app/services/option_data.py            | 232 ++++++++++++
 .../btc-options/docs/memories/current_state.md     |  29 +-
 .../btc-options/docs/memories/session_end_log.md   |  56 +++
 .../btc-options/docs/memories/work_log_claude.md   |  84 +++++
 .../src/components/backtest/BacktestForm.tsx       | 100 ++++-
 .../src/components/backtest/BacktestStatsPanel.tsx |  40 +-
 .../components/backtest/BacktestTradeLogTable.tsx  | 331 +++++++++++------
 .../historical/HistoricalOptionChain.tsx           |  39 +-
 .../src/components/historical/StrategyPanel.tsx    | 210 ++++++++---
 .../frontend/src/pages/BacktestDashboard.tsx       |  69 +++-
 .../frontend/src/pages/HistoricalDashboard.tsx     | 104 +++++-
 .../frontend/src/services/historical_api.ts        |  33 ++
 .../btc-options/frontend/src/types/backtest.ts     |  53 ++-
 .../btc-options/frontend/src/types/historical.ts   |  37 ++
 19 files changed, 2099 insertions(+), 216 deletions(-)
```

### Git Status
```
 M CLAUDE.md
 M HANDOFF.md
 M backend/app/api/backtest.py
 M backend/app/api/historical.py
 M backend/app/services/backtest.py
 M backend/app/services/option_data.py
 M docs/memories/current_state.md
 M docs/memories/session_end_log.md
 M docs/memories/work_log_claude.md
 M frontend/src/components/backtest/BacktestForm.tsx
 M frontend/src/components/backtest/BacktestStatsPanel.tsx
 M frontend/src/components/backtest/BacktestTradeLogTable.tsx
 M frontend/src/components/historical/HistoricalOptionChain.tsx
 M frontend/src/components/historical/StrategyPanel.tsx
 M frontend/src/pages/BacktestDashboard.tsx
 M frontend/src/pages/HistoricalDashboard.tsx
 M frontend/src/services/historical_api.ts
 M frontend/src/types/backtest.ts
 M frontend/src/types/historical.ts
?? "Slipage calculation SS/"
?? backend/app/analytics/enrich_derived.py
?? backend/app/analytics/enrich_options.py
?? backend/app/services/indicators.py
?? backend/tests/test_enrich_derived.py
?? backend/tests/test_enrich_options.py
?? frontend/src/components/historical/IndicatorConfigPanel.tsx
?? frontend/src/components/historical/SpotChart.tsx
?? frontend/src/utils/slippage_v2.ts
?? margin-calculator.jsx
?? margin_check.py
?? modify-codebase-for-deployment.md
?? scripts/
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-03 15:30 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
92d4362 docs: M4 + M5 v2 + live recorder pipeline now fully operational
d9e3772 feat(types): add 'calibrated_v2' to BacktestTrade.quality_source enum
bd05f94 test(M5v2): add backfill_attribution unit tests
847da38 feat(M4+M5v2): batch backtester + outcome-aware calibration enrichment
58d67c2 fix(M2): per-expiry Stage A checkpoint to survive kills
b2597ac feat: market context snapshot + multi-TF confluence + quality fallback + start-platform.sh
8dad76c feat: extend calibration delta grid to include 0.30 and 0.50 (ATM)
9c3eb36 feat: live WS recorder + nightly merge job (data_live/ → data/)
7377822 feat: strangle analytics — credit%, decomposition, quality score, master ratios
8dac468 feat: Modules 2 + 3 — options enrichment + derived metrics/patterns
```

### Uncommitted Changes (git diff --stat)
```
 btc_options_fullstack/btc-options/CLAUDE.md        | 161 ++++++++++++++
 .../btc-options/backend/app/api/backtest.py        |   8 +
 .../backend/app/services/option_data.py            | 232 +++++++++++++++++++++
 .../btc-options/docs/memories/session_end_log.md   | 134 ++++++++++++
 .../src/components/backtest/BacktestForm.tsx       | 100 ++++++++-
 .../src/components/backtest/BacktestStatsPanel.tsx |  40 +++-
 .../historical/HistoricalOptionChain.tsx           |  39 +++-
 .../frontend/src/pages/HistoricalDashboard.tsx     | 104 ++++++++-
 .../btc-options/frontend/src/types/historical.ts   |  37 ++++
 9 files changed, 848 insertions(+), 7 deletions(-)
```

### Git Status
```
 M CLAUDE.md
 M backend/app/api/backtest.py
 M backend/app/services/option_data.py
 M docs/memories/session_end_log.md
 M frontend/src/components/backtest/BacktestForm.tsx
 M frontend/src/components/backtest/BacktestStatsPanel.tsx
 M frontend/src/components/historical/HistoricalOptionChain.tsx
 M frontend/src/pages/HistoricalDashboard.tsx
 M frontend/src/types/historical.ts
?? "Slipage calculation SS/"
?? backend/app/services/indicators.py
?? backend/app/services/live_signal_compute.py
?? frontend/src/components/historical/IndicatorConfigPanel.tsx
?? frontend/src/components/historical/SpotChart.tsx
?? frontend/src/utils/slippage_v2.ts
?? margin-calculator.jsx
?? margin_check.py
?? modify-codebase-for-deployment.md
?? scripts/calibrate_full.py
?? scripts/calibrate_loop.sh
?? scripts/calibrate_loop_v2.sh
?? scripts/calibrate_v2.py
?? scripts/calibration_constants.json
?? scripts/calibration_data.json
?? scripts/calibration_history.csv
?? scripts/calibration_report.xlsx
?? scripts/calibration_v2_history.csv
?? scripts/calibration_v2_report.xlsx
?? scripts/check_user_strategy.py
?? scripts/compare_margin_models.py
?? scripts/compare_results.csv
?? scripts/fit_margin_scale.py
?? scripts/fit_v2.py
?? scripts/friday_overnight_pnl.py
?? scripts/friday_overnight_pnl.xlsx
?? scripts/historical_margin.py
?? scripts/margin_engine.py
?? scripts/margin_engine_v2.py
?? scripts/margin_engine_v2_constants.json
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-05 11:09 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
454dc84 feat(M7): Friday→Saturday strangle/straddle sweep with rich 1m path + rule-based exit derivation
92d4362 docs: M4 + M5 v2 + live recorder pipeline now fully operational
d9e3772 feat(types): add 'calibrated_v2' to BacktestTrade.quality_source enum
bd05f94 test(M5v2): add backfill_attribution unit tests
847da38 feat(M4+M5v2): batch backtester + outcome-aware calibration enrichment
58d67c2 fix(M2): per-expiry Stage A checkpoint to survive kills
b2597ac feat: market context snapshot + multi-TF confluence + quality fallback + start-platform.sh
8dad76c feat: extend calibration delta grid to include 0.30 and 0.50 (ATM)
9c3eb36 feat: live WS recorder + nightly merge job (data_live/ → data/)
7377822 feat: strangle analytics — credit%, decomposition, quality score, master ratios
```

### Uncommitted Changes (git diff --stat)
```
 btc_options_fullstack/btc-options/CLAUDE.md        | 161 ++++++++++++++
 btc_options_fullstack/btc-options/HANDOFF.md       |  13 +-
 .../btc-options/backend/app/api/backtest.py        |   8 +
 .../btc-options/backend/app/api/historical.py      |  47 ++++-
 .../btc-options/backend/app/services/backtest.py   |   8 +-
 .../backend/app/services/live_recorder.py          |  17 ++
 .../backend/app/services/option_data.py            | 232 +++++++++++++++++++++
 .../backend/tests/test_calibration_api.py          |   7 +-
 .../btc-options/docs/memories/session_end_log.md   | 209 +++++++++++++++++++
 .../src/components/backtest/BacktestForm.tsx       | 100 ++++++++-
 .../src/components/backtest/BacktestStatsPanel.tsx |  40 +++-
 .../components/backtest/BacktestTradeLogTable.tsx  | 123 ++++++++++-
 .../historical/HistoricalOptionChain.tsx           |  39 +++-
 .../frontend/src/pages/HistoricalDashboard.tsx     | 104 ++++++++-
 .../btc-options/frontend/src/types/backtest.ts     |   1 +
 .../btc-options/frontend/src/types/historical.ts   |  37 ++++
 16 files changed, 1127 insertions(+), 19 deletions(-)
```

### Git Status
```
 M CLAUDE.md
 M HANDOFF.md
 M backend/app/api/backtest.py
 M backend/app/api/historical.py
 M backend/app/services/backtest.py
 M backend/app/services/live_recorder.py
 M backend/app/services/option_data.py
 M backend/tests/test_calibration_api.py
 M docs/memories/session_end_log.md
 M frontend/src/components/backtest/BacktestForm.tsx
 M frontend/src/components/backtest/BacktestStatsPanel.tsx
 M frontend/src/components/backtest/BacktestTradeLogTable.tsx
 M frontend/src/components/historical/HistoricalOptionChain.tsx
 M frontend/src/pages/HistoricalDashboard.tsx
 M frontend/src/types/backtest.ts
 M frontend/src/types/historical.ts
?? "../../backtest result for best expiry and delta/"
?? "Slipage calculation SS/"
?? backend/app/api/live_signal.py
?? backend/app/api/m4_results.py
?? backend/app/services/indicators.py
?? backend/app/services/live_signal_compute.py
?? backend/tests/test_live_signal.py
?? backend/tests/test_m4_api.py
?? docs/m4_findings_and_full_sweep_plan.md
?? docs/per_cell_trade_analysis.md
?? docs/weekly_next_to_next_analysis.md
?? frontend/src/components/historical/IndicatorConfigPanel.tsx
?? frontend/src/components/historical/SpotChart.tsx
?? frontend/src/components/m4/
?? frontend/src/pages/LiveSignalDashboard.tsx
?? frontend/src/pages/M4ResultsDashboard.tsx
?? frontend/src/services/live_signal_api.ts
?? frontend/src/services/m4_api.ts
?? frontend/src/utils/slippage_v2.ts
?? margin-calculator.jsx
?? margin_check.py
?? modify-codebase-for-deployment.md
?? scripts/backfill_m4_enriched.py
?? scripts/calibrate_full.py
?? scripts/calibrate_loop.sh
?? scripts/calibrate_loop_v2.sh
?? scripts/calibrate_v2.py
?? scripts/calibration_constants.json
?? scripts/calibration_data.json
?? scripts/calibration_history.csv
?? scripts/calibration_report.xlsx
?? scripts/calibration_v2_history.csv
?? scripts/calibration_v2_report.xlsx
?? scripts/check_user_strategy.py
?? scripts/compare_margin_models.py
?? scripts/compare_results.csv
?? scripts/fit_margin_scale.py
?? scripts/fit_v2.py
?? scripts/friday_overnight_pnl.py
?? scripts/friday_overnight_pnl.xlsx
?? scripts/historical_margin.py
?? scripts/margin_engine.py
?? scripts/margin_engine_v2.py
?? scripts/margin_engine_v2_constants.json
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-12 12:20 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
b98feb0 docs(M7): Session 20 — capital deployment analysis + M7 UI polish
b5ef61c docs(M7): Session 19 — touched-band coverage toggle handoff
cc6f313 feat(M7): loss anatomy iteration — best-combo grid builder, full-coverage tweaks, missed-friday recovery
9059841 feat(M7): loss anatomy toolkit — classifier, scope toggles, per-trade diagnostic
dd26998 docs(M7): handoff + work log for Chunk 1 (per-leg attribution)
d6a9ec5 feat(M7): Chunk 1 — Per-leg attribution + skew analytics
0aa0c96 feat(M7): exit-derivation cache, expiry buckets, missed-Fridays + best-combo path markers
9211594 feat(M7): complete M7 Fri→Sat sweep — backfill done, enrichment, exit-hour UI
454dc84 feat(M7): Friday→Saturday strangle/straddle sweep with rich 1m path + rule-based exit derivation
92d4362 docs: M4 + M5 v2 + live recorder pipeline now fully operational
```

### Uncommitted Changes (git diff --stat)
```
 .../btc-options/backend/app/api/m7_best_combo.py   | 165 ++++++++-
 .../src/components/m7/M7CellWorstFridaysTable.tsx  |   4 +-
 .../src/components/m7/M7IvBandBestComboTable.tsx   | 402 ++++++++++++++++-----
 .../components/m7/M7IvBandFullCoverageTable.tsx    |  18 +-
 .../src/components/m7/M7IvBandSummaryTable.tsx     |  55 ++-
 .../src/components/m7/M7LossesExplorer.tsx         |   6 +-
 .../src/components/m7/M7MissedFridaysTable.tsx     |  18 +-
 .../src/components/m7/M7TradeDiagnosticModal.tsx   |   6 +-
 .../btc-options/frontend/src/services/m7_api.ts    |  17 +
 9 files changed, 547 insertions(+), 144 deletions(-)
```

### Git Status
```
 M backend/app/api/m7_best_combo.py
 M frontend/src/components/m7/M7CellWorstFridaysTable.tsx
 M frontend/src/components/m7/M7IvBandBestComboTable.tsx
 M frontend/src/components/m7/M7IvBandFullCoverageTable.tsx
 M frontend/src/components/m7/M7IvBandSummaryTable.tsx
 M frontend/src/components/m7/M7LossesExplorer.tsx
 M frontend/src/components/m7/M7MissedFridaysTable.tsx
 M frontend/src/components/m7/M7TradeDiagnosticModal.tsx
 M frontend/src/services/m7_api.ts
?? "../../backtest result for best expiry and delta/"
?? .playwright-mcp/
?? "Slipage calculation SS/"
?? "UI ss/feb 6 2026 iv more than 130.jpeg"
?? frontend/src/components/m7/exportXlsx.tsx
?? m7-bestcombo-tiebreak-controls.png
?? m7-sweep-after-restart.png
?? m7_after_restart.md
?? m7_back_to_force.md
?? m7_best_combo_rule_hits_renamed.png
?? m7_full_coverage_initial.png
?? m7_full_coverage_touched_band.png
?? m7_info_icon_popover.png
?? m7_initial.md
?? m7_sweep.md
?? m7_sweep2.md
?? m7_touched_band.md
?? scripts/calibration_history.csv
?? scripts/calibration_report.xlsx
?? scripts/calibration_v2_history.csv
?? scripts/calibration_v2_report.xlsx
?? scripts/compare_results.csv
?? scripts/friday_overnight_pnl.xlsx
?? scripts/m7_4setup_comparison.xlsx
?? scripts/m7_exit_rule_sweep.xlsx
?? scripts/m7_iv_band_best_combo.xlsx
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-13 09:50 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
baf6cf9 docs(M-Month): Session 24 handoff — Phase A+B+B+ + multi-agent verification
864cd32 feat(M-Month): module for monthly + bimonthly + last-Fri-rolling strangles
b96127e feat(M7): Phase 1 closeout — Pro Metrics columns + pct_drop fix + handoff
1d83f2b feat(M7): Phase 1 — Friday Coverage drilldown UI (Features A/B/C)
4405d0b feat(M7): Phase 0+1 backend + Conservative preset + rule-comparison modal
b98feb0 docs(M7): Session 20 — capital deployment analysis + M7 UI polish
b5ef61c docs(M7): Session 19 — touched-band coverage toggle handoff
cc6f313 feat(M7): loss anatomy iteration — best-combo grid builder, full-coverage tweaks, missed-friday recovery
9059841 feat(M7): loss anatomy toolkit — classifier, scope toggles, per-trade diagnostic
dd26998 docs(M7): handoff + work log for Chunk 1 (per-leg attribution)
```

### Uncommitted Changes (git diff --stat)
```
 .../app/analytics/m_month_batch_backtester.py      |  6 +-
 .../btc-options/backend/app/api/m7_best_combo.py   | 26 +++++++
 .../backend/app/api/m_month_best_combo.py          | 42 +++++++++++
 .../btc-options/backend/app/api/m_month_results.py | 24 +++++--
 .../btc-options/docs/memories/session_end_log.md   | 71 +++++++++++++++++++
 .../src/components/m7/M7IvBandBestComboTable.tsx   | 34 ++++++++-
 .../frontend/src/pages/MMonthSweepDashboard.tsx    | 81 ++++++++++++++++++++++
 .../btc-options/frontend/src/services/m7_api.ts    |  7 ++
 .../frontend/src/services/m_month_api.ts           | 22 ++++++
 9 files changed, 304 insertions(+), 9 deletions(-)
```

### Git Status
```
 M backend/app/analytics/m_month_batch_backtester.py
 M backend/app/api/m7_best_combo.py
 M backend/app/api/m_month_best_combo.py
 M backend/app/api/m_month_results.py
 M docs/memories/session_end_log.md
 M frontend/src/components/m7/M7IvBandBestComboTable.tsx
 M frontend/src/pages/MMonthSweepDashboard.tsx
 M frontend/src/services/m7_api.ts
 M frontend/src/services/m_month_api.ts
?? "../../backtest result for best expiry and delta/"
?? .playwright-mcp/
?? "Slipage calculation SS/"
?? "UI ss/feb 6 2026 iv more than 130.jpeg"
?? m7-bestcombo-tiebreak-controls.png
?? m7-sweep-after-restart.png
?? m7_after_restart.md
?? m7_back_to_force.md
?? m7_best_combo_rule_hits_renamed.png
?? m7_feature_A_missed_fridays_force_fit.png
?? m7_feature_BC_single_combo_modal.png
?? m7_full_coverage_initial.png
?? m7_full_coverage_touched_band.png
?? m7_info_icon_popover.png
?? m7_initial.md
?? m7_phase01_header.png
?? m7_phase01_hit_pct_column.png
?? m7_phase01_rule_comparison_modal.png
?? m7_pro_metrics_columns.png
?? m7_sweep.md
?? m7_sweep2.md
?? m7_touched_band.md
?? m_month_all_cycles_full_grid.png
?? m_month_dashboard_stage1.png
?? m_month_max_profit_25.png
?? m_month_phase_b_partial.png
?? scripts/calibration_history.csv
?? scripts/calibration_report.xlsx
?? scripts/calibration_v2_history.csv
?? scripts/calibration_v2_report.xlsx
?? scripts/compare_results.csv
?? scripts/friday_overnight_pnl.xlsx
?? scripts/m7_4setup_comparison.xlsx
?? scripts/m7_exit_rule_sweep.xlsx
?? scripts/m7_iv_band_best_combo.xlsx
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-14 13:18 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
88f31e1 docs(M7): rule label vs realized return audit + session_end snapshot
7e27539 feat(M7): Best Combo picker has its own Missed Fridays table
cf2286f feat(M7): family-aware filter tagging + Best fallback exit column
a928d78 fix(M7): rule-comparison modal shows per-rule lots + filter status
958fb81 feat(M-Month): filter bar + lastfri_bimonthly @18:00 IST + Rule #5 (dedicated container)
acde2fc fix(M7): derive sum_net_pnl at grid-load so 'Total net P&L' dropdown works
4b6e78b fix(M7): scale modal $ values to the picked cell's actual lots
d4ada07 feat(M7): aggregate-hours picker mode + win-rate filter
baf6cf9 docs(M-Month): Session 24 handoff — Phase A+B+B+ + multi-agent verification
864cd32 feat(M-Month): module for monthly + bimonthly + last-Fri-rolling strangles
```

### Uncommitted Changes (git diff --stat)
```
 .../btc-options/backend/app/main.py                |  3 +-
 .../src/components/m7/M7CellAnalysisModal.tsx      | 16 +++++++--
 .../src/components/m7/M7RuleComparisonModal.tsx    |  8 ++++-
 .../frontend/src/pages/M7SweepDashboard.tsx        | 37 +++++++++++++++++++-
 .../btc-options/frontend/src/services/m7_api.ts    | 39 +++++++++++++++++++---
 5 files changed, 93 insertions(+), 10 deletions(-)
```

### Git Status
```
 M backend/app/main.py
 M frontend/src/components/m7/M7CellAnalysisModal.tsx
 M frontend/src/components/m7/M7RuleComparisonModal.tsx
 M frontend/src/pages/M7SweepDashboard.tsx
 M frontend/src/services/m7_api.ts
?? "../../backtest result for best expiry and delta/"
?? .playwright-mcp/
?? "Slipage calculation SS/"
?? "UI ss/feb 6 2026 iv more than 130.jpeg"
?? backend/app/api/m7_friday_band_best_combo.py
?? backend/app/scripts/build_m7_friday_band_grid.py
?? backend/app/scripts/build_m7_friday_band_mode_grids.py
?? backend/app/scripts/build_m7_friday_sat_iv.py
?? docs/m7_friday_band_explained.docx
?? frontend/src/components/m7/M7FridayBandBestComboTable.tsx
?? m7-bestcombo-tiebreak-controls.png
?? m7-friday-band-a1-final.png
?? m7-friday-band-a1-initial.png
?? m7-friday-band-b1.png
?? m7-friday-band-cell-analysis-final.png
?? m7-friday-band-d1-default.png
?? m7-friday-band-d1-with-3-tiebreakers.png
?? m7-friday-band-tab-a1.png
?? m7-friday-band-tab-b1.png
?? m7-friday-band-tab-d1.png
?? m7-sweep-after-restart.png
?? m7_after_restart.md
?? m7_aggregate_hours_mode.png
?? m7_back_to_force.md
?? m7_best_combo_rule_hits_renamed.png
?? m7_feature_A_missed_fridays_force_fit.png
?? m7_feature_BC_single_combo_modal.png
?? m7_full_coverage_initial.png
?? m7_full_coverage_touched_band.png
?? m7_info_icon_popover.png
?? m7_initial.md
?? m7_modal_scaled_to_lots.png
?? m7_phase01_header.png
?? m7_phase01_hit_pct_column.png
?? m7_phase01_rule_comparison_modal.png
?? m7_pro_metrics_columns.png
?? m7_rule_comparison_per_rule_lots_filters.png
?? m7_sweep.md
?? m7_sweep2.md
?? m7_touched_band.md
?? m_month_all_cycles_full_grid.png
?? m_month_dashboard_stage1.png
?? m_month_lfb_final.png
?? m_month_max_profit_25.png
?? m_month_phase_b_partial.png
?? scripts/audit_rule_label_vs_realized.py
?? scripts/calibration_history.csv
?? scripts/calibration_report.xlsx
?? scripts/calibration_v2_history.csv
?? scripts/calibration_v2_report.xlsx
?? scripts/compare_results.csv
?? scripts/friday_overnight_pnl.xlsx
?? scripts/m7_4setup_comparison.xlsx
?? scripts/m7_exit_rule_sweep.xlsx
?? scripts/m7_iv_band_best_combo.xlsx
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-14 15:59 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
3274f08 fix(M7): auto-retry transient 500s during backend restart
226eb89 feat(M7): Friday-locked-band parallel dashboard
e5041fe feat(M7): restrict picker to short-dated expiries only
2f03a45 ux(M7): add ✓ All quick-select for Expiry/Δ/Hour filters
0461f83 ux(M7): make Expiry/Δ/Hour filters' empty=all state explicit
55c3d34 feat(M7): expiry / Δ / entry-hour whitelist filters on Best Combo picker
88f31e1 docs(M7): rule label vs realized return audit + session_end snapshot
7e27539 feat(M7): Best Combo picker has its own Missed Fridays table
cf2286f feat(M7): family-aware filter tagging + Best fallback exit column
a928d78 fix(M7): rule-comparison modal shows per-rule lots + filter status
```

### Uncommitted Changes (git diff --stat)
```
 .../btc-options/docs/memories/session_end_log.md   | 92 ++++++++++++++++++++++
 1 file changed, 92 insertions(+)
```

### Git Status
```
 M docs/memories/session_end_log.md
?? "../../backtest result for best expiry and delta/"
?? .playwright-mcp/
?? "Slipage calculation SS/"
?? "UI ss/feb 6 2026 iv more than 130.jpeg"
?? backend/app/analytics/m9_friday_weekly_backtester.py
?? backend/app/api/m9_friday_weekly_best_combo.py
?? backend/app/api/m9_friday_weekly_results.py
?? frontend/src/pages/M9FridayWeeklyDashboard.tsx
?? frontend/src/services/m9_api.ts
?? m7-bestcombo-tiebreak-controls.png
?? m7-fb-final-banner-v2.png
?? m7-fb-final-banner.png
?? m7-fb-final-with-iv-banner.png
?? m7-fb-iv-banner.png
?? m7-fb-scroll1.png
?? m7-fb-scroll2.png
?? m7-fb-scroll3.png
?? m7-friday-band-a1-final.png
?? m7-friday-band-a1-initial.png
?? m7-friday-band-b1.png
?? m7-friday-band-cell-analysis-final.png
?? m7-friday-band-d1-default.png
?? m7-friday-band-d1-with-3-tiebreakers.png
?? m7-friday-band-dashboard-step2.png
?? m7-friday-band-tab-a1.png
?? m7-friday-band-tab-b1.png
?? m7-friday-band-tab-d1.png
?? m7-sweep-after-restart.png
?? m7_after_restart.md
?? m7_aggregate_hours_mode.png
?? m7_back_to_force.md
?? m7_best_combo_rule_hits_renamed.png
?? m7_feature_A_missed_fridays_force_fit.png
?? m7_feature_BC_single_combo_modal.png
?? m7_friday_band_after_d1.png
?? m7_friday_band_final.png
?? m7_friday_band_initial.png
?? m7_full_coverage_initial.png
?? m7_full_coverage_touched_band.png
?? m7_info_icon_popover.png
?? m7_initial.md
?? m7_modal_scaled_to_lots.png
?? m7_phase01_header.png
?? m7_phase01_hit_pct_column.png
?? m7_phase01_rule_comparison_modal.png
?? m7_pro_metrics_columns.png
?? m7_rule_comparison_per_rule_lots_filters.png
?? m7_sweep.md
?? m7_sweep2.md
?? m7_touched_band.md
?? m9_biweekly_5d_snap.yml
?? m9_stage1_biweekly.png
?? m9_stage1_biweekly_3d.png
?? m9_stage1_biweekly_5d.png
?? m9_stage1_biweekly_composite_rule.png
?? m9_stage1_weekly_default.png
?? m_month_all_cycles_full_grid.png
?? m_month_dashboard_stage1.png
?? m_month_lfb_final.png
?? m_month_max_profit_25.png
?? m_month_phase_b_partial.png
?? phaseC_m7sweep.png
?? phaseC_m7sweep_regression.png
?? scripts/audit_rule_label_vs_realized.py
?? scripts/calibration_history.csv
?? scripts/calibration_report.xlsx
?? scripts/calibration_v2_history.csv
?? scripts/calibration_v2_report.xlsx
?? scripts/compare_results.csv
?? scripts/friday_overnight_pnl.xlsx
?? scripts/m7_4setup_comparison.xlsx
?? scripts/m7_exit_rule_sweep.xlsx
?? scripts/m7_iv_band_best_combo.xlsx
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-14 19:51 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
3274f08 fix(M7): auto-retry transient 500s during backend restart
226eb89 feat(M7): Friday-locked-band parallel dashboard
e5041fe feat(M7): restrict picker to short-dated expiries only
2f03a45 ux(M7): add ✓ All quick-select for Expiry/Δ/Hour filters
0461f83 ux(M7): make Expiry/Δ/Hour filters' empty=all state explicit
55c3d34 feat(M7): expiry / Δ / entry-hour whitelist filters on Best Combo picker
88f31e1 docs(M7): rule label vs realized return audit + session_end snapshot
7e27539 feat(M7): Best Combo picker has its own Missed Fridays table
cf2286f feat(M7): family-aware filter tagging + Best fallback exit column
a928d78 fix(M7): rule-comparison modal shows per-rule lots + filter status
```

### Uncommitted Changes (git diff --stat)
```
 btc_options_fullstack/btc-options/HANDOFF.md       |  65 +++-
 .../btc-options/backend/app/api/m7_best_combo.py   | 384 +++++++++++++++++++--
 .../backend/app/api/m7_friday_band_best_combo.py   | 198 ++++++++++-
 .../backend/app/api/m7_friday_band_results.py      |  48 ++-
 .../btc-options/backend/app/api/m7_results.py      | 124 ++++++-
 .../btc-options/backend/app/main.py                |  16 +-
 .../btc-options/docs/memories/session_end_log.md   | 194 +++++++++++
 .../m7/M7BestComboMissedFridaysTable.tsx           |  38 +-
 .../components/m7/M7FridayBandBestComboTable.tsx   |  82 ++++-
 .../src/components/m7/M7IvBandBestComboTable.tsx   |  76 +++-
 .../frontend/src/pages/M7FridayBandDashboard.tsx   |  12 +-
 .../frontend/src/pages/M7SweepDashboard.tsx        | 216 ++----------
 .../btc-options/frontend/src/services/m7_api.ts    | 125 ++++++-
 .../btc-options/frontend/vite.config.ts            |  10 +-
 14 files changed, 1329 insertions(+), 259 deletions(-)
```

### Git Status
```
 M HANDOFF.md
 M backend/app/api/m7_best_combo.py
 M backend/app/api/m7_friday_band_best_combo.py
 M backend/app/api/m7_friday_band_results.py
 M backend/app/api/m7_results.py
 M backend/app/main.py
 M docs/memories/session_end_log.md
 M frontend/src/components/m7/M7BestComboMissedFridaysTable.tsx
 M frontend/src/components/m7/M7FridayBandBestComboTable.tsx
 M frontend/src/components/m7/M7IvBandBestComboTable.tsx
 M frontend/src/pages/M7FridayBandDashboard.tsx
 M frontend/src/pages/M7SweepDashboard.tsx
 M frontend/src/services/m7_api.ts
 M frontend/vite.config.ts
?? "../../backtest result for best expiry and delta/"
?? .playwright-mcp/
?? "Slipage calculation SS/"
?? "UI ss/feb 6 2026 iv more than 130.jpeg"
?? backend/app/analytics/m9_friday_weekly_backtester.py
?? backend/app/api/m7_ranking_config.py
?? backend/app/api/m9_friday_weekly_best_combo.py
?? backend/app/api/m9_friday_weekly_results.py
?? frontend/src/pages/M9FridayWeeklyDashboard.tsx
?? frontend/src/services/m9_api.ts
?? m7-bestcombo-tiebreak-controls.png
?? m7-fb-after-throttle.png
?? m7-fb-best-combo-loaded.png
?? m7-fb-final-banner-v2.png
?? m7-fb-final-banner.png
?? m7-fb-final-with-iv-banner.png
?? m7-fb-fixed.png
?? m7-fb-iv-banner.png
?? m7-fb-leg-attr.png
?? m7-fb-progress-after-build.png
?? m7-fb-progress-bar-v2.png
?? m7-fb-progress-bar-v3.png
?? m7-fb-progress-bar.png
?? m7-fb-progress-mid-build.png
?? m7-fb-progress-v4.png
?? m7-fb-scroll1.png
?? m7-fb-scroll2.png
?? m7-fb-scroll3.png
?? m7-fb-section-bestcombo.png
?? m7-fb-section-bottom.png
?? m7-fb-self-healed.png
?? m7-fb-throttled.png
?? m7-friday-band-a1-final.png
?? m7-friday-band-a1-initial.png
?? m7-friday-band-b1.png
?? m7-friday-band-cell-analysis-final.png
?? m7-friday-band-d1-default.png
?? m7-friday-band-d1-with-3-tiebreakers.png
?? m7-friday-band-dashboard-step2.png
?? m7-friday-band-tab-a1.png
?? m7-friday-band-tab-b1.png
?? m7-friday-band-tab-d1.png
?? m7-rescue-2.png
?? m7-rescue.png
?? m7-sweep-after-restart.png
?? m7-sweep-streak-filter-and-no-friday-tab.png
?? m7-sweep-total-win-loss-and-pro-dd-cap.png
?? m7-sweep-trimmed.png
?? m7_after_restart.md
?? m7_aggregate_hours_mode.png
?? m7_back_to_force.md
?? m7_best_combo_rule_hits_renamed.png
?? m7_feature_A_missed_fridays_force_fit.png
?? m7_feature_BC_single_combo_modal.png
?? m7_friday_band_after_d1.png
?? m7_friday_band_final.png
?? m7_friday_band_initial.png
?? m7_full_coverage_initial.png
?? m7_full_coverage_touched_band.png
?? m7_info_icon_popover.png
?? m7_initial.md
?? m7_modal_scaled_to_lots.png
?? m7_phase01_header.png
?? m7_phase01_hit_pct_column.png
?? m7_phase01_rule_comparison_modal.png
?? m7_pro_metrics_columns.png
?? m7_rule_comparison_per_rule_lots_filters.png
?? m7_sweep.md
?? m7_sweep2.md
?? m7_touched_band.md
?? m9_biweekly_5d_snap.yml
?? m9_stage1_biweekly.png
?? m9_stage1_biweekly_3d.png
?? m9_stage1_biweekly_5d.png
?? m9_stage1_biweekly_composite_rule.png
?? m9_stage1_weekly_default.png
?? m_month_all_cycles_full_grid.png
?? m_month_dashboard_stage1.png
?? m_month_lfb_final.png
?? m_month_max_profit_25.png
?? m_month_phase_b_partial.png
?? phaseC_m7sweep.png
?? phaseC_m7sweep_regression.png
?? scripts/audit_rule_label_vs_realized.py
?? scripts/calibration_history.csv
?? scripts/calibration_report.xlsx
?? scripts/calibration_v2_history.csv
?? scripts/calibration_v2_report.xlsx
?? scripts/compare_results.csv
?? scripts/friday_overnight_pnl.xlsx
?? scripts/m7_4setup_comparison.xlsx
?? scripts/m7_exit_rule_sweep.xlsx
?? scripts/m7_iv_band_best_combo.xlsx
?? v2-live.png
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-14 20:04 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
3274f08 fix(M7): auto-retry transient 500s during backend restart
226eb89 feat(M7): Friday-locked-band parallel dashboard
e5041fe feat(M7): restrict picker to short-dated expiries only
2f03a45 ux(M7): add ✓ All quick-select for Expiry/Δ/Hour filters
0461f83 ux(M7): make Expiry/Δ/Hour filters' empty=all state explicit
55c3d34 feat(M7): expiry / Δ / entry-hour whitelist filters on Best Combo picker
88f31e1 docs(M7): rule label vs realized return audit + session_end snapshot
7e27539 feat(M7): Best Combo picker has its own Missed Fridays table
cf2286f feat(M7): family-aware filter tagging + Best fallback exit column
a928d78 fix(M7): rule-comparison modal shows per-rule lots + filter status
```

### Uncommitted Changes (git diff --stat)
```
 btc_options_fullstack/btc-options/HANDOFF.md       |  65 ++-
 .../btc-options/backend/app/api/m7_best_combo.py   | 446 +++++++++++++++++++--
 .../backend/app/api/m7_friday_band_best_combo.py   | 198 ++++++++-
 .../backend/app/api/m7_friday_band_results.py      |  48 ++-
 .../btc-options/backend/app/api/m7_results.py      | 165 +++++++-
 .../btc-options/backend/app/main.py                |  16 +-
 .../tests/test_m7_losses_distribution_scope.py     |  93 +++++
 .../backend/tests/test_m7_trade_diagnostic.py      |   4 +-
 .../btc-options/docs/memories/session_end_log.md   | 343 ++++++++++++++++
 .../m7/M7BestComboMissedFridaysTable.tsx           |  38 +-
 .../components/m7/M7FridayBandBestComboTable.tsx   |  82 +++-
 .../src/components/m7/M7IvBandBestComboTable.tsx   | 165 +++++++-
 .../src/components/m7/M7LossesExplorer.tsx         |  95 +++--
 .../src/components/m7/M7RuleComparisonModal.tsx    |   7 +-
 .../src/components/m7/M7TradeDiagnosticModal.tsx   |  95 ++++-
 .../frontend/src/pages/M7FridayBandDashboard.tsx   |  12 +-
 .../frontend/src/pages/M7SweepDashboard.tsx        | 244 +++--------
 .../btc-options/frontend/src/services/m7_api.ts    | 181 ++++++++-
 .../btc-options/frontend/vite.config.ts            |  10 +-
 19 files changed, 2001 insertions(+), 306 deletions(-)
```

### Git Status
```
 M HANDOFF.md
 M backend/app/api/m7_best_combo.py
 M backend/app/api/m7_friday_band_best_combo.py
 M backend/app/api/m7_friday_band_results.py
 M backend/app/api/m7_results.py
 M backend/app/main.py
 M backend/tests/test_m7_losses_distribution_scope.py
 M backend/tests/test_m7_trade_diagnostic.py
 M docs/memories/session_end_log.md
 M frontend/src/components/m7/M7BestComboMissedFridaysTable.tsx
 M frontend/src/components/m7/M7FridayBandBestComboTable.tsx
 M frontend/src/components/m7/M7IvBandBestComboTable.tsx
 M frontend/src/components/m7/M7LossesExplorer.tsx
 M frontend/src/components/m7/M7RuleComparisonModal.tsx
 M frontend/src/components/m7/M7TradeDiagnosticModal.tsx
 M frontend/src/pages/M7FridayBandDashboard.tsx
 M frontend/src/pages/M7SweepDashboard.tsx
 M frontend/src/services/m7_api.ts
 M frontend/vite.config.ts
?? "../../backtest result for best expiry and delta/"
?? .playwright-mcp/
?? "Slipage calculation SS/"
?? "UI ss/feb 6 2026 iv more than 130.jpeg"
?? backend/app/analytics/m9_friday_weekly_backtester.py
?? backend/app/api/m7_ranking_config.py
?? backend/app/api/m9_friday_weekly_best_combo.py
?? backend/app/api/m9_friday_weekly_results.py
?? frontend/src/pages/M9FridayWeeklyDashboard.tsx
?? frontend/src/services/m9_api.ts
?? m7-bestcombo-tiebreak-controls.png
?? m7-fb-after-throttle.png
?? m7-fb-best-combo-loaded.png
?? m7-fb-final-banner-v2.png
?? m7-fb-final-banner.png
?? m7-fb-final-with-iv-banner.png
?? m7-fb-fixed.png
?? m7-fb-iv-banner.png
?? m7-fb-leg-attr.png
?? m7-fb-progress-after-build.png
?? m7-fb-progress-bar-v2.png
?? m7-fb-progress-bar-v3.png
?? m7-fb-progress-bar.png
?? m7-fb-progress-mid-build.png
?? m7-fb-progress-v4.png
?? m7-fb-scroll1.png
?? m7-fb-scroll2.png
?? m7-fb-scroll3.png
?? m7-fb-section-bestcombo.png
?? m7-fb-section-bottom.png
?? m7-fb-self-healed.png
?? m7-fb-throttled.png
?? m7-friday-band-a1-final.png
?? m7-friday-band-a1-initial.png
?? m7-friday-band-b1.png
?? m7-friday-band-cell-analysis-final.png
?? m7-friday-band-d1-default.png
?? m7-friday-band-d1-with-3-tiebreakers.png
?? m7-friday-band-dashboard-step2.png
?? m7-friday-band-tab-a1.png
?? m7-friday-band-tab-b1.png
?? m7-friday-band-tab-d1.png
?? m7-rescue-2.png
?? m7-rescue.png
?? m7-sweep-after-restart.png
?? m7-sweep-streak-filter-and-no-friday-tab.png
?? m7-sweep-total-win-loss-and-pro-dd-cap.png
?? m7-sweep-trimmed.png
?? m7_after_restart.md
?? m7_aggregate_hours_mode.png
?? m7_back_to_force.md
?? m7_best_combo_rule_hits_renamed.png
?? m7_feature_A_missed_fridays_force_fit.png
?? m7_feature_BC_single_combo_modal.png
?? m7_friday_band_after_d1.png
?? m7_friday_band_final.png
?? m7_friday_band_initial.png
?? m7_full_coverage_initial.png
?? m7_full_coverage_touched_band.png
?? m7_info_icon_popover.png
?? m7_initial.md
?? m7_modal_scaled_to_lots.png
?? m7_phase01_header.png
?? m7_phase01_hit_pct_column.png
?? m7_phase01_rule_comparison_modal.png
?? m7_pro_metrics_columns.png
?? m7_rule_comparison_per_rule_lots_filters.png
?? m7_sweep.md
?? m7_sweep2.md
?? m7_touched_band.md
?? m9_biweekly_5d_snap.yml
?? m9_stage1_biweekly.png
?? m9_stage1_biweekly_3d.png
?? m9_stage1_biweekly_5d.png
?? m9_stage1_biweekly_composite_rule.png
?? m9_stage1_weekly_default.png
?? m_month_all_cycles_full_grid.png
?? m_month_dashboard_stage1.png
?? m_month_lfb_final.png
?? m_month_max_profit_25.png
?? m_month_phase_b_partial.png
?? phaseC_m7sweep.png
?? phaseC_m7sweep_regression.png
?? scripts/audit_rule_label_vs_realized.py
?? scripts/calibration_history.csv
?? scripts/calibration_report.xlsx
?? scripts/calibration_v2_history.csv
?? scripts/calibration_v2_report.xlsx
?? scripts/compare_results.csv
?? scripts/friday_overnight_pnl.xlsx
?? scripts/m7_4setup_comparison.xlsx
?? scripts/m7_exit_rule_sweep.xlsx
?? scripts/m7_iv_band_best_combo.xlsx
?? v2-live.png
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-15 14:33 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
88a4110 feat(M7): Best Combo + Full Coverage table — deduped Friday attribution
3274f08 fix(M7): auto-retry transient 500s during backend restart
226eb89 feat(M7): Friday-locked-band parallel dashboard
e5041fe feat(M7): restrict picker to short-dated expiries only
2f03a45 ux(M7): add ✓ All quick-select for Expiry/Δ/Hour filters
0461f83 ux(M7): make Expiry/Δ/Hour filters' empty=all state explicit
55c3d34 feat(M7): expiry / Δ / entry-hour whitelist filters on Best Combo picker
88f31e1 docs(M7): rule label vs realized return audit + session_end snapshot
7e27539 feat(M7): Best Combo picker has its own Missed Fridays table
cf2286f feat(M7): family-aware filter tagging + Best fallback exit column
```

### Uncommitted Changes (git diff --stat)
```
 btc_options_fullstack/btc-options/HANDOFF.md       | 219 ++++++++-
 .../btc-options/backend/app/api/m7_best_combo.py   | 359 ++++++++++++++-
 .../backend/app/api/m7_friday_band_best_combo.py   | 200 +++++++-
 .../backend/app/api/m7_friday_band_results.py      |  48 +-
 .../btc-options/backend/app/api/m7_results.py      | 286 +++++++++++-
 .../btc-options/backend/app/main.py                |  16 +-
 .../backend/tests/test_m7_best_combo.py            |  17 +-
 .../backend/tests/test_m7_best_combo_coverage.py   | 162 +++++++
 .../backend/tests/test_m7_full_coverage.py         |   5 +
 .../tests/test_m7_losses_distribution_scope.py     | 155 ++++++-
 .../backend/tests/test_m7_trade_diagnostic.py      |   4 +-
 .../btc-options/docs/memories/session_end_log.md   | 502 +++++++++++++++++++++
 .../btc-options/docs/memories/work_log_claude.md   |  25 +
 .../src/components/m7/M7BestComboCoverageTable.tsx |  25 +-
 .../m7/M7BestComboMissedFridaysTable.tsx           |  38 +-
 .../components/m7/M7FridayBandBestComboTable.tsx   |  82 +++-
 .../src/components/m7/M7IvBandBestComboTable.tsx   | 344 +++++++++++++-
 .../src/components/m7/M7LossesExplorer.tsx         | 134 ++++--
 .../src/components/m7/M7RuleComparisonModal.tsx    |   7 +-
 .../src/components/m7/M7TradeDiagnosticModal.tsx   | 109 ++++-
 .../frontend/src/pages/M7FridayBandDashboard.tsx   |  12 +-
 .../btc-options/frontend/src/services/m7_api.ts    |   4 +
 .../btc-options/frontend/vite.config.ts            |  10 +-
 23 files changed, 2658 insertions(+), 105 deletions(-)
```

### Git Status
```
 M HANDOFF.md
 M backend/app/api/m7_best_combo.py
 M backend/app/api/m7_friday_band_best_combo.py
 M backend/app/api/m7_friday_band_results.py
 M backend/app/api/m7_results.py
 M backend/app/main.py
 M backend/tests/test_m7_best_combo.py
 M backend/tests/test_m7_best_combo_coverage.py
 M backend/tests/test_m7_full_coverage.py
 M backend/tests/test_m7_losses_distribution_scope.py
 M backend/tests/test_m7_trade_diagnostic.py
 M docs/memories/session_end_log.md
 M docs/memories/work_log_claude.md
 M frontend/src/components/m7/M7BestComboCoverageTable.tsx
 M frontend/src/components/m7/M7BestComboMissedFridaysTable.tsx
 M frontend/src/components/m7/M7FridayBandBestComboTable.tsx
 M frontend/src/components/m7/M7IvBandBestComboTable.tsx
 M frontend/src/components/m7/M7LossesExplorer.tsx
 M frontend/src/components/m7/M7RuleComparisonModal.tsx
 M frontend/src/components/m7/M7TradeDiagnosticModal.tsx
 M frontend/src/pages/M7FridayBandDashboard.tsx
 M frontend/src/services/m7_api.ts
 M frontend/vite.config.ts
?? "../../backtest result for best expiry and delta/"
?? .claude/
?? .playwright-mcp/
?? "Slipage calculation SS/"
?? "UI ss/feb 6 2026 iv more than 130.jpeg"
?? backend/app/analytics/m9_friday_weekly_backtester.py
?? backend/app/api/m7_ranking_config.py
?? backend/app/api/m9_friday_weekly_best_combo.py
?? backend/app/api/m9_friday_weekly_results.py
?? backend/app/scripts/build_m7_bucketed_grids.py
?? backend/app/scripts/calibrate_m7_slope_cutoffs.py
?? backend/app/scripts/enrich_m7_trades_with_iv_slopes.py
?? backend/app/scripts/m7_composite_score_calibration.py
?? frontend/src/pages/M9FridayWeeklyDashboard.tsx
?? frontend/src/services/m9_api.ts
?? m7-bestcombo-tiebreak-controls.png
?? m7-coverage-table.png
?? m7-fb-after-throttle.png
?? m7-fb-best-combo-loaded.png
?? m7-fb-final-banner-v2.png
?? m7-fb-final-banner.png
?? m7-fb-final-with-iv-banner.png
?? m7-fb-fixed.png
?? m7-fb-iv-banner.png
?? m7-fb-leg-attr.png
?? m7-fb-progress-after-build.png
?? m7-fb-progress-bar-v2.png
?? m7-fb-progress-bar-v3.png
?? m7-fb-progress-bar.png
?? m7-fb-progress-mid-build.png
?? m7-fb-progress-v4.png
?? m7-fb-scroll1.png
?? m7-fb-scroll2.png
?? m7-fb-scroll3.png
?? m7-fb-section-bestcombo.png
?? m7-fb-section-bottom.png
?? m7-fb-self-healed.png
?? m7-fb-throttled.png
?? m7-friday-band-a1-final.png
?? m7-friday-band-a1-initial.png
?? m7-friday-band-b1.png
?? m7-friday-band-cell-analysis-final.png
?? m7-friday-band-d1-default.png
?? m7-friday-band-d1-with-3-tiebreakers.png
?? m7-friday-band-dashboard-step2.png
?? m7-friday-band-tab-a1.png
?? m7-friday-band-tab-b1.png
?? m7-friday-band-tab-d1.png
?? m7-fullcov-table.png
?? m7-fullcov-touched.png
?? m7-multi-dd-cap.png
?? m7-rescue-2.png
?? m7-rescue.png
?? m7-sweep-after-restart.png
?? m7-sweep-streak-filter-and-no-friday-tab.png
?? m7-sweep-total-win-loss-and-pro-dd-cap.png
?? m7-sweep-trimmed.png
?? m7-sweep-v7-zigzag-cols-pre-rebuild.png
?? m7-sweep-v7-zigzag-lazy-live.png
?? m7-sweep-v7-zigzag-populated.png
?? m7_after_restart.md
?? m7_aggregate_hours_mode.png
?? m7_back_to_force.md
?? m7_best_combo_rule_hits_renamed.png
?? m7_feature_A_missed_fridays_force_fit.png
?? m7_feature_BC_single_combo_modal.png
?? m7_friday_band_after_d1.png
?? m7_friday_band_final.png
?? m7_friday_band_initial.png
?? m7_full_coverage_initial.png
?? m7_full_coverage_touched_band.png
?? m7_info_icon_popover.png
?? m7_initial.md
?? m7_modal_scaled_to_lots.png
?? m7_phase01_header.png
?? m7_phase01_hit_pct_column.png
?? m7_phase01_rule_comparison_modal.png
?? m7_pro_metrics_columns.png
?? m7_rule_comparison_per_rule_lots_filters.png
?? m7_sweep.md
?? m7_sweep2.md
?? m7_touched_band.md
?? m9_biweekly_5d_snap.yml
?? m9_stage1_biweekly.png
?? m9_stage1_biweekly_3d.png
?? m9_stage1_biweekly_5d.png
?? m9_stage1_biweekly_composite_rule.png
?? m9_stage1_weekly_default.png
?? m_month_all_cycles_full_grid.png
?? m_month_dashboard_stage1.png
?? m_month_lfb_final.png
?? m_month_max_profit_25.png
?? m_month_phase_b_partial.png
?? phaseB-tabs.png
?? phaseC_m7sweep.png
?? phaseC_m7sweep_regression.png
?? scripts/audit_rule_label_vs_realized.py
?? scripts/calibration_history.csv
?? scripts/calibration_report.xlsx
?? scripts/calibration_v2_history.csv
?? scripts/calibration_v2_report.xlsx
?? scripts/compare_results.csv
?? scripts/friday_overnight_pnl.xlsx
?? scripts/m7_4setup_comparison.xlsx
?? scripts/m7_exit_rule_sweep.xlsx
?? scripts/m7_iv_band_best_combo.xlsx
?? v2-live.png
?? "../../new platform arch/"
```

---
## Snapshot — 2026-05-17 22:30 (branch: mainbranch-gemini_claude)

### Recent Commits (last 10)
```
8109c08 feat(M7): add Sat 05/06/07 IST exit hours + partial-grid rebuild script
da68fce feat(M7): Friday-Band MTM Overlay panel — avg + extremes per band
88a4110 feat(M7): Best Combo + Full Coverage table — deduped Friday attribution
3274f08 fix(M7): auto-retry transient 500s during backend restart
226eb89 feat(M7): Friday-locked-band parallel dashboard
e5041fe feat(M7): restrict picker to short-dated expiries only
2f03a45 ux(M7): add ✓ All quick-select for Expiry/Δ/Hour filters
0461f83 ux(M7): make Expiry/Δ/Hour filters' empty=all state explicit
55c3d34 feat(M7): expiry / Δ / entry-hour whitelist filters on Best Combo picker
88f31e1 docs(M7): rule label vs realized return audit + session_end snapshot
```

### Uncommitted Changes (git diff --stat)
```
 btc_options_fullstack/btc-options/HANDOFF.md       | 206 +++++-
 .../backend/app/analytics/m7_batch_backtester.py   |  94 ++-
 .../btc-options/backend/app/api/m7_best_combo.py   | 772 ++++++++++++++++++---
 .../backend/app/api/m7_friday_band_best_combo.py   | 200 +++++-
 .../backend/app/api/m7_full_coverage.py            |   6 +-
 .../btc-options/backend/app/api/m7_results.py      | 575 ++++++++++++---
 .../btc-options/backend/app/main.py                |  19 +-
 .../backend/tests/test_m7_best_combo.py            |  17 +-
 .../backend/tests/test_m7_best_combo_coverage.py   | 162 +++++
 .../backend/tests/test_m7_full_coverage.py         |   5 +
 .../tests/test_m7_losses_distribution_scope.py     | 155 ++++-
 .../backend/tests/test_m7_trade_diagnostic.py      |   4 +-
 .../btc-options/docs/memories/current_state.md     |  52 ++
 .../btc-options/docs/memories/session_end_log.md   | 682 ++++++++++++++++++
 .../btc-options/docs/memories/work_log_claude.md   |  74 ++
 .../src/components/m7/M7BestComboCoverageTable.tsx |  29 +-
 .../m7/M7BestComboMissedFridaysTable.tsx           |  45 +-
 .../src/components/m7/M7BestComboPathMarkers.tsx   |  10 +-
 .../components/m7/M7FridayBandBestComboTable.tsx   |  82 ++-
 .../src/components/m7/M7IvBandBestComboTable.tsx   | 406 ++++++++++-
 .../src/components/m7/M7LossesExplorer.tsx         | 140 +++-
 .../src/components/m7/M7RuleComparisonModal.tsx    |   7 +-
 .../src/components/m7/M7TradeDiagnosticModal.tsx   | 109 ++-
 .../frontend/src/pages/M7SweepDashboard.tsx        | 100 ++-
 .../btc-options/frontend/src/services/m7_api.ts    | 178 ++++-
 .../btc-options/frontend/src/utils/marginEngine.ts |   8 +-
 .../btc-options/frontend/vite.config.ts            |  10 +-
 .../btc-options/scripts/margin_engine.py           |   8 +-
 28 files changed, 3791 insertions(+), 364 deletions(-)
```

### Git Status
```
 M HANDOFF.md
 M backend/app/analytics/m7_batch_backtester.py
 M backend/app/api/m7_best_combo.py
 M backend/app/api/m7_friday_band_best_combo.py
 M backend/app/api/m7_full_coverage.py
 M backend/app/api/m7_results.py
 M backend/app/main.py
 M backend/tests/test_m7_best_combo.py
 M backend/tests/test_m7_best_combo_coverage.py
 M backend/tests/test_m7_full_coverage.py
 M backend/tests/test_m7_losses_distribution_scope.py
 M backend/tests/test_m7_trade_diagnostic.py
 M docs/memories/current_state.md
 M docs/memories/session_end_log.md
 M docs/memories/work_log_claude.md
 M frontend/src/components/m7/M7BestComboCoverageTable.tsx
 M frontend/src/components/m7/M7BestComboMissedFridaysTable.tsx
 M frontend/src/components/m7/M7BestComboPathMarkers.tsx
 M frontend/src/components/m7/M7FridayBandBestComboTable.tsx
 M frontend/src/components/m7/M7IvBandBestComboTable.tsx
 M frontend/src/components/m7/M7LossesExplorer.tsx
 M frontend/src/components/m7/M7RuleComparisonModal.tsx
 M frontend/src/components/m7/M7TradeDiagnosticModal.tsx
 M frontend/src/pages/M7SweepDashboard.tsx
 M frontend/src/services/m7_api.ts
 M frontend/src/utils/marginEngine.ts
 M frontend/vite.config.ts
 M scripts/margin_engine.py
?? "../../backtest result for best expiry and delta/"
?? .playwright-mcp/
?? "Slipage calculation SS/"
?? "UI ss/feb 6 2026 iv more than 130.jpeg"
?? backend/app/analytics/m7_batch_backtester_joint.py
?? backend/app/analytics/m7_strike_picker_joint.py
?? backend/app/analytics/m9_friday_weekly_backtester.py
?? backend/app/api/m7_best_combo_hybrid.py
?? backend/app/api/m7_hybrid_results.py
?? backend/app/api/m7_joint_match_stats.py
?? backend/app/api/m7_ranking_config.py
?? backend/app/api/m9_friday_weekly_best_combo.py
?? backend/app/api/m9_friday_weekly_results.py
?? backend/app/scripts/build_m7_best_combo_grid_hybrid.py
?? backend/app/scripts/build_m7_best_combo_grid_price_matched.py
?? backend/app/scripts/build_m7_bucketed_grids.py
?? backend/app/scripts/calibrate_m7_slope_cutoffs.py
?? backend/app/scripts/enrich_m7_trades_with_iv_slopes.py
?? backend/app/scripts/m7_composite_score_calibration.py
?? backend/tests/test_m7_batch_backtester_append.py
?? backend/tests/test_m7_best_combo_hybrid.py
?? backend/tests/test_m7_joint_match.py
?? frontend/src/components/m7/M7JointMatchStats.tsx
?? frontend/src/pages/M9FridayWeeklyDashboard.tsx
?? frontend/src/services/m9_api.ts
?? m7-after-fixes-delta-match.png
?? m7-after-fixes-initial.png
?? m7-after-fixes-price-match-final.png
?? m7-after-reload.png
?? m7-bestcombo-tiebreak-controls.png
?? m7-coverage-table.png
?? m7-crash-on-load.png
?? m7-fb-after-throttle.png
?? m7-fb-best-combo-loaded.png
?? m7-fb-final-banner-v2.png
?? m7-fb-final-banner.png
?? m7-fb-final-with-iv-banner.png
?? m7-fb-fixed.png
?? m7-fb-iv-banner.png
?? m7-fb-leg-attr.png
?? m7-fb-mtm-overlay-panel-mounted.png
?? m7-fb-progress-after-build.png
?? m7-fb-progress-bar-v2.png
?? m7-fb-progress-bar-v3.png
?? m7-fb-progress-bar.png
?? m7-fb-progress-mid-build.png
?? m7-fb-progress-v4.png
?? m7-fb-scroll1.png
?? m7-fb-scroll2.png
?? m7-fb-scroll3.png
?? m7-fb-section-bestcombo.png
?? m7-fb-section-bottom.png
?? m7-fb-self-healed.png
?? m7-fb-throttled.png
?? m7-friday-band-a1-final.png
?? m7-friday-band-a1-initial.png
?? m7-friday-band-b1.png
?? m7-friday-band-cell-analysis-final.png
?? m7-friday-band-d1-default.png
?? m7-friday-band-d1-with-3-tiebreakers.png
?? m7-friday-band-dashboard-step2.png
?? m7-friday-band-tab-a1.png
?? m7-friday-band-tab-b1.png
?? m7-friday-band-tab-d1.png
?? m7-fullcov-table.png
?? m7-fullcov-touched.png
?? m7-multi-dd-cap.png
?? m7-rescue-2.png
?? m7-rescue.png
?? m7-sweep-after-restart.png
?? m7-sweep-back-to-delta.png
?? m7-sweep-initial.png
?? m7-sweep-price-match.png
?? m7-sweep-streak-filter-and-no-friday-tab.png
?? m7-sweep-total-win-loss-and-pro-dd-cap.png
?? m7-sweep-trimmed.png
?? m7-sweep-v7-zigzag-cols-pre-rebuild.png
?? m7-sweep-v7-zigzag-lazy-live.png
?? m7-sweep-v7-zigzag-populated.png
?? m7_after_restart.md
?? m7_aggregate_hours_mode.png
?? m7_back_to_force.md
?? m7_best_combo_rule_hits_renamed.png
?? m7_feature_A_missed_fridays_force_fit.png
?? m7_feature_BC_single_combo_modal.png
?? m7_friday_band_after_d1.png
?? m7_friday_band_final.png
?? m7_friday_band_initial.png
?? m7_full_coverage_initial.png
?? m7_full_coverage_touched_band.png
?? m7_info_icon_popover.png
?? m7_initial.md
?? m7_modal_scaled_to_lots.png
?? m7_phase01_header.png
?? m7_phase01_hit_pct_column.png
?? m7_phase01_rule_comparison_modal.png
?? m7_pro_metrics_columns.png
?? m7_rule_comparison_per_rule_lots_filters.png
?? m7_sweep.md
?? m7_sweep2.md
?? m7_touched_band.md
?? m7sweep-exit-time-filter.png
?? m9_biweekly_5d_snap.yml
?? m9_stage1_biweekly.png
?? m9_stage1_biweekly_3d.png
?? m9_stage1_biweekly_5d.png
?? m9_stage1_biweekly_composite_rule.png
?? m9_stage1_weekly_default.png
?? m_month_all_cycles_full_grid.png
?? m_month_dashboard_stage1.png
?? m_month_lfb_final.png
?? m_month_max_profit_25.png
?? m_month_phase_b_partial.png
?? phaseB-tabs.png
?? phaseC_m7sweep.png
?? phaseC_m7sweep_regression.png
?? scripts/audit_rule_label_vs_realized.py
?? scripts/calibration_history.csv
?? scripts/calibration_report.xlsx
?? scripts/calibration_v2_history.csv
?? scripts/calibration_v2_report.xlsx
?? scripts/compare_results.csv
?? scripts/friday_overnight_pnl.xlsx
?? scripts/m7_4setup_comparison.xlsx
?? scripts/m7_exit_rule_sweep.xlsx
?? scripts/m7_iv_band_best_combo.xlsx
?? v2-live.png
?? "../../new platform arch/"
```

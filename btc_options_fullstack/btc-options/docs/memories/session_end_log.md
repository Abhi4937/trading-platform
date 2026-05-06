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

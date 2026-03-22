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

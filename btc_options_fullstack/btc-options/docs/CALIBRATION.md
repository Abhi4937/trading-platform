# Calibration & Margin Engine Reference

This document records the calibration loop's mechanics and key numerical
facts about the platform. For rules on HOW Claude should behave around
margin safety (RULE #3), see `CLAUDE.md`.

---

## Margin calibration loop (background process, may be running)
`scripts/calibrate_loop_v2.sh` runs every 15 min for 24h to record `our_pm` vs
`delta_arm` across 7 expiry buckets × 6 deltas × 13 lot sizes (546 scenarios per run).
- Output: `scripts/calibration_v2_history.csv`
- PID file: `/tmp/calib_v2_loop.pid`
- Live log: `/tmp/calib_v2_loop.log`
- If `delta_arm` column is empty in newly-appended rows, Delta API key's IP whitelist
  needs updating (WSL IP rotates) — user must fix via the Delta dashboard.
- After 24h completes, `scripts/fit_margin_scale.py` (or a successor) refits
  `T0`/`p`/shock-span constants from the full grid.

## Key Facts
- Greeks computed with Black-Scholes server-side (verified match with Delta's live greeks)
- OI and Volume displayed in USD using Delta's `oi_value_usd` and `turnover_usd` fields
- WS product list refreshed every 1 hour (new expiries added weekly)
- Settlement time: 5:30 PM IST = 12:00 UTC
- Contract size: 0.001 BTC per contract

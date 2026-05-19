"""Centralised thresholds and weights for the M7 composite-ranking v2
pipeline. Constants only — no logic. Imported by m7_best_combo.py and
the calibration scripts so anyone tuning the formulae has one place to
look.

Numbers here mirror the spec in
/home/abhis/.claude/plans/now-for-best-combo-lively-creek.md (Part A).
Changing any value here changes both the picker and the audit dump on
next backend restart.
"""
from __future__ import annotations

import json
import os
from typing import Optional

# ── Hard-filter gates ────────────────────────────────────────────────────────
# A cell that fails ANY of these (except n) is tagged rank_status="filtered"
# and sorted to the bottom of every ranking; it remains visible so the user
# can see what was excluded. The n gate is two-tier: n < LOW_N_THRESHOLD →
# cell dropped entirely; LOW_N_THRESHOLD ≤ n < MIN_N_TRADES → rank_status
# ="low_n" (still ranked but flagged); n ≥ MIN_N_TRADES → ranked normally.

MIN_N_TRADES: int = 20
LOW_N_THRESHOLD: int = 10
MIN_WIN_RATE: float = 0.70
MAX_CVAR_TO_CREDIT_RATIO: float = 2.0  # |CVaR_95| > 2.0 × avg_credit → filter
MAX_LOSING_STREAK: int = 3              # max_consec_losses > this → filter


# ── Composite-score v2 component weights ─────────────────────────────────────
# Must sum to 1.0. Weights are applied to MIN-MAX-NORMALISED component
# values within the active grouping_keys block (band, band×ivrv, or
# band×ivrv×slope). When CVaR is undefined (n in the tail too small to
# estimate), the avg_net/CVaR term is dropped and the remaining four are
# re-weighted to sum to 1.0.

COMPOSITE_V2_WEIGHTS: dict[str, float] = {
    "sortino_per_trade":         0.35,
    "calmar_like":               0.25,
    "avg_net_to_abs_cvar_ratio": 0.20,
    "avg_pct_return_on_margin":  0.10,
    "win_rate":                  0.10,
}
assert abs(sum(COMPOSITE_V2_WEIGHTS.values()) - 1.0) < 1e-9, \
    "COMPOSITE_V2_WEIGHTS must sum to 1.0"


# ── IVRV bucket cutoffs ──────────────────────────────────────────────────────
# Applied to per-trade column `ctx_iv_rv_spread_7d` at trade-load time so
# the band×ivrv grids can group by them. Values are absolute IV-RV spread
# units (decimals, e.g. 0.10 = 10pp).

IVRV_RICH_THRESHOLD: float = 0.10   # spread > this → "rich"
IVRV_CHEAP_THRESHOLD: float = 0.0   # spread < this → "cheap"
                                     # in between → "fair"


# ── Slope-bucket cutoffs cache file ──────────────────────────────────────────
# The 4 candidate IV-curve slopes (current↔next, next↔next_to_next,
# current↔next_to_next, plus the legacy 7d-30d slope as a control) each
# get empirical p33/p67 cutoffs. The calibration script writes this file;
# the backend reloads it at startup.
SLOPE_CUTOFFS_PATH: str = "/home/abhis/btc-data/derived/m7/slope_cutoffs_v1.json"

# Conservative fallback cutoffs if the JSON is missing on first boot —
# wide enough to still produce three roughly-balanced buckets. Real values
# get overwritten by the calibration script.
_SLOPE_FALLBACK = {"p33": -0.01, "p67": 0.01}


def load_slope_cutoffs() -> dict[str, dict[str, float]]:
    """Read slope cutoffs from disk. Returns a dict
    `{slope_col: {"p33": x, "p67": y}}` for each of the 4 slopes; falls
    back to a conservative default if the JSON is absent."""
    if not os.path.exists(SLOPE_CUTOFFS_PATH):
        return {k: dict(_SLOPE_FALLBACK) for k in (
            "slope_current_next",
            "slope_next_next_to_next",
            "slope_current_next_to_next",
            "ctx_term_slope_7_30",
        )}
    with open(SLOPE_CUTOFFS_PATH) as f:
        return json.load(f)

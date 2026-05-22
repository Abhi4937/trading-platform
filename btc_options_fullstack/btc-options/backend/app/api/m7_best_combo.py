"""M7 best-combo per IV band — sweeps a fixed exit-rule space, ranks by
% return on credit OR % return on margin.

Per IV band, finds the (expiry_bucket, delta_target, exit_rule) that maxes
the chosen ranking metric. Premium SL is fixed at 100% always-on; the rule
sweep varies max_profit_pct ∈ {10..100} and margin_target_pct ∈ {10..100}.

Adds three exit-time columns the iv_band_summary doesn't expose:
  - avg_exit_offset_minutes        (mean over all trades in the cell)
  - avg_loser_exit_offset_minutes  (restricted to net_pnl < 0; null if no losers)
  - avg_winner_exit_offset_minutes (restricted to net_pnl > 0; null if no winners)

Endpoint: GET /api/v1/m7/iv_band_best_combo?ranking=credit|margin
Optional: &include_grid=true  → also return the full 11,760-cell grid.

Cache strategy: 21 rule variants × ~30 s cold-DuckDB-scan each = ~10 min
total. That's too slow for a synchronous request, so the grid is computed
once in a background thread (kicked off at app startup AND on first
request). While the thread runs, the endpoint returns 202 with progress.
After completion the result is held in `_GRID_STATE["grid"]` for the
process lifetime; backend restart re-warms.
"""
from __future__ import annotations

from typing import Optional
import hashlib
import json
import logging
import math
import os
import re
import threading
import time

from fastapi import APIRouter, HTTPException, Query
import numpy as np
import pandas as pd

from app.api import m7_results as m7r

router = APIRouter()
log = logging.getLogger(__name__)

# Persist the final grid to disk so backend restarts skip the ~10 min rebuild.
# Invalidated by trades-parquet mtime — when the dataset changes, the grid
# file is recomputed.
# v6 adds path peak-trough-peak fields, risk-adjusted metrics, tail risk
# (VaR/CVaR), drawdown sequence, edge stability — and bakes in the Phase 0A
# NaN-gross drop so cells reflect only valid strangle trades.
# v7 zigzag columns (P2_mid/T2_mid) removed — logic was suspect and the lazy
# DuckDB scan caused 600 s+ cold-start timeouts.
GRID_PARQUET_PATH = os.path.join(m7r.M7_BASE_DIR, "m7_best_combo_grid_v6.parquet")
# v4 fallback — load v4 while v6 is still rebuilding so the UI keeps
# working; new v6 columns will simply be None for those cells.
_GRID_FALLBACK_PATH = os.path.join(m7r.M7_BASE_DIR, "m7_best_combo_grid_v4.parquet")
# Joint delta+price-matched grid — built by build_m7_best_combo_grid_price_matched.py
# (separate script, not part of this refactor). When dataset=price_match is
# requested and this file is absent, every endpoint returns a clear no_data
# response rather than 500-ing.
GRID_PARQUET_PATH_PRICE_MATCHED = os.path.join(
    m7r.M7_BASE_DIR, "m7_best_combo_grid_v6_price_matched.parquet",
)


def _grid_path_for_dataset(dataset: str) -> str:
    return (GRID_PARQUET_PATH_PRICE_MATCHED if dataset == "price_match"
            else GRID_PARQUET_PATH)


def _price_match_no_data_payload(extra: Optional[dict] = None) -> dict:
    """Standard response shape when dataset=price_match is requested but the
    price-matched grid hasn't been built. Frontend consumes `status` and
    `message`; the rest of the keys match the regular endpoint shape so the
    UI doesn't crash on missing fields."""
    base = {
        "status": "no_data",
        "message": (f"price-matched grid not built. "
                    f"Run the price-matched backtester + grid builder first."),
        "rows": [], "n_rules": 0, "n_cells": 0,
    }
    if extra:
        base.update(extra)
    return base


# ── Sweep dimensions ──────────────────────────────────────────────────────────

# Five premium SL levels × three take-profit families per SL:
#   1 baseline + 7 margin_target + 3 capital_target + 14 fixed-hour
#   = 25 variants per SL × 5 SLs = 125 total premium-SL rule variants.
# max_profit_pct removed entirely — holding 11–15 hr makes 40%+ unreachable.
_PREMIUM_SL_PCTS = [50, 75, 100, 150, 200]
_MARGIN_TARGET_PCTS = [10, 15, 20, 25, 30, 40, 50]   # 60/75/100 removed — rarely hit intraday
# Hourly Saturday exits 5 AM..5 PM IST + 5:29 PM (just before settlement).
# 17.4833 = 17h + 29m / 60 — the engine accepts decimal hours.
_FIXED_HOURS: list[float] = [5.0, 6.0, 7.0,
                              8.0, 9.0, 10.0, 11.0, 12.0, 13.0,
                              14.0, 15.0, 16.0, 17.0, 17.4833]

# Capital-based SL: fires when loss ≥ pct% of total allocated capital.
# Capital basis is fixed at _CAPITAL_USD (not per-trade margin).
# cap10 → fires at $100 loss, cap15 → $150, cap20 → $200.
_CAPITAL_SL_PCTS = [10, 15, 20]
_CAPITAL_USD = 1000.0  # total capital allocated to this strategy


def _hour_label(h: float) -> str:
    """Format a fixed-hour value into a stable label suffix.
    8.0 → '8', 17.0 → '17', 17.4833 → '1729' (5:29 PM)."""
    minutes = round((h - int(h)) * 60)
    if minutes == 0:
        return f"{int(h)}"
    return f"{int(h)}{minutes:02d}"


def _rule_variants(dataset: str = "delta_match") -> list[tuple[str, dict]]:
    """Rule label → rule_dict tuples for the grid sweep.

    delta_match: 110 variants
      = 5 baselines + 5×7 margin_target + 5×14 exit_hr
      = 5 + 35 + 70 = 110 total.

    Intentionally excluded: max_profit_pct (unreachable intraday),
    capital_sl/capital_target (design deferred — cap basis vs. lot size incoherent).
    Phase 3 (future session): capital-based rules with proper per-trade K sizing.

    price_match: 243 variants (unchanged for backwards compat).
    """
    out: list[tuple[str, dict]] = []
    sl_grid = _PREMIUM_SL_PCTS if dataset == "delta_match" else [50, 75, 100]
    for sl in sl_grid:
        out.append((f"sl{sl}_baseline", {"premium_sl_pct": sl}))
        for p in _MARGIN_TARGET_PCTS:
            out.append((f"sl{sl}_margin_target_{p}",
                        {"premium_sl_pct": sl, "margin_target_pct": p}))
        for h in _FIXED_HOURS:
            out.append((f"sl{sl}_exit_hr_{_hour_label(h)}",
                        {"premium_sl_pct": sl, "fixed_exit_hour_ist": h}))
    if dataset == "price_match":
        # 3-way hybrid: cap_sl × premium_sl × exit_hr (3 × 3 × 14 = 126).
        for csl in _CAPITAL_SL_PCTS:
            for sl in sl_grid:
                for h in _FIXED_HOURS:
                    out.append((
                        f"sl{sl}_cap{csl}_exit_hr_{_hour_label(h)}",
                        {"premium_sl_pct": sl,
                         "capital_sl_pct": csl,
                         "capital_usd": _CAPITAL_USD,
                         "fixed_exit_hour_ist": h},
                    ))
    return out


def _label_to_rule(label: str, dataset: str = "delta_match") -> Optional[dict]:
    """Resolve a rule label string back to its rule_dict, or None if unknown.
    Iterates `_rule_variants(dataset)` once — variant lists are small (≤243).
    """
    for lbl, rule in _rule_variants(dataset):
        if lbl == label:
            return rule
    return None


def _rule_category(label: str) -> str:
    """Tag a rule label with its category for the frontend filter chip.

    Categories (mutually exclusive):
      - single_baseline                — sl{X}_baseline
      - single_max_profit              — sl{X}_max_profit_{P}
      - single_margin_target           — sl{X}_margin_target_{P}
      - single_exit_hr                 — sl{X}_exit_hr_{H}
      - single_capital_sl_standalone   — cap{X}_baseline (no other constraint)
      - hybrid_2way_sl                 — sl{X}_cap{Y}
      - hybrid_3way                    — sl{X}_cap{Y}_exit_hr_{H}
    """
    if label.startswith("cap") and "_baseline" in label:
        return "single_capital_sl_standalone"
    if "_cap" in label and "_exit_hr_" in label:
        return "hybrid_3way"
    if "_cap" in label:
        return "hybrid_2way_sl"
    if "_max_profit_" in label:
        return "single_max_profit"
    if "_margin_target_" in label:
        return "single_margin_target"
    if "_exit_hr_" in label:
        return "single_exit_hr"
    if label.endswith("_baseline"):
        return "single_baseline"
    return "unknown"


# ── Ranking metrics — name → natural-best direction ──────────────────────────
# Used by _pick_best_per_band. "max" = larger is better (idxmax). "min" =
# smaller is better (idxmin). max_loss_usd is "max" because it's stored as
# a negative number (less negative = smaller loss = better).
_METRIC_DIRECTIONS: dict[str, str] = {
    # P&L (net of all costs)
    "avg_net_pnl":           "max",
    "sum_net_pnl":           "max",
    "avg_win_usd":           "max",
    "avg_loss_usd":          "max",  # losses negative → less negative is better
    "max_win_usd":           "max",
    "max_loss_usd":          "max",  # same — less negative is better
    "total_win_mtm":         "max",
    "total_loss_mtm":        "max",  # losses negative → less negative is better
    # % return
    "avg_pct_return_on_credit":  "max",
    "avg_pct_return_on_margin":  "max",
    "avg_pct_return_on_credit_winners": "max",
    "avg_pct_return_on_margin_winners": "max",
    "avg_pct_max_mtm_on_credit": "max",  # peak unrealized as % credit
    "avg_pct_min_mtm_on_credit": "max",  # trough as % credit (less negative better)
    # Risk (drawdown / streaks)
    "avg_min_mtm_losers":    "max",  # negative → less negative is better
    "avg_min_mtm_winners":   "max",  # winners' avg trough — drawdown winners endured
    "min_mtm_winners":       "max",  # worst trough across any winner — deepest pain a winner endured
    "avg_max_mtm_losers":    "max",  # higher peak among losers = they showed more profit before turning
    "min_mtm_losers":        "max",  # most-negative trough across all losers; less negative = better
    "max_mtm_losers":        "max",  # highest peak across all losers
    # Overall (all-trades) MTM composites — derived at grid-load time
    "avg_min_mtm":           "max",  # weighted mean of avg_min_mtm_winners and avg_min_mtm_losers
    "min_mtm":               "max",  # worst trough across any trade in cell
    "avg_max_mtm":           "max",  # weighted mean peak across all trades
    "max_mtm":               "max",  # best peak across any trade in cell
    # Composite — derived at grid-load time
    "composite_score":       "max",  # v1: win_rate × ret_on_credit ÷ (1 + |avg_min_mtm|/avg_credit)
    "composite_score_v2":    "max",  # v2: band-local min-max weighted score (5 components, hard-filtered)
    # Path peak-trough-peak (v6 — cell aggregates of per-trade path fields)
    "avg_peak_before_trough": "max",  # higher peak-1 = more profit ridden before turn
    "avg_peak_after_trough":  "max",  # higher peak-2 = stronger recovery
    "avg_pct_drop_peak_to_trough":   "min",  # smaller drop = less stress
    "avg_pct_recovery_trough_to_peak": "max",  # bigger recovery = more bounce
    "avg_alt_net_if_exit_at_peak1":  "max",  # bigger alt-net = more left on the table by actual exit
    # Risk-adjusted (v6 — computed at grid-load from stdev_net_pnl / stdev_losses_only)
    "sharpe_per_trade":      "max",  # avg_net / stdev_net
    "sortino_per_trade":     "max",  # avg_net / stdev_losses_only
    "calmar_like":           "max",  # avg_net / |max_loss|
    # Tail-risk (v6 — special metrics aggregated at grid build)
    "worst_5_avg_net":       "max",  # mean of 5 worst trades; less-negative = better
    "var_95_net":            "max",  # 5th percentile; less-negative = better
    "cvar_95_net":           "max",  # expected loss in tail; less-negative = better
    # Drawdown sequence
    "max_consec_loss_dollars": "max",  # negative $; less-negative = better
    # Edge stability (v6)
    "avg_net_pnl_last_26w":  "max",
    "win_rate_last_26w":     "max",
    # Fixed-hour exit count (v6 — separate from rule_trigger / hard_cap)
    "n_fixed_hour_ist":      "max",  # higher = rule was time-driven (deterministic)
    "stdev_net_pnl":         "min",  # lower vol = better consistency
    "avg_loss_mtm":          "max",  # less negative = better
    "largest_loss_mtm":      "max",  # less negative = better
    "max_consec_losses":     "min",
    "max_consec_sl_hits":    "min",
    "max_consec_premium_sl_hits": "min",  # fewer back-to-back real SL fires = better
    "n_premium_sl_hit":      "min",  # fewer real SL fires = better
    "n_rule_trigger":        "min",  # fewer rule-driven exits = better (loose interpretation)
    "n_hard_cap":            "min",  # fewer "ran to settlement" trades — proxy for "rule didn't fire"
    "n_losers_above_avg_max_mtm": "min",  # fewer missed-opportunity losers = better
    "avg_loser_exit_offset_minutes":  "min",  # shorter loser holds = less drag time
    # Win counts
    "win_rate":              "max",
    "n_wins":                "max",
    "n_losses":              "min",
    "n_trades":              "max",
}


# Metrics replicated from get_iv_band_summary's EXTRA_METRICS list — keeps
# this endpoint's row schema a superset of what the frontend already renders.
_EXTRA_METRICS = [
    "avg_net_pnl", "win_rate",
    "avg_loss_usd", "avg_win_usd",
    "avg_credit", "avg_margin",
    "avg_pct_return_on_margin", "avg_pct_return_on_credit",
    "avg_pct_return_on_margin_winners", "avg_pct_return_on_credit_winners",
    "avg_max_mtm_winners", "avg_min_mtm_winners",
    "max_mtm_winners", "min_mtm_winners",
    "avg_max_mtm_losers", "avg_min_mtm_losers",
    "max_mtm_losers", "min_mtm_losers",
    "max_loss_usd", "max_win_usd",
    "avg_exit_mtm",
    "avg_win_mtm", "largest_win_mtm", "total_win_mtm",
    "avg_loss_mtm", "largest_loss_mtm", "total_loss_mtm",
    "avg_pct_max_mtm_on_credit", "avg_pct_min_mtm_on_credit",
    "n_rule_trigger", "n_premium_sl_hit", "n_hard_cap", "n_losses", "n_wins",
    "max_consec_losses", "max_consec_wins",
    "max_consec_sl_hits", "max_consec_premium_sl_hits",
    "n_winners_below_avg_min_mtm", "n_losers_above_avg_max_mtm",
    # v6 additions — path peak-trough-peak (cells from new _SIMPLE_METRICS)
    "avg_peak_before_trough", "avg_peak_after_trough",
    "avg_rel_time_peak_before", "avg_rel_time_peak_after",
    "avg_rel_time_trough", "avg_rel_time_peak",
    "avg_pct_drop_peak_to_trough", "avg_pct_recovery_trough_to_peak",
    "avg_alt_net_if_exit_at_peak1",
    # v6 — risk-adjusted base columns
    "stdev_net_pnl", "stdev_losses_only",
    # v6 — tail risk / drawdown sequence / edge stability
    "worst_5_avg_net", "var_95_net", "cvar_95_net",
    "max_consec_loss_dollars",
    "avg_net_pnl_last_26w", "win_rate_last_26w",
    # v6 — fixed-hour exit counter
    "n_fixed_hour_ist",
]


def _band_sort_key(b) -> int:
    """Natural order: 0-20, 20-30, …, 100+."""
    if b == "100+":
        return 1000
    try:
        return int(str(b).split("-")[0])
    except (ValueError, AttributeError):
        return 9999


def _safe_mean(s: pd.Series) -> Optional[float]:
    """Mean that returns None on empty / all-NaN."""
    if s.empty:
        return None
    val = s.mean()
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return float(val)


def _compute_cell_metrics(sub: pd.DataFrame) -> dict:
    """Compute every EXTRA_METRIC + the three exit-time means for one cell.

    `sub` is the slice of derived trades for one (iv_band, expiry, delta, rule)
    cell. Empty cells are caller-handled (we never receive an empty sub here).
    """
    grp = sub.groupby(lambda _: 0, sort=False)  # single group → single row
    metrics: dict[str, Optional[float]] = {}
    for m in _EXTRA_METRICS:
        try:
            val = float(m7r._metric_score(grp, m).iloc[0])
            metrics[m] = m7r._round_score(m, val)
        except Exception:
            metrics[m] = None

    # Exit-time means — computed from exit_ts (unix sec) and entry_ts_utc.
    if "exit_ts" in sub.columns and "entry_ts_utc" in sub.columns:
        offset_min = (sub["exit_ts"].astype(float)
                      - sub["entry_ts_utc"].astype(float)) / 60.0
        metrics["avg_exit_offset_minutes"] = _safe_mean(offset_min)
        if "is_win" in sub.columns:
            metrics["avg_winner_exit_offset_minutes"] = _safe_mean(
                offset_min[sub["is_win"].astype(bool)]
            )
            metrics["avg_loser_exit_offset_minutes"] = _safe_mean(
                offset_min[~sub["is_win"].astype(bool)]
            )
        else:
            metrics["avg_winner_exit_offset_minutes"] = None
            metrics["avg_loser_exit_offset_minutes"] = None
    else:
        metrics["avg_exit_offset_minutes"] = None
        metrics["avg_winner_exit_offset_minutes"] = None
        metrics["avg_loser_exit_offset_minutes"] = None

    # Round exit-time fields to 1 decimal minute for the wire.
    for k in ("avg_exit_offset_minutes",
              "avg_winner_exit_offset_minutes",
              "avg_loser_exit_offset_minutes"):
        if metrics[k] is not None:
            metrics[k] = round(metrics[k], 1)

    metrics["n_trades"] = int(len(sub))
    return metrics


_BASE_GROUP_KEYS: tuple[str, ...] = (
    "entry_atm_iv_band", "expiry_bucket", "delta_target", "entry_hour_ist",
)


def _build_grid(
    progress_cb=None,
    extra_group_keys: tuple[str, ...] = (),
    dataset: str = "delta_match",
) -> pd.DataFrame:
    """Compute the full (iv_band × expiry × delta × entry_hour × ext... × rule) grid.

    Per rule, derive the full trade set once, group by the base 4 keys plus
    `extra_group_keys`, compute metrics, append rows. After processing each
    rule we DROP that rule's entry from `_EXIT_CACHE` to keep peak memory
    bounded (~16 MB × N rules otherwise; many WSL setups OOM-kill at ~750 MB
    resident).

    `extra_group_keys` lets the caller produce a regime-conditioned grid
    (e.g. add `("ivrv_bucket",)` to bucket by IV-RV richness, or
    `("ivrv_bucket", "slope_cn_bucket")` for the 3-axis Tab 3A grid). The
    extra columns must already exist on the per-trade table (attached by
    `m7_results._attach_ivrv_and_slope_buckets`).

    Optional `progress_cb(rules_done, rules_total)` is called after each
    rule finishes — used by `_warmup_thread` to update the public progress
    counter so 202 responses can show a useful "X/N" hint.
    """
    rows: list[dict] = []
    variants = _rule_variants(dataset)
    full_keys = list(_BASE_GROUP_KEYS) + list(extra_group_keys)
    for i, (rule_label, rule_dict) in enumerate(variants):
        # Track whether the cache existed before our call so we know whether
        # to evict afterwards. (Cache hit → leave it alone; cache miss → drop.)
        m7r._load_trades()  # ensure mtime is fresh
        rule_key = (
            __import__("json").dumps(rule_dict or {}, sort_keys=True),
            m7r._TRADES_MTIME,
        )
        was_cached_before = rule_key in m7r._EXIT_CACHE

        derived = m7r._derive_exits({}, rule_dict)
        if progress_cb is not None:
            progress_cb(i + 1, len(variants))

        if derived is not None and not derived.empty:
            # Drop trades with NaN gross_pnl — these are missing-leg-quote
            # trades (e.g. 0.10Δ put unpriced at entry) that aren't valid
            # strangles. Keeping them propagates NaN into cell aggregates:
            # is_win = NaN>0 = False (counted as loss) but mean/std are NaN
            # (displayed as —). Drop them so n_trades reflects only valid
            # observations.
            mask = (
                derived["entry_atm_iv_band"].notna()
                & derived["expiry_bucket"].notna()
                & derived["delta_target"].notna()
                & derived["entry_hour_ist"].notna()
                & derived["gross_pnl_usd"].notna()
            )
            # Also drop rows missing any extra-group key value (regime
            # column can be NaN/None for trades with missing source data).
            for k in extra_group_keys:
                if k in derived.columns:
                    mask &= derived[k].notna()
                else:
                    mask &= False  # extra key missing entirely → produce empty grid
            keep = derived[mask]
            if not keep.empty:
                grouped = keep.groupby(full_keys, dropna=False, sort=False)
                for key_vals, sub in grouped:
                    if sub.empty:
                        continue
                    # `key_vals` is a tuple of all groupby key values in order.
                    iv_band, expiry, delta, hour = key_vals[:4]
                    extras = dict(zip(extra_group_keys, key_vals[4:]))
                    cell = _compute_cell_metrics(sub)
                    cell.update({
                        "iv_band": iv_band,
                        "expiry_bucket": expiry,
                        "delta_target": float(delta) if delta is not None else None,
                        "entry_hour_ist": int(hour) if hour is not None else None,
                        "rule_label": rule_label,
                        "rule_category": _rule_category(rule_label),
                        "rule": rule_dict,
                        **extras,
                    })
                    rows.append(cell)

        # Memory hygiene — drop the freshly-populated cache entry now that
        # we've extracted everything we need from it.
        if not was_cached_before:
            m7r._EXIT_CACHE.pop(rule_key, None)
        del derived

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _flatten_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """The grid stores `rule` as a dict — pyarrow can't write nested dicts
    natively. Flatten to explicit float columns before persisting.

    Columns written (all float64, NaN when not applicable):
      rule_premium_sl_pct, rule_margin_target_pct, rule_fixed_exit_hour_ist
    max_profit_pct is intentionally omitted — removed from all rule variants.
    """
    if df.empty or "rule" not in df.columns:
        return df
    import numpy as np
    out = df.copy()
    _RULE_FIELDS = [
        ("rule_premium_sl_pct",       "premium_sl_pct"),
        ("rule_margin_target_pct",     "margin_target_pct"),
        ("rule_fixed_exit_hour_ist",   "fixed_exit_hour_ist"),
    ]
    for col, key in _RULE_FIELDS:
        out[col] = out["rule"].apply(
            lambda r, k=key: (r or {}).get(k)
        ).astype(float)  # None → NaN; pyarrow handles float64 NaN cleanly
    out = out.drop(columns=["rule"])
    return out


def _unflatten_after_load(df: pd.DataFrame) -> pd.DataFrame:
    """Inverse of `_flatten_for_parquet` — reconstruct nested rule dict."""
    if df.empty:
        return df
    # Support both old schema (rule_max_profit_pct) and new (rule_fixed_exit_hour_ist).
    # Exclude rule_label — it's a plain string column, not a rule-dict field.
    _RULE_FIELD_COLS = frozenset({
        "rule_premium_sl_pct", "rule_max_profit_pct",
        "rule_margin_target_pct", "rule_fixed_exit_hour_ist",
    })
    rule_cols = [c for c in df.columns if c in _RULE_FIELD_COLS]
    if not rule_cols:
        return df
    out = df.copy()

    _COL_TO_KEY = {
        "rule_premium_sl_pct":       "premium_sl_pct",
        "rule_max_profit_pct":       "max_profit_pct",
        "rule_margin_target_pct":    "margin_target_pct",
        "rule_fixed_exit_hour_ist":  "fixed_exit_hour_ist",
    }

    def _build_rule(row):
        d = {}
        for col in rule_cols:
            v = row.get(col)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            key = _COL_TO_KEY.get(col, col.replace("rule_", ""))
            d[key] = v
        return d

    out["rule"] = out[rule_cols].to_dict(orient="records")
    out["rule"] = out["rule"].apply(_build_rule)
    out = out.drop(columns=rule_cols)
    return out


def _grid_path_is_valid(path: str) -> bool:
    """True if the parquet snapshot at `path` exists AND is newer than the
    trades parquet AND has the expected rule-variant cardinality. The
    third check guards against stale grids from earlier `_rule_variants()`
    counts — without it, expanding the rule sweep (e.g. 21 → 96 variants)
    silently kept serving old data.

    Cross-checks against the appropriate trades parquet — `price_match`
    variants compare against the price-matched trades file, all others
    against the delta-match trades file.
    """
    if not os.path.exists(path):
        return False
    grid_mtime = os.path.getmtime(path)
    # Heuristic: if the grid path name hints at price_match, validate against
    # the price-matched trades parquet; otherwise the delta-match one.
    if "price_matched" in os.path.basename(path):
        trades_path = m7r.TRADES_PATH_PRICE_MATCHED
        dataset_for_count = "price_match"
    else:
        trades_path = (m7r.TRADES_ENRICHED_PATH
                       if os.path.exists(m7r.TRADES_ENRICHED_PATH)
                       else m7r.TRADES_PATH)
        dataset_for_count = "delta_match"
    if os.path.exists(trades_path) and grid_mtime < os.path.getmtime(trades_path):
        return False
    # Cardinality check — load only the rule_label column for speed.
    try:
        rule_labels = pd.read_parquet(path, columns=["rule_label"])
        cached_count = int(rule_labels["rule_label"].nunique())
        expected = len(_rule_variants(dataset_for_count))
        if cached_count != expected:
            log.warning(
                "M7 best-combo grid at %s has %d unique rule_labels but current "
                "_rule_variants() returns %d — treating as stale; will rebuild.",
                path, cached_count, expected,
            )
            return False
    except Exception as exc:  # noqa: BLE001
        log.warning("M7 best-combo grid cardinality check failed for %s: %s",
                    path, exc)
        return False
    return True


def _grid_cache_is_valid() -> bool:
    """Back-compat alias — primary grid path validity."""
    return _grid_path_is_valid(GRID_PARQUET_PATH)


def _enrich_grid_with_overall_mtm(df: pd.DataFrame) -> pd.DataFrame:
    """Add 'overall' (all-trades) MTM aggregates derived from per-side fields.

    These are mathematically exact composites of existing cell columns, so we
    can attach them at load time instead of requiring a v6 grid rebuild:

        avg_min_mtm = (avg_min_mtm_winners × n_wins + avg_min_mtm_losers × n_losses) / n_trades
        avg_max_mtm = (avg_max_mtm_winners × n_wins + avg_max_mtm_losers × n_losses) / n_trades
        min_mtm     = min(min_mtm_winners, min_mtm_losers)   — worst trough across any trade
        max_mtm     = max(max_mtm_winners, max_mtm_losers)   — best peak across any trade

    Skips cells where the required components are missing (cell has no
    winners or no losers — falls back to whichever side has data).
    """
    if df.empty:
        return df
    out = df.copy()
    n_w = pd.to_numeric(out.get("n_wins"), errors="coerce").fillna(0)
    n_l = pd.to_numeric(out.get("n_losses"), errors="coerce").fillna(0)
    n_t = pd.to_numeric(out.get("n_trades"), errors="coerce").replace(0, np.nan)
    a_min_w = pd.to_numeric(out.get("avg_min_mtm_winners"), errors="coerce")
    a_min_l = pd.to_numeric(out.get("avg_min_mtm_losers"),  errors="coerce")
    a_max_w = pd.to_numeric(out.get("avg_max_mtm_winners"), errors="coerce")
    a_max_l = pd.to_numeric(out.get("avg_max_mtm_losers"),  errors="coerce")
    # Weighted mean — fill missing side with 0× weight = 0 contribution.
    out["avg_min_mtm"] = (a_min_w.fillna(0) * n_w + a_min_l.fillna(0) * n_l) / n_t
    out["avg_max_mtm"] = (a_max_w.fillna(0) * n_w + a_max_l.fillna(0) * n_l) / n_t
    m_min_w = pd.to_numeric(out.get("min_mtm_winners"), errors="coerce")
    m_min_l = pd.to_numeric(out.get("min_mtm_losers"),  errors="coerce")
    m_max_w = pd.to_numeric(out.get("max_mtm_winners"), errors="coerce")
    m_max_l = pd.to_numeric(out.get("max_mtm_losers"),  errors="coerce")
    out["min_mtm"] = pd.concat([m_min_w, m_min_l], axis=1).min(axis=1, skipna=True)
    out["max_mtm"] = pd.concat([m_max_w, m_max_l], axis=1).max(axis=1, skipna=True)
    # sum_net_pnl = avg_net_pnl × n_trades (mathematically exact since both
    # are computed from the same set of per-trade net P&L values). Lets the
    # picker rank by "Total net P&L" without needing a grid rebuild.
    avg_net = pd.to_numeric(out.get("avg_net_pnl"), errors="coerce")
    n_total = pd.to_numeric(out.get("n_trades"), errors="coerce")
    out["sum_net_pnl"] = avg_net * n_total
    return out


def _attach_composite_score(df: pd.DataFrame) -> pd.DataFrame:
    """Composite-score column for capital-preservation ranking.

        composite_score = win_rate × avg_pct_return_on_credit
                          ÷ (1 + |avg_min_mtm| / avg_credit)

    All inputs are existing cell columns (overall avg_min_mtm comes from
    `_enrich_grid_with_overall_mtm` — call this AFTER that). Cells with
    n_losses=0 have avg_min_mtm=NaN, in which case the dd penalty is
    treated as 0 (no drawdown to penalise → composite = win_rate × ret).

    Higher = better risk-adjusted edge. Returns a NEW frame with
    `composite_score` column attached.
    """
    if df.empty:
        return df
    out = df.copy()
    def _series(col):
        if col not in out.columns:
            return pd.Series([np.nan] * len(out), index=out.index)
        return pd.to_numeric(out[col], errors="coerce")
    wr = _series("win_rate")
    ret = _series("avg_pct_return_on_credit")
    dd = _series("avg_min_mtm").abs()
    cr = _series("avg_credit").replace(0, np.nan)
    dd_norm = (dd / cr).fillna(0.0)
    out["composite_score"] = wr * ret / (1.0 + dd_norm)
    return out


def _attach_risk_adjusted(df: pd.DataFrame) -> pd.DataFrame:
    """Risk-adjusted ratios — computed at grid-load time when v6 columns exist.

        sharpe_per_trade  = avg_net_pnl / stdev_net_pnl  (full-distribution vol)
        sortino_per_trade = avg_net_pnl / stdev_losses_only  (downside vol)
        calmar_like       = avg_net_pnl / |max_loss_usd|  (return per unit of worst-case)

    Returns ratios as None when denominator is 0 or missing (avoids div-by-zero
    on single-trade or no-loss cells).
    """
    if df.empty:
        return df
    out = df.copy()
    def _series(col):
        if col not in out.columns:
            return pd.Series([np.nan] * len(out), index=out.index)
        return pd.to_numeric(out[col], errors="coerce")
    avg = _series("avg_net_pnl")
    std = _series("stdev_net_pnl").replace(0, np.nan)
    std_l = _series("stdev_losses_only").replace(0, np.nan)
    ml = _series("max_loss_usd").abs().replace(0, np.nan)
    out["sharpe_per_trade"] = avg / std
    out["sortino_per_trade"] = avg / std_l
    out["calmar_like"] = avg / ml
    return out


def _apply_composite_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Tag each cell with rank_status ∈ {ranked, low_n, filtered} and
    populate filter_reason. Hard gates per m7_ranking_config:
      - n_trades < LOW_N_THRESHOLD → filtered ("n<10")
      - LOW_N_THRESHOLD ≤ n_trades < MIN_N_TRADES → low_n
      - win_rate < MIN_WIN_RATE → filtered
      - |cvar_95_net| > MAX_CVAR_TO_CREDIT_RATIO × avg_credit → filtered
      - max_consec_losses > MAX_LOSING_STREAK → filtered
    Rows are NEVER dropped — UI handles visibility via filter chips.
    """
    from app.api.m7_ranking_config import (
        MIN_N_TRADES, LOW_N_THRESHOLD, MIN_WIN_RATE,
        MAX_CVAR_TO_CREDIT_RATIO, MAX_LOSING_STREAK,
    )
    if df.empty:
        return df
    out = df.copy()

    def _num(col, default=np.nan):
        if col not in out.columns:
            return pd.Series([default] * len(out), index=out.index, dtype=float)
        return pd.to_numeric(out[col], errors="coerce")

    n_arr = _num("n_trades", 0).fillna(0).to_numpy()
    wr_arr = _num("win_rate").fillna(0).to_numpy()
    cvar_arr = _num("cvar_95_net").abs().fillna(0).to_numpy()
    credit_arr = _num("avg_credit").fillna(0).to_numpy()
    streak_arr = _num("max_consec_losses", 0).fillna(0).to_numpy()

    is_low_n  = (n_arr >= LOW_N_THRESHOLD) & (n_arr < MIN_N_TRADES)
    is_drop_n = n_arr < LOW_N_THRESHOLD
    fail_wr = wr_arr < MIN_WIN_RATE
    fail_cvar = (credit_arr > 0) & (cvar_arr > MAX_CVAR_TO_CREDIT_RATIO * credit_arr)
    fail_streak = streak_arr > MAX_LOSING_STREAK

    reasons: list[list[str]] = [[] for _ in range(len(out))]
    statuses: list[str] = []
    for i in range(len(out)):
        rs = reasons[i]
        if is_drop_n[i]:
            rs.append(f"n<{LOW_N_THRESHOLD}")
        elif is_low_n[i]:
            rs.append(f"n<{MIN_N_TRADES}")
        if fail_wr[i]:
            rs.append(f"win_rate<{MIN_WIN_RATE:.2f}")
        if fail_cvar[i]:
            rs.append(f"|CVaR|>{MAX_CVAR_TO_CREDIT_RATIO:.1f}×credit")
        if fail_streak[i]:
            rs.append(f"losing_streak>{MAX_LOSING_STREAK}")
        any_non_n = fail_wr[i] or fail_cvar[i] or fail_streak[i]
        if is_drop_n[i] or any_non_n:
            statuses.append("filtered")
        elif is_low_n[i]:
            statuses.append("low_n")
        else:
            statuses.append("ranked")
    out["rank_status"] = statuses
    out["filter_reason"] = [",".join(r) for r in reasons]
    return out


def _attach_composite_score_v2(
    df: pd.DataFrame,
    group_keys: tuple[str, ...] = ("iv_band",),
) -> pd.DataFrame:
    """Band-local min-max-normalised weighted composite score.

    Components: sortino_per_trade, calmar_like, avg_net/|cvar_95_net|,
    avg_pct_return_on_margin, win_rate — weights in m7_ranking_config.
    Edge cases per spec:
      - sortino undefined (no losers)   → cap at 2 × sharpe_per_trade
      - calmar undefined (no drawdown)  → fill with 90th-percentile of
        defined calmars across the FULL grid (global-conservative)
      - cvar undefined / zero           → drop the avg_net/CVaR term,
        re-weight remaining four to sum to 1.0
    Filtered rows are excluded from min/max calibration but still
    receive a clipped score so they're sortable to the bottom.

    Attaches columns:
      composite_score_v2, composite_score_v2_components_used,
      score_components (JSON str), rank_in_band (Int64 within group_keys).
    """
    from app.api.m7_ranking_config import COMPOSITE_V2_WEIGHTS
    import json as _json
    if df.empty:
        return df
    out = df.copy()

    def _num(col):
        if col not in out.columns:
            return pd.Series([np.nan] * len(out), index=out.index, dtype=float)
        return pd.to_numeric(out[col], errors="coerce")

    sortino = _num("sortino_per_trade")
    sharpe  = _num("sharpe_per_trade")
    calmar  = _num("calmar_like")
    avg_net = _num("avg_net_pnl")
    cvar    = _num("cvar_95_net").abs()
    ret_m   = _num("avg_pct_return_on_margin")
    wr      = _num("win_rate")

    sortino_filled = sortino.where(sortino.notna(), sharpe.fillna(0) * 2.0)

    defined_calmars = calmar.dropna()
    calmar_fallback = (float(defined_calmars.quantile(0.9))
                       if len(defined_calmars) > 0 else 0.0)
    calmar_filled = calmar.where(calmar.notna(), calmar_fallback)

    anc = avg_net / cvar.where(cvar > 0)

    comp = pd.DataFrame({
        "sortino_per_trade":         sortino_filled,
        "calmar_like":               calmar_filled,
        "avg_net_to_abs_cvar_ratio": anc,
        "avg_pct_return_on_margin":  ret_m,
        "win_rate":                  wr,
    }, index=out.index)

    out["composite_score_v2_components_used"] = np.where(
        comp["avg_net_to_abs_cvar_ratio"].notna(), 5, 4,
    )

    if group_keys and all(k in out.columns for k in group_keys):
        group_id = out[list(group_keys)].astype(str).agg("|".join, axis=1)
    else:
        group_id = pd.Series(["__all__"] * len(out), index=out.index)

    ranked_mask = (
        out["rank_status"].astype(str).ne("filtered")
        if "rank_status" in out.columns
        else pd.Series([True] * len(out), index=out.index)
    )

    normed = pd.DataFrame(index=out.index, dtype=float)
    for comp_name in COMPOSITE_V2_WEIGHTS.keys():
        col = comp[comp_name]
        scaled_full = pd.Series(np.nan, index=out.index, dtype=float)
        for _gid, idx in group_id.groupby(group_id, sort=False).groups.items():
            gcol = col.loc[idx]
            ranked_gcol = gcol.where(ranked_mask.loc[idx])
            lo = ranked_gcol.min()
            hi = ranked_gcol.max()
            if pd.isna(lo) or pd.isna(hi) or hi <= lo:
                scaled_full.loc[idx] = np.where(gcol.notna(), 0.5, np.nan)
            else:
                scaled = (gcol - lo) / (hi - lo)
                scaled_full.loc[idx] = scaled.clip(0.0, 1.0)
        normed[comp_name] = scaled_full

    score_arr = np.full(len(out), np.nan, dtype=float)
    for i, idx in enumerate(out.index):
        row_weights = dict(COMPOSITE_V2_WEIGHTS)
        if pd.isna(normed.at[idx, "avg_net_to_abs_cvar_ratio"]):
            row_weights.pop("avg_net_to_abs_cvar_ratio")
            total = sum(row_weights.values()) or 1.0
            row_weights = {k: v / total for k, v in row_weights.items()}
        s, ok = 0.0, True
        for comp_name, w in row_weights.items():
            v = normed.at[idx, comp_name]
            if pd.isna(v):
                ok = False
                break
            s += w * float(v)
        score_arr[i] = s if ok else np.nan
    out["composite_score_v2"] = score_arr

    components_str: list[str] = []
    for idx in out.index:
        d = {k: (None if pd.isna(comp.at[idx, k]) else float(comp.at[idx, k]))
             for k in COMPOSITE_V2_WEIGHTS.keys()}
        components_str.append(_json.dumps(d))
    out["score_components"] = components_str

    sort_score = out["composite_score_v2"].fillna(-1.0).astype(float)
    if "rank_status" in out.columns:
        is_filtered = out["rank_status"].astype(str).eq("filtered").to_numpy()
        sort_score = sort_score.where(~is_filtered, sort_score - 1e6)
    out["_v2_sort"] = sort_score
    if group_keys and all(k in out.columns for k in group_keys):
        out["rank_in_band"] = (
            out.groupby(list(group_keys), sort=False, group_keys=False)["_v2_sort"]
               .rank(method="dense", ascending=False)
               .astype("Int64")
        )
    else:
        out["rank_in_band"] = out["_v2_sort"].rank(method="dense", ascending=False).astype("Int64")
    out = out.drop(columns=["_v2_sort"])
    return out


# Permanently-excluded expiry buckets — the longer-dated expiries
# (biweekly / monthly / quarterly) carry too few historical Fridays to
# be useful in the picker AND distort the search space. User decision
# 2026-05-14: only the four short expiries are used everywhere.
_ALLOWED_EXPIRIES = {
    "current (Sat)", "next (Sun)", "next_to_next (Mon)", "weekly (7d)",
}


def _try_load_grid_from_disk(dataset: str = "delta_match") -> Optional[pd.DataFrame]:
    """Load persisted grid for `dataset` if present and fresh; else None.

    delta_match: tries v6 first, then v4 fallback. v4 lacks path
    peak-trough-peak / risk-adjusted columns — those flow through as None /
    NaN. After load we enrich with overall (all-trades) MTM composites AND
    composite score, then drop the longer-dated expiry buckets per
    `_ALLOWED_EXPIRIES`.

    price_match: only the v6 price-matched grid is supported (no v4
    fallback).
    """
    if dataset == "price_match":
        candidate_paths = (GRID_PARQUET_PATH_PRICE_MATCHED,)
    else:
        candidate_paths = (GRID_PARQUET_PATH, _GRID_FALLBACK_PATH)
    for path in candidate_paths:
        if not _grid_path_is_valid(path):
            continue
        try:
            df = pd.read_parquet(path)
            df = _unflatten_after_load(df)
            df = _enrich_grid_with_overall_mtm(df)
            df = _attach_composite_score(df)
            df = _attach_risk_adjusted(df)
            # Composite v2 — runs AFTER expiry drop so normalisation only
            # considers allowed expiries (filtered cells get a score too
            # but sort to the bottom; the expiry drop happens first so
            # they don't pollute the scale).
            if "expiry_bucket" in df.columns:
                df = df[df["expiry_bucket"].isin(_ALLOWED_EXPIRIES)].copy()
            df = _apply_composite_filters(df)
            df = _attach_composite_score_v2(df, group_keys=("iv_band",))
            return df
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to load M7 best-combo grid from %s: %s",
                        path, exc)
    return None


def _persist_grid_to_disk(df: pd.DataFrame) -> None:
    """Write the grid to parquet so future restarts skip the rebuild."""
    if df is None or df.empty:
        return
    try:
        out = _flatten_for_parquet(df)
        # Pyarrow rejects pure-NaN object cols; coerce to float where safe.
        for c in out.columns:
            if out[c].dtype == object:
                # Try numeric coercion; leave strings alone
                if c in ("iv_band", "expiry_bucket", "rule_label"):
                    continue
                with pd.option_context("future.no_silent_downcasting", True):
                    out[c] = pd.to_numeric(out[c], errors="ignore")
        out.to_parquet(GRID_PARQUET_PATH, index=False)
        log.info("M7 best-combo grid persisted to %s (%d cells)",
                 GRID_PARQUET_PATH, len(out))
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to persist M7 best-combo grid: %s", exc)


# ── Background warmup ─────────────────────────────────────────────────────────

# Single-process state — fine for the single-uvicorn-worker deployment.
# Resets on every backend restart (intentional; keeps things simple and the
# grid is fully recomputable from disk parquets).
#
# Partitioned by dataset so delta_match and price_match warm independently.
# Each entry retains the original {status, rules_done, rules_total, grid,
# started_at, finished_at, error} shape. The `_GRID_STATE` global below
# proxies the delta_match entry for back-compat with callers that haven't
# been threaded yet.
def _new_grid_state() -> dict:
    return {
        "status": "pending",
        "rules_done": 0,
        "rules_total": len(_rule_variants()),
        "grid": None,
        "started_at": None,
        "finished_at": None,
        "error": None,
    }


_GRID_STATE_BY_DATASET: dict[str, dict] = {
    "delta_match": _new_grid_state(),
    "price_match": _new_grid_state(),
}

# Back-compat alias — points at delta_match. Module-level reads still work
# (no rebinding needed) because dicts are mutated in place. Endpoints that
# need dataset-aware behavior must use `_get_grid_state(dataset)` instead.
_GRID_STATE: dict = _GRID_STATE_BY_DATASET["delta_match"]
_GRID_LOCK = threading.Lock()


def _get_grid_state(dataset: str) -> dict:
    """Return the per-dataset grid state dict, creating it lazily."""
    if dataset not in _GRID_STATE_BY_DATASET:
        _GRID_STATE_BY_DATASET[dataset] = _new_grid_state()
    return _GRID_STATE_BY_DATASET[dataset]


def _get_grid(dataset: str) -> Optional[pd.DataFrame]:
    """Return the in-memory grid for `dataset`, attempting a disk-load on
    first access. Returns None if not yet built / failed to load."""
    state = _get_grid_state(dataset)
    if state["status"] != "ready":
        try_load_grid_only(dataset)
    return state.get("grid")


# ── Multi-dimensional bucketed grids (Phase B) ────────────────────────────────
# Each tab dispatches to a grid grouped by (band, hour, expiry, delta) plus
# extra regime columns. Tab "band" is the legacy single-grid behaviour.
# Other tabs are lazy-built on first request (cached on disk + in memory).
_TAB_DEFS: dict[str, tuple[str, ...]] = {
    "band":                  (),
    "band_ivrv":             ("ivrv_bucket",),
    "band_ivrv_slope_cn":    ("ivrv_bucket", "slope_cn_bucket"),
    "band_ivrv_slope_nn":    ("ivrv_bucket", "slope_nn_bucket"),
    "band_ivrv_slope_cnn":   ("ivrv_bucket", "slope_cnn_bucket"),
    "band_ivrv_ts_legacy":   ("ivrv_bucket", "ts_legacy_bucket"),
}


def _bucketed_grid_path(tab: str, dataset: str = "delta_match") -> str:
    suffix = "_price_matched" if dataset == "price_match" else ""
    return os.path.join(
        m7r.M7_BASE_DIR,
        f"m7_best_combo_grid_v7_{tab}{suffix}.parquet",
    )


# Each entry keyed by (dataset, tab):
#   {"status": "pending"|"building"|"ready"|"error", "grid": df, "error": str}
def _new_bucket_state() -> dict:
    return {"status": "pending", "grid": None, "error": None}


_BUCKETED_GRIDS: dict[tuple[str, str], dict] = {
    (ds, name): _new_bucket_state()
    for ds in ("delta_match", "price_match")
    for name in _TAB_DEFS.keys()
    if name != "band"
}
_BUCKETED_LOCK = threading.Lock()


def _get_bucket_state(dataset: str, tab: str) -> dict:
    """Per (dataset, tab) bucket state, lazily created."""
    key = (dataset, tab)
    if key not in _BUCKETED_GRIDS:
        _BUCKETED_GRIDS[key] = _new_bucket_state()
    return _BUCKETED_GRIDS[key]


def _post_load_enrich(df: pd.DataFrame, group_keys: tuple[str, ...]) -> pd.DataFrame:
    """Run the same post-build enrichment used on the base grid:
    overall MTM composites, v1 composite, risk-adjusted, expiry drop,
    composite filters, composite v2 (normalised within group_keys).
    Called from both disk-load and freshly-built paths so the schema
    matches across both."""
    df = _enrich_grid_with_overall_mtm(df)
    df = _attach_composite_score(df)
    df = _attach_risk_adjusted(df)
    if "expiry_bucket" in df.columns:
        df = df[df["expiry_bucket"].isin(_ALLOWED_EXPIRIES)].copy()
    df = _apply_composite_filters(df)
    df = _attach_composite_score_v2(df, group_keys=group_keys or ("iv_band",))
    return df


def _build_and_cache_bucketed_grid(tab: str, dataset: str = "delta_match") -> None:
    """Build the grid for `tab`, persist to disk, store in _BUCKETED_GRIDS.
    Called inside _BUCKETED_LOCK so concurrent requests don't double-build.
    Run synchronously in the request thread so the caller blocks until ready.
    """
    if tab not in _TAB_DEFS or tab == "band":
        raise ValueError(f"bad tab name: {tab}")

    extra = _TAB_DEFS[tab]
    state = _get_bucket_state(dataset, tab)
    state["status"] = "building"
    state["error"] = None
    try:
        # Try disk first.
        disk_path = _bucketed_grid_path(tab, dataset)
        if _grid_path_is_valid(disk_path):
            grid = pd.read_parquet(disk_path)
            grid = _unflatten_after_load(grid)
            grid = _post_load_enrich(
                grid,
                group_keys=("iv_band",) + tuple(extra),
            )
            state["grid"] = grid
            state["status"] = "ready"
            log.info("M7 bucketed grid '%s' loaded from disk (%d cells)",
                     tab, len(grid))
            return

        # Build inline. Per-trade table must already have the bucket cols
        # (m7r._load_trades attaches them at load time).
        log.info("M7 bucketed grid '%s' — building (extra_group_keys=%s)",
                 tab, extra)
        t0 = time.time()
        grid = _build_grid(progress_cb=None, extra_group_keys=extra)
        if grid is None or grid.empty:
            state["status"] = "error"
            state["error"] = "empty grid (per-trade bucket columns missing?)"
            return
        # Drop low-n cells (noise floor) per the spec for bucketed tabs.
        from app.api.m7_ranking_config import LOW_N_THRESHOLD
        if "n_trades" in grid.columns:
            grid = grid[grid["n_trades"].fillna(0).astype(int) >= LOW_N_THRESHOLD].copy()
        # Persist (without the dict-typed `rule` column).
        try:
            flat = _flatten_for_parquet(grid)
            flat.to_parquet(disk_path, index=False)
            log.info("M7 bucketed grid '%s' persisted to %s", tab, disk_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to persist bucketed grid '%s': %s", tab, exc)
        # Final enrichment pass — composites + v2 normalised within group.
        grid = _post_load_enrich(grid, group_keys=("iv_band",) + tuple(extra))
        state["grid"] = grid
        state["status"] = "ready"
        log.info("M7 bucketed grid '%s' built in %.1fs (%d cells)",
                 tab, time.time() - t0, len(grid))
    except Exception as exc:  # noqa: BLE001
        state["status"] = "error"
        state["error"] = repr(exc)
        log.exception("M7 bucketed grid '%s' build failed", tab)


def get_grid_for_tab(tab: str, dataset: str = "delta_match") -> Optional[pd.DataFrame]:
    """Return the grid for `tab` + `dataset`, lazy-building if needed.
    Returns None when not yet ready (caller should return a 'building'
    status to the client). Tab='band' returns the per-dataset main grid
    from `_GRID_STATE_BY_DATASET`."""
    if tab == "band":
        return _get_grid_state(dataset).get("grid")
    if tab not in _TAB_DEFS:
        return None
    state = _get_bucket_state(dataset, tab)
    if state["status"] == "ready":
        return state["grid"]
    with _BUCKETED_LOCK:
        # Re-check inside the lock.
        if state["status"] == "ready":
            return state["grid"]
        if state["status"] == "error":
            return None
        # Build inline. Blocks the request thread (~5-30s on warm exit cache;
        # longer when cold). Subsequent requests hit the cache.
        _build_and_cache_bucketed_grid(tab, dataset=dataset)
        return state.get("grid") if state["status"] == "ready" else None


def bucketed_grid_status(tab: str, dataset: str = "delta_match") -> dict:
    """Public status accessor for /iv_band_best_combo/status."""
    if tab == "band":
        gs = _get_grid_state(dataset)
        return {"status": gs.get("status"), "n_cells":
                int(0 if gs.get("grid") is None
                    else len(gs["grid"]))}
    if tab not in _TAB_DEFS:
        return {"status": "unknown_tab"}
    state = _get_bucket_state(dataset, tab)
    return {
        "status": state["status"],
        "n_cells": int(0 if state["grid"] is None else len(state["grid"])),
        "error": state["error"],
    }


def _warmup_thread() -> None:
    """Run the grid build in a background thread, updating `_GRID_STATE`.

    Persists the result to `GRID_PARQUET_PATH` so future backend restarts
    skip the rebuild entirely.
    """
    def _on_progress(done: int, total: int) -> None:
        _GRID_STATE["rules_done"] = done
        _GRID_STATE["rules_total"] = total

    try:
        _GRID_STATE["status"] = "warming"
        _GRID_STATE["started_at"] = time.time()
        grid = _build_grid(progress_cb=_on_progress)
        # Mirror the load-path filter: keep only the four allowed expiries
        # in the in-memory grid so the in-process build matches the cached
        # disk-load path. The persisted parquet keeps all expiries (cheap)
        # so we can change _ALLOWED_EXPIRIES later without a rebuild.
        if grid is not None and not grid.empty and "expiry_bucket" in grid.columns:
            grid = grid[grid["expiry_bucket"].isin(_ALLOWED_EXPIRIES)].copy()
        _GRID_STATE["grid"] = grid
        _GRID_STATE["status"] = "ready"
        _GRID_STATE["finished_at"] = time.time()
        # Persist AFTER marking ready — even if the disk write fails the
        # grid is usable in-memory for this process lifetime.
        _persist_grid_to_disk(grid)
    except Exception as exc:  # noqa: BLE001 — surface to the endpoint
        _GRID_STATE["status"] = "error"
        _GRID_STATE["error"] = repr(exc)
        _GRID_STATE["finished_at"] = time.time()


def try_load_grid_only(dataset: str = "delta_match") -> bool:
    """Load the grid from disk for `dataset` if a fresh cache exists. Never
    spawns a background build — the build is run out-of-process by
    `scripts/build_m7_best_combo_grid.py` (delta_match) or
    `scripts/build_m7_best_combo_grid_price_matched.py` (price_match).
    Returns True iff a cached grid was loaded.
    """
    state = _get_grid_state(dataset)
    with _GRID_LOCK:
        if state["status"] == "ready":
            return True
        cached = _try_load_grid_from_disk(dataset)
        if cached is not None and not cached.empty:
            state["grid"] = cached
            state["status"] = "ready"
            state["rules_done"] = state["rules_total"]
            state["started_at"] = time.time()
            state["finished_at"] = time.time()
            log.info("M7 best-combo grid (%s) loaded from disk cache (%d cells)",
                     dataset, len(cached))
            return True
    return False


def kick_off_warmup(dataset: str = "delta_match") -> bool:
    """DEPRECATED: kept for backwards compatibility. The build is now run
    out-of-process via `scripts/build_m7_best_combo_grid.py`. This function
    only loads from disk if present. Returns False (never spawns a thread).
    """
    return try_load_grid_only(dataset) and False


def _resolve_metric(name: str) -> str:
    """Map historical short-form names to the actual grid column.
    Keeps the older `ranking=credit|margin` URLs working."""
    aliases = {
        "credit": "avg_pct_return_on_credit",
        "margin": "avg_pct_return_on_margin",
    }
    return aliases.get(name, name)


def _idx_best(series: pd.Series, direction: str) -> int:
    """Index of best value in a Series under the given direction."""
    return series.idxmin() if direction == "min" else series.idxmax()


# ── Aggregate across entry hours ─────────────────────────────────────────────
# When the user wants "every Friday tested" for a band (not just the Fridays
# whose IV happened to land in this band at one specific hour), we collapse
# the entry_hour_ist dimension. Picker then chooses one (expiry, Δ, rule) per
# band; the displayed metrics are the weighted aggregate across every hour
# where that band's IV appeared during a Friday entry.

# Per-column aggregation strategy when collapsing entry_hour_ist:
#   SUM           — counts and totals
#   MAX           — best across hours (peaks, max streaks, top wins)
#   MIN           — worst across hours (most-negative loss / VaR / drawdowns)
#   WEIGHTED_MEAN — averages and %s, weighted by n_trades
# Anything not enumerated falls to weighted mean by default.
_AGG_SUM = {
    "n_trades", "n_wins", "n_losses",
    "n_rule_trigger", "n_premium_sl_hit", "n_hard_cap", "n_fixed_hour_ist",
    "n_winners_below_avg_min_mtm", "n_losers_above_avg_max_mtm",
    "total_win_mtm", "total_loss_mtm",
}
_AGG_MAX = {
    "max_win_usd",
    "max_mtm_winners", "max_mtm_losers", "max_mtm",
    "max_consec_wins", "max_consec_losses",
    "max_consec_sl_hits", "max_consec_premium_sl_hits",
    "largest_win_mtm",
}
_AGG_MIN = {
    "max_loss_usd",
    "min_mtm_winners", "min_mtm_losers", "min_mtm",
    "largest_loss_mtm",
    "worst_5_avg_net", "var_95_net", "cvar_95_net",
    "max_consec_loss_dollars",
}
_AGG_FIRST = {
    "iv_band", "expiry_bucket", "delta_target", "rule_label",
    "entry_hour_ist", "rule",
}


def _aggregate_across_hours(grid: pd.DataFrame) -> pd.DataFrame:
    """Collapse the entry_hour_ist dimension (vectorised pandas groupby).

    Group by (iv_band, expiry_bucket, delta_target, rule_label) and aggregate.
    Returned frame has one row per (band, expiry, Δ, rule) — n_trades reflects
    the total Fridays this combo would have entered across every hour that
    band touched. `entry_hour_ist` is set to None (sentinel for "all hours";
    frontend renders as "All hours").

    Composite/Sharpe/Sortino/Calmar are re-derived after aggregation since
    they're functions of the aggregated inputs.
    """
    if grid.empty:
        return grid.iloc[0:0]
    g = grid.copy()
    group_keys = ["iv_band", "expiry_bucket", "delta_target", "rule_label"]
    for k in group_keys:
        if k not in g.columns:
            return g
    n_tr = pd.to_numeric(g.get("n_trades"), errors="coerce").fillna(0.0)
    g["_n_tr"] = n_tr

    numeric_cols: list[str] = [
        c for c in g.columns
        if c not in group_keys
        and c not in _AGG_FIRST
        and c != "_n_tr"
        and pd.api.types.is_numeric_dtype(g[c])
    ]
    sum_cols = [c for c in numeric_cols if c in _AGG_SUM]
    max_cols = [c for c in numeric_cols if c in _AGG_MAX]
    min_cols = [c for c in numeric_cols if c in _AGG_MIN]
    used = set(sum_cols) | set(max_cols) | set(min_cols)
    wmean_cols = [c for c in numeric_cols if c not in used]

    # Pre-multiply weighted-mean columns by weights so groupby.sum() gives
    # the weighted total; we divide by the per-group weight sum afterwards.
    for c in wmean_cols:
        g[f"__w_{c}"] = pd.to_numeric(g[c], errors="coerce") * g["_n_tr"]
        g[f"__wn_{c}"] = pd.to_numeric(g[c], errors="coerce").notna().astype(float) * g["_n_tr"]

    agg_dict: dict = {}
    for c in sum_cols:
        agg_dict[c] = "sum"
    for c in max_cols:
        agg_dict[c] = "max"
    for c in min_cols:
        agg_dict[c] = "min"
    for c in wmean_cols:
        agg_dict[f"__w_{c}"] = "sum"
        agg_dict[f"__wn_{c}"] = "sum"

    gb = g.groupby(group_keys, dropna=False, sort=False, observed=True)
    agg = gb.agg(agg_dict).reset_index()

    # Compute weighted means: weighted_sum / weight_sum_present.
    for c in wmean_cols:
        ws = agg[f"__w_{c}"]
        wn = agg[f"__wn_{c}"]
        agg[c] = np.where(wn > 0, ws / wn, np.nan)
        agg.drop(columns=[f"__w_{c}", f"__wn_{c}"], inplace=True)

    # Re-derive win_rate from summed counts (exact, not weighted).
    if "n_trades" in agg.columns and "n_wins" in agg.columns:
        nt = pd.to_numeric(agg["n_trades"], errors="coerce")
        nw = pd.to_numeric(agg["n_wins"], errors="coerce")
        agg["win_rate"] = np.where(nt > 0, nw / nt, np.nan)

    agg["entry_hour_ist"] = None  # collapsed

    # Carry rule dict if present — first non-null per group.
    if "rule" in g.columns:
        rule_first = (
            g.dropna(subset=["rule"])
             .groupby(group_keys, dropna=False, sort=False, observed=True)["rule"]
             .first()
             .reset_index()
        )
        agg = agg.merge(rule_first, on=group_keys, how="left")

    # Re-derive composites that depend on aggregated inputs.
    agg = _enrich_grid_with_overall_mtm(agg)
    agg = _attach_composite_score(agg)
    agg = _attach_risk_adjusted(agg)
    agg = _apply_composite_filters(agg)
    agg = _attach_composite_score_v2(agg, group_keys=("iv_band",))
    return agg


def _attach_lots_column(
    grid: pd.DataFrame,
    total_capital_usd: Optional[float],
    pct_deploy: float,
    dd_metric: Optional[str] = None,
    dd_threshold: Optional[float] = None,
    *,
    dd_constraints: Optional[list[tuple[str, float]]] = None,
) -> pd.DataFrame:
    """Compute per-cell `lots` given capital + optional drawdown constraints.

    Backtester sized every trade at 100 lots; cell metrics are mean/sum/etc.
    over those 100-lot trades. Portfolio margin engine is linear in qty:
    margin(N) = margin(100) × N/100 exactly. So:

      lots_from_margin = floor(deployable_capital × 100 / avg_margin)
      lots_from_dd_i   = floor(|threshold_i| × 100 / |metric_i_per_100|)  per constraint
      lots             = max(0, min(lots_from_margin, lots_from_dd_1, lots_from_dd_2, ...))

    Multiple DD constraints (e.g. "cap by min_mtm AND avg_loss") combine via
    `min()` — the most restrictive constraint wins. Order of constraints in
    the list does not matter.

    Where deployable_capital = total_capital_usd × pct_deploy / 100.

    If `total_capital_usd` is None or non-positive, `lots = 100` (unscaled —
    metrics displayed as the backtester baseline). Returns a copy of `grid`
    with `_lots` column attached.
    """
    out = grid.copy()
    if total_capital_usd is None or total_capital_usd <= 0:
        out["_lots"] = 100  # backtester baseline → no scaling
        return out

    deployable = float(total_capital_usd) * max(0.0, float(pct_deploy)) / 100.0
    BIG = 10 ** 9  # effectively-infinite cap when a constraint isn't set
    am = out["avg_margin"].astype(float) if "avg_margin" in out.columns else None
    if am is None:
        out["_lots"] = 0
        return out
    lots_margin = np.where(am > 0, np.floor(deployable * 100.0 / am.where(am > 0, 1.0)), 0)

    # Build the constraint list — combine legacy single + new list.
    constraints: list[tuple[str, float]] = []
    if dd_metric and dd_threshold is not None:
        constraints.append((dd_metric, abs(float(dd_threshold))))
    if dd_constraints:
        for m, t in dd_constraints:
            if m and t is not None:
                constraints.append((m, abs(float(t))))

    if constraints:
        lots_dd = np.full(len(out), float(BIG))
        for metric, thr in constraints:
            if metric not in out.columns:
                continue
            mv = out[metric].astype(float).abs()
            lots_per = np.where(mv > 0,
                                 np.floor(thr * 100.0 / mv.where(mv > 0, 1.0)),
                                 BIG)
            lots_dd = np.minimum(lots_dd, lots_per)
    else:
        lots_dd = BIG

    lots = np.minimum(lots_margin, lots_dd)
    out["_lots"] = np.clip(lots, 0, None).astype(int)
    return out


def _pick_best_per_band(
    grid: pd.DataFrame,
    ranking: str,
    *,
    secondary: Optional[str] = None,
    tolerance_pct: Optional[float] = None,
    total_capital_usd: Optional[float] = None,
    pct_deploy: float = 100.0,
    dd_metric: Optional[str] = None,
    dd_threshold: Optional[float] = None,
    dd_constraints: Optional[list[tuple[str, float]]] = None,
    min_hit_pct: Optional[float] = 50.0,
    max_loss_cap_pct: Optional[float] = None,
    max_drop_peak_to_trough_pct: Optional[float] = None,
    min_n_trades: int = 5,
    min_win_rate: Optional[float] = None,
    max_losing_streak: Optional[int] = None,
) -> pd.DataFrame:
    """For each IV band, pick one cell.

    Pure mode (no `secondary`): per-band idxmax/idxmin on `ranking`.
    Tiebreak mode (secondary given): per band, find primary's best value,
    keep cells whose primary is within `tolerance_pct` of that best, then
    pick by secondary.

    Tolerance is relative — `tolerance_pct=5.0` keeps cells whose primary
    differs from the best by ≤ 5% of |best|. This works across metric units
    (USD, %, counts) without unit-specific logic.

    Filters applied BEFORE ranking (compose with AND):
    - `min_hit_pct` (default 50): drop cells where the labelled rule didn't
      fire on ≥ X% of trades. Hit % = (n_trades − n_hard_cap) / n_trades,
      which counts ANY deterministic non-hard-cap exit (rule_trigger,
      premium_sl, max_profit, margin_target, fixed_hour) as "effective".
      Set to 0 to disable.
    - `max_loss_cap_pct`: when capital sizing is on, drop cells whose
      scaled |max_loss| × lots/100 > deployable × cap%.
    - `max_drop_peak_to_trough_pct`: drop cells whose avg peak→trough
      drop exceeds this fraction. Only effective after v6 grid (column
      `avg_pct_drop_peak_to_trough` exists).

    NO n-gate — every band that has any cells shows up.
    """
    primary_col = _resolve_metric(ranking)
    if grid.empty or primary_col not in grid.columns:
        return grid.iloc[0:0]

    # composite_score_v2 — hard-filter gates apply: exclude cells tagged
    # rank_status="filtered" from the picker entirely. They still appear in
    # the table (rank_in_band is set) but never get picked for "best of band."
    # Other ranking metrics (avg_net_pnl, composite_score v1, sortino, etc.)
    # use only the user-controlled filters below.
    if primary_col == "composite_score_v2" and "rank_status" in grid.columns:
        grid = grid[grid["rank_status"].astype(str).ne("filtered")].copy()
        if grid.empty:
            return grid.iloc[0:0]

    # Sample-size filter — drops cells with too few trades to be statistically
    # credible. With STRICT-first / FALLBACK pattern: try min_n_trades first,
    # fall back per-band if no cells survive (so high-IV bands with only n=1
    # cells still show up rather than disappearing). The fallback is recorded
    # as `_low_sample_warning` on the row so the UI can flag it.
    if min_n_trades is not None and int(min_n_trades) > 0 and "n_trades" in grid.columns:
        thr = int(min_n_trades)
        strict_grid = grid[grid["n_trades"].fillna(0).astype(int) >= thr].copy()
        # Bands that have NO cell meeting strict threshold → fall back to
        # the full grid for those bands only. Tag survivors so the UI knows.
        strict_bands = set(strict_grid["iv_band"].dropna().unique()) if not strict_grid.empty else set()
        if "iv_band" in grid.columns:
            fallback_grid = grid[~grid["iv_band"].isin(strict_bands)].copy()
        else:
            fallback_grid = grid.iloc[0:0].copy()
        strict_grid["_low_sample_warning"] = False
        fallback_grid["_low_sample_warning"] = True
        grid = pd.concat([strict_grid, fallback_grid], ignore_index=False)
        if grid.empty:
            return grid.iloc[0:0]

    # Hit-% filter — done FIRST, before any sizing so it composes uniformly.
    if min_hit_pct is not None and float(min_hit_pct) > 0 and "n_hard_cap" in grid.columns and "n_trades" in grid.columns:
        n_tr = grid["n_trades"].astype(float)
        nhc = grid["n_hard_cap"].astype(float)
        # Effective hit % = fraction of trades that exited via a deterministic
        # firing (rule_trigger, premium_sl_hit, max_profit, margin_target,
        # fixed_hour). Hard-cap exits are the complement.
        eff_hit = np.where(n_tr > 0, (n_tr - nhc) / n_tr * 100.0, 0.0)
        grid = grid[eff_hit >= float(min_hit_pct)].copy()
        if grid.empty:
            return grid.iloc[0:0]

    # Max losing streak — drop cells whose worst losing streak exceeds the cap.
    # Streaks don't scale with lots; raw column comparison.
    if (max_losing_streak is not None
            and int(max_losing_streak) > 0
            and "max_consec_losses" in grid.columns):
        cap = int(max_losing_streak)
        ls = pd.to_numeric(grid["max_consec_losses"], errors="coerce")
        # NaN streak → drop (we can't certify it's within cap).
        grid = grid[ls.notna() & (ls.astype(int) <= cap)].copy()
        if grid.empty:
            return grid.iloc[0:0]

    # Win-rate floor — drop cells with win_rate below the user's tolerance.
    if min_win_rate is not None and float(min_win_rate) > 0 and "win_rate" in grid.columns:
        wr = pd.to_numeric(grid["win_rate"], errors="coerce")
        thr = float(min_win_rate) / 100.0
        grid = grid[wr.fillna(-1.0) >= thr].copy()
        if grid.empty:
            return grid.iloc[0:0]

    # Peak→trough hard filter (only after v6 grid lands).
    if (max_drop_peak_to_trough_pct is not None
            and float(max_drop_peak_to_trough_pct) > 0
            and "avg_pct_drop_peak_to_trough" in grid.columns):
        cap = float(max_drop_peak_to_trough_pct) / 100.0
        drop_col = grid["avg_pct_drop_peak_to_trough"].astype(float)
        # Keep cells where drop is unknown (NaN — pre-v6) OR within cap.
        grid = grid[drop_col.isna() | (drop_col <= cap)].copy()
        if grid.empty:
            return grid.iloc[0:0]

    # Capital-sizing: compute per-cell `lots` and re-rank on the scaled
    # primary metric (primary × lots/100) when capital is provided. Cells
    # where lots == 0 (margin too high or DD constraint blocks them) drop
    # out of contention because their scaled primary is 0.
    sizing_active = total_capital_usd is not None and total_capital_usd > 0
    if sizing_active:
        grid = _attach_lots_column(
            grid, total_capital_usd, pct_deploy, dd_metric, dd_threshold,
            dd_constraints=dd_constraints,
        )
        # Max-loss cap — drop cells where scaled |max_loss| exceeds the cap.
        if (max_loss_cap_pct is not None
                and float(max_loss_cap_pct) > 0
                and "max_loss_usd" in grid.columns):
            deployable = float(total_capital_usd) * max(0.0, float(pct_deploy)) / 100.0
            cap_dollars = deployable * float(max_loss_cap_pct) / 100.0
            scaled_max_loss = (grid["max_loss_usd"].astype(float).abs()
                               * grid["_lots"].astype(float) / 100.0)
            # NaN max_loss → drop (we don't know the worst-case loss; can't
            # certify it's within budget). This complements the upstream
            # NaN-gross drop in _build_grid.
            grid = grid[scaled_max_loss.notna() & (scaled_max_loss <= cap_dollars)].copy()
            if grid.empty:
                return grid.iloc[0:0]
        # Build a scaled-primary column for ranking. Primary metric in $ or %
        # scales linearly with lots; we use that for re-rank only — original
        # per-100 columns stay untouched in `grid` for downstream display.
        scaled_col = "_scaled_primary"
        grid[scaled_col] = grid[primary_col].astype(float) * grid["_lots"] / 100.0
        ranking_col_for_pick = scaled_col
    else:
        grid["_lots"] = 100  # baseline → no scaling factor
        ranking_col_for_pick = primary_col

    primary_dir = _METRIC_DIRECTIONS.get(primary_col, "max")
    valid = grid.dropna(subset=[ranking_col_for_pick])
    if valid.empty:
        return grid.iloc[0:0]

    use_tiebreak = (
        secondary is not None
        and tolerance_pct is not None
        and tolerance_pct > 0
    )
    secondary_col = _resolve_metric(secondary) if use_tiebreak else None
    if use_tiebreak and (secondary_col not in valid.columns):
        # Secondary not present in grid — silently fall back to pure mode.
        use_tiebreak = False
    secondary_dir = (
        _METRIC_DIRECTIONS.get(secondary_col, "max") if use_tiebreak else None
    )

    rows: list[pd.Series] = []
    for band, sub in valid.groupby("iv_band", dropna=False, sort=False):
        if sub.empty:
            continue
        if not use_tiebreak:
            idx = _idx_best(sub[ranking_col_for_pick], primary_dir)
            rows.append(sub.loc[idx])
            continue
        # Tiebreak: filter to within-tolerance of band's best, then pick by secondary.
        # Tolerance is computed on the *picking* column (scaled primary when
        # sizing is active, plain primary otherwise).
        best_val = (sub[ranking_col_for_pick].min() if primary_dir == "min"
                    else sub[ranking_col_for_pick].max())
        if best_val is None or pd.isna(best_val):
            continue
        denom = abs(float(best_val)) if best_val != 0 else 1e-9
        delta = abs(sub[ranking_col_for_pick].astype(float) - float(best_val))
        within = sub[(delta / denom) * 100.0 <= float(tolerance_pct)]
        sub_for_secondary = within.dropna(subset=[secondary_col])
        if sub_for_secondary.empty:
            idx = _idx_best(sub[ranking_col_for_pick], primary_dir)
            rows.append(sub.loc[idx])
            continue
        idx = _idx_best(sub_for_secondary[secondary_col], secondary_dir)
        rows.append(sub_for_secondary.loc[idx])

    if not rows:
        return valid.iloc[0:0]
    best = pd.DataFrame(rows).copy()
    best["score"] = best[primary_col]  # report ORIGINAL per-100-lot primary
    if use_tiebreak and secondary_col is not None:
        best["secondary_score"] = best[secondary_col]
    if "_lots" in best.columns:
        best["lots"] = best["_lots"].astype(int)
        best = best.drop(columns=["_lots"])
    if "_scaled_primary" in best.columns:
        best = best.drop(columns=["_scaled_primary"])
    best = best.sort_values(
        "iv_band",
        key=lambda s: s.map(_band_sort_key),
    ).reset_index(drop=True)
    return best





def _records(df: pd.DataFrame) -> list[dict]:
    """JSON-safe records — NaN/NaT → None, numpy scalars → python primitives."""
    if df.empty:
        return []
    out: list[dict] = []
    for rec in df.replace({np.nan: None, pd.NaT: None}).to_dict(orient="records"):
        clean: dict = {}
        for k, v in rec.items():
            if v is None:
                clean[k] = None
            elif isinstance(v, (np.integer,)):
                clean[k] = int(v)
            elif isinstance(v, (np.floating,)):
                clean[k] = None if math.isnan(float(v)) else float(v)
            elif isinstance(v, (np.bool_,)):
                clean[k] = bool(v)
            else:
                clean[k] = v
        out.append(clean)
    return out


# ── Endpoint ──────────────────────────────────────────────────────────────────

_VALID_RANKINGS = set(_METRIC_DIRECTIONS.keys()) | {"credit", "margin"}
_VALID_RULE_FAMILIES = {"all", "max_profit", "margin_target"}
# Union of premium-SL pct values across all datasets. delta_match uses all
# five; price_match uses the first three. Validation accepts any of the five —
# a request for an SL that isn't in the active dataset just yields zero rows
# for that SL (no exception), which is friendlier than 400'ing the request.
_VALID_PREMIUM_SLS = {50, 75, 100, 150, 200}


def _apply_dimension_filters(
    grid: pd.DataFrame,
    expiry_buckets: Optional[str],
    delta_targets: Optional[str],
    entry_hours: Optional[str],
    iv_bands: Optional[str] = None,
    exit_hours: Optional[str] = None,
    premium_sl_pcts: Optional[str] = None,
) -> pd.DataFrame:
    """Pre-filter the grid by IV band / expiry / delta / hour before per-band picking.

    Each param is a comma-separated CSV ('' or None disables that filter).
    Lets the user constrain the picker's search space (e.g. only Saturday
    expiry and Δ ≤ 0.20 — useful when the picker keeps choosing
    high-delta straddles you don't want to trade).

    iv_bands lets you isolate one or a subset of bands (e.g. "30-40,40-50")
    so the picker only emits picks for those bands — useful when comparing
    per-band best-fits side-by-side without the noise of the other 8 bands.

    exit_hours restricts to fixed-hour rule variants `sl{X}_exit_hr_{h}` whose
    hour-suffix is in the CSV (suffix matches `_hour_label()`: '8'..'17' and
    '1729' for 17:29). When set, non-fixed-hour rule families (baseline,
    max_profit, margin_target) are excluded by construction.

    premium_sl_pcts restricts to rule variants whose label starts with
    `sl{X}_` for X in the CSV (e.g. "50,75" keeps only the SL=50 and SL=75
    variants across every family). Unknown values are dropped silently.
    """
    if iv_bands:
        keep_b = {s.strip() for s in iv_bands.split(",") if s.strip()}
        if keep_b and "iv_band" in grid.columns:
            grid = grid[grid["iv_band"].astype(str).isin(keep_b)]
    if expiry_buckets:
        keep = {s.strip() for s in expiry_buckets.split(",") if s.strip()}
        if keep:
            grid = grid[grid["expiry_bucket"].isin(keep)]
    if delta_targets:
        try:
            keep_d = {round(float(s.strip()), 2) for s in delta_targets.split(",") if s.strip()}
        except ValueError:
            keep_d = set()
        if keep_d:
            grid = grid[grid["delta_target"].astype(float).round(2).isin(keep_d)]
    if entry_hours:
        try:
            keep_h = {int(s.strip()) for s in entry_hours.split(",") if s.strip()}
        except ValueError:
            keep_h = set()
        if keep_h and "entry_hour_ist" in grid.columns:
            # Handle None (aggregate_hours mode) — pass through; else filter
            mask = grid["entry_hour_ist"].isna() | grid["entry_hour_ist"].astype("Int64").isin(keep_h)
            grid = grid[mask]
    if exit_hours:
        keep_eh = {s.strip() for s in exit_hours.split(",") if s.strip()}
        if keep_eh and "rule_label" in grid.columns:
            suffix_pat = "|".join(re.escape(s) for s in keep_eh)
            grid = grid[grid["rule_label"].astype(str).str.contains(
                rf"_exit_hr_({suffix_pat})$", regex=True, na=False)]
    if premium_sl_pcts:
        try:
            keep_sl = {int(s.strip()) for s in premium_sl_pcts.split(",") if s.strip()}
        except ValueError:
            keep_sl = set()
        keep_sl &= _VALID_PREMIUM_SLS
        if keep_sl and "rule_label" in grid.columns:
            prefix_pat = "|".join(f"sl{x}" for x in sorted(keep_sl))
            grid = grid[grid["rule_label"].astype(str).str.contains(
                rf"^(?:{prefix_pat})_", regex=True, na=False)]
    return grid


def _parse_dd_constraints(
    dd_metrics: Optional[str],
    dd_thresholds: Optional[str],
) -> list[tuple[str, float]]:
    """Parse CSV `dd_metrics` + CSV `dd_thresholds` into [(metric, threshold), ...].

    Pairs are formed by index. Returns empty list if either input is missing
    or counts mismatch. Each threshold is coerced to abs(float); invalid
    entries are skipped.
    """
    if not dd_metrics or not dd_thresholds:
        return []
    metrics = [s.strip() for s in dd_metrics.split(",") if s.strip()]
    thresholds_raw = [s.strip() for s in dd_thresholds.split(",") if s.strip()]
    if len(metrics) != len(thresholds_raw):
        return []
    out: list[tuple[str, float]] = []
    for m, t_str in zip(metrics, thresholds_raw):
        try:
            t = abs(float(t_str))
        except (ValueError, TypeError):
            continue
        out.append((m, t))
    return out


def _filter_grid_by_family(grid: pd.DataFrame, rule_family: str) -> pd.DataFrame:
    """Restrict the grid to a take-profit family before per-band picking.

    'max_profit'    → only sl{X}_max_profit_{Y} rules
    'margin_target' → only sl{X}_margin_target_{Y} rules
    'all'           → no filter (every variant in play)
    """
    if rule_family == "max_profit":
        return grid[grid["rule_label"].str.contains("_max_profit_", na=False)]
    if rule_family == "margin_target":
        return grid[grid["rule_label"].str.contains("_margin_target_", na=False)]
    return grid


@router.get("/iv_band_best_combo")
def get_iv_band_best_combo(
    ranking: str = Query("avg_net_pnl",
                         description="Primary metric. Any key in _METRIC_DIRECTIONS, or legacy 'credit'/'margin'."),
    secondary: Optional[str] = Query(None,
                                     description="Tiebreak metric. When given, cells within tolerance_pct of the per-band primary best are re-ranked by this."),
    tolerance_pct: float = Query(5.0, ge=0.0, le=100.0,
                                  description="Relative tolerance (% of |primary best|). Only used when secondary is provided."),
    rule_family: str = Query("all",
                             description="Restrict rule space. 'all' | 'max_profit' | 'margin_target'."),
    total_capital_usd: Optional[float] = Query(None, ge=0,
                                                description="Total deployable USD capital. When provided, per-cell lots is computed and the primary metric is re-ranked on the scaled-by-lots value."),
    pct_deploy: float = Query(100.0, ge=0, le=100,
                              description="Percent of total_capital_usd actually deployed (default 100). Deployable = capital × pct/100."),
    dd_metric: Optional[str] = Query(None,
                                      description="Legacy single drawdown-constraint metric (e.g. avg_loss_usd, avg_min_mtm_losers, max_loss_usd). Cell's per-100-lot value × lots/100 must be ≤ |dd_threshold|. Combined with dd_metrics/dd_thresholds via min()."),
    dd_threshold: Optional[float] = Query(None,
                                           description="Threshold for dd_metric (absolute value used for comparison)."),
    dd_metrics: Optional[str] = Query(None,
                                       description="CSV list of drawdown-constraint metrics (e.g. 'avg_loss_usd,avg_min_mtm_losers,max_loss_usd'). Must have same length as dd_thresholds. Each (metric, threshold) caps lots independently; final lots = min across all caps + the legacy single + the margin cap."),
    dd_thresholds: Optional[str] = Query(None,
                                          description="CSV list of thresholds matching dd_metrics (absolute USD)."),
    min_hit_pct: float = Query(50.0, ge=0.0, le=100.0,
                                description="Filter out cells where the labelled rule didn't fire on ≥ X% of trades (effective hit % = (n_trades − n_hard_cap) / n_trades). Default 50 — set to 0 to disable."),
    max_loss_cap_pct: Optional[float] = Query(None, ge=0.0, le=100.0,
                                              description="When capital sizing is on, drop cells whose scaled |max_loss| × lots/100 exceeds this fraction of deployable capital."),
    max_drop_peak_to_trough_pct: Optional[float] = Query(None, ge=0.0, le=100.0,
                                                         description="Drop cells whose avg peak→trough drop exceeds this %. Effective after v6 grid lands (column avg_pct_drop_peak_to_trough)."),
    min_n_trades: int = Query(5, ge=0,
                              description="Minimum n_trades for statistical credibility. Cells below this are dropped per band. If no cells in a band meet the threshold, the band falls back to the full grid and survivors are tagged with _low_sample_warning. Default 5; set to 0 to disable."),
    min_win_rate: Optional[float] = Query(None, ge=0.0, le=100.0,
                                          description="Filter out cells whose win_rate is below this percentage (0–100). Default off."),
    max_losing_streak: Optional[int] = Query(None, ge=1,
                                              description="Drop cells whose max_consec_losses (longest run of consecutive losing trades) exceeds X. Default off."),
    pick_mode: str = Query("by_hour",
                            description="'by_hour' (default) picks one cell per (band, hour); 'aggregate_hours' collapses the entry_hour dimension so each band's pick reflects every Friday whose IV landed in that band across all entry hours — much larger n_trades per pick."),
    expiry_buckets: Optional[str] = Query(None,
        description="CSV whitelist of expiry buckets (e.g. 'current (Sat),next (Sun)'). Empty/absent = no filter."),
    delta_targets: Optional[str] = Query(None,
        description="CSV whitelist of delta targets (e.g. '0.1,0.2,0.5'). Empty/absent = no filter."),
    entry_hours: Optional[str] = Query(None,
        description="CSV whitelist of entry hours IST (e.g. '21,22,23'). Empty/absent = no filter."),
    iv_bands: Optional[str] = Query(None,
        description="CSV whitelist of IV bands (e.g. '30-40,40-50'). Empty/absent = all 10 bands. Useful to focus the picker on one band for per-band best-fit testing."),
    exit_hours: Optional[str] = Query(None,
        description="CSV whitelist of fixed-exit hour suffixes (e.g. '14,15,1729'). Values match _hour_label() output. When set, the picker restricts to sl{X}_exit_hr_{h} rule variants whose hour matches — non-fixed-hour rule families (baseline, max_profit, margin_target) are dropped."),
    premium_sl_pcts: Optional[str] = Query(None,
        description="CSV whitelist of premium-SL pct values (e.g. '50,75'). When set, only rule variants whose label starts with sl{X}_ for X in the list are kept. Composes with rule_family. Unknown values silently dropped. Empty/absent = all SLs (no filter)."),
    tab: str = Query("band",
        description="Multi-dim bucketing tab. 'band' (default — legacy single grid) | 'band_ivrv' | 'band_ivrv_slope_cn' | 'band_ivrv_slope_nn' | 'band_ivrv_slope_cnn' | 'band_ivrv_ts_legacy'. Bucketed tabs lazy-build their grid on first request (~5-30s, persisted on disk)."),
    ivrv_bucket: Optional[str] = Query(None,
        description="When using a bucketed tab, optional filter to a single IVRV bucket ('rich' | 'fair' | 'cheap'). Empty = no filter."),
    slope_bucket: Optional[str] = Query(None,
        description="When using a slope tab, optional filter to a single slope bucket ('backwardation' | 'neutral' | 'contango'). Empty = no filter."),
    include_grid: bool = Query(False,
                               description="If true, also return the full grid"),
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match' for joint "
        "delta+price-matched parquet. When 'price_match' and the price-matched "
        "grid isn't built yet, returns status='no_data'."),
):
    """For each of the 10 IV bands, the best (expiry, delta, exit_rule) combo
    by the chosen ranking metric.

    Sweep: 96 rule variants — premium_sl ∈ {50, 75, 100} × {baseline, 10
    max_profit, 10 margin_target, 11 fixed_hour} = 32 per SL × 3 = 96.

    Multi-criteria selection (optional): pass `secondary` and `tolerance_pct`
    to filter per-band cells to within tolerance of the primary's best, then
    re-rank by `secondary`. Lets the user trade off (e.g.) net P&L against
    drawdown.

    Returns 200 once the background grid is ready. Returns 202 with progress
    while warming. Either kicks off the warmup if it hasn't started yet.
    """
    if dataset == "price_match":
        if not os.path.exists(GRID_PARQUET_PATH_PRICE_MATCHED):
            return _price_match_no_data_payload({
                "ranking": ranking, "secondary": secondary,
                "tolerance_pct": tolerance_pct, "rule_family": rule_family,
                "tab": tab, "dataset": dataset,
            })
    if ranking not in _VALID_RANKINGS:
        raise HTTPException(status_code=400,
                            detail=f"ranking must be one of {sorted(_VALID_RANKINGS)}")
    if secondary is not None and secondary not in _VALID_RANKINGS:
        raise HTTPException(status_code=400,
                            detail=f"secondary must be one of {sorted(_VALID_RANKINGS)}")
    if rule_family not in _VALID_RULE_FAMILIES:
        raise HTTPException(status_code=400,
                            detail=f"rule_family must be one of {sorted(_VALID_RULE_FAMILIES)}")
    if pick_mode not in ("by_hour", "aggregate_hours"):
        raise HTTPException(status_code=400,
                            detail="pick_mode must be 'by_hour' or 'aggregate_hours'")
    if tab not in _TAB_DEFS:
        raise HTTPException(status_code=400,
                            detail=f"tab must be one of {sorted(_TAB_DEFS.keys())}")

    # Try fast disk-load (idempotent — no-op if already ready).
    try_load_grid_only(dataset)
    grid_state = _get_grid_state(dataset)

    if grid_state["status"] == "pending":
        # Grid not built — instruct caller to run the CLI builder.
        return {
            "ranking": ranking,
            "secondary": secondary,
            "tolerance_pct": tolerance_pct,
            "status": "not_built",
            "message": "Grid not built. Run "
                       "`docker exec docker-backend-1 python -m "
                       "app.scripts.build_m7_best_combo_grid` to build.",
            "rows": [],
        }
    if grid_state["status"] == "error":
        raise HTTPException(status_code=500,
                            detail=f"warmup failed: {grid_state['error']}")

    # Multi-tab dispatch — lazy-builds bucketed grids on first request.
    if tab == "band":
        grid: pd.DataFrame = grid_state["grid"]
    else:
        grid = get_grid_for_tab(tab, dataset=dataset)
        if grid is None:
            state = _get_bucket_state(dataset, tab)
            return {
                "ranking": ranking, "tab": tab,
                "status": state.get("status", "unknown"),
                "rows": [], "n_rules": 0, "n_cells": 0,
                "error": state.get("error"),
            }
    if grid is None or grid.empty:
        return {"ranking": ranking, "secondary": secondary,
                "tolerance_pct": tolerance_pct,
                "rule_family": rule_family,
                "tab": tab,
                "status": "ready", "rows": [],
                "n_rules": 0, "n_cells": 0}

    family_grid = _filter_grid_by_family(grid, rule_family)
    # Dimension whitelists — applied BEFORE aggregation/picker so they
    # constrain the search space exactly the way the user expects.
    family_grid = _apply_dimension_filters(
        family_grid, expiry_buckets, delta_targets, entry_hours,
        iv_bands=iv_bands, exit_hours=exit_hours,
        premium_sl_pcts=premium_sl_pcts,
    )
    # Bucketed-tab dimension filters — narrow to a specific IVRV / slope
    # bucket so the user can drill into "rich+contango" cells, etc.
    if ivrv_bucket and "ivrv_bucket" in family_grid.columns:
        family_grid = family_grid[family_grid["ivrv_bucket"] == ivrv_bucket]
    if slope_bucket:
        # Try each of the slope columns this tab might have.
        for col in ("slope_cn_bucket", "slope_nn_bucket",
                    "slope_cnn_bucket", "ts_legacy_bucket"):
            if col in family_grid.columns:
                family_grid = family_grid[family_grid[col] == slope_bucket]
                break
    if pick_mode == "aggregate_hours":
        # Collapse entry_hour_ist BEFORE filters/picker so n_trades reflects
        # all-hours coverage and the picker chooses from the aggregated grid.
        family_grid = _aggregate_across_hours(family_grid)

    # Parse CSV multi-DD-cap params into list of (metric, threshold) tuples.
    dd_constraints_list = _parse_dd_constraints(dd_metrics, dd_thresholds)

    best = _pick_best_per_band(
        family_grid, ranking,
        secondary=secondary,
        tolerance_pct=tolerance_pct if secondary else None,
        total_capital_usd=total_capital_usd,
        pct_deploy=pct_deploy,
        dd_metric=dd_metric,
        dd_threshold=dd_threshold,
        dd_constraints=dd_constraints_list,
        min_hit_pct=min_hit_pct,
        max_loss_cap_pct=max_loss_cap_pct,
        max_drop_peak_to_trough_pct=max_drop_peak_to_trough_pct,
        min_n_trades=min_n_trades,
        min_win_rate=min_win_rate,
        max_losing_streak=max_losing_streak,
    )

    # Best fallback exit-hour — for each picked cell where the rule doesn't
    # always fire, find the highest-avg_net `sl{X}_exit_hr_*` variant at the
    # SAME (band, expiry, Δ, hour) on the UNFILTERED grid (ignoring family
    # restriction; we want the best deterministic fallback exit regardless
    # of the user's primary family). Attach the fallback's hour + avg_net to
    # each picked row so the parent table can render a column.
    if not best.empty and "rule_label" in grid.columns:
        fallback_hours: list[Optional[int]] = []
        fallback_nets: list[Optional[float]] = []
        fallback_labels: list[Optional[str]] = []
        # The grid we search is the UNFILTERED in-memory grid, so exit_hr_*
        # rules are always available regardless of the parent's rule_family.
        for _, prow in best.iterrows():
            sub = grid[
                (grid["iv_band"] == prow["iv_band"])
                & (grid["expiry_bucket"] == prow["expiry_bucket"])
                & (np.isclose(grid["delta_target"].astype(float),
                              float(prow["delta_target"]), atol=0.001))
                & (grid["entry_hour_ist"] == prow["entry_hour_ist"])
                & (grid["rule_label"].str.contains("_exit_hr_", na=False))
            ]
            if sub.empty:
                fallback_hours.append(None); fallback_nets.append(None); fallback_labels.append(None)
                continue
            # Pick the fallback by avg_net_pnl per-100 (apples-to-apples vs
            # picked rule; scaling happens client-side via lots).
            idx = sub["avg_net_pnl"].astype(float).idxmax()
            top = sub.loc[idx]
            fallback_labels.append(top["rule_label"])
            fallback_nets.append(float(top["avg_net_pnl"]))
            # Parse hour from label, e.g. sl100_exit_hr_15 → 15, _1729 → 17.5
            label = str(top["rule_label"])
            try:
                tail = label.rsplit("_exit_hr_", 1)[1]
                if tail == "1729":
                    fallback_hours.append(17)
                else:
                    fallback_hours.append(int(tail))
            except Exception:
                fallback_hours.append(None)
        best = best.copy()
        best["fallback_exit_hour"] = fallback_hours
        best["fallback_exit_avg_net"] = fallback_nets
        best["fallback_exit_rule_label"] = fallback_labels

    payload = {
        "ranking": ranking,
        "secondary": secondary,
        "tolerance_pct": tolerance_pct,
        "rule_family": rule_family,
        "premium_sl_pcts": premium_sl_pcts,
        "total_capital_usd": total_capital_usd,
        "pct_deploy": pct_deploy,
        "dd_metric": dd_metric,
        "dd_threshold": dd_threshold,
        "dd_metrics": dd_metrics,
        "dd_thresholds": dd_thresholds,
        "dd_constraints_applied": [{"metric": m, "threshold": t}
                                    for m, t in dd_constraints_list],
        "min_hit_pct": min_hit_pct,
        "max_loss_cap_pct": max_loss_cap_pct,
        "max_drop_peak_to_trough_pct": max_drop_peak_to_trough_pct,
        "min_n_trades": min_n_trades,
        "min_win_rate": min_win_rate,
        "max_losing_streak": max_losing_streak,
        "pick_mode": pick_mode,
        "status": "ready",
        "rows": _records(best),
        "n_rules": len(_rule_variants()),
        "n_cells": int(len(family_grid)),
    }
    if include_grid:
        payload["grid"] = _records(family_grid)
    return payload


@router.get("/iv_band_best_combo/status")
def get_iv_band_best_combo_status(
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """Lightweight progress probe for clients polling during warmup."""
    state = _get_grid_state(dataset)
    return {
        "status": state["status"],
        "rules_done": int(state["rules_done"]),
        "rules_total": int(state["rules_total"]),
        "started_at": state["started_at"],
        "finished_at": state["finished_at"],
        "error": state["error"],
    }


# ── Diagnostic endpoints ──────────────────────────────────────────────────────


@router.get("/iv_band_best_combo/rule_comparison")
def get_rule_comparison(
    band: str = Query(..., description="IV band, e.g. '20-30'"),
    expiry_bucket: str = Query(..., description="Expiry bucket label"),
    delta_target: float = Query(..., ge=0.0, le=1.0),
    entry_hour_ist: int = Query(..., ge=0, le=23),
    total_capital_usd: Optional[float] = Query(None, ge=0,
        description="When set, apply per-rule sizing under the same constraints as the main picker."),
    pct_deploy: float = Query(100.0, ge=0, le=100),
    dd_metric: Optional[str] = Query(None,
        description="Legacy single DD-cap metric column. Mirrors /iv_band_best_combo."),
    dd_threshold: Optional[float] = Query(None,
        description="Legacy DD-cap threshold (per-100-lot absolute value)."),
    dd_metrics: Optional[str] = Query(None,
        description="CSV list of DD-cap metrics. Mirrors /iv_band_best_combo."),
    dd_thresholds: Optional[str] = Query(None,
        description="CSV list of DD-cap thresholds matching dd_metrics."),
    min_hit_pct: float = Query(0.0, ge=0.0, le=100.0,
        description="Tag rules where hit % < this as filtered (mirrors main picker)."),
    max_loss_cap_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    max_drop_peak_to_trough_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    min_n_trades: int = Query(0, ge=0),
    min_win_rate: Optional[float] = Query(None, ge=0.0, le=100.0),
    rule_family: str = Query("all",
        description="Mirrors parent picker: 'all' (no family filter) | 'max_profit' (only sl{X}_max_profit_*) | 'margin_target' (only sl{X}_margin_target_*). Rules outside the family are tagged so the user sees why the picker ignored them."),
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """Return all rule variants for a fixed (band, expiry, delta, hour) cell.

    When sizing params are provided, also returns the *per-rule* `lots` (and
    `scaled_avg_net_pnl`, `scaled_max_loss_usd`) so the user can see exactly
    what the picker optimises on. Without sizing, baseline per-100-lot
    columns are returned.
    """
    if dataset == "price_match":
        if not os.path.exists(GRID_PARQUET_PATH_PRICE_MATCHED):
            return _price_match_no_data_payload({"dataset": dataset})
    try_load_grid_only(dataset)
    grid_state = _get_grid_state(dataset)
    if grid_state["status"] != "ready":
        return {"rows": [], "status": grid_state["status"]}
    grid: pd.DataFrame = grid_state["grid"]
    if grid is None or grid.empty:
        return {"rows": [], "status": "empty"}
    sub = grid[
        (grid["iv_band"] == band)
        & (grid["expiry_bucket"] == expiry_bucket)
        & (np.isclose(grid["delta_target"].astype(float), float(delta_target), atol=0.001))
        & (grid["entry_hour_ist"] == int(entry_hour_ist))
    ].copy()
    if sub.empty:
        return {"rows": [], "status": "ready", "band": band,
                "expiry_bucket": expiry_bucket,
                "delta_target": delta_target,
                "entry_hour_ist": entry_hour_ist}
    # Hit % = fraction of non-hard-cap exits.
    n_tr = sub["n_trades"].astype(float)
    nhc = sub["n_hard_cap"].astype(float)
    sub["hit_pct"] = np.where(n_tr > 0, (n_tr - nhc) / n_tr, np.nan)

    # Per-rule sizing — same code path as _pick_best_per_band so the modal
    # mirrors what the picker actually sees. Each rule gets its own lots
    # because each has its own avg_margin and per-100 value of dd_metric.
    sizing_active = total_capital_usd is not None and total_capital_usd > 0
    dd_constraints_list = _parse_dd_constraints(dd_metrics, dd_thresholds)
    if sizing_active:
        sub = _attach_lots_column(
            sub, total_capital_usd, pct_deploy, dd_metric, dd_threshold,
            dd_constraints=dd_constraints_list,
        )
        sub["lots"] = sub["_lots"].astype(int)
        sub = sub.drop(columns=["_lots"], errors="ignore")
    else:
        sub["lots"] = 100  # baseline

    # Backend-computed scaled columns so the frontend doesn't have to
    # recompute. Picker ranks on scaled_avg_net_pnl.
    sub["scaled_avg_net_pnl"] = sub["avg_net_pnl"].astype(float) * sub["lots"] / 100.0
    if "max_loss_usd" in sub.columns:
        sub["scaled_max_loss_usd"] = (
            sub["max_loss_usd"].astype(float) * sub["lots"] / 100.0
        )

    # Tag each row with which filter(s) would have excluded it from the picker.
    # Modal renders these as badges so the user understands why higher-ranking
    # alternatives may not have been picked under Conservative-preset filters.
    filter_reasons: list[list[str]] = [[] for _ in range(len(sub))]
    # Family filter — rules outside the active family aren't in the picker's
    # consideration set. Tag them with a dedicated reason so users see this.
    if rule_family == "max_profit":
        mask = ~sub["rule_label"].str.contains("_max_profit_", na=False)
        for i, bad in enumerate(mask.to_numpy()):
            if bool(bad):
                filter_reasons[i].append("family:max_profit")
    elif rule_family == "margin_target":
        mask = ~sub["rule_label"].str.contains("_margin_target_", na=False)
        for i, bad in enumerate(mask.to_numpy()):
            if bool(bad):
                filter_reasons[i].append("family:margin_target")
    if min_hit_pct > 0:
        hit_mask = (sub["hit_pct"].fillna(-1.0) * 100.0) < float(min_hit_pct)
        for i, bad in enumerate(hit_mask.to_numpy()):
            if bool(bad):
                filter_reasons[i].append(f"hit<{int(min_hit_pct)}")
    if min_n_trades > 0 and "n_trades" in sub.columns:
        n_mask = sub["n_trades"].fillna(0).astype(int) < int(min_n_trades)
        for i, bad in enumerate(n_mask.to_numpy()):
            if bool(bad):
                filter_reasons[i].append(f"n<{int(min_n_trades)}")
    if min_win_rate is not None and float(min_win_rate) > 0 and "win_rate" in sub.columns:
        wr_mask = sub["win_rate"].fillna(-1.0) < (float(min_win_rate) / 100.0)
        for i, bad in enumerate(wr_mask.to_numpy()):
            if bool(bad):
                filter_reasons[i].append(f"win%<{int(min_win_rate)}")
    if (max_drop_peak_to_trough_pct is not None
            and float(max_drop_peak_to_trough_pct) > 0
            and "avg_pct_drop_peak_to_trough" in sub.columns):
        cap = float(max_drop_peak_to_trough_pct) / 100.0
        drop_mask = sub["avg_pct_drop_peak_to_trough"].astype(float).fillna(-1.0) > cap
        for i, bad in enumerate(drop_mask.to_numpy()):
            if bool(bad):
                filter_reasons[i].append(f"drop>{int(max_drop_peak_to_trough_pct)}%")
    if (sizing_active and max_loss_cap_pct is not None
            and float(max_loss_cap_pct) > 0 and "scaled_max_loss_usd" in sub.columns):
        deployable = float(total_capital_usd) * max(0.0, float(pct_deploy)) / 100.0
        cap_dollars = deployable * float(max_loss_cap_pct) / 100.0
        ml_mask = sub["scaled_max_loss_usd"].astype(float).abs() > cap_dollars
        for i, bad in enumerate(ml_mask.fillna(False).to_numpy()):
            if bool(bad):
                filter_reasons[i].append(f"loss>{int(max_loss_cap_pct)}%")
    sub["filter_reasons"] = ["; ".join(r) for r in filter_reasons]
    sub["filtered_out"] = [len(r) > 0 for r in filter_reasons]

    sub = sub.sort_values(["hit_pct", "avg_net_pnl"], ascending=[False, False])
    return {
        "rows": _records(sub),
        "status": "ready",
        "band": band,
        "expiry_bucket": expiry_bucket,
        "delta_target": delta_target,
        "entry_hour_ist": entry_hour_ist,
        "n_rules": int(len(sub)),
        "sizing_active": sizing_active,
        "total_capital_usd": total_capital_usd,
        "pct_deploy": pct_deploy,
        "dd_metric": dd_metric,
        "dd_threshold": dd_threshold,
        "dd_metrics": dd_metrics,
        "dd_thresholds": dd_thresholds,
        "dd_constraints_applied": [{"metric": m, "threshold": t}
                                    for m, t in dd_constraints_list],
    }


@router.get("/iv_band_best_combo/cross_band_check")
def get_cross_band_check(
    band: str = Query(..., description="The picked cell's band (e.g. '0-20')"),
    expiry_bucket: str = Query(..., description="The picked cell's expiry bucket"),
    delta_target: float = Query(..., ge=0.0, le=1.0),
    entry_hour_ist: int = Query(..., ge=0, le=23),
    rule_label: str = Query(..., description="Rule label of the picked cell, e.g. sl75_max_profit_25"),
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """For the picked cell (rule + entry_hour + expiry + delta), compute
    its P&L decomposed by which IV band each Friday's actual entry IV landed
    in. Answers 'does this combo's rule generalise across IV regimes?'
    """
    if dataset == "price_match":
        if not os.path.exists(GRID_PARQUET_PATH_PRICE_MATCHED):
            return _price_match_no_data_payload({"dataset": dataset})
    try_load_grid_only(dataset)
    grid_state = _get_grid_state(dataset)
    if grid_state["status"] != "ready":
        return {"rows": [], "status": grid_state["status"]}
    grid: pd.DataFrame = grid_state["grid"]
    if grid is None or grid.empty:
        return {"rows": [], "status": "empty"}
    # Same (expiry, delta, hour, rule) — all 10 bands' cells.
    sub = grid[
        (grid["expiry_bucket"] == expiry_bucket)
        & (np.isclose(grid["delta_target"].astype(float), float(delta_target), atol=0.001))
        & (grid["entry_hour_ist"] == int(entry_hour_ist))
        & (grid["rule_label"] == rule_label)
    ].copy()
    if sub.empty:
        return {"rows": [], "status": "ready"}
    sub = sub.sort_values("iv_band", key=lambda s: s.map(_band_sort_key))
    return {
        "rows": _records(sub),
        "status": "ready",
        "picked_band": band,
        "expiry_bucket": expiry_bucket,
        "delta_target": delta_target,
        "entry_hour_ist": entry_hour_ist,
        "rule_label": rule_label,
    }


# ── cell_friday_detail — Friday-level drilldown for one Best Combo cell ───────

# Columns surfaced per trade row. Same shape across all four buckets (losers,
# worst_winner, largest_win, winners_below_avg_min_mtm) so the frontend can
# use a single renderer.
_CELL_FRIDAY_DETAIL_COLS = [
    "trade_id",
    "friday_date_ist",
    "net_pnl_estimate_usd",
    "min_mtm_usd",
    "max_mtm_usd",
    "exit_reason",
]


def _friday_detail_row(r: pd.Series) -> dict:
    """Coerce one per-trade row to the wire shape. Floats are rounded to 2dp
    and NaN is mapped to None to keep the JSON payload clean."""
    out: dict = {}
    for c in _CELL_FRIDAY_DETAIL_COLS:
        v = r.get(c)
        if c == "trade_id":
            out[c] = "" if v is None else str(v)
        elif c == "friday_date_ist":
            out[c] = "" if v is None else str(v)
        elif c == "exit_reason":
            out[c] = "" if v is None else str(v)
        else:
            try:
                fv = float(v)
                out[c] = None if math.isnan(fv) else round(fv, 2)
            except (TypeError, ValueError):
                out[c] = None
    return out


def _compute_cell_friday_detail(
    *,
    band: str,
    expiry_bucket: str,
    delta_target: float,
    entry_hour_ist: int,
    rule_label: str,
    dataset: str = "delta_match",
    friday_set: Optional[set[str]] = None,
) -> dict:
    """Shared implementation behind both IV-Band and Friday-Band cell_friday_detail
    endpoints. When `friday_set` is set (Friday-Band mode), trades are
    additionally filtered to those Fridays — the band assignment is then
    Friday-locked, not per-trade-IV-locked.
    """
    rule_dict = _label_to_rule(rule_label, dataset)
    if rule_dict is None:
        return {
            "status": "unknown_rule",
            "message": f"rule_label '{rule_label}' not found for dataset '{dataset}'.",
            "cell": None,
            "losers": [],
            "worst_winner": None,
            "largest_win": None,
            "winners_below_avg_min_mtm": [],
        }

    derived = m7r._derive_exits({}, rule_dict, dataset=dataset)
    if derived is None or derived.empty:
        return {
            "status": "no_trades",
            "cell": None,
            "losers": [],
            "worst_winner": None,
            "largest_win": None,
            "winners_below_avg_min_mtm": [],
        }

    # Per-cell slice — same shape used by `_compute_cell_metrics` during grid
    # build. For Friday-Band mode the band classification is per-Friday rather
    # than per-trade, so the `entry_atm_iv_band` filter is replaced by the
    # explicit friday_set membership.
    mask = (
        (derived["expiry_bucket"] == expiry_bucket)
        & (np.isclose(derived["delta_target"].astype(float), float(delta_target), atol=0.001))
        & (derived["entry_hour_ist"] == int(entry_hour_ist))
    )
    if friday_set is None:
        mask &= (derived["entry_atm_iv_band"] == band)
    else:
        mask &= derived["friday_date_ist"].astype(str).isin(friday_set)
    sub = derived[mask].copy()
    if sub.empty:
        return {
            "status": "no_trades",
            "cell": {
                "band": band,
                "expiry_bucket": expiry_bucket,
                "delta_target": float(delta_target),
                "entry_hour_ist": int(entry_hour_ist),
                "rule_label": rule_label,
                "n_losses": 0,
                "n_winners_below_avg_min_mtm": 0,
                "max_win_usd": None,
                "min_mtm_winners": None,
                "avg_min_mtm_winners": None,
            },
            "losers": [],
            "worst_winner": None,
            "largest_win": None,
            "winners_below_avg_min_mtm": [],
        }

    is_win = sub["is_win"].astype(bool)
    winners = sub[is_win]
    losers = sub[~is_win]

    # Avg min MTM (winners) — same formula as _n_winners_below_avg_min_mtm.
    if winners.empty:
        avg_min_mtm_w: Optional[float] = None
        min_mtm_winners: Optional[float] = None
        max_win_usd: Optional[float] = None
    else:
        m = float(winners["min_mtm_usd"].mean())
        avg_min_mtm_w = None if math.isnan(m) else m
        mn = float(winners["min_mtm_usd"].min())
        min_mtm_winners = None if math.isnan(mn) else mn
        mx = float(winners["net_pnl_estimate_usd"].max())
        max_win_usd = None if math.isnan(mx) else mx

    losers_sorted = losers.sort_values("net_pnl_estimate_usd", ascending=True, kind="stable")
    losers_out = [_friday_detail_row(r) for _, r in losers_sorted.iterrows()]

    worst_winner_out: Optional[dict] = None
    largest_win_out: Optional[dict] = None
    if not winners.empty:
        worst_idx = winners["min_mtm_usd"].astype(float).idxmin()
        worst_winner_out = _friday_detail_row(winners.loc[worst_idx])
        lw_idx = winners["net_pnl_estimate_usd"].astype(float).idxmax()
        largest_win_out = _friday_detail_row(winners.loc[lw_idx])

    if avg_min_mtm_w is not None:
        below = winners[winners["min_mtm_usd"].astype(float) < avg_min_mtm_w]
        below_sorted = below.sort_values("min_mtm_usd", ascending=True, kind="stable")
        below_out = [_friday_detail_row(r) for _, r in below_sorted.iterrows()]
    else:
        below_out = []

    return {
        "status": "ok",
        "cell": {
            "band": band,
            "expiry_bucket": expiry_bucket,
            "delta_target": float(delta_target),
            "entry_hour_ist": int(entry_hour_ist),
            "rule_label": rule_label,
            "n_trades": int(len(sub)),
            "n_wins": int(len(winners)),
            "n_losses": int(len(losers)),
            "n_winners_below_avg_min_mtm": int(len(below_out)),
            "max_win_usd": None if max_win_usd is None else round(max_win_usd, 2),
            "min_mtm_winners": None if min_mtm_winners is None else round(min_mtm_winners, 2),
            "avg_min_mtm_winners": None if avg_min_mtm_w is None else round(avg_min_mtm_w, 2),
        },
        "losers": losers_out,
        "worst_winner": worst_winner_out,
        "largest_win": largest_win_out,
        "winners_below_avg_min_mtm": below_out,
    }


@router.get("/iv_band_best_combo/cell_friday_detail")
def get_cell_friday_detail(
    band: str = Query(..., description="The picked cell's IV band (e.g. '0-20')"),
    expiry_bucket: str = Query(...),
    delta_target: float = Query(..., ge=0.0, le=1.0),
    entry_hour_ist: int = Query(..., ge=0, le=23),
    rule_label: str = Query(..., description="Rule label of the picked cell, e.g. sl75_max_profit_25"),
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """For one Best Combo cell, surface the Friday dates behind these four
    aggregates: losing-trade fridays, the worst-MTM winner, the largest-win
    trade, and the winners whose min_mtm dipped below the cell's avg.

    Reuses the per-rule `_EXIT_CACHE` so cold call ≈ 5–15 s, warm call ≈ ms.
    """
    if dataset == "price_match":
        if not os.path.exists(GRID_PARQUET_PATH_PRICE_MATCHED):
            return _price_match_no_data_payload({"dataset": dataset})
    return _compute_cell_friday_detail(
        band=band,
        expiry_bucket=expiry_bucket,
        delta_target=delta_target,
        entry_hour_ist=entry_hour_ist,
        rule_label=rule_label,
        dataset=dataset,
        friday_set=None,
    )


@router.get("/iv_band_best_combo/single_combo_simulation")
def get_single_combo_simulation(
    expiry_bucket: str = Query(...),
    delta_target: float = Query(..., ge=0.0, le=1.0),
    entry_hour_ist: int = Query(..., ge=0, le=23),
    rule_label: str = Query(...),
    total_capital_usd: Optional[float] = Query(None, ge=0.0),
    pct_deploy: float = Query(100.0, ge=0.0, le=100.0),
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """Counterfactual: what if every Friday traded this single combo regardless
    of IV regime? Aggregates the picked combo's cells across ALL bands and
    returns one combined headline.
    """
    if dataset == "price_match":
        if not os.path.exists(GRID_PARQUET_PATH_PRICE_MATCHED):
            return _price_match_no_data_payload({"dataset": dataset})
    try_load_grid_only(dataset)
    grid_state = _get_grid_state(dataset)
    if grid_state["status"] != "ready":
        return {"status": grid_state["status"]}
    grid: pd.DataFrame = grid_state["grid"]
    if grid is None or grid.empty:
        return {"status": "empty"}
    sub = grid[
        (grid["expiry_bucket"] == expiry_bucket)
        & (np.isclose(grid["delta_target"].astype(float), float(delta_target), atol=0.001))
        & (grid["entry_hour_ist"] == int(entry_hour_ist))
        & (grid["rule_label"] == rule_label)
    ].copy()
    if sub.empty:
        return {"status": "ready", "summary": None,
                "expiry_bucket": expiry_bucket, "delta_target": delta_target,
                "entry_hour_ist": entry_hour_ist, "rule_label": rule_label}
    # Combine cells across all bands: weighted avg by n_trades, sum for
    # *_total and *_count metrics, min/max for extremes.
    n_tot = float(sub["n_trades"].sum())
    if n_tot == 0:
        return {"status": "ready", "summary": None}
    def _w_mean(col):
        if col not in sub.columns:
            return None
        vals = pd.to_numeric(sub[col], errors="coerce")
        w = sub["n_trades"].astype(float)
        mask = vals.notna() & (w > 0)
        if not mask.any():
            return None
        return float((vals[mask] * w[mask]).sum() / w[mask].sum())
    def _w_sum(col):
        if col not in sub.columns:
            return None
        vals = pd.to_numeric(sub[col], errors="coerce")
        return float(vals.sum())
    summary = {
        "n_trades": int(n_tot),
        "n_wins": _w_sum("n_wins"),
        "n_losses": _w_sum("n_losses"),
        "win_rate": _w_mean("win_rate"),
        "avg_net_pnl": _w_mean("avg_net_pnl"),
        "total_net_pnl": _w_sum("avg_net_pnl") and _w_mean("avg_net_pnl") and (_w_mean("avg_net_pnl") * n_tot),
        "avg_credit": _w_mean("avg_credit"),
        "avg_margin": _w_mean("avg_margin"),
        "max_loss_usd": float(sub["max_loss_usd"].min()) if "max_loss_usd" in sub.columns else None,
        "n_rule_trigger": _w_sum("n_rule_trigger"),
        "n_hard_cap": _w_sum("n_hard_cap"),
        "avg_pct_return_on_credit": _w_mean("avg_pct_return_on_credit"),
        "composite_score": _w_mean("composite_score"),
        "sharpe_per_trade": _w_mean("sharpe_per_trade"),
        "n_bands_covered": int(sub["iv_band"].nunique()),
    }
    # Optional scaling by capital.
    if total_capital_usd is not None and total_capital_usd > 0:
        deployable = float(total_capital_usd) * max(0.0, float(pct_deploy)) / 100.0
        avg_margin = summary.get("avg_margin")
        if avg_margin and avg_margin > 0:
            lots = int(np.floor(deployable * 100.0 / avg_margin))
            summary["lots"] = lots
            summary["scaled_avg_net_pnl"] = summary["avg_net_pnl"] * lots / 100.0 if summary["avg_net_pnl"] is not None else None
            summary["scaled_total_net_pnl"] = summary["total_net_pnl"] * lots / 100.0 if summary["total_net_pnl"] is not None else None
            summary["scaled_max_loss_usd"] = summary["max_loss_usd"] * lots / 100.0 if summary["max_loss_usd"] is not None else None
    return {
        "status": "ready",
        "summary": summary,
        "per_band_breakdown": _records(sub.sort_values("iv_band", key=lambda s: s.map(_band_sort_key))),
        "expiry_bucket": expiry_bucket,
        "delta_target": delta_target,
        "entry_hour_ist": entry_hour_ist,
        "rule_label": rule_label,
        "total_capital_usd": total_capital_usd,
        "pct_deploy": pct_deploy,
    }


def _compute_missed_fridays(picks: pd.DataFrame,
                              dataset: str = "delta_match") -> dict:
    """Shared body: given a picks frame (rule_label + (band, hour, expiry, Δ)
    per band), find Fridays not naturally covered by any pick and return
    per-Friday availability of each pick's (hour, expiry, Δ)."""
    if picks.empty:
        return {"rows": [], "status": "no_picks"}
    trades = m7r._load_trades(dataset)
    if trades.empty:
        return {"rows": [], "status": "no_trades"}
    if "expiry_bucket" not in trades.columns and "dte_days" in trades.columns:
        trades = trades.copy()
        trades["expiry_bucket"] = pd.cut(
            trades["dte_days"],
            bins=[0, 1.5, 2.5, 5, 10, 20, 45, float("inf")],
            labels=["current (Sat)", "next (Sun)", "next_to_next (Mon)",
                    "weekly (7d)", "biweekly (14d)", "monthly (30d)", "quarterly"],
        ).astype(str)

    all_fridays = sorted(set(trades["friday_date_ist"].astype(str).unique()))
    matched_fridays: set[str] = set()
    pick_records: list[dict] = []
    for _, p in picks.iterrows():
        cell_trades = trades[
            (trades["entry_atm_iv_band"] == p["iv_band"])
            & (trades["entry_hour_ist"] == p["entry_hour_ist"])
            & (trades["expiry_bucket"] == p["expiry_bucket"])
            & (np.isclose(trades["delta_target"].astype(float),
                          float(p["delta_target"]), atol=0.001))
        ]
        matched_fridays.update(cell_trades["friday_date_ist"].astype(str).unique())
        rule_dict = p["rule"] if "rule" in p.index else None
        pick_records.append({
            "band": p["iv_band"],
            "entry_hour_ist": int(p["entry_hour_ist"]) if pd.notna(p["entry_hour_ist"]) else None,
            "expiry_bucket": p["expiry_bucket"],
            "delta_target": float(p["delta_target"]),
            "rule_label": p["rule_label"],
            "rule_dict": rule_dict if isinstance(rule_dict, dict) else {},
        })

    missed = sorted(set(all_fridays) - matched_fridays)
    if not missed:
        return {"rows": [], "status": "ready", "n_missed": 0,
                "n_total_fridays": len(all_fridays),
                "n_matched": len(matched_fridays),
                "n_rescuable": 0,
                "picks": [_strip_rule_dict(r) for r in pick_records]}

    # Rescue computation: derive each pick's rule-driven outcomes once
    # (cached in _EXIT_CACHE by rule), filtered to that pick's (hour, expiry, Δ).
    # Index by Friday so we can look up the actual net P&L of THAT pick on each
    # missed Friday — i.e. what the user would have gotten if they had
    # relaxed the band-match constraint and let this pick absorb the Friday.
    pick_friday_outcome: list[dict] = []
    for p in pick_records:
        flt = {
            "entry_hour_ist": str(p["entry_hour_ist"]) if p["entry_hour_ist"] is not None else None,
            "expiry_bucket": p["expiry_bucket"],
            "delta_target": f"{p['delta_target']:.4g}",
        }
        try:
            derived = m7r._derive_exits(flt, p["rule_dict"], dataset=dataset)
        except Exception as exc:  # noqa: BLE001
            log.warning("missed-Fridays rescue: derive_exits failed for pick %s: %s",
                        p["rule_label"], exc)
            pick_friday_outcome.append({})
            continue
        if derived.empty:
            pick_friday_outcome.append({})
            continue
        # δ tolerance: _apply_filters rejects values that don't coerce-match
        # exactly. Belt-and-suspenders with isclose to handle 0.05 vs 0.0500001.
        derived = derived[np.isclose(
            derived["delta_target"].astype(float),
            float(p["delta_target"]), atol=0.001,
        )]
        by_fri = {}
        for _, row in derived.iterrows():
            fday = str(row["friday_date_ist"])
            raw_net = row.get("net_pnl_estimate_usd")
            if raw_net is None or pd.isna(raw_net):
                continue
            net_pnl = float(raw_net)
            band_val = row.get("entry_atm_iv_band")
            by_fri[fday] = {
                "net_pnl": net_pnl,
                "is_win": net_pnl > 0.0,
                "exit_reason": str(row.get("exit_reason") or ""),
                "actual_band": str(band_val) if band_val is not None and not pd.isna(band_val) else None,
            }
        pick_friday_outcome.append(by_fri)

    rows = []
    n_rescuable = 0
    for fday in missed:
        fday_trades = trades[trades["friday_date_ist"].astype(str) == fday]
        bands_touched = sorted(
            fday_trades["entry_atm_iv_band"].dropna().unique().tolist())
        pick_availability = []
        for idx, p in enumerate(pick_records):
            m = fday_trades[
                (fday_trades["entry_hour_ist"] == p["entry_hour_ist"])
                & (fday_trades["expiry_bucket"] == p["expiry_bucket"])
                & (np.isclose(fday_trades["delta_target"].astype(float),
                              float(p["delta_target"]), atol=0.001))
            ]
            fits = not m.empty
            actual_band = None
            if fits:
                band_series = m["entry_atm_iv_band"].dropna()
                if not band_series.empty:
                    actual_band = str(band_series.iloc[0])
            outcome = pick_friday_outcome[idx].get(fday) if fits else None
            pick_availability.append({
                "pick_band": p["band"],
                "rule_label": p["rule_label"],
                "fits": fits,
                "actual_iv_band_on_this_friday": actual_band,
                "rule_net_pnl": outcome["net_pnl"] if outcome else None,
                "rule_is_win": outcome["is_win"] if outcome else None,
                "rule_exit_reason": outcome["exit_reason"] if outcome else None,
            })
        # Best rescue = fitting pick with highest rule_net_pnl. Falls back to
        # availability-only fit (no outcome) if every fit produced an empty
        # derive (rare — usually means the rule cache missed this Friday).
        rescue = None
        candidates = [pa for pa in pick_availability
                      if pa["fits"] and pa["rule_net_pnl"] is not None]
        if candidates:
            best = max(candidates, key=lambda pa: pa["rule_net_pnl"])
            rescue = {
                "rescued_band": best["pick_band"],
                "rescued_rule_label": best["rule_label"],
                "rescued_net_pnl": best["rule_net_pnl"],
                "rescued_is_win": best["rule_is_win"],
                "rescued_exit_reason": best["rule_exit_reason"],
            }
            n_rescuable += 1
        rows.append({
            "friday_date_ist": fday,
            "n_total_trades": int(len(fday_trades)),
            "bands_touched": bands_touched,
            "pick_availability": pick_availability,
            "rescue": rescue,
        })

    return {
        "rows": rows,
        "status": "ready",
        "n_missed": len(missed),
        "n_total_fridays": len(all_fridays),
        "n_matched": len(matched_fridays),
        "n_rescuable": n_rescuable,
        "picks": [_strip_rule_dict(r) for r in pick_records],
    }


def _strip_rule_dict(rec: dict) -> dict:
    """Drop the rule_dict from a pick record before serialising — it's a Python
    dict that may contain non-JSON values, and the frontend doesn't need it."""
    return {k: v for k, v in rec.items() if k != "rule_dict"}


# ── Missed-Fridays concurrency cap + tiny response cache ────────────────────
# Heavy endpoint: each request serially calls _derive_exits for ~10 picks. On
# cold cache that's 50-300s per request. With FastAPI's default 40-thread
# anyio pool, 8+ concurrent calls (e.g. user rapid-clicking filters) saturate
# the pool and ALL other sync routes (including /api/v1/session-id) queue up,
# causing the frontend's session check to time out → black screen.
#
# Two-part defense:
#   1) Semaphore(4) — at most 4 concurrent heavy computes; 5th+ waits its
#      turn (still consumes a thread slot but for a much shorter window).
#   2) Tiny LRU response cache — same args within the cache window return
#      instantly without re-running _derive_exits. Invalidated by trades-
#      parquet mtime so a data refresh forces a recompute.
_MISSED_FRIDAYS_SEM = threading.BoundedSemaphore(4)
_MISSED_FRIDAYS_CACHE: dict[tuple, tuple[float, dict]] = {}  # key → (ds_mtime, response)
_MISSED_FRIDAYS_CACHE_MAX = 32
_MISSED_FRIDAYS_CACHE_LOCK = threading.Lock()


def _trades_parquet_mtime(dataset: str) -> float:
    """Return mtime of the trades parquet for the dataset (used for cache
    invalidation). Returns 0.0 if the file doesn't exist."""
    if dataset == "price_match":
        path = m7r.TRADES_PATH_PRICE_MATCHED
    else:
        path = (m7r.TRADES_ENRICHED_PATH if os.path.exists(m7r.TRADES_ENRICHED_PATH)
                else m7r.TRADES_PATH)
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _missed_fridays_cache_get(key: tuple, ds_mtime: float) -> Optional[dict]:
    with _MISSED_FRIDAYS_CACHE_LOCK:
        entry = _MISSED_FRIDAYS_CACHE.get(key)
        if entry is None:
            return None
        cached_mtime, response = entry
        if cached_mtime != ds_mtime:
            del _MISSED_FRIDAYS_CACHE[key]
            return None
        # LRU bump
        _MISSED_FRIDAYS_CACHE[key] = (cached_mtime, response)
        return response


def _missed_fridays_cache_set(key: tuple, ds_mtime: float, response: dict) -> None:
    with _MISSED_FRIDAYS_CACHE_LOCK:
        _MISSED_FRIDAYS_CACHE[key] = (ds_mtime, response)
        # Evict oldest (first-inserted) if over cap
        while len(_MISSED_FRIDAYS_CACHE) > _MISSED_FRIDAYS_CACHE_MAX:
            _MISSED_FRIDAYS_CACHE.pop(next(iter(_MISSED_FRIDAYS_CACHE)))


@router.get("/iv_band_best_combo/missed_fridays")
def get_missed_fridays_for_best_combo(
    ranking: str = Query("avg_net_pnl"),
    secondary: Optional[str] = Query(None),
    tolerance_pct: float = Query(5.0, ge=0.0, le=100.0),
    rule_family: str = Query("all"),
    total_capital_usd: Optional[float] = Query(None, ge=0),
    pct_deploy: float = Query(100.0, ge=0, le=100),
    dd_metric: Optional[str] = Query(None),
    dd_threshold: Optional[float] = Query(None),
    dd_metrics: Optional[str] = Query(None),
    dd_thresholds: Optional[str] = Query(None),
    min_hit_pct: float = Query(50.0, ge=0.0, le=100.0),
    max_loss_cap_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    max_drop_peak_to_trough_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    min_n_trades: int = Query(5, ge=0),
    min_win_rate: Optional[float] = Query(None, ge=0.0, le=100.0),
    max_losing_streak: Optional[int] = Query(None, ge=1),
    pick_mode: str = Query("by_hour"),
    expiry_buckets: Optional[str] = Query(None),
    delta_targets: Optional[str] = Query(None),
    entry_hours: Optional[str] = Query(None),
    premium_sl_pcts: Optional[str] = Query(None,
        description="CSV whitelist of premium-SL pct values (e.g. '50,75'). Mirrors /iv_band_best_combo so the missed-Fridays panel stays in sync with the table."),
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """Missed Fridays tied to the Best Combo picker. Accepts ALL sizing + filter
    params from /iv_band_best_combo so the picks computed here exactly match
    what the Best Combo table shows. Use this when the user has applied
    Conservative-preset or custom filters and wants to see which Fridays the
    Best Combo picks cover.
    """
    if ranking not in _VALID_RANKINGS:
        raise HTTPException(status_code=400, detail=f"ranking must be one of {sorted(_VALID_RANKINGS)}")
    if rule_family not in _VALID_RULE_FAMILIES:
        raise HTTPException(status_code=400, detail=f"rule_family must be one of {sorted(_VALID_RULE_FAMILIES)}")
    if dataset == "price_match":
        if not os.path.exists(GRID_PARQUET_PATH_PRICE_MATCHED):
            return _price_match_no_data_payload({"dataset": dataset})

    # Cache key — all args that affect the result
    cache_key = (
        ranking, secondary, tolerance_pct, rule_family, total_capital_usd, pct_deploy,
        dd_metric, dd_threshold, dd_metrics, dd_thresholds,
        min_hit_pct, max_loss_cap_pct, max_drop_peak_to_trough_pct,
        min_n_trades, min_win_rate, max_losing_streak, pick_mode,
        expiry_buckets, delta_targets, entry_hours, premium_sl_pcts, dataset,
    )
    ds_mtime = _trades_parquet_mtime(dataset)
    cached = _missed_fridays_cache_get(cache_key, ds_mtime)
    if cached is not None:
        return cached

    # Cap concurrency. acquire(blocking=True) is fine — the lock is held only
    # while a heavy compute runs (seconds, not minutes for the typical case);
    # waiting requests get processed in order without piling up indefinitely.
    with _MISSED_FRIDAYS_SEM:
        # Re-check cache under the semaphore — another concurrent request may
        # have just populated it while we were waiting.
        cached = _missed_fridays_cache_get(cache_key, ds_mtime)
        if cached is not None:
            return cached

        try_load_grid_only(dataset)
        grid_state = _get_grid_state(dataset)
        if grid_state["status"] != "ready":
            return {"rows": [], "status": grid_state["status"]}
        grid: pd.DataFrame = grid_state["grid"]
        if grid is None or grid.empty:
            return {"rows": [], "status": "empty"}
        family_grid = _filter_grid_by_family(grid, rule_family)
        family_grid = _apply_dimension_filters(
            family_grid, expiry_buckets, delta_targets, entry_hours,
            premium_sl_pcts=premium_sl_pcts,
        )
        if pick_mode == "aggregate_hours":
            family_grid = _aggregate_across_hours(family_grid)
        dd_constraints_list = _parse_dd_constraints(dd_metrics, dd_thresholds)
        picks = _pick_best_per_band(
            family_grid, ranking,
            secondary=secondary,
            tolerance_pct=tolerance_pct if secondary else None,
            total_capital_usd=total_capital_usd,
            pct_deploy=pct_deploy,
            dd_metric=dd_metric,
            dd_threshold=dd_threshold,
            dd_constraints=dd_constraints_list,
            min_hit_pct=min_hit_pct,
            max_loss_cap_pct=max_loss_cap_pct,
            max_drop_peak_to_trough_pct=max_drop_peak_to_trough_pct,
            min_n_trades=min_n_trades,
            min_win_rate=min_win_rate,
            max_losing_streak=max_losing_streak,
        )
        response = _compute_missed_fridays(picks, dataset=dataset)
        _missed_fridays_cache_set(cache_key, ds_mtime, response)
        return response


@router.get("/iv_band_best_combo/missed_fridays_force_fit")
def get_missed_fridays_force_fit(
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """For each Friday NOT covered by the current 10 best-combo picks (under
    default conditions), report force-fit details: which of the 10 picks the
    Friday has a trade at, and (where the trade exists) the realized P&L for
    that pick on that Friday.

    Picks are computed in-endpoint using the default avg_net_pnl ranking +
    min_hit_pct=50 — i.e. matches what the Best Combo table picks by default.

    Returns: per-Friday list with {friday_date_ist, n_total_trades, bands_touched,
    pick_availability: [{pick_band, fits, net_pnl_if_fit, win_if_fit}]}.
    """
    if dataset == "price_match":
        if not os.path.exists(GRID_PARQUET_PATH_PRICE_MATCHED):
            return _price_match_no_data_payload({"dataset": dataset})
    try_load_grid_only(dataset)
    grid_state = _get_grid_state(dataset)
    if grid_state["status"] != "ready":
        return {"rows": [], "status": grid_state["status"]}
    grid: pd.DataFrame = grid_state["grid"]
    if grid is None or grid.empty:
        return {"rows": [], "status": "empty"}
    picks = _pick_best_per_band(grid, "avg_net_pnl", min_hit_pct=50.0)
    return _compute_missed_fridays(picks, dataset=dataset)


# Tiny in-memory cache for coverage responses keyed by (query-args tuple,
# trades_mtime). Mode-toggle round-trips should be sub-second after the
# first call. Invalidated whenever the underlying trades parquet changes.
_COVERAGE_CACHE: dict[tuple, dict] = {}
_COVERAGE_CACHE_MAX = 16

# L2 disk persistence for _COVERAGE_CACHE — survives backend restarts.
_COVERAGE_DISK_DIR = os.path.join(m7r.M7_BASE_DIR, "coverage_cache")


def _coverage_cache_path(cache_key: tuple) -> str:
    h = hashlib.sha1(repr(cache_key).encode()).hexdigest()
    return os.path.join(_COVERAGE_DISK_DIR, f"{h}.json")


def _load_coverage_disk(cache_key: tuple, ds_mtime: float) -> Optional[dict]:
    path = _coverage_cache_path(cache_key)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("_ds_mtime") != ds_mtime:
            return None
        data.pop("_ds_mtime", None)
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("Coverage disk cache read failed (%s): %s", path, exc)
        return None


def _save_coverage_disk(cache_key: tuple, response: dict, ds_mtime: float) -> None:
    try:
        os.makedirs(_COVERAGE_DISK_DIR, exist_ok=True)
        path = _coverage_cache_path(cache_key)
        payload = {**response, "_ds_mtime": ds_mtime}
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        log.warning("Coverage disk cache write failed: %s", exc)

# Per-cache-key async warmup state. Cold calls used to block ~18s (picker
# + classifier) — long enough for the browser/proxy to time out, surfacing
# as a 500. Now: on cache miss, kick off a daemon thread that computes the
# payload and writes it into _COVERAGE_CACHE; the endpoint returns a
# warming response immediately so the frontend can poll. Idempotent —
# multiple requests for the same key share one thread.
_COVERAGE_WARMUP_TASKS: dict[tuple, threading.Thread] = {}
_COVERAGE_WARMUP_LOCK = threading.Lock()


@router.get("/iv_band_best_combo/coverage")
def get_iv_band_best_combo_coverage(
    ranking: str = Query("avg_net_pnl"),
    secondary: Optional[str] = Query(None),
    tolerance_pct: float = Query(5.0, ge=0.0, le=100.0),
    rule_family: str = Query("all"),
    total_capital_usd: Optional[float] = Query(None, ge=0),
    pct_deploy: float = Query(100.0, ge=0, le=100),
    dd_metric: Optional[str] = Query(None),
    dd_threshold: Optional[float] = Query(None),
    dd_metrics: Optional[str] = Query(None),
    dd_thresholds: Optional[str] = Query(None),
    min_hit_pct: float = Query(50.0, ge=0.0, le=100.0),
    max_loss_cap_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    max_drop_peak_to_trough_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    min_n_trades: int = Query(5, ge=0),
    min_win_rate: Optional[float] = Query(None, ge=0.0, le=100.0),
    max_losing_streak: Optional[int] = Query(None, ge=1),
    pick_mode: str = Query("by_hour"),
    expiry_buckets: Optional[str] = Query(None),
    delta_targets: Optional[str] = Query(None),
    entry_hours: Optional[str] = Query(None),
    premium_sl_pcts: Optional[str] = Query(None,
        description="CSV whitelist of premium-SL pct values (e.g. '50,75'). Mirrors /iv_band_best_combo so coverage stays in sync with the table."),
    coverage_mode: str = Query(
        "force_fit",
        description="Friday-dedup mode: 'force_fit' (any (h,e,Δ) match across bands + closest-fallback) or 'touched_band' (only bands the Friday's IV touched; no closest-fallback)."),
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """Best Combo picker + Friday dedup overlay (sibling of /iv_band_best_combo).

    Same picks as /iv_band_best_combo, but each of the 121 Fridays is
    assigned to exactly one of the 10 picked bands (or counted as
    uncovered) via `_classify_fridays_to_cells` from m7_full_coverage.
    Each row carries 5 new fields (`n_assigned`, `n_rule`, `n_force_fit`,
    `n_touched_band`, `n_closest_fallback`) plus a top-level
    `coverage_summary` block.
    """
    if dataset == "price_match":
        if not os.path.exists(GRID_PARQUET_PATH_PRICE_MATCHED):
            return _price_match_no_data_payload({"dataset": dataset,
                                                  "coverage_mode": coverage_mode})
    if coverage_mode not in ("force_fit", "touched_band"):
        raise HTTPException(400, "coverage_mode must be 'force_fit' or 'touched_band'")
    if ranking not in _VALID_RANKINGS:
        raise HTTPException(400, f"ranking must be one of {sorted(_VALID_RANKINGS)}")
    if rule_family not in _VALID_RULE_FAMILIES:
        raise HTTPException(400, f"rule_family must be one of {sorted(_VALID_RULE_FAMILIES)}")
    if pick_mode not in ("by_hour", "aggregate_hours"):
        raise HTTPException(400, "pick_mode must be 'by_hour' or 'aggregate_hours'")

    try_load_grid_only(dataset)
    grid_state = _get_grid_state(dataset)

    # ── Response cache lookup ─────────────────────────────────────────────────
    # Picker (~7s) + classifier (~9s) is too slow to block the request on a
    # cold call — long enough to hit browser/proxy timeouts (the original
    # "500 Internal Server Error" the UI showed). Two-layer strategy:
    #   1. Hot cache → return immediately
    #   2. Cold key → spawn a daemon thread that runs the heavy work and
    #      writes the result into the cache; return a warming response so
    #      the frontend can poll. Each individual request stays sub-second,
    #      surviving backend restarts.
    m7r._load_trades(dataset)  # ensure mtime is current for this dataset
    _, ds_mtime = m7r._TRADES_BY_DATASET.get(dataset, (None, 0.0))
    cache_key = (
        dataset,
        coverage_mode, ranking, secondary, tolerance_pct, rule_family,
        total_capital_usd, pct_deploy, dd_metric, dd_threshold,
        dd_metrics, dd_thresholds, min_hit_pct, max_loss_cap_pct,
        max_drop_peak_to_trough_pct, min_n_trades, min_win_rate,
        max_losing_streak, pick_mode,
        expiry_buckets, delta_targets, entry_hours,
        premium_sl_pcts,
        ds_mtime,
    )
    cached = _COVERAGE_CACHE.get(cache_key)
    if cached is None:
        cached = _load_coverage_disk(cache_key, ds_mtime)
        if cached is not None:
            _COVERAGE_CACHE[cache_key] = cached
    if cached is not None:
        return cached
    if grid_state["status"] == "pending":
        return {
            "ranking": ranking,
            "coverage_mode": coverage_mode,
            "status": "not_built",
            "message": "Grid not built. Run "
                       "`docker exec docker-backend-1 python -m "
                       "app.scripts.build_m7_best_combo_grid` to build.",
            "rows": [],
        }
    if grid_state["status"] == "error":
        raise HTTPException(status_code=500,
                            detail=f"warmup failed: {grid_state['error']}")

    grid: pd.DataFrame = grid_state["grid"]
    if grid is None or grid.empty:
        return {
            "ranking": ranking, "coverage_mode": coverage_mode,
            "status": "ready", "rows": [],
            "coverage_summary": {
                "total_fridays": 0, "n_assigned": 0, "n_uncovered": 0,
                "n_rule": 0, "n_force_fit": 0,
                "n_touched_band": 0, "n_closest_fallback": 0,
            },
            "n_rules": 0, "n_cells": 0,
        }

    # Cache miss with a ready grid → kick off async computation.
    _kick_off_coverage_warmup(
        cache_key,
        ranking=ranking, secondary=secondary, tolerance_pct=tolerance_pct,
        rule_family=rule_family, total_capital_usd=total_capital_usd,
        pct_deploy=pct_deploy, dd_metric=dd_metric, dd_threshold=dd_threshold,
        dd_metrics=dd_metrics, dd_thresholds=dd_thresholds,
        min_hit_pct=min_hit_pct, max_loss_cap_pct=max_loss_cap_pct,
        max_drop_peak_to_trough_pct=max_drop_peak_to_trough_pct,
        min_n_trades=min_n_trades, min_win_rate=min_win_rate,
        max_losing_streak=max_losing_streak, pick_mode=pick_mode,
        expiry_buckets=expiry_buckets, delta_targets=delta_targets,
        entry_hours=entry_hours, premium_sl_pcts=premium_sl_pcts,
        coverage_mode=coverage_mode,
        dataset=dataset,
    )
    return {
        "ranking": ranking,
        "coverage_mode": coverage_mode,
        "status": "warming",
        "rows": [],
        "coverage_summary": {
            "total_fridays": 0, "n_assigned": 0, "n_uncovered": 0,
            "n_rule": 0, "n_force_fit": 0,
            "n_touched_band": 0, "n_closest_fallback": 0,
        },
        "n_rules": len(_rule_variants()),
        "n_cells": 0,
    }


def _kick_off_coverage_warmup(cache_key: tuple, **payload_kwargs) -> None:
    """Idempotent: spawn one daemon thread per cache_key that computes the
    coverage payload and writes it into `_COVERAGE_CACHE`. If a thread is
    already running for the same key, do nothing (callers can poll until
    the cache fills)."""
    with _COVERAGE_WARMUP_LOCK:
        if _COVERAGE_CACHE.get(cache_key) is not None:
            return
        existing = _COVERAGE_WARMUP_TASKS.get(cache_key)
        if existing is not None and existing.is_alive():
            return

        def _do_warmup() -> None:
            try:
                _compute_coverage_payload(cache_key=cache_key, **payload_kwargs)
            except Exception as exc:  # noqa: BLE001
                log.warning("coverage warmup failed for key=%s: %s",
                            cache_key, exc)
            finally:
                with _COVERAGE_WARMUP_LOCK:
                    _COVERAGE_WARMUP_TASKS.pop(cache_key, None)

        t = threading.Thread(target=_do_warmup, daemon=True,
                             name=f"coverage-warmup")
        _COVERAGE_WARMUP_TASKS[cache_key] = t
        t.start()


def _compute_coverage_payload(
    *, cache_key: tuple,
    ranking: str, secondary: Optional[str], tolerance_pct: float,
    rule_family: str, total_capital_usd: Optional[float],
    pct_deploy: float, dd_metric: Optional[str],
    dd_threshold: Optional[float], dd_metrics: Optional[str],
    dd_thresholds: Optional[str], min_hit_pct: float,
    max_loss_cap_pct: Optional[float],
    max_drop_peak_to_trough_pct: Optional[float],
    min_n_trades: int, min_win_rate: Optional[float],
    max_losing_streak: Optional[int], pick_mode: str,
    expiry_buckets: Optional[str], delta_targets: Optional[str],
    entry_hours: Optional[str],
    premium_sl_pcts: Optional[str], coverage_mode: str,
    dataset: str = "delta_match",
) -> dict:
    """The heavy lifting: picker + classifier + row assembly. Called from
    a daemon thread on cache miss; result is stored in `_COVERAGE_CACHE`
    so subsequent requests for the same args return instantly. The
    endpoint itself never invokes this directly — it always returns the
    cached payload or a warming response."""
    grid: pd.DataFrame = _get_grid_state(dataset)["grid"]

    # 1. Run the same picker logic as /iv_band_best_combo
    family_grid = _filter_grid_by_family(grid, rule_family)
    family_grid = _apply_dimension_filters(
        family_grid, expiry_buckets, delta_targets, entry_hours,
        premium_sl_pcts=premium_sl_pcts,
    )
    if pick_mode == "aggregate_hours":
        family_grid = _aggregate_across_hours(family_grid)
    dd_constraints_list = _parse_dd_constraints(dd_metrics, dd_thresholds)
    best = _pick_best_per_band(
        family_grid, ranking,
        secondary=secondary,
        tolerance_pct=tolerance_pct if secondary else None,
        total_capital_usd=total_capital_usd,
        pct_deploy=pct_deploy,
        dd_metric=dd_metric,
        dd_threshold=dd_threshold,
        dd_constraints=dd_constraints_list,
        min_hit_pct=min_hit_pct,
        max_loss_cap_pct=max_loss_cap_pct,
        max_drop_peak_to_trough_pct=max_drop_peak_to_trough_pct,
        min_n_trades=min_n_trades,
        min_win_rate=min_win_rate,
        max_losing_streak=max_losing_streak,
    )

    # 2. Fallback exit-hour (matches /iv_band_best_combo for row-schema parity)
    if not best.empty and "rule_label" in grid.columns:
        fallback_hours: list[Optional[int]] = []
        fallback_nets: list[Optional[float]] = []
        fallback_labels: list[Optional[str]] = []
        for _, prow in best.iterrows():
            sub = grid[
                (grid["iv_band"] == prow["iv_band"])
                & (grid["expiry_bucket"] == prow["expiry_bucket"])
                & (np.isclose(grid["delta_target"].astype(float),
                              float(prow["delta_target"]), atol=0.001))
                & (grid["entry_hour_ist"] == prow["entry_hour_ist"])
                & (grid["rule_label"].str.contains("_exit_hr_", na=False))
            ]
            if sub.empty:
                fallback_hours.append(None)
                fallback_nets.append(None)
                fallback_labels.append(None)
                continue
            idx = sub["avg_net_pnl"].astype(float).idxmax()
            top = sub.loc[idx]
            fallback_labels.append(top["rule_label"])
            fallback_nets.append(float(top["avg_net_pnl"]))
            label = str(top["rule_label"])
            try:
                tail = label.rsplit("_exit_hr_", 1)[1]
                fallback_hours.append(17 if tail == "1729" else int(tail))
            except Exception:
                fallback_hours.append(None)
        best = best.copy()
        best["fallback_exit_hour"] = fallback_hours
        best["fallback_exit_avg_net"] = fallback_nets
        best["fallback_exit_rule_label"] = fallback_labels

    # 3. Load trades + classify Fridays. Use RAW trades (not _derive_exits) —
    # _derive_exits is 30-40s cold and unnecessary here: the classifier only
    # needs (friday, band, hour, expiry, Δ, trade_id) for dim-matching; pnl
    # tiebreaks fall back to first-match-wins which is stable enough for
    # dedup attribution. Future enhancement: re-introduce per-rule pnl
    # tiebreaks via the cache when warm.
    from app.api.m7_full_coverage import _classify_fridays_to_cells  # avoid circular import at module load

    try:
        derived = m7r._load_trades(dataset).copy()
    except Exception as exc:  # noqa: BLE001
        log.warning("coverage: _load_trades failed: %s", exc)
        derived = pd.DataFrame()

    # Backfill expiry_bucket if the trades parquet uses dte_days only (same
    # pattern used by `_compute_missed_fridays`).
    if not derived.empty and "expiry_bucket" not in derived.columns and "dte_days" in derived.columns:
        derived["expiry_bucket"] = pd.cut(
            derived["dte_days"],
            bins=[0, 1.5, 2.5, 5, 10, 20, 45, float("inf")],
            labels=["current (Sat)", "next (Sun)", "next_to_next (Mon)",
                    "weekly (7d)", "biweekly (14d)", "monthly (30d)", "quarterly"],
        ).astype(str)
    # Synthetic pnl col so the classifier's tiebreak logic doesn't KeyError.
    if not derived.empty and "net_pnl_estimate_usd" not in derived.columns:
        derived["net_pnl_estimate_usd"] = 0.0

    total_fridays = (int(derived["friday_date_ist"].astype(str).nunique())
                     if not derived.empty else 0)

    if best.empty or derived.empty:
        assignments = pd.DataFrame(columns=["friday_date_ist", "trade_id",
                                             "assigned_band", "kind"])
    else:
        # `_classify_fridays_to_cells` expects `entry_atm_iv_band` (from the
        # trades schema), but Best Combo's grid uses `iv_band`. Build a
        # compatibility view with the column renamed.
        best_for_classifier = best.rename(
            columns={"iv_band": "entry_atm_iv_band"}
        )
        # `score` must be present — _pick_best_per_band always sets it.
        if "score" not in best_for_classifier.columns:
            best_for_classifier = best_for_classifier.assign(score=float("nan"))
        assignments = _classify_fridays_to_cells(
            derived, best_for_classifier, coverage_mode=coverage_mode,
        )

    # 4. Per-row assignment counts
    if not assignments.empty:
        band_kind = (
            assignments.groupby(["assigned_band", "kind"]).size()
            .unstack(fill_value=0).to_dict("index")
        )
    else:
        band_kind = {}

    rows_records = _records(best)
    for row in rows_records:
        band = row.get("iv_band")
        kinds = band_kind.get(band, {}) if band is not None else {}
        n_rule = int(kinds.get("rule", 0))
        n_force_fit = int(kinds.get("force_fit", 0))
        n_touched_band = int(kinds.get("touched_band", 0))
        n_closest_fallback = int(kinds.get("closest_fallback", 0))
        row["n_rule"] = n_rule
        row["n_force_fit"] = n_force_fit
        row["n_touched_band"] = n_touched_band
        row["n_closest_fallback"] = n_closest_fallback
        row["n_assigned"] = (
            n_rule + n_force_fit + n_touched_band + n_closest_fallback
        )

    # 5. Summary
    kind_totals = (assignments["kind"].value_counts().to_dict()
                   if not assignments.empty else {})
    n_assigned_total = int(len(assignments))
    n_uncovered = max(0, total_fridays - n_assigned_total)

    response = {
        "ranking": ranking,
        "secondary": secondary,
        "tolerance_pct": tolerance_pct,
        "rule_family": rule_family,
        "total_capital_usd": total_capital_usd,
        "pct_deploy": pct_deploy,
        "dd_metric": dd_metric,
        "dd_threshold": dd_threshold,
        "dd_metrics": dd_metrics,
        "dd_thresholds": dd_thresholds,
        "dd_constraints_applied": [{"metric": m, "threshold": t}
                                    for m, t in dd_constraints_list],
        "min_hit_pct": min_hit_pct,
        "max_loss_cap_pct": max_loss_cap_pct,
        "max_drop_peak_to_trough_pct": max_drop_peak_to_trough_pct,
        "min_n_trades": min_n_trades,
        "min_win_rate": min_win_rate,
        "max_losing_streak": max_losing_streak,
        "pick_mode": pick_mode,
        "coverage_mode": coverage_mode,
        "status": "ready",
        "rows": rows_records,
        "coverage_summary": {
            "total_fridays": total_fridays,
            "n_assigned": n_assigned_total,
            "n_uncovered": n_uncovered,
            "n_rule": int(kind_totals.get("rule", 0)),
            "n_force_fit": int(kind_totals.get("force_fit", 0)),
            "n_touched_band": int(kind_totals.get("touched_band", 0)),
            "n_closest_fallback": int(kind_totals.get("closest_fallback", 0)),
        },
        "n_rules": len(_rule_variants()),
        "n_cells": int(len(family_grid)),
    }
    # Store in memory cache (LRU-ish: drop oldest when over limit) and on disk.
    if len(_COVERAGE_CACHE) >= _COVERAGE_CACHE_MAX:
        try:
            _COVERAGE_CACHE.pop(next(iter(_COVERAGE_CACHE)))
        except StopIteration:
            pass
    _COVERAGE_CACHE[cache_key] = response
    _save_coverage_disk(cache_key, response, ds_mtime)
    return response

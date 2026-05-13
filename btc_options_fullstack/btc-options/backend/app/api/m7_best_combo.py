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
import logging
import math
import os
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
GRID_PARQUET_PATH = os.path.join(m7r.M7_BASE_DIR, "m7_best_combo_grid_v6.parquet")
# v4 fallback — load v4 while v6 is still rebuilding so the UI keeps
# working; new v6 columns will simply be None for those cells.
_GRID_FALLBACK_PATH = os.path.join(m7r.M7_BASE_DIR, "m7_best_combo_grid_v4.parquet")


# ── Sweep dimensions ──────────────────────────────────────────────────────────

# Three premium SL levels × four take-profit families per SL:
#   1 baseline (SL only) + 10 max_profit + 10 margin_target + 11 fixed-hour
#   = 32 variants per SL × 3 SLs = 96 total rule variants.
_PREMIUM_SL_PCTS = [50, 75, 100]
_PCT_GRID = [10, 15, 20, 25, 30, 40, 50, 60, 75, 100]
# Hourly Saturday exits 8 AM..5 PM IST + 5:29 PM (just before settlement).
# 17.4833 = 17h + 29m / 60 — the engine accepts decimal hours.
_FIXED_HOURS: list[float] = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0,
                              14.0, 15.0, 16.0, 17.0, 17.4833]


def _hour_label(h: float) -> str:
    """Format a fixed-hour value into a stable label suffix.
    8.0 → '8', 17.0 → '17', 17.4833 → '1729' (5:29 PM)."""
    minutes = round((h - int(h)) * 60)
    if minutes == 0:
        return f"{int(h)}"
    return f"{int(h)}{minutes:02d}"


def _rule_variants() -> list[tuple[str, dict]]:
    """96 (label, rule_dict) tuples. Each dict goes to _derive_exits."""
    out: list[tuple[str, dict]] = []
    for sl in _PREMIUM_SL_PCTS:
        out.append((f"sl{sl}_baseline", {"premium_sl_pct": sl}))
        for p in _PCT_GRID:
            out.append((f"sl{sl}_max_profit_{p}",
                        {"premium_sl_pct": sl, "max_profit_pct": p}))
        for p in _PCT_GRID:
            out.append((f"sl{sl}_margin_target_{p}",
                        {"premium_sl_pct": sl, "margin_target_pct": p}))
        for h in _FIXED_HOURS:
            out.append((f"sl{sl}_exit_hr_{_hour_label(h)}",
                        {"premium_sl_pct": sl, "fixed_exit_hour_ist": h}))
    return out


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
    "composite_score":       "max",  # win_rate × ret_on_credit ÷ (1 + |avg_min_mtm|/avg_credit)
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


def _build_grid(progress_cb=None) -> pd.DataFrame:
    """Compute the full (iv_band × expiry × delta × entry_hour × rule) grid.

    Per rule, derive the full trade set once, group by (iv_band, expiry,
    delta, entry_hour), compute metrics, append rows. After processing each
    rule we DROP that rule's entry from `_EXIT_CACHE` to keep peak memory
    bounded (~16 MB × N rules otherwise; many WSL setups OOM-kill at ~750 MB
    resident).

    Optional `progress_cb(rules_done, rules_total)` is called after each
    rule finishes — used by `_warmup_thread` to update the public progress
    counter so 202 responses can show a useful "X/N" hint.
    """
    rows: list[dict] = []
    variants = _rule_variants()
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
            keep = derived[
                derived["entry_atm_iv_band"].notna()
                & derived["expiry_bucket"].notna()
                & derived["delta_target"].notna()
                & derived["entry_hour_ist"].notna()
                & derived["gross_pnl_usd"].notna()
            ]
            if not keep.empty:
                grouped = keep.groupby(
                    ["entry_atm_iv_band", "expiry_bucket",
                     "delta_target", "entry_hour_ist"],
                    dropna=False, sort=False,
                )
                for (iv_band, expiry, delta, hour), sub in grouped:
                    if sub.empty:
                        continue
                    cell = _compute_cell_metrics(sub)
                    cell.update({
                        "iv_band": iv_band,
                        "expiry_bucket": expiry,
                        "delta_target": float(delta) if delta is not None else None,
                        "entry_hour_ist": int(hour) if hour is not None else None,
                        "rule_label": rule_label,
                        "rule": rule_dict,
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
    natively. Flatten to three rule_* numeric columns before persisting."""
    if df.empty or "rule" not in df.columns:
        return df
    out = df.copy()
    out["rule_premium_sl_pct"] = out["rule"].apply(
        lambda r: (r or {}).get("premium_sl_pct"))
    out["rule_max_profit_pct"] = out["rule"].apply(
        lambda r: (r or {}).get("max_profit_pct"))
    out["rule_margin_target_pct"] = out["rule"].apply(
        lambda r: (r or {}).get("margin_target_pct"))
    out = out.drop(columns=["rule"])
    return out


def _unflatten_after_load(df: pd.DataFrame) -> pd.DataFrame:
    """Inverse of `_flatten_for_parquet` — reconstruct nested rule dict."""
    if df.empty:
        return df
    rule_cols = ["rule_premium_sl_pct", "rule_max_profit_pct", "rule_margin_target_pct"]
    if not all(c in df.columns for c in rule_cols):
        return df
    out = df.copy()

    def _build_rule(r):
        d = {}
        for src, dst in zip(rule_cols, ["premium_sl_pct", "max_profit_pct", "margin_target_pct"]):
            v = r.get(src)
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                d[dst] = int(v) if dst != "premium_sl_pct" or v == int(v) else v
        return d

    out["rule"] = out[rule_cols].to_dict(orient="records")
    out["rule"] = out["rule"].apply(_build_rule)
    out = out.drop(columns=rule_cols)
    return out


def _grid_path_is_valid(path: str) -> bool:
    """True if the parquet snapshot at `path` exists AND is newer than the
    trades parquet (i.e. the dataset hasn't changed since we last computed)."""
    if not os.path.exists(path):
        return False
    grid_mtime = os.path.getmtime(path)
    trades_path = (m7r.TRADES_ENRICHED_PATH
                   if os.path.exists(m7r.TRADES_ENRICHED_PATH)
                   else m7r.TRADES_PATH)
    if not os.path.exists(trades_path):
        return True  # weird, but trust the snapshot
    return grid_mtime >= os.path.getmtime(trades_path)


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


def _try_load_grid_from_disk() -> Optional[pd.DataFrame]:
    """Load persisted grid if present and fresh; else None.

    Tries v6 first, then v4. v4 lacks path peak-trough-peak / risk-adjusted
    columns — those flow through as None / NaN. After load we enrich with
    overall (all-trades) MTM composites AND composite score.
    """
    for path in (GRID_PARQUET_PATH, _GRID_FALLBACK_PATH):
        if not _grid_path_is_valid(path):
            continue
        try:
            df = pd.read_parquet(path)
            df = _unflatten_after_load(df)
            df = _enrich_grid_with_overall_mtm(df)
            df = _attach_composite_score(df)
            return _attach_risk_adjusted(df)
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
_GRID_STATE: dict = {
    "status": "pending",          # pending | warming | ready | error
    "rules_done": 0,
    "rules_total": len(_rule_variants()),
    "grid": None,                 # pd.DataFrame once status == "ready"
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_GRID_LOCK = threading.Lock()


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


def try_load_grid_only() -> bool:
    """Load the grid from disk if a fresh cache exists. Never spawns a
    background build — the build is run out-of-process by
    `scripts/build_m7_best_combo_grid.py`. Returns True iff a cached grid
    was loaded.
    """
    with _GRID_LOCK:
        if _GRID_STATE["status"] == "ready":
            return True
        cached = _try_load_grid_from_disk()
        if cached is not None and not cached.empty:
            _GRID_STATE["grid"] = cached
            _GRID_STATE["status"] = "ready"
            _GRID_STATE["rules_done"] = _GRID_STATE["rules_total"]
            _GRID_STATE["started_at"] = time.time()
            _GRID_STATE["finished_at"] = time.time()
            log.info("M7 best-combo grid loaded from disk cache (%d cells)",
                     len(cached))
            return True
    return False


def kick_off_warmup() -> bool:
    """DEPRECATED: kept for backwards compatibility. The build is now run
    out-of-process via `scripts/build_m7_best_combo_grid.py`. This function
    only loads from disk if present. Returns False (never spawns a thread).
    """
    return try_load_grid_only() and False


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
    return agg


def _attach_lots_column(
    grid: pd.DataFrame,
    total_capital_usd: Optional[float],
    pct_deploy: float,
    dd_metric: Optional[str],
    dd_threshold: Optional[float],
) -> pd.DataFrame:
    """Compute per-cell `lots` given capital + optional drawdown constraint.

    Backtester sized every trade at 100 lots; cell metrics are mean/sum/etc.
    over those 100-lot trades. Portfolio margin engine is linear in qty:
    margin(N) = margin(100) × N/100 exactly. So:

      lots_from_margin = floor(deployable_capital × 100 / avg_margin)
      lots_from_dd     = floor(|dd_threshold| × 100 / |dd_metric_per_100|)
      lots             = max(0, min(lots_from_margin, lots_from_dd))

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

    if dd_metric and dd_threshold is not None and dd_metric in out.columns:
        # Drawdown constraint — abs-value comparison so a negative threshold
        # (e.g. avg_loss = −$100) works the same as a positive one.
        mv = out[dd_metric].astype(float).abs()
        thr = abs(float(dd_threshold))
        lots_dd = np.where(mv > 0, np.floor(thr * 100.0 / mv.where(mv > 0, 1.0)), BIG)
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
    min_hit_pct: Optional[float] = 50.0,
    max_loss_cap_pct: Optional[float] = None,
    max_drop_peak_to_trough_pct: Optional[float] = None,
    min_n_trades: int = 5,
    min_win_rate: Optional[float] = None,
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
                                      description="Optional drawdown-constraint metric (e.g. avg_loss_usd, avg_min_mtm_losers, max_loss_usd). Cell's per-100-lot value × lots/100 must be ≤ |dd_threshold|."),
    dd_threshold: Optional[float] = Query(None,
                                           description="Threshold for dd_metric (absolute value used for comparison). Lots are capped so the cell's scaled dd_metric stays within this."),
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
    pick_mode: str = Query("by_hour",
                            description="'by_hour' (default) picks one cell per (band, hour); 'aggregate_hours' collapses the entry_hour dimension so each band's pick reflects every Friday whose IV landed in that band across all entry hours — much larger n_trades per pick."),
    include_grid: bool = Query(False,
                               description="If true, also return the full grid"),
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

    # Try fast disk-load (idempotent — no-op if already ready).
    try_load_grid_only()

    if _GRID_STATE["status"] == "pending":
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
    if _GRID_STATE["status"] == "error":
        raise HTTPException(status_code=500,
                            detail=f"warmup failed: {_GRID_STATE['error']}")

    grid: pd.DataFrame = _GRID_STATE["grid"]
    if grid is None or grid.empty:
        return {"ranking": ranking, "secondary": secondary,
                "tolerance_pct": tolerance_pct,
                "rule_family": rule_family,
                "status": "ready", "rows": [],
                "n_rules": 0, "n_cells": 0}

    family_grid = _filter_grid_by_family(grid, rule_family)
    if pick_mode == "aggregate_hours":
        # Collapse entry_hour_ist BEFORE filters/picker so n_trades reflects
        # all-hours coverage and the picker chooses from the aggregated grid.
        family_grid = _aggregate_across_hours(family_grid)
    best = _pick_best_per_band(
        family_grid, ranking,
        secondary=secondary,
        tolerance_pct=tolerance_pct if secondary else None,
        total_capital_usd=total_capital_usd,
        pct_deploy=pct_deploy,
        dd_metric=dd_metric,
        dd_threshold=dd_threshold,
        min_hit_pct=min_hit_pct,
        max_loss_cap_pct=max_loss_cap_pct,
        max_drop_peak_to_trough_pct=max_drop_peak_to_trough_pct,
        min_n_trades=min_n_trades,
        min_win_rate=min_win_rate,
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
        "total_capital_usd": total_capital_usd,
        "pct_deploy": pct_deploy,
        "dd_metric": dd_metric,
        "dd_threshold": dd_threshold,
        "min_hit_pct": min_hit_pct,
        "max_loss_cap_pct": max_loss_cap_pct,
        "max_drop_peak_to_trough_pct": max_drop_peak_to_trough_pct,
        "min_n_trades": min_n_trades,
        "min_win_rate": min_win_rate,
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
def get_iv_band_best_combo_status():
    """Lightweight progress probe for clients polling during warmup."""
    return {
        "status": _GRID_STATE["status"],
        "rules_done": int(_GRID_STATE["rules_done"]),
        "rules_total": int(_GRID_STATE["rules_total"]),
        "started_at": _GRID_STATE["started_at"],
        "finished_at": _GRID_STATE["finished_at"],
        "error": _GRID_STATE["error"],
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
        description="DD-cap metric column (e.g. largest_loss_mtm). Mirrors /iv_band_best_combo."),
    dd_threshold: Optional[float] = Query(None,
        description="DD-cap threshold (per-100-lot absolute value)."),
    min_hit_pct: float = Query(0.0, ge=0.0, le=100.0,
        description="Tag rules where hit % < this as filtered (mirrors main picker)."),
    max_loss_cap_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    max_drop_peak_to_trough_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    min_n_trades: int = Query(0, ge=0),
    min_win_rate: Optional[float] = Query(None, ge=0.0, le=100.0),
    rule_family: str = Query("all",
        description="Mirrors parent picker: 'all' (no family filter) | 'max_profit' (only sl{X}_max_profit_*) | 'margin_target' (only sl{X}_margin_target_*). Rules outside the family are tagged so the user sees why the picker ignored them."),
):
    """Return all rule variants for a fixed (band, expiry, delta, hour) cell.

    When sizing params are provided, also returns the *per-rule* `lots` (and
    `scaled_avg_net_pnl`, `scaled_max_loss_usd`) so the user can see exactly
    what the picker optimises on. Without sizing, baseline per-100-lot
    columns are returned.
    """
    try_load_grid_only()
    if _GRID_STATE["status"] != "ready":
        return {"rows": [], "status": _GRID_STATE["status"]}
    grid: pd.DataFrame = _GRID_STATE["grid"]
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
    if sizing_active:
        sub = _attach_lots_column(
            sub, total_capital_usd, pct_deploy, dd_metric, dd_threshold,
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
    }


@router.get("/iv_band_best_combo/cross_band_check")
def get_cross_band_check(
    band: str = Query(..., description="The picked cell's band (e.g. '0-20')"),
    expiry_bucket: str = Query(..., description="The picked cell's expiry bucket"),
    delta_target: float = Query(..., ge=0.0, le=1.0),
    entry_hour_ist: int = Query(..., ge=0, le=23),
    rule_label: str = Query(..., description="Rule label of the picked cell, e.g. sl75_max_profit_25"),
):
    """For the picked cell (rule + entry_hour + expiry + delta), compute
    its P&L decomposed by which IV band each Friday's actual entry IV landed
    in. Answers 'does this combo's rule generalise across IV regimes?'
    """
    try_load_grid_only()
    if _GRID_STATE["status"] != "ready":
        return {"rows": [], "status": _GRID_STATE["status"]}
    grid: pd.DataFrame = _GRID_STATE["grid"]
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


@router.get("/iv_band_best_combo/single_combo_simulation")
def get_single_combo_simulation(
    expiry_bucket: str = Query(...),
    delta_target: float = Query(..., ge=0.0, le=1.0),
    entry_hour_ist: int = Query(..., ge=0, le=23),
    rule_label: str = Query(...),
    total_capital_usd: Optional[float] = Query(None, ge=0.0),
    pct_deploy: float = Query(100.0, ge=0.0, le=100.0),
):
    """Counterfactual: what if every Friday traded this single combo regardless
    of IV regime? Aggregates the picked combo's cells across ALL bands and
    returns one combined headline.
    """
    try_load_grid_only()
    if _GRID_STATE["status"] != "ready":
        return {"status": _GRID_STATE["status"]}
    grid: pd.DataFrame = _GRID_STATE["grid"]
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


def _compute_missed_fridays(picks: pd.DataFrame) -> dict:
    """Shared body: given a picks frame (rule_label + (band, hour, expiry, Δ)
    per band), find Fridays not naturally covered by any pick and return
    per-Friday availability of each pick's (hour, expiry, Δ)."""
    if picks.empty:
        return {"rows": [], "status": "no_picks"}
    trades = m7r._load_trades()
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
        pick_records.append({
            "band": p["iv_band"],
            "entry_hour_ist": int(p["entry_hour_ist"]) if pd.notna(p["entry_hour_ist"]) else None,
            "expiry_bucket": p["expiry_bucket"],
            "delta_target": float(p["delta_target"]),
            "rule_label": p["rule_label"],
        })

    missed = sorted(set(all_fridays) - matched_fridays)
    if not missed:
        return {"rows": [], "status": "ready", "n_missed": 0,
                "n_total_fridays": len(all_fridays),
                "n_matched": len(matched_fridays),
                "picks": pick_records}

    rows = []
    for fday in missed:
        fday_trades = trades[trades["friday_date_ist"].astype(str) == fday]
        bands_touched = sorted(
            fday_trades["entry_atm_iv_band"].dropna().unique().tolist())
        pick_availability = []
        for p in pick_records:
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
            pick_availability.append({
                "pick_band": p["band"],
                "rule_label": p["rule_label"],
                "fits": fits,
                "actual_iv_band_on_this_friday": actual_band,
            })
        rows.append({
            "friday_date_ist": fday,
            "n_total_trades": int(len(fday_trades)),
            "bands_touched": bands_touched,
            "pick_availability": pick_availability,
        })

    return {
        "rows": rows,
        "status": "ready",
        "n_missed": len(missed),
        "n_total_fridays": len(all_fridays),
        "n_matched": len(matched_fridays),
        "picks": pick_records,
    }


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
    min_hit_pct: float = Query(50.0, ge=0.0, le=100.0),
    max_loss_cap_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    max_drop_peak_to_trough_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    min_n_trades: int = Query(5, ge=0),
    min_win_rate: Optional[float] = Query(None, ge=0.0, le=100.0),
    pick_mode: str = Query("by_hour"),
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
    try_load_grid_only()
    if _GRID_STATE["status"] != "ready":
        return {"rows": [], "status": _GRID_STATE["status"]}
    grid: pd.DataFrame = _GRID_STATE["grid"]
    if grid is None or grid.empty:
        return {"rows": [], "status": "empty"}
    family_grid = _filter_grid_by_family(grid, rule_family)
    if pick_mode == "aggregate_hours":
        family_grid = _aggregate_across_hours(family_grid)
    picks = _pick_best_per_band(
        family_grid, ranking,
        secondary=secondary,
        tolerance_pct=tolerance_pct if secondary else None,
        total_capital_usd=total_capital_usd,
        pct_deploy=pct_deploy,
        dd_metric=dd_metric,
        dd_threshold=dd_threshold,
        min_hit_pct=min_hit_pct,
        max_loss_cap_pct=max_loss_cap_pct,
        max_drop_peak_to_trough_pct=max_drop_peak_to_trough_pct,
        min_n_trades=min_n_trades,
        min_win_rate=min_win_rate,
    )
    return _compute_missed_fridays(picks)


@router.get("/iv_band_best_combo/missed_fridays_force_fit")
def get_missed_fridays_force_fit():
    """For each Friday NOT covered by the current 10 best-combo picks (under
    default conditions), report force-fit details: which of the 10 picks the
    Friday has a trade at, and (where the trade exists) the realized P&L for
    that pick on that Friday.

    Picks are computed in-endpoint using the default avg_net_pnl ranking +
    min_hit_pct=50 — i.e. matches what the Best Combo table picks by default.

    Returns: per-Friday list with {friday_date_ist, n_total_trades, bands_touched,
    pick_availability: [{pick_band, fits, net_pnl_if_fit, win_if_fit}]}.
    """
    try_load_grid_only()
    if _GRID_STATE["status"] != "ready":
        return {"rows": [], "status": _GRID_STATE["status"]}
    grid: pd.DataFrame = _GRID_STATE["grid"]
    if grid is None or grid.empty:
        return {"rows": [], "status": "empty"}
    picks = _pick_best_per_band(grid, "avg_net_pnl", min_hit_pct=50.0)
    return _compute_missed_fridays(picks)

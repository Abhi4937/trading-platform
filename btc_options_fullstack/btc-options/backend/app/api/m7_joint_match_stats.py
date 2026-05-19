"""M7 joint-match stats endpoint.

Summarises the joint delta+price-matched dataset:
- Joint-match vs delta-fallback counts (overall, per delta target, per IV
  band, per (delta × band)).
- Outcome breakdown for each subset (profit / SL / neutral, avg PnL, win rate).
- Price-diff distribution percentiles (p25 / median / p75 / p95).

Reads `m7_trades_price_matched.parquet` via `m7_results._derive_exits()` so
the exit classification matches the rest of the dashboard. When the parquet
is absent (no backfill run yet), returns `{"status": "no_data", ...}` with
HTTP 200 so the frontend can render a clear message.

Endpoint: GET /api/v1/m7/joint_match_stats?dataset=price_match
"""
from __future__ import annotations

import logging
import math
import os
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query

from app.api import m7_results as m7r

router = APIRouter()
log = logging.getLogger(__name__)


# Thresholds for outcome classification — kept simple to mirror what the
# frontend KPI cards expect. "Win" = positive net P&L; "SL hit" = the exit
# fired off `is_premium_sl_hit`; "neutral" = small magnitude (within ±10%
# of avg credit). Re-use existing columns from `_derive_exits` (see
# `m7_results._compute_all_exits`).
_NEUTRAL_BAND_PCT_OF_CREDIT = 10.0  # within ±10% of credit = neutral


def _outcome_block(df: pd.DataFrame) -> dict:
    """Compute the standard outcome KPIs for a subset of trades.
    Assumes df already has the columns produced by `_derive_exits`:
    `is_win`, `net_pnl_estimate_usd`, `credit_usd`, `is_premium_sl_hit`.
    """
    n = int(len(df))
    if n == 0:
        return {"n": 0, "profit_count": 0, "sl_count": 0,
                "neutral_count": 0, "avg_pnl_usd": 0.0, "win_rate": 0.0}

    is_win = df["is_win"] if "is_win" in df.columns else (df.get("net_pnl_estimate_usd", 0) > 0)
    sl_col = df["is_premium_sl_hit"] if "is_premium_sl_hit" in df.columns \
        else pd.Series([False] * n, index=df.index)
    net = pd.to_numeric(df.get("net_pnl_estimate_usd"), errors="coerce").fillna(0.0)
    credit = pd.to_numeric(df.get("credit_usd"), errors="coerce").replace(0, np.nan)
    pct_of_credit = (net / credit * 100.0).fillna(0.0)
    neutral = pct_of_credit.abs() <= _NEUTRAL_BAND_PCT_OF_CREDIT

    win_mask = is_win & ~neutral
    sl_mask = sl_col & ~win_mask

    return {
        "n": n,
        "profit_count": int(win_mask.sum()),
        "sl_count": int(sl_mask.sum()),
        "neutral_count": int(neutral.sum()),
        "avg_pnl_usd": round(float(net.mean()), 4),
        "win_rate": round(float(is_win.sum()) / n, 4),
    }


def _flat_row(group_col: str, key, joint_sub: pd.DataFrame,
               fb_sub: pd.DataFrame) -> dict:
    """Flatten joint vs fallback into a single row matching the frontend
    `M7JointMatchPerDelta` / `M7JointMatchPerBand` types: keys are
    `joint`, `fallback` (counts) plus `_winrate` / `_avg_pnl` for each side.

    Renames `entry_atm_iv_band` -> `iv_band` for response so the frontend
    `M7JointMatchPerBand` type's key matches.
    """
    joint_block = _outcome_block(joint_sub)
    fb_block = _outcome_block(fb_sub)
    key_val = (float(key) if isinstance(key, (np.floating, np.integer, float, int))
               else str(key))
    public_key = "iv_band" if group_col == "entry_atm_iv_band" else group_col
    return {
        public_key: key_val,
        "joint": int(joint_block["n"]),
        "fallback": int(fb_block["n"]),
        "joint_winrate": float(joint_block["win_rate"]),
        "fallback_winrate": float(fb_block["win_rate"]),
        "joint_avg_pnl": float(joint_block["avg_pnl_usd"]),
        "fallback_avg_pnl": float(fb_block["avg_pnl_usd"]),
    }


def _iv_band_lower_bound(label) -> float:
    """Parse the IV band lower bound (e.g. '20-30' -> 20.0) for stable
    ascending sort. Anything unparseable sorts to the end."""
    try:
        s = str(label)
        lo = s.split("-", 1)[0].strip()
        return float(lo)
    except Exception:
        return float("inf")


def _per_group_split(df: pd.DataFrame, group_col: str) -> list[dict]:
    """For each unique value of group_col, return a FLAT row with joint vs
    fallback counts + win rates + avg P&L. Sorted ascending by delta_target
    (numeric) or by IV band's lower bound (numeric, parsed from label)."""
    out: list[dict] = []
    if group_col not in df.columns:
        return out
    keys = df[group_col].dropna().unique().tolist()
    if group_col == "entry_atm_iv_band":
        keys.sort(key=_iv_band_lower_bound)
    else:
        try:
            keys.sort(key=lambda k: float(k))
        except Exception:
            keys.sort(key=str)
    for key in keys:
        sub = df[df[group_col] == key]
        joint_sub = sub[sub["match_mode"] == "joint"]
        fb_sub = sub[sub["match_mode"] == "delta_fallback"]
        out.append(_flat_row(group_col, key, joint_sub, fb_sub))
    return out


def _per_delta_x_band_split(df: pd.DataFrame) -> list[dict]:
    """Per (delta_target, iv_band) joint vs fallback breakdown — flat shape
    matching `M7JointMatchPerDeltaBand`. Sorted by (delta_target ascending,
    iv_band lower bound ascending)."""
    out: list[dict] = []
    if "delta_target" not in df.columns or "entry_atm_iv_band" not in df.columns:
        return out
    grp = df.groupby(["delta_target", "entry_atm_iv_band"], dropna=False)
    rows: list[tuple[float, float, dict]] = []
    for (delta, band), sub in grp:
        if pd.isna(delta) or pd.isna(band):
            continue
        joint_sub = sub[sub["match_mode"] == "joint"]
        fb_sub = sub[sub["match_mode"] == "delta_fallback"]
        joint_block = _outcome_block(joint_sub)
        fb_block = _outcome_block(fb_sub)
        row = {
            "delta_target": float(delta),
            "iv_band": str(band),
            "joint": int(joint_block["n"]),
            "fallback": int(fb_block["n"]),
            "joint_winrate": float(joint_block["win_rate"]),
            "fallback_winrate": float(fb_block["win_rate"]),
            "joint_avg_pnl": float(joint_block["avg_pnl_usd"]),
            "fallback_avg_pnl": float(fb_block["avg_pnl_usd"]),
        }
        rows.append((float(delta), _iv_band_lower_bound(band), row))
    rows.sort(key=lambda t: (t[0], t[1]))
    out = [r[2] for r in rows]
    return out


def _price_diff_percentiles(df: pd.DataFrame) -> dict:
    """Distribution percentiles of price_diff_pct across joint-mode rows
    only (delta-fallback rows have NaN since no joint match was found).

    Filter to match_mode == 'joint' BEFORE numeric coerce so the fallback
    rows' sentinel/NaN values don't dilute the percentile sample.
    """
    if "price_diff_pct" not in df.columns:
        return {"p25": None, "median": None, "p75": None, "p95": None, "n": 0}
    if "match_mode" in df.columns:
        joint_df = df[df["match_mode"] == "joint"]
    else:
        joint_df = df
    series = pd.to_numeric(joint_df["price_diff_pct"], errors="coerce").dropna()
    if series.empty:
        return {"p25": None, "median": None, "p75": None, "p95": None, "n": 0}
    return {
        "p25": round(float(series.quantile(0.25)), 4),
        "median": round(float(series.median()), 4),
        "p75": round(float(series.quantile(0.75)), 4),
        "p95": round(float(series.quantile(0.95)), 4),
        "n": int(len(series)),
    }


@router.get("/joint_match_stats")
def get_joint_match_stats(
    dataset: str = Query("delta_match",
                          description="Which dataset to summarise. Only "
                          "'price_match' (the joint dataset) has match_mode "
                          "data; passing 'delta_match' returns status='no_data' "
                          "with a clear message."),
    exit_rule: Optional[str] = Query(None,
                                       description="Optional JSON exit-rule (matches "
                                       "/aggregate). Default = baseline (hard-cap exit)."),
):
    """Return joint-match vs delta-fallback breakdowns for the price-matched
    dataset.

    The endpoint default is `dataset='delta_match'` for consistency with the
    rest of the M7 API surface. Passing the default returns a `no_data`
    response immediately since the delta-match dataset has no
    `match_mode` column. The frontend must call with `dataset=price_match`
    to get real stats.

    If the price-matched parquet doesn't exist yet, returns
    `{"status":"no_data", "message":"..."}` with 200.
    """
    if dataset == "delta_match":
        return {
            "status": "no_data",
            "dataset": dataset,
            "message": ("joint-match stats only available for price_match "
                        "dataset"),
        }
    parquet_path = m7r._trades_path_for_dataset(dataset)
    if not os.path.exists(parquet_path):
        return {
            "status": "no_data",
            "dataset": dataset,
            "message": ("price-matched trades parquet missing — run "
                        "`python -m app.analytics.m7_batch_backtester_joint` first."),
        }

    rule = m7r._parse_exit_rule(exit_rule)
    derived = m7r._derive_exits({}, rule, dataset=dataset)
    if derived.empty:
        return {
            "status": "no_trades", "dataset": dataset,
            "total_trades": 0, "joint_match_count": 0, "delta_fallback_count": 0,
            "per_delta_target": [], "per_iv_band": [], "per_delta_x_band": [],
            "joint_outcomes": _outcome_block(derived),
            "fallback_outcomes": _outcome_block(derived),
            "price_diff_distribution": _price_diff_percentiles(derived),
        }

    # match_mode might be absent on the delta-match dataset — treat all rows
    # as "joint" in that case so the response is well-formed.
    if "match_mode" not in derived.columns:
        derived = derived.copy()
        derived["match_mode"] = "joint"

    n_total = int(len(derived))
    n_joint = int((derived["match_mode"] == "joint").sum())
    n_fallback = int((derived["match_mode"] == "delta_fallback").sum())

    joint_df = derived[derived["match_mode"] == "joint"]
    fb_df = derived[derived["match_mode"] == "delta_fallback"]

    return {
        "status": "ok",
        "dataset": dataset,
        "total_trades": n_total,
        "joint_match_count": n_joint,
        "delta_fallback_count": n_fallback,
        "joint_match_pct": round(n_joint / n_total * 100.0, 2) if n_total else 0.0,
        "fallback_pct": round(n_fallback / n_total * 100.0, 2) if n_total else 0.0,
        "joint_outcomes": _outcome_block(joint_df),
        "fallback_outcomes": _outcome_block(fb_df),
        "per_delta_target": _per_group_split(derived, "delta_target"),
        "per_iv_band": _per_group_split(derived, "entry_atm_iv_band"),
        "per_delta_x_band": _per_delta_x_band_split(derived),
        "price_diff_distribution": _price_diff_percentiles(derived),
    }

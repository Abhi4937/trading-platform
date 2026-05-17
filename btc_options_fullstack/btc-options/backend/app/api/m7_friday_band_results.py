"""M7 Friday-band parallel-dashboard endpoints.

These mirror the M7-Sweep endpoints in `m7_results.py` but aggregate by
**Friday-band** (the band assigned to each Friday under the chosen mode
A1 / B1 / D1) rather than per-trade `entry_atm_iv_band`.

Endpoints:
  GET /m7/friday_band_summary                  — headline universe metrics + per-band counts
  GET /m7/friday_band_summary_table            — best (hour, expiry, delta) per Friday-band
  GET /m7/friday_band_best_combo_markers       — per-trade path markers grouped by Friday-band
  GET /m7/friday_band_losses_distribution      — losses anatomy with friday_band dimension

The trade universe is identical to /m7/summary; only the grouping dimension
differs. Each Friday is resolved to exactly one band under the chosen mode,
so trade counts (and headline strip values) are conserved.
"""
from __future__ import annotations

import asyncio
import threading
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
import duckdb
import numpy as np
import pandas as pd
from pydantic import BaseModel

from app.api import m7_results as m7r
from app.api import m7_best_combo as m7bc
from app.api import m7_friday_band_best_combo as m7fb

router = APIRouter()
log = logging.getLogger(__name__)


# Friday→band map cache, keyed by (band_mode, sorted tiebreakers).
# CRITICAL: without this cache, every endpoint call rebuilds the map. For D1
# with lookahead tiebreakers, the build does a groupby on the 3.25M-row
# per-trade archive — 5 concurrent rebuilds = OOM/crash. Sharing one map
# across the dashboard's burst (5 endpoints fire simultaneously) eliminates
# the crash.
_FB_MAP_CACHE: dict[tuple, dict[str, str]] = {}
_FB_MAP_LOCK = threading.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_friday_band_map(band_mode: str, d1_tiebreakers_raw: Optional[str]) -> dict[str, str]:
    """Return {friday_date_ist (str) → band_label} for the chosen mode.

    Validates `band_mode` and parses `d1_tiebreakers` via the same helper used
    by the best-combo endpoint so behaviour is identical.
    """
    if band_mode not in m7fb._VALID_BAND_MODES:
        raise HTTPException(400, f"band_mode must be one of {sorted(m7fb._VALID_BAND_MODES)}")
    # Make sure Sat-IV map is loaded.
    if not m7fb._load_grid_and_sat_iv():
        raise HTTPException(
            503,
            detail=f"Friday-band grid not loaded: {m7fb._GRID_STATE.get('error')}",
        )
    sat = m7fb._GRID_STATE["sat_iv"]
    if sat is None or sat.empty:
        return {}
    tb = m7fb._parse_d1_tiebreakers(d1_tiebreakers_raw) if band_mode == "D1" else []

    # Cache the (band_mode, tiebreakers) → Friday→band map so concurrent
    # endpoint calls share one result. Lookahead D1 builds do a groupby on
    # the 3.25M-row per-trade archive — without this cache, 5 concurrent
    # rebuilds crash uvicorn under memory/CPU pressure.
    cache_key: tuple = (band_mode, tuple(tb))
    cached = _FB_MAP_CACHE.get(cache_key)
    if cached is not None:
        return cached
    with _FB_MAP_LOCK:
        cached = _FB_MAP_CACHE.get(cache_key)
        if cached is not None:
            return cached

        if band_mode == "A1":
            fb_map = m7fb._friday_band_map_a1(sat)
        elif band_mode == "B1":
            fb_map = m7fb._friday_band_map_b1(sat)
        else:  # D1
            pt = m7fb._load_per_trade_archive() if any(
                t in m7fb._D1_TIEBREAKERS_LOOKAHEAD for t in tb
            ) else None
            fb_map = m7fb._friday_band_map_d1(sat, pt, tb)

        _FB_MAP_CACHE[cache_key] = fb_map
        log.info("M7 Friday-band map cached: mode=%s tb=%s (%d Fridays)",
                 band_mode, ",".join(tb) if tb else "-", len(fb_map))
        return fb_map


def _annotate_friday_band(derived: pd.DataFrame, fb_map: dict[str, str]) -> pd.DataFrame:
    """Add `friday_band` column to a derived-exits DataFrame.

    Also overwrites `entry_atm_iv_band` so downstream code that groups on that
    column (and frontend components that read it) gets the Friday-locked band
    instead of the per-trade band — keeps the existing m7_results helpers
    (winners-only loss rates, by_band breakdowns, dimensions support) usable
    without any forking.

    `derived` rows whose `friday_date_ist` isn't in the map are dropped.
    """
    if derived is None or derived.empty:
        return derived
    df = derived.copy()
    df["friday_date_ist_str"] = df["friday_date_ist"].astype(str)
    df["friday_band"] = df["friday_date_ist_str"].map(fb_map)
    df = df[df["friday_band"].notna()].copy()
    df.drop(columns=["friday_date_ist_str"], inplace=True)
    # Mirror friday_band onto entry_atm_iv_band so legacy column-aware code
    # (e.g. dimension grouping in _friday_band_losses_assemble) finds it.
    df["entry_atm_iv_band"] = df["friday_band"]
    return df


def _apply_friday_band_filter(df: pd.DataFrame, friday_band: Optional[str]) -> pd.DataFrame:
    if friday_band is None or friday_band == "" or "friday_band" not in df.columns:
        return df
    vals = [v.strip() for v in str(friday_band).split(",") if v.strip()]
    if not vals:
        return df
    return df[df["friday_band"].isin(vals)]


# Default expiry-bucket restriction for the new dashboard's pickers — mirrors
# the friday_band_best_combo endpoint. The user explicitly limited
# cross-scheme comparison to "current/next/next_to_next/weekly" since those
# are the 4 expiries reliably tradeable on every Friday. Pass empty string to
# the `expiry_buckets` query param to allow all expiries.
_DEFAULT_EXPIRY_BUCKETS = "current (Sat),next (Sun),next_to_next (Mon),weekly (7d)"


def _apply_expiry_buckets_filter(df: pd.DataFrame, raw: Optional[str]) -> pd.DataFrame:
    """Restrict `df` to the listed expiry_buckets. Empty/None → no restriction.

    Default value (kept by callers) restricts to the 4 popular expiries the
    user explicitly chose for cross-scheme comparison.
    """
    if raw is None or not str(raw).strip():
        return df
    keep = {s.strip() for s in str(raw).split(",") if s.strip()}
    if not keep or "expiry_bucket" not in df.columns:
        return df
    return df[df["expiry_bucket"].isin(keep)]


def _band_sort_key(b) -> int:
    if b == "100+":
        return 1000
    try:
        return int(str(b).split("-")[0])
    except (ValueError, AttributeError):
        return 9999


# ── Endpoint 1: /friday_band_summary ──────────────────────────────────────────

@router.get("/friday_band_summary")
def get_friday_band_summary(
    band_mode: str = Query("A1"),
    d1_tiebreakers: Optional[str] = Query(None),
    exit_rule: Optional[str] = None,
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    friday_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
    iv_skew_bucket: Optional[str] = None,
    delta_skew_bucket: Optional[str] = None,
    premium_skew_bucket: Optional[str] = None,
    leg_winner: Optional[str] = None,
    loss_cause: Optional[str] = None,
):
    """Headline universe metrics for the new Friday-band dashboard.

    The trade set is identical to /m7/summary for the same exit-rule + filters;
    this endpoint just resolves the Friday→band map for the chosen mode and
    returns per-band counts so the headline strip can render the right side
    of the breakdown without a second call.
    """
    fb_map = _resolve_friday_band_map(band_mode, d1_tiebreakers)
    filters = m7r._query_filters(delta_target, is_straddle, expiry_date,
                                  None,  # entry_atm_iv_band — band is resolved via friday_band map
                                  entry_hour_ist, dte_bucket, spot_bucket,
                                  ivp_bucket, ctx_pattern, ctx_gex_regime,
                                  friday_date_ist, expiry_bucket,
                                  iv_skew_bucket=iv_skew_bucket,
                                  delta_skew_bucket=delta_skew_bucket,
                                  premium_skew_bucket=premium_skew_bucket,
                                  leg_winner=leg_winner,
                                  loss_cause=loss_cause)
    rule = m7r._parse_exit_rule(exit_rule)
    derived = m7r._derive_exits(filters, rule)
    derived = _annotate_friday_band(derived, fb_map)
    derived = _apply_friday_band_filter(derived, friday_band)

    if derived is None or derived.empty:
        # Per-band Friday counts from the map. Deliberately NOT narrowed by the
        # friday_band query filter — this is the universe denominator for the
        # headline strip's per-band breakdown, used to compute coverage % even
        # when a user filters to a single band.
        n_fridays_per_band: dict[str, int] = {}
        for b in fb_map.values():
            n_fridays_per_band[b] = n_fridays_per_band.get(b, 0) + 1
        return {
            "band_mode": band_mode,
            "n_trades": 0, "n_wins": 0, "win_rate": 0.0,
            "avg_net_pnl_usd": 0.0, "total_net_pnl_usd": 0.0,
            "avg_credit_usd": 0.0, "avg_margin_usd": 0.0,
            "exit_reason_counts": {},
            "n_fridays_per_band": n_fridays_per_band,
            "n_fridays_total": sum(n_fridays_per_band.values()),
        }

    n = len(derived)
    n_wins = int(derived["is_win"].sum())

    n_fridays_per_band: dict[str, int] = {}
    for b in fb_map.values():
        n_fridays_per_band[b] = n_fridays_per_band.get(b, 0) + 1

    return {
        "band_mode": band_mode,
        "n_trades": n,
        "n_wins": n_wins,
        "win_rate": round(n_wins / n, 4),
        "avg_net_pnl_usd": round(float(derived["net_pnl_estimate_usd"].mean()), 4),
        "total_net_pnl_usd": round(float(derived["net_pnl_estimate_usd"].sum()), 4),
        "avg_gross_pnl_usd": round(float(derived["gross_pnl_usd"].mean()), 4),
        "avg_credit_usd": round(float(derived["credit_usd"].mean()), 4),
        "avg_margin_usd": round(float(derived["margin_used_usd_at_entry"].dropna().mean() or 0.0), 2),
        "exit_reason_counts": derived["exit_reason"].value_counts().to_dict(),
        "n_fridays_per_band": n_fridays_per_band,
        "n_fridays_total": sum(n_fridays_per_band.values()),
    }


# ── Endpoint 2: /friday_band_summary_table ────────────────────────────────────

@router.get("/friday_band_summary_table")
def get_friday_band_summary_table(
    band_mode: str = Query("A1"),
    d1_tiebreakers: Optional[str] = Query(None),
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
    expiry_buckets: Optional[str] = Query(
        _DEFAULT_EXPIRY_BUCKETS,
        description="CSV of allowed expiry buckets. Default restricts to the 4 popular expiries tradeable on every Friday (matches /friday_band_best_combo). Pass empty string to allow all."),
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    friday_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
    iv_skew_bucket: Optional[str] = None,
    delta_skew_bucket: Optional[str] = None,
    premium_skew_bucket: Optional[str] = None,
    leg_winner: Optional[str] = None,
    loss_cause: Optional[str] = None,
):
    """For each Friday-band, find the best (entry_hour, expiry, delta) combo
    by the chosen metric. Mirrors /iv_band_summary but groups on friday_band.
    """
    fb_map = _resolve_friday_band_map(band_mode, d1_tiebreakers)
    filters = m7r._query_filters(delta_target, is_straddle, expiry_date,
                                  None, entry_hour_ist, dte_bucket,
                                  spot_bucket, ivp_bucket, ctx_pattern,
                                  ctx_gex_regime, friday_date_ist, expiry_bucket,
                                  loss_cause=loss_cause)
    rule = m7r._parse_exit_rule(exit_rule)
    derived = m7r._derive_exits(filters, rule)
    derived = _annotate_friday_band(derived, fb_map)
    derived = _apply_friday_band_filter(derived, friday_band)
    derived = _apply_expiry_buckets_filter(derived, expiry_buckets)
    if derived is None or derived.empty:
        return {"rows": [], "metric": metric, "band_mode": band_mode}

    derived = derived[derived["gross_pnl_usd"].notna()]
    if derived.empty:
        return {"rows": [], "metric": metric, "band_mode": band_mode}

    dims = ["friday_band", "entry_hour_ist", "expiry_bucket", "delta_target"]
    grp = derived.groupby(dims, dropna=False)
    score = m7r._metric_score(grp, metric)
    n = grp.size()
    df = pd.DataFrame({"score": score, "n_trades": n}).reset_index()

    df_valid = df.dropna(subset=["score"])
    strict = df_valid[df_valid["n_trades"] >= 3]
    strict_idx = strict.groupby("friday_band", dropna=False)["score"].idxmax() if not strict.empty else pd.Index([])
    strict_best = strict.loc[strict_idx] if len(strict_idx) else strict.iloc[0:0]
    covered = set(strict_best["friday_band"].dropna().tolist())
    fallback = df_valid[~df_valid["friday_band"].isin(covered)]
    if not fallback.empty:
        fb_idx = fallback.groupby("friday_band", dropna=False)["score"].idxmax()
        fallback_best = fallback.loc[fb_idx]
        best = pd.concat([strict_best, fallback_best], ignore_index=True)
    else:
        best = strict_best.reset_index(drop=True)

    EXTRA_METRICS = [
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
        "avg_exit_offset_minutes", "avg_winner_exit_offset_minutes",
        "avg_loser_exit_offset_minutes",
    ]
    for m in EXTRA_METRICS:
        best[f"_{m}"] = float("nan")
    for i, row in best.iterrows():
        sub = derived[
            (derived["friday_band"] == row["friday_band"]) &
            (derived["entry_hour_ist"] == row["entry_hour_ist"]) &
            (derived["expiry_bucket"] == row["expiry_bucket"]) &
            (derived["delta_target"] == row["delta_target"])
        ]
        if sub.empty:
            continue
        sub_grp = sub.groupby(lambda _: 0)
        for m in EXTRA_METRICS:
            try:
                val = float(m7r._metric_score(sub_grp, m).iloc[0])
            except Exception:
                val = float("nan")
            best.at[i, f"_{m}"] = val
    best = best.rename(columns={f"_{m}": m for m in EXTRA_METRICS})

    best = best.sort_values("friday_band",
                            key=lambda s: s.map(_band_sort_key)).reset_index(drop=True)
    best["score"] = best["score"].apply(lambda v: m7r._round_score(metric, v))
    for m in EXTRA_METRICS:
        if m in best.columns:
            best[m] = best[m].apply(lambda v: m7r._round_score(m, v))

    # Per-band Friday counts so the UI can render a coverage column.
    n_fridays_per_band: dict[str, int] = {}
    for b in fb_map.values():
        n_fridays_per_band[b] = n_fridays_per_band.get(b, 0) + 1

    rows = m7r._to_records(best)
    for r in rows:
        b = r.get("friday_band")
        r["n_fridays_in_band"] = int(n_fridays_per_band.get(b, 0))
        r["entry_atm_iv_band"] = b  # alias for components that read entry_atm_iv_band
    return {
        "rows": rows,
        "metric": metric,
        "band_mode": band_mode,
        "n_fridays_per_band": n_fridays_per_band,
    }


# ── Endpoint 3: /friday_band_best_combo_markers ──────────────────────────────

@router.get("/friday_band_best_combo_markers")
def get_friday_band_best_combo_markers(
    band_mode: str = Query("A1"),
    d1_tiebreakers: Optional[str] = Query(None),
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
    expiry_buckets: Optional[str] = Query(
        _DEFAULT_EXPIRY_BUCKETS,
        description="CSV of allowed expiry buckets. Default = 4 popular expiries."),
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    friday_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
    iv_skew_bucket: Optional[str] = None,
    delta_skew_bucket: Optional[str] = None,
    premium_skew_bucket: Optional[str] = None,
    leg_winner: Optional[str] = None,
    loss_cause: Optional[str] = None,
):
    """For each Friday-band's best combo, return per-trade path-marker rows.
    Mirrors /best_combo_markers but groups on friday_band.
    """
    fb_map = _resolve_friday_band_map(band_mode, d1_tiebreakers)
    filters = m7r._query_filters(delta_target, is_straddle, expiry_date,
                                  None, entry_hour_ist, dte_bucket,
                                  spot_bucket, ivp_bucket, ctx_pattern,
                                  ctx_gex_regime, friday_date_ist, expiry_bucket,
                                  loss_cause=loss_cause)
    rule = m7r._parse_exit_rule(exit_rule)
    derived = m7r._derive_exits(filters, rule)
    derived = _annotate_friday_band(derived, fb_map)
    derived = _apply_friday_band_filter(derived, friday_band)
    derived = _apply_expiry_buckets_filter(derived, expiry_buckets)
    if derived is None or derived.empty:
        return {"metric": metric, "band_mode": band_mode, "bands": []}

    dims = ["friday_band", "entry_hour_ist", "expiry_bucket", "delta_target"]
    grp = derived.groupby(dims, dropna=False)
    score = m7r._metric_score(grp, metric)
    n = grp.size()
    df = pd.DataFrame({"score": score, "n_trades": n}).reset_index()

    df_valid = df.dropna(subset=["score"])
    strict = df_valid[df_valid["n_trades"] >= 3]
    strict_idx = strict.groupby("friday_band", dropna=False)["score"].idxmax() if not strict.empty else pd.Index([])
    strict_best = strict.loc[strict_idx] if len(strict_idx) else strict.iloc[0:0]
    covered = set(strict_best["friday_band"].dropna().tolist())
    fallback = df_valid[~df_valid["friday_band"].isin(covered)]
    if not fallback.empty:
        fb_idx = fallback.groupby("friday_band", dropna=False)["score"].idxmax()
        fallback_best = fallback.loc[fb_idx]
        best = pd.concat([strict_best, fallback_best], ignore_index=True)
    else:
        best = strict_best.reset_index(drop=True)

    best = best.sort_values("friday_band",
                            key=lambda s: s.map(_band_sort_key)).reset_index(drop=True)

    keep_cols = [
        "friday_date_ist",
        "rel_time_max_mtm", "rel_time_min_mtm",
        "max_mtm_usd", "min_mtm_usd",
        "exit_mtm_usd",
        "net_pnl_estimate_usd",
        "is_win", "exit_reason",
    ]
    keep_cols = [c for c in keep_cols if c in derived.columns]

    bands_out: list[dict] = []
    for _, row in best.iterrows():
        sub = derived[
            (derived["friday_band"] == row["friday_band"]) &
            (derived["entry_hour_ist"] == row["entry_hour_ist"]) &
            (derived["expiry_bucket"] == row["expiry_bucket"]) &
            (derived["delta_target"] == row["delta_target"])
        ]
        if sub.empty:
            continue
        n_wins = int(sub["is_win"].sum()) if "is_win" in sub.columns else 0
        n_losses = int((~sub["is_win"].astype(bool)).sum()) if "is_win" in sub.columns else 0
        sub_proj = sub[keep_cols].copy()
        for c in ("rel_time_max_mtm", "rel_time_min_mtm"):
            if c in sub_proj.columns:
                sub_proj[c] = sub_proj[c].apply(
                    lambda v: round(float(v), 4) if pd.notna(v) else None)
        for c in ("max_mtm_usd", "min_mtm_usd", "exit_mtm_usd", "net_pnl_estimate_usd"):
            if c in sub_proj.columns:
                sub_proj[c] = sub_proj[c].apply(
                    lambda v: round(float(v), 2) if pd.notna(v) else None)
        sub_proj = sub_proj.sort_values("friday_date_ist").reset_index(drop=True)
        bands_out.append({
            "friday_band": row["friday_band"],
            "entry_atm_iv_band": row["friday_band"],  # alias for component compat
            "entry_hour_ist": int(row["entry_hour_ist"]),
            "expiry_bucket": row["expiry_bucket"],
            "delta_target": float(row["delta_target"]),
            "n_trades": int(row["n_trades"]),
            "n_wins": n_wins,
            "n_losses": n_losses,
            "trades": m7r._to_records(sub_proj),
        })

    return {"metric": metric, "band_mode": band_mode, "bands": bands_out}


# ── Endpoint 4: /friday_band_losses_distribution ─────────────────────────────

@router.get("/friday_band_losses_distribution")
def get_friday_band_losses_distribution(
    band_mode: str = Query("A1"),
    d1_tiebreakers: Optional[str] = Query(None),
    dimensions: Optional[str] = Query(None),
    exit_rule: Optional[str] = None,
    metric: str = Query("avg_net_pnl"),
    scope: Optional[str] = Query(None),
    expiry_buckets: Optional[str] = Query(
        _DEFAULT_EXPIRY_BUCKETS,
        description="CSV of allowed expiry buckets. Default = 4 popular expiries."),
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    friday_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
    iv_skew_bucket: Optional[str] = None,
    delta_skew_bucket: Optional[str] = None,
    premium_skew_bucket: Optional[str] = None,
    leg_winner: Optional[str] = None,
    loss_cause: Optional[str] = None,
    include_trades: bool = Query(False),
    trades_limit: int = Query(50, ge=1, le=200),
    trades_offset: int = Query(0, ge=0),
    trades_sort: str = Query("pnl_asc"),
    only_sl_hits: bool = Query(False),
):
    """Loss distribution over the Friday-band universe.

    Scopes:
      - default: universe (all filtered trades).
      - 'best_combo': per-band best-combo trade set (uses Friday-band grid).
    `scope=full_coverage` is not supported (dropped on this dashboard).
    """
    if scope == "full_coverage":
        raise HTTPException(
            400,
            detail="scope=full_coverage not supported on the Friday-band dashboard "
                   "— every Friday is already assigned to exactly one band.",
        )

    # Validate dim names up-front so a bad query rejects fast (don't pay the
    # 1-3s _derive_exits cost first).
    _ALLOWED_LOSSES_DIMS = {
        "loss_cause", "entry_atm_iv_band", "friday_band",
        "delta_target", "expiry_bucket", "entry_hour_ist",
        "leg_winner", "exit_reason",
    }
    if dimensions:
        bad = [d.strip() for d in dimensions.split(",")
               if d.strip() and d.strip() not in _ALLOWED_LOSSES_DIMS]
        if bad:
            raise HTTPException(400, detail=f"Unknown dimension(s): {bad}")

    fb_map = _resolve_friday_band_map(band_mode, d1_tiebreakers)
    filters = m7r._query_filters(delta_target, is_straddle, expiry_date,
                                  None, entry_hour_ist, dte_bucket,
                                  spot_bucket, ivp_bucket, ctx_pattern,
                                  ctx_gex_regime, friday_date_ist, expiry_bucket,
                                  loss_cause=loss_cause)
    rule = m7r._parse_exit_rule(exit_rule)

    scope_summary: dict = {
        "scope": scope,
        "metric": metric if scope == "best_combo" else None,
        "n_in_scope": 0,
        "band_mode": band_mode,
        "per_band_rules": [],
    }

    if scope == "best_combo":
        # Pick best combos from the Friday-band grid for the chosen mode.
        tb = m7fb._parse_d1_tiebreakers(d1_tiebreakers) if band_mode == "D1" else []
        try:
            grid = m7fb._get_grid_for_mode(band_mode, tb)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"Friday-band grid load failed: {exc}")
        if grid is None or grid.empty:
            derived_all = m7r._derive_exits(filters, rule)
            derived_all = _annotate_friday_band(derived_all, fb_map)
            derived_all = _apply_friday_band_filter(derived_all, friday_band)
            return _friday_band_losses_assemble(derived_all, scope_summary, dimensions,
                                                include_trades, trades_limit, trades_offset,
                                                trades_sort, only_sl_hits)
        # Use existing m7_best_combo._pick_best_per_band logic on iv_band column.
        best = m7bc._pick_best_per_band(grid, "avg_net_pnl")
        if best.empty:
            return _friday_band_losses_assemble(pd.DataFrame(), scope_summary, dimensions,
                                                include_trades, trades_limit, trades_offset,
                                                trades_sort, only_sl_hits)

        per_band_rules: list[dict] = []
        per_band_dfs: list[pd.DataFrame] = []
        for _, row in best.iterrows():
            band = row.get("iv_band")
            expiry = row.get("expiry_bucket")
            delta = row.get("delta_target")
            rule_label = row.get("rule_label")
            rule_dict = row.get("rule") if row.get("rule") is not None else {}
            if not isinstance(rule_dict, dict):
                rule_dict = {}
            try:
                band_derived = m7r._derive_exits(filters, rule_dict)
            except Exception:
                continue
            band_derived = _annotate_friday_band(band_derived, fb_map)
            band_derived = _apply_friday_band_filter(band_derived, friday_band)
            band_derived = _apply_expiry_buckets_filter(band_derived, expiry_buckets)
            if band_derived is None or band_derived.empty:
                per_band_rules.append({
                    "band": str(band) if band is not None else None,
                    "rule_label": str(rule_label) if rule_label is not None else None,
                    "rule_dict": dict(rule_dict),
                    "expiry_bucket": str(expiry) if expiry is not None else None,
                    "delta_target": float(delta) if delta is not None else None,
                    "n_trades": 0,
                })
                continue
            try:
                delta_f = float(delta) if delta is not None else None
            except (TypeError, ValueError):
                delta_f = None
            mask = (band_derived["friday_band"] == band) & \
                   (band_derived["expiry_bucket"] == expiry)
            if delta_f is not None:
                mask = mask & (band_derived["delta_target"].astype(float) == delta_f)
            sub = band_derived[mask]
            per_band_dfs.append(sub)
            per_band_rules.append({
                "band": str(band) if band is not None else None,
                "rule_label": str(rule_label) if rule_label is not None else None,
                "rule_dict": dict(rule_dict),
                "expiry_bucket": str(expiry) if expiry is not None else None,
                "delta_target": float(delta) if delta is not None else None,
                "n_trades": int(len(sub)),
            })
        scope_summary["per_band_rules"] = per_band_rules
        scope_summary["exit_rule_overridden"] = True
        derived = pd.concat(per_band_dfs, ignore_index=True) if per_band_dfs else pd.DataFrame()
        scope_summary["n_in_scope"] = int(len(derived))
    else:
        derived = m7r._derive_exits(filters, rule)
        derived = _annotate_friday_band(derived, fb_map)
        derived = _apply_friday_band_filter(derived, friday_band)
        derived = _apply_expiry_buckets_filter(derived, expiry_buckets)
        scope_summary["n_in_scope"] = int(len(derived))

    return _friday_band_losses_assemble(derived, scope_summary, dimensions,
                                        include_trades, trades_limit, trades_offset,
                                        trades_sort, only_sl_hits)


def _friday_band_losses_assemble(
    derived: pd.DataFrame, scope_summary: dict, dimensions: Optional[str],
    include_trades: bool, trades_limit: int, trades_offset: int,
    trades_sort: str, only_sl_hits: bool,
) -> dict:
    """Assemble loss-distribution payload from a derived-exits frame
    that's already annotated with `friday_band`."""
    import math
    if derived is None or derived.empty:
        return {
            "n_losses": 0, "n_total": 0, "loss_rate": 0.0,
            "avg_loss_usd": 0.0, "total_loss_usd": 0.0, "worst_loss_usd": 0.0,
            "by_cause": {}, "by_band": {}, "by_band_stats": [], "rows": [],
            "scope_summary": scope_summary,
            "losers_sample": [], "losers_sample_total": 0,
            "losers_sample_offset": 0, "losers_sample_limit": 0,
        }

    n_total = len(derived)
    losers = derived[~derived["is_win"]]
    n_losses = len(losers)
    loss_rate = n_losses / n_total if n_total else 0.0
    avg_loss = float(losers["net_pnl_estimate_usd"].mean()) if n_losses else 0.0
    total_loss = float(losers["net_pnl_estimate_usd"].sum()) if n_losses else 0.0
    worst_loss = float(losers["net_pnl_estimate_usd"].min()) if n_losses else 0.0

    by_cause = {}
    if "loss_cause" in losers.columns:
        for cause, sub in losers.groupby("loss_cause", dropna=False):
            key = str(cause) if cause is not None and not (isinstance(cause, float) and pd.isna(cause)) else "unclassified"
            by_cause[key] = int(len(sub))

    by_band: dict[str, int] = {}
    if "friday_band" in losers.columns:
        for band, sub in losers.groupby("friday_band", dropna=False):
            by_band[str(band)] = int(len(sub))

    def _safe_stat(s: pd.Series, op: str):
        try:
            if s.empty or s.isna().all():
                return None
            val = getattr(s, op)()
            if pd.isna(val):
                return None
            return round(float(val), 4)
        except Exception:
            return None

    by_band_stats: list[dict] = []
    if not losers.empty and "friday_band" in losers.columns:
        derived_band_n = (derived.groupby("friday_band", dropna=False)
                                  .size().to_dict())
        for band, sub in losers.groupby("friday_band", dropna=False):
            band_label = str(band) if band is not None and not (
                isinstance(band, float) and pd.isna(band)) else None

            net      = sub["net_pnl_estimate_usd"] if "net_pnl_estimate_usd" in sub.columns else pd.Series(dtype=float)
            exit_mtm = sub["exit_mtm_usd"]         if "exit_mtm_usd"         in sub.columns else pd.Series(dtype=float)
            max_mtm  = sub["max_mtm_usd"]          if "max_mtm_usd"          in sub.columns else pd.Series(dtype=float)
            min_mtm  = sub["min_mtm_usd"]          if "min_mtm_usd"          in sub.columns else pd.Series(dtype=float)

            avg_max_l = _safe_stat(max_mtm, "mean")
            n_above_avg_peak = 0
            if avg_max_l is not None and not max_mtm.empty:
                n_above_avg_peak = int((max_mtm > avg_max_l).sum())

            n_rule_trigger = 0
            n_hard_cap = 0
            if "exit_reason" in sub.columns:
                er = sub["exit_reason"].fillna("")
                n_rule_trigger = int((er == "rule_trigger").sum())
                n_hard_cap     = int((er == "hard_cap").sum())

            by_band_stats.append({
                "friday_band": band_label,
                "entry_atm_iv_band": band_label,  # alias
                "n_band_total":   int(derived_band_n.get(band, 0)),
                "n_loss":         int(len(sub)),
                "avg_loss_usd":     _safe_stat(net, "mean"),
                "total_loss_usd":   _safe_stat(net, "sum"),
                "largest_loss_usd": _safe_stat(net, "min"),
                "avg_loss_mtm":      _safe_stat(exit_mtm, "mean"),
                "total_loss_mtm":    _safe_stat(exit_mtm, "sum"),
                "largest_loss_mtm":  _safe_stat(exit_mtm, "min"),
                "avg_max_mtm_losers":_safe_stat(max_mtm,  "mean"),
                "avg_min_mtm_losers":_safe_stat(min_mtm,  "mean"),
                "max_mtm_losers":    _safe_stat(max_mtm,  "max"),
                "min_mtm_losers":    _safe_stat(min_mtm,  "min"),
                "n_losers_above_avg_max_mtm": n_above_avg_peak,
                "n_rule_trigger": n_rule_trigger,
                "n_hard_cap":     n_hard_cap,
            })
        by_band_stats.sort(key=lambda r: _band_sort_key(r.get("friday_band")))

    rows = []
    if dimensions:
        dims = [d.strip() for d in dimensions.split(",") if d.strip()]
        for d in dims:
            if d not in losers.columns:
                raise HTTPException(status_code=400,
                    detail=f"Unknown dimension: {d}")
        if dims:
            grp = losers.groupby(dims, dropna=False)
            for key, sub in grp:
                if not isinstance(key, tuple):
                    key = (key,)
                row = {dim: (None if pd.isna(v) else
                             (int(v) if isinstance(v, (int, np.integer))
                              else float(v) if isinstance(v, (float, np.floating))
                              else str(v)))
                       for dim, v in zip(dims, key)}
                row["n"] = int(len(sub))
                row["avg_loss_usd"] = round(float(sub["net_pnl_estimate_usd"].mean()), 4)
                row["total_loss_usd"] = round(float(sub["net_pnl_estimate_usd"].sum()), 4)
                row["share"] = round(len(sub) / n_total, 6) if n_total else 0
                rows.append(row)
            rows.sort(key=lambda r: r["n"], reverse=True)

    losers_sample: list[dict] = []
    losers_total = 0
    if include_trades and not losers.empty:
        sample = losers
        if only_sl_hits and "exit_reason" in sample.columns:
            sample = sample[sample["exit_reason"] == "rule_trigger"]
        losers_total = int(len(sample))

        sort_key, asc = "net_pnl_estimate_usd", True
        if trades_sort == "pnl_desc":         sort_key, asc = "net_pnl_estimate_usd", False
        elif trades_sort == "friday_asc":     sort_key, asc = "friday_date_ist",      True
        elif trades_sort == "friday_desc":    sort_key, asc = "friday_date_ist",      False
        elif trades_sort == "band":           sort_key, asc = "friday_band",          True
        if sort_key in sample.columns:
            sample = sample.sort_values(sort_key, ascending=asc, kind="mergesort")

        sample = sample.iloc[trades_offset: trades_offset + trades_limit]
        for _, r in sample.iterrows():
            mx = r.get("max_mtm_usd")
            mn = r.get("min_mtm_usd")
            try:
                mx_f = float(mx) if mx is not None else float("nan")
                mn_f = float(mn) if mn is not None else float("nan")
                if math.isnan(mx_f) and math.isnan(mn_f):
                    swing = None
                else:
                    cand = []
                    if not math.isnan(mx_f): cand.append(abs(mx_f))
                    if not math.isnan(mn_f): cand.append(abs(mn_f))
                    swing = round(max(cand), 4) if cand else None
            except (TypeError, ValueError):
                swing = None

            def _ns(v):
                if v is None: return None
                try:
                    f = float(v)
                    return None if math.isnan(f) else round(f, 4)
                except (TypeError, ValueError):
                    return None
            band_v = r.get("friday_band")
            losers_sample.append({
                "trade_id": str(r["trade_id"]) if r.get("trade_id") is not None else None,
                "friday_date_ist": str(r["friday_date_ist"]) if r.get("friday_date_ist") is not None else None,
                "friday_band": str(band_v) if band_v is not None else None,
                "entry_atm_iv_band": str(band_v) if band_v is not None else None,
                "entry_hour_ist": int(r["entry_hour_ist"]) if r.get("entry_hour_ist") is not None and not (isinstance(r["entry_hour_ist"], float) and math.isnan(r["entry_hour_ist"])) else None,
                "expiry_bucket": str(r["expiry_bucket"]) if r.get("expiry_bucket") is not None else None,
                "delta_target": _ns(r.get("delta_target")),
                "exit_reason": str(r["exit_reason"]) if r.get("exit_reason") is not None else None,
                "loss_cause": str(r["loss_cause"]) if r.get("loss_cause") is not None and not (isinstance(r["loss_cause"], float) and math.isnan(r["loss_cause"])) else None,
                "net_pnl_estimate_usd": _ns(r.get("net_pnl_estimate_usd")),
                "max_mtm_usd": _ns(r.get("max_mtm_usd")),
                "min_mtm_usd": _ns(r.get("min_mtm_usd")),
                "largest_swing_usd": swing,
            })

    return {
        "n_losses": n_losses,
        "n_total": n_total,
        "loss_rate": round(loss_rate, 4),
        "avg_loss_usd": round(avg_loss, 4),
        "total_loss_usd": round(total_loss, 4),
        "worst_loss_usd": round(worst_loss, 4),
        "by_cause": by_cause,
        "by_band": by_band,
        "by_band_stats": by_band_stats,
        "rows": rows,
        "scope_summary": scope_summary,
        "losers_sample": losers_sample,
        "losers_sample_total": losers_total,
        "losers_sample_offset": trades_offset if include_trades else 0,
        "losers_sample_limit":  trades_limit  if include_trades else 0,
    }


# ── Endpoint 5: /friday_band_mtm_overlay ─────────────────────────────────────

# ── Pydantic response models ──────────────────────────────────────────────────

class _PickPath(BaseModel):
    trade_id: str
    friday_date_ist: str
    minutes: List[int]
    pnl: List[float]
    net_pnl_estimate_usd: Optional[float] = None
    max_mtm_usd: Optional[float] = None
    min_mtm_usd: Optional[float] = None


class _BandPicks(BaseModel):
    best: Optional[_PickPath] = None
    worst: Optional[_PickPath] = None
    best_max_mtm: Optional[_PickPath] = None
    worst_min_mtm: Optional[_PickPath] = None


class _AvgCurve(BaseModel):
    minutes: List[int]
    mean_pnl: List[float]
    n_trades_alive: List[int]


class _BandOverlay(BaseModel):
    friday_band: str
    entry_hour_ist: int
    expiry_bucket: str
    delta_target: float
    n_trades: int
    picks: _BandPicks
    avg_curve: _AvgCurve


class FridayBandMtmOverlayResponse(BaseModel):
    band_mode: str
    bands: List[_BandOverlay]


# ── Overlay cache (mirrors _FB_MAP_CACHE pattern) ────────────────────────────

_OVERLAY_CACHE: dict[tuple, FridayBandMtmOverlayResponse] = {}
_OVERLAY_CACHE_LOCK = threading.Lock()


# ── Private helpers ───────────────────────────────────────────────────────────

def _pick_trade_row(df: pd.DataFrame, col: str, ascending: bool):
    """Return the first row of df sorted by (col, trade_id ASC), or None."""
    d = df.dropna(subset=[col])
    if d.empty:
        return None
    return d.sort_values([col, "trade_id"], ascending=[ascending, True]).iloc[0]


def _run_overlay_query(
    friday_dates: tuple[str, ...],
    trade_ids: tuple[int, ...],
    max_minutes: int,
) -> pd.DataFrame:
    """Execute one DuckDB query that fetches path rows for all picked trades.

    Acquires _EXIT_COMPUTE_LOCK (threading.Lock) synchronously — safe to call
    from a thread spawned by asyncio.to_thread.

    Returns a DataFrame with columns: trade_id (int64), minute_offset, gross_pnl_usd.
    Returns empty DataFrame if no data.
    """
    if not friday_dates or not trade_ids:
        return pd.DataFrame(columns=["trade_id", "minute_offset", "gross_pnl_usd"])

    dates_csv = ", ".join(f"'{d}'" for d in friday_dates)
    ids_csv = ", ".join(str(i) for i in trade_ids)

    sql = f"""
    SELECT trade_id, minute_offset, gross_pnl_usd
    FROM read_parquet('{m7r.PATHS_GLOB}', hive_partitioning=true)
    WHERE friday_date IN ({dates_csv})
      AND trade_id IN ({ids_csv})
      AND minute_offset BETWEEN 0 AND {max_minutes}
    ORDER BY trade_id, minute_offset
    """
    with m7r._EXIT_COMPUTE_LOCK:
        conn = duckdb.connect()
        try:
            df = conn.execute(sql).df()
        finally:
            conn.close()
    return df


def _build_pick_path(
    row,
    path_df: pd.DataFrame,
    max_minutes: int,
    net_pnl_col: Optional[str],
    scalar_col: Optional[str],
) -> Optional[_PickPath]:
    """Build a _PickPath from a derived-frame row + pre-fetched path DataFrame."""
    if row is None:
        return None
    tid = int(row["trade_id"])
    trade_path = path_df[path_df["trade_id"] == tid].sort_values("minute_offset")
    if trade_path.empty:
        return None

    minutes_list = trade_path["minute_offset"].tolist()
    pnl_list = [round(float(v), 4) for v in trade_path["gross_pnl_usd"]]

    net_pnl = None
    if net_pnl_col and net_pnl_col in row.index and pd.notna(row[net_pnl_col]):
        net_pnl = round(float(row[net_pnl_col]), 2)

    scalar_val = None
    if scalar_col and scalar_col in row.index and pd.notna(row[scalar_col]):
        scalar_val = round(float(row[scalar_col]), 2)

    kwargs: dict = {
        "trade_id": str(tid),
        "friday_date_ist": str(row["friday_date_ist"]),
        "minutes": minutes_list,
        "pnl": pnl_list,
        "net_pnl_estimate_usd": net_pnl,
    }
    if scalar_col == "max_mtm_usd":
        kwargs["max_mtm_usd"] = scalar_val
    elif scalar_col == "min_mtm_usd":
        kwargs["min_mtm_usd"] = scalar_val

    return _PickPath(**kwargs)


def _build_avg_curve(
    band_df: pd.DataFrame,
    path_df: pd.DataFrame,
    max_minutes: int,
    min_n_trades_in_avg: int,
) -> _AvgCurve:
    """Build the carry-forward average curve for one band.

    Carry-forward: after a trade's last recorded minute its final gross_pnl_usd
    is propagated to max_minutes so it keeps contributing to the per-minute mean.
    n_trades_alive counts trades whose last path minute >= m (still actually live
    at that minute per the path data, before carry-forward).

    mean_pnl denominator is always n_trades (constant) — carry-forward semantics.
    Tail minutes with n_trades_alive < min_n_trades_in_avg are dropped.
    """
    n_trades = len(band_df)
    if n_trades == 0:
        return _AvgCurve(minutes=[], mean_pnl=[], n_trades_alive=[])

    trade_ids_in_band = set(band_df["trade_id"].astype(int).tolist())
    band_paths = path_df[path_df["trade_id"].isin(trade_ids_in_band)].copy()

    if band_paths.empty:
        return _AvgCurve(minutes=[], mean_pnl=[], n_trades_alive=[])

    all_minutes = list(range(0, max_minutes + 1))

    # Build a pivot: rows=minute_offset, columns=trade_id, values=gross_pnl_usd
    # Only include minutes present in the path; fill gaps per trade with ffill.
    pivot = (
        band_paths
        .pivot_table(index="minute_offset", columns="trade_id",
                     values="gross_pnl_usd", aggfunc="last")
        .reindex(all_minutes)
    )

    # Forward-fill each trade's column to carry the exit value forward.
    pivot = pivot.ffill()

    # n_trades_alive: at minute m, count of trades whose last recorded minute >= m.
    last_minute_by_trade = band_paths.groupby("trade_id")["minute_offset"].max()
    # Align to trade_ids actually present in pivot columns.
    last_mins = last_minute_by_trade.reindex(pivot.columns).fillna(-1).values

    n_alive_arr = np.array([
        int((last_mins >= m).sum())
        for m in all_minutes
    ])

    # Compute mean_pnl: sum of carry-forward values / n_trades (not n_alive).
    # Trades with no path data at all contribute 0 (their column stays NaN after
    # ffill until their first minute, which at minute 0 should be 0).
    row_sums = pivot.sum(axis=1, skipna=True).values
    mean_pnl_arr = row_sums / n_trades

    # Apply min_n_trades_in_avg truncation: drop tail where n_alive < threshold.
    # Find the last minute where n_alive >= threshold.
    qualifying = np.where(n_alive_arr >= min_n_trades_in_avg)[0]
    if len(qualifying) == 0:
        return _AvgCurve(minutes=[], mean_pnl=[], n_trades_alive=[])
    last_valid = int(qualifying[-1])

    minutes_out = all_minutes[:last_valid + 1]
    mean_pnl_out = [round(float(v), 4) for v in mean_pnl_arr[:last_valid + 1]]
    n_alive_out = [int(v) for v in n_alive_arr[:last_valid + 1]]

    return _AvgCurve(minutes=minutes_out, mean_pnl=mean_pnl_out, n_trades_alive=n_alive_out)


def _compute_overlay_response(
    derived: pd.DataFrame,
    best: pd.DataFrame,
    fb_map: dict[str, str],
    band_mode: str,
    max_minutes: int,
    min_n_trades_in_avg: int,
) -> FridayBandMtmOverlayResponse:
    """Pure computation: build the overlay response from a derived-exits frame.

    `best` is the per-band winning-combo DataFrame (same selection logic as
    get_friday_band_best_combo_markers). For each band, only the trades that
    belong to that band's winning (entry_hour_ist, expiry_bucket, delta_target)
    combo are used — matching the markers endpoint's trade universe exactly.

    Separated from the endpoint so the result can be cached keyed on param tuple.
    """
    if derived is None or derived.empty or best is None or best.empty:
        return FridayBandMtmOverlayResponse(band_mode=band_mode, bands=[])

    # Determine ordered band list (same order as _band_sort_key, mirrors markers endpoint).
    all_bands = sorted(set(fb_map.values()), key=_band_sort_key)

    # For each band, identify the 4 picked trade rows.
    # Collect all (friday_date, trade_id) pairs we need paths for.
    all_friday_dates: set[str] = set()
    all_trade_ids: set[int] = set()

    band_trade_map: dict[str, pd.DataFrame] = {}
    band_pick_rows: dict[str, dict] = {}
    band_combo_meta: dict[str, dict] = {}

    for _, combo_row in best.iterrows():
        band = combo_row["friday_band"]
        # Restrict derived to this band's winning combo — same filter as markers endpoint.
        sub = derived[
            (derived["friday_band"] == combo_row["friday_band"]) &
            (derived["entry_hour_ist"] == combo_row["entry_hour_ist"]) &
            (derived["expiry_bucket"] == combo_row["expiry_bucket"]) &
            (derived["delta_target"] == combo_row["delta_target"])
        ]
        if sub.empty:
            continue

        band_trade_map[band] = sub
        band_combo_meta[band] = {
            "entry_hour_ist": int(combo_row["entry_hour_ist"]),
            "expiry_bucket": str(combo_row["expiry_bucket"]),
            "delta_target": float(combo_row["delta_target"]),
            "n_trades": int(combo_row["n_trades"]),
        }
        picks = {
            "best":          _pick_trade_row(sub, "net_pnl_estimate_usd", False),
            "worst":         _pick_trade_row(sub, "net_pnl_estimate_usd", True),
            "best_max_mtm":  _pick_trade_row(sub, "max_mtm_usd",          False),
            "worst_min_mtm": _pick_trade_row(sub, "min_mtm_usd",          True),
        }
        band_pick_rows[band] = picks

        # Collect only the combo-restricted friday_dates for partition pruning.
        all_friday_dates.update(sub["friday_date_ist"].astype(str).tolist())

        # Collect picked trade_ids.
        for pick_row in picks.values():
            if pick_row is not None:
                all_trade_ids.add(int(pick_row["trade_id"]))

    if not all_trade_ids:
        return FridayBandMtmOverlayResponse(band_mode=band_mode, bands=[])

    # ONE DuckDB query for all trade paths (partition-pruned by friday_date).
    path_df = _run_overlay_query(
        friday_dates=tuple(sorted(all_friday_dates)),
        trade_ids=tuple(sorted(all_trade_ids)),
        max_minutes=max_minutes,
    )

    # Build per-band overlay entries (in sorted band order).
    bands_out: list[_BandOverlay] = []
    for band in all_bands:
        sub = band_trade_map.get(band)
        if sub is None or sub.empty:
            continue

        meta = band_combo_meta[band]
        n_trades = len(sub)
        picks_rows = band_pick_rows[band]

        best_path = _build_pick_path(
            picks_rows["best"], path_df, max_minutes,
            net_pnl_col="net_pnl_estimate_usd", scalar_col=None,
        )
        worst_path = _build_pick_path(
            picks_rows["worst"], path_df, max_minutes,
            net_pnl_col="net_pnl_estimate_usd", scalar_col=None,
        )
        best_max_path = _build_pick_path(
            picks_rows["best_max_mtm"], path_df, max_minutes,
            net_pnl_col="net_pnl_estimate_usd", scalar_col="max_mtm_usd",
        )
        worst_min_path = _build_pick_path(
            picks_rows["worst_min_mtm"], path_df, max_minutes,
            net_pnl_col="net_pnl_estimate_usd", scalar_col="min_mtm_usd",
        )

        avg_curve = _build_avg_curve(sub, path_df, max_minutes, min_n_trades_in_avg)

        bands_out.append(_BandOverlay(
            friday_band=band,
            entry_hour_ist=meta["entry_hour_ist"],
            expiry_bucket=meta["expiry_bucket"],
            delta_target=meta["delta_target"],
            n_trades=n_trades,
            picks=_BandPicks(
                best=best_path,
                worst=worst_path,
                best_max_mtm=best_max_path,
                worst_min_mtm=worst_min_path,
            ),
            avg_curve=avg_curve,
        ))

    return FridayBandMtmOverlayResponse(band_mode=band_mode, bands=bands_out)


@router.get("/friday_band_mtm_overlay", response_model=FridayBandMtmOverlayResponse)
async def get_friday_band_mtm_overlay(
    band_mode: str = Query("A1"),
    d1_tiebreakers: Optional[str] = Query(None),
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
    expiry_buckets: Optional[str] = Query(
        _DEFAULT_EXPIRY_BUCKETS,
        description="CSV of allowed expiry buckets. Default = 4 popular expiries."),
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    friday_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
    iv_skew_bucket: Optional[str] = None,
    delta_skew_bucket: Optional[str] = None,
    premium_skew_bucket: Optional[str] = None,
    leg_winner: Optional[str] = None,
    loss_cause: Optional[str] = None,
    max_minutes: int = Query(720, ge=1, le=1440),
    min_n_trades_in_avg: int = Query(3, ge=1, le=50),
):
    """Per-band MTM overlay: average curve + 4 extreme trade paths per IV-band.

    Returns columnar path data ({minutes: [...], pnl: [...]}) for:
      - avg curve (carry-forward mean across all trades in band)
      - best trade (max net_pnl_estimate_usd)
      - worst trade (min net_pnl_estimate_usd)
      - best_max_mtm (max max_mtm_usd)
      - worst_min_mtm (min min_mtm_usd)

    Bands with n_trades == 0 are omitted from the response.
    avg_curve tail minutes with n_trades_alive < min_n_trades_in_avg are dropped.
    """
    cache_key = (
        band_mode, d1_tiebreakers or "",
        exit_rule or "", metric,
        expiry_buckets or "", delta_target or "",
        is_straddle or "", expiry_date or "",
        expiry_bucket or "", friday_band or "",
        entry_hour_ist or "", dte_bucket or "",
        spot_bucket or "", ivp_bucket or "",
        ctx_pattern or "", ctx_gex_regime or "",
        friday_date_ist or "", iv_skew_bucket or "",
        delta_skew_bucket or "", premium_skew_bucket or "",
        leg_winner or "", loss_cause or "",
        max_minutes, min_n_trades_in_avg,
    )

    cached = _OVERLAY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # Resolve filters and derived frame (synchronous — these are fast pandas ops,
    # no DuckDB scan; _derive_exits uses the in-memory exit cache).
    fb_map = _resolve_friday_band_map(band_mode, d1_tiebreakers)
    filters = m7r._query_filters(
        delta_target, is_straddle, expiry_date,
        None, entry_hour_ist, dte_bucket,
        spot_bucket, ivp_bucket, ctx_pattern, ctx_gex_regime,
        friday_date_ist, expiry_bucket,
        iv_skew_bucket=iv_skew_bucket,
        delta_skew_bucket=delta_skew_bucket,
        premium_skew_bucket=premium_skew_bucket,
        leg_winner=leg_winner,
        loss_cause=loss_cause,
    )
    rule = m7r._parse_exit_rule(exit_rule)
    derived = m7r._derive_exits(filters, rule)
    derived = _annotate_friday_band(derived, fb_map)
    derived = _apply_friday_band_filter(derived, friday_band)
    derived = _apply_expiry_buckets_filter(derived, expiry_buckets)

    if derived is None or derived.empty:
        result = FridayBandMtmOverlayResponse(band_mode=band_mode, bands=[])
        with _OVERLAY_CACHE_LOCK:
            _OVERLAY_CACHE[cache_key] = result
        return result

    # Select the best (entry_hour_ist, expiry_bucket, delta_target) combo per band,
    # exactly as get_friday_band_best_combo_markers does (lines 433–450 above).
    # The overlay MUST operate on the same per-band trade universe as the markers endpoint.
    dims = ["friday_band", "entry_hour_ist", "expiry_bucket", "delta_target"]
    grp = derived.groupby(dims, dropna=False)
    score = m7r._metric_score(grp, metric)
    n_combo = grp.size()
    df_combos = pd.DataFrame({"score": score, "n_trades": n_combo}).reset_index()

    df_valid = df_combos.dropna(subset=["score"])
    strict = df_valid[df_valid["n_trades"] >= 3]
    strict_idx = (strict.groupby("friday_band", dropna=False)["score"].idxmax()
                  if not strict.empty else pd.Index([]))
    strict_best = strict.loc[strict_idx] if len(strict_idx) else strict.iloc[0:0]
    covered = set(strict_best["friday_band"].dropna().tolist())
    fallback = df_valid[~df_valid["friday_band"].isin(covered)]
    if not fallback.empty:
        fb_idx = fallback.groupby("friday_band", dropna=False)["score"].idxmax()
        fallback_best = fallback.loc[fb_idx]
        best = pd.concat([strict_best, fallback_best], ignore_index=True)
    else:
        best = strict_best.reset_index(drop=True)

    if best.empty:
        result = FridayBandMtmOverlayResponse(band_mode=band_mode, bands=[])
        with _OVERLAY_CACHE_LOCK:
            _OVERLAY_CACHE[cache_key] = result
        return result

    # Run the heavy DuckDB scan + compute in a thread so we don't block the
    # event loop. The _run_overlay_query function acquires _EXIT_COMPUTE_LOCK
    # (threading.Lock) inside the thread, serialising concurrent DuckDB path scans.
    result = await asyncio.to_thread(
        _compute_overlay_response,
        derived, best, fb_map, band_mode, max_minutes, min_n_trades_in_avg,
    )

    with _OVERLAY_CACHE_LOCK:
        _OVERLAY_CACHE[cache_key] = result

    return result

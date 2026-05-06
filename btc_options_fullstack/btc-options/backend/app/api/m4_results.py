"""M6 dashboard API — exposes M4 batch backtest outputs.

Endpoints:
  GET /api/v1/m4/summary                — counts/win-rate/total P&L
  GET /api/v1/m4/trades                 — paginated trade rows (filterable)
  GET /api/v1/m4/aggregate              — group-by reductions (heatmaps)
  GET /api/v1/m4/scatter                — row-level scatter data
  GET /api/v1/m4/path?trade_id=...      — hourly path snapshots for one trade
  GET /api/v1/m4/quality_calibration    — empirical winrate per quality decile
  GET /api/v1/m4/expiry_grid            — full (contract_type × IV band × Δ)
                                          aggregation with MTM/cost columns

Backed by `m4_trades.parquet` (5,274 rows) + `m4_paths.parquet` (49k rows),
loaded once into module-level cache. Filters/aggs all run in-process via
pandas — fast enough for the dataset size, no DuckDB needed.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
log = logging.getLogger(__name__)

TRADES_PATH = "/home/abhis/btc-data/derived/m4_trades.parquet"
TRADES_ENRICHED_PATH = "/home/abhis/btc-data/derived/m4_trades_enriched.parquet"
PATHS_PATH = "/home/abhis/btc-data/derived/m4_paths.parquet"

# Module-level cache. Loaded lazily on first request, never invalidated —
# parquets only refresh when M4 batcher reruns (manual). A backend rebuild
# clears the cache.
_TRADES_DF: Optional[pd.DataFrame] = None
_PATHS_DF: Optional[pd.DataFrame] = None


def _load_trades() -> pd.DataFrame:
    """Prefer m4_trades_enriched.parquet (with IV-premium decomp + per-leg
    Greeks) when present; fall back to plain m4_trades.parquet."""
    global _TRADES_DF
    if _TRADES_DF is None:
        if os.path.exists(TRADES_ENRICHED_PATH):
            _TRADES_DF = pd.read_parquet(TRADES_ENRICHED_PATH)
        elif os.path.exists(TRADES_PATH):
            _TRADES_DF = pd.read_parquet(TRADES_PATH)
        else:
            raise HTTPException(status_code=503,
                                detail=f"m4_trades.parquet missing at {TRADES_PATH}")
    return _TRADES_DF


def _load_paths() -> pd.DataFrame:
    global _PATHS_DF
    if _PATHS_DF is None:
        if not os.path.exists(PATHS_PATH):
            raise HTTPException(status_code=503,
                                detail=f"m4_paths.parquet missing at {PATHS_PATH}")
        _PATHS_DF = pd.read_parquet(PATHS_PATH)
    return _PATHS_DF


# ── Filtering ────────────────────────────────────────────────────────────────

_FILTER_COLS = {
    "delta_target": str,
    "dte_bucket": str,
    "spot_bucket": str,
    "ivp_bucket": str,
    "ctx_pattern": str,
    "outcome": str,
    "exit_reason": str,
    "breaching_leg": str,
    "flt_ivp_gt50": bool,
    "flt_iv_rv_spread_pos": bool,
    "flt_adx_lt30": bool,
    "flt_dte_5_14": bool,
}


def _apply_filters(df: pd.DataFrame, filters: dict[str, str]) -> pd.DataFrame:
    """Filters: {col: "value1,value2"}. Bool cols accept "true"/"false"."""
    if not filters:
        return df
    out = df
    for col, raw in filters.items():
        if col not in _FILTER_COLS or raw is None or raw == "":
            continue
        coltype = _FILTER_COLS[col]
        vals = [v.strip() for v in str(raw).split(",") if v.strip()]
        if coltype is bool:
            bvals = [v.lower() in ("true", "1", "yes") for v in vals]
            out = out[out[col].isin(bvals)]
        else:
            out = out[out[col].astype(str).isin(vals)]
    return out


def _filter_kwargs_from_query(
    delta_target: Optional[str], dte_bucket: Optional[str],
    spot_bucket: Optional[str], ivp_bucket: Optional[str],
    ctx_pattern: Optional[str], outcome: Optional[str],
    exit_reason: Optional[str], breaching_leg: Optional[str],
    flt_ivp_gt50: Optional[str], flt_iv_rv_spread_pos: Optional[str],
    flt_adx_lt30: Optional[str], flt_dte_5_14: Optional[str],
) -> dict:
    return {
        "delta_target": delta_target, "dte_bucket": dte_bucket,
        "spot_bucket": spot_bucket, "ivp_bucket": ivp_bucket,
        "ctx_pattern": ctx_pattern, "outcome": outcome,
        "exit_reason": exit_reason, "breaching_leg": breaching_leg,
        "flt_ivp_gt50": flt_ivp_gt50,
        "flt_iv_rv_spread_pos": flt_iv_rv_spread_pos,
        "flt_adx_lt30": flt_adx_lt30, "flt_dte_5_14": flt_dte_5_14,
    }


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/summary")
def summary(
    delta_target: Optional[str] = None, dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None, ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None, outcome: Optional[str] = None,
    exit_reason: Optional[str] = None, breaching_leg: Optional[str] = None,
    flt_ivp_gt50: Optional[str] = None, flt_iv_rv_spread_pos: Optional[str] = None,
    flt_adx_lt30: Optional[str] = None, flt_dte_5_14: Optional[str] = None,
):
    """Header-strip stats for the M6 dashboard."""
    df = _apply_filters(_load_trades(), _filter_kwargs_from_query(
        delta_target, dte_bucket, spot_bucket, ivp_bucket, ctx_pattern,
        outcome, exit_reason, breaching_leg,
        flt_ivp_gt50, flt_iv_rv_spread_pos, flt_adx_lt30, flt_dte_5_14,
    ))
    n = int(len(df))
    if n == 0:
        return {"n_trades": 0, "n_winners": 0, "win_rate": 0.0,
                "total_net_pnl_usd": 0.0, "avg_net_pnl_usd": 0.0,
                "sl_hit_rate": 0.0, "avg_credit_pct": 0.0,
                "avg_max_mtm_usd": 0.0, "avg_min_mtm_usd": 0.0}
    n_win = int((df["outcome"] == "win").sum())
    return {
        "n_trades": n, "n_winners": n_win,
        "win_rate": round(n_win / n, 4),
        "total_net_pnl_usd": float(df["net_pnl_usd"].sum()),
        "avg_net_pnl_usd":   float(df["net_pnl_usd"].mean()),
        "sl_hit_rate":       float(df["exit_reason"].isin(["SL", "LegSL"]).mean()),
        "avg_credit_pct":    float(df["credit_pct"].mean()),
        "avg_max_mtm_usd":   float(df["max_mtm_usd"].mean()),
        "avg_min_mtm_usd":   float(df["min_mtm_usd"].mean()),
    }


@router.get("/trades")
def trades(
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("entry_ts"),
    sort_desc: bool = Query(True),
    delta_target: Optional[str] = None, dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None, ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None, outcome: Optional[str] = None,
    exit_reason: Optional[str] = None, breaching_leg: Optional[str] = None,
    flt_ivp_gt50: Optional[str] = None, flt_iv_rv_spread_pos: Optional[str] = None,
    flt_adx_lt30: Optional[str] = None, flt_dte_5_14: Optional[str] = None,
):
    """Paginated trade rows. sort_by must be a column in the parquet."""
    df = _apply_filters(_load_trades(), _filter_kwargs_from_query(
        delta_target, dte_bucket, spot_bucket, ivp_bucket, ctx_pattern,
        outcome, exit_reason, breaching_leg,
        flt_ivp_gt50, flt_iv_rv_spread_pos, flt_adx_lt30, flt_dte_5_14,
    ))
    if sort_by not in df.columns:
        raise HTTPException(status_code=400, detail=f"unknown sort_by: {sort_by}")
    df = df.sort_values(sort_by, ascending=not sort_desc, kind="stable", na_position="last")
    total = int(len(df))
    page = df.iloc[offset:offset + limit]
    rows = page.replace({np.nan: None}).to_dict(orient="records")
    # Cast trade_id to str to avoid JS losing precision on uint64
    for r in rows:
        if "trade_id" in r and r["trade_id"] is not None:
            r["trade_id"] = str(r["trade_id"])
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


_AGGREGATE_METRICS = {
    "count":         lambda g: int(len(g)),
    "win_rate":      lambda g: float((g["outcome"] == "win").mean()) if len(g) else 0.0,
    "n_winners":     lambda g: int((g["outcome"] == "win").sum()),
    "avg_pnl_usd":   lambda g: float(g["net_pnl_usd"].mean()) if len(g) else 0.0,
    "sum_pnl_usd":   lambda g: float(g["net_pnl_usd"].sum()),
    "avg_credit_pct":lambda g: float(g["credit_pct"].mean()) if len(g) else 0.0,
    "avg_max_mtm":   lambda g: float(g["max_mtm_usd"].mean()) if len(g) else 0.0,
    "avg_min_mtm":   lambda g: float(g["min_mtm_usd"].mean()) if len(g) else 0.0,
    "sl_rate":       lambda g: float(g["exit_reason"].isin(["SL", "LegSL"]).mean()) if len(g) else 0.0,
}

_AGG_DIMENSIONS = {
    "delta_target", "dte_bucket", "spot_bucket", "ivp_bucket",
    "ctx_pattern", "exit_reason", "breaching_leg",
    "outcome", "entry_date_ist",
}


@router.get("/aggregate")
def aggregate(
    dimension: list[str] = Query(..., description="One or more group-by columns"),
    metric: str = Query("win_rate"),
    delta_target: Optional[str] = None, dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None, ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None, outcome: Optional[str] = None,
    exit_reason: Optional[str] = None, breaching_leg: Optional[str] = None,
    flt_ivp_gt50: Optional[str] = None, flt_iv_rv_spread_pos: Optional[str] = None,
    flt_adx_lt30: Optional[str] = None, flt_dte_5_14: Optional[str] = None,
):
    """Group-by aggregation. Pass `dimension` repeated to get multi-dim group-by
    (heatmap input). Returns flat list of {dim1, dim2, ..., metric_value, n}."""
    bad = [d for d in dimension if d not in _AGG_DIMENSIONS]
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown dimension(s): {bad}")
    if metric not in _AGGREGATE_METRICS:
        raise HTTPException(status_code=400, detail=f"unknown metric: {metric}")
    df = _apply_filters(_load_trades(), _filter_kwargs_from_query(
        delta_target, dte_bucket, spot_bucket, ivp_bucket, ctx_pattern,
        outcome, exit_reason, breaching_leg,
        flt_ivp_gt50, flt_iv_rv_spread_pos, flt_adx_lt30, flt_dte_5_14,
    ))
    if df.empty:
        return {"dimension": dimension, "metric": metric, "rows": []}

    fn = _AGGREGATE_METRICS[metric]
    grouped = df.groupby(dimension, dropna=False)
    rows = []
    for key, g in grouped:
        if not isinstance(key, tuple):
            key = (key,)
        d = {dim: (None if (isinstance(k, float) and np.isnan(k)) else k)
             for dim, k in zip(dimension, key)}
        d["n"] = int(len(g))
        d[metric] = fn(g)
        rows.append(d)
    return {"dimension": dimension, "metric": metric, "rows": rows}


_SCATTER_NUM_COLS = {
    "credit_pct", "credit_pct_normalized", "net_pnl_usd",
    "net_pnl_pct_credit", "net_pnl_pct_margin",
    "max_mtm_usd", "min_mtm_usd", "margin_used_usd",
    "ctx_atm_iv_7d", "ctx_ivp_atm_7d_90d", "ctx_rv_7d",
    "ctx_iv_rv_spread_7d", "ctx_vrp_pct_7d",
    "ctx_adx_14_4h", "dte_days",
}


@router.get("/scatter")
def scatter(
    x: str = Query("credit_pct"),
    y: str = Query("net_pnl_usd"),
    color_by: Optional[str] = Query("outcome"),
    limit: int = Query(2000, ge=1, le=10000),
    delta_target: Optional[str] = None, dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None, ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None, outcome: Optional[str] = None,
    exit_reason: Optional[str] = None, breaching_leg: Optional[str] = None,
    flt_ivp_gt50: Optional[str] = None, flt_iv_rv_spread_pos: Optional[str] = None,
    flt_adx_lt30: Optional[str] = None, flt_dte_5_14: Optional[str] = None,
):
    """Row-level scatter data. Returns up to `limit` points."""
    if x not in _SCATTER_NUM_COLS or y not in _SCATTER_NUM_COLS:
        raise HTTPException(
            status_code=400,
            detail=f"x/y must be one of {sorted(_SCATTER_NUM_COLS)}",
        )
    df = _apply_filters(_load_trades(), _filter_kwargs_from_query(
        delta_target, dte_bucket, spot_bucket, ivp_bucket, ctx_pattern,
        outcome, exit_reason, breaching_leg,
        flt_ivp_gt50, flt_iv_rv_spread_pos, flt_adx_lt30, flt_dte_5_14,
    ))
    cols = ["trade_id", x, y]
    if color_by and color_by in df.columns:
        cols.append(color_by)
    sub = df[cols].dropna(subset=[x, y]).head(limit).copy()
    sub["trade_id"] = sub["trade_id"].astype(str)
    return {"x": x, "y": y, "color_by": color_by,
            "n": int(len(sub)),
            "points": sub.replace({np.nan: None}).to_dict(orient="records")}


@router.get("/path")
def path(trade_id: str = Query(..., description="trade_id from /trades response")):
    """Hourly path snapshots for one trade."""
    paths_df = _load_paths()
    try:
        tid = int(trade_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="trade_id must be int-castable")
    sub = paths_df[paths_df["trade_id"] == tid].sort_values("ts")
    if sub.empty:
        raise HTTPException(status_code=404, detail="no path data for trade_id")
    sub = sub.copy()
    sub["trade_id"] = sub["trade_id"].astype(str)
    return {"trade_id": trade_id, "n": int(len(sub)),
            "snapshots": sub.replace({np.nan: None}).to_dict(orient="records")}


@router.get("/quality_calibration")
def quality_calibration(n_buckets: int = Query(10, ge=2, le=20)):
    """Empirical win-rate per credit_pct decile (proxy for quality calibration
    when the parquet doesn't carry quality_score directly). Frontend can
    compare expected vs realised win rate per bucket."""
    df = _load_trades()
    if df.empty:
        return {"buckets": []}
    cp = df["credit_pct"].copy()
    try:
        df["_bucket"] = pd.qcut(cp, n_buckets, labels=False, duplicates="drop")
    except ValueError:
        return {"buckets": []}
    out = []
    for b, g in df.groupby("_bucket"):
        out.append({
            "bucket": int(b),
            "n": int(len(g)),
            "credit_pct_min": float(g["credit_pct"].min()),
            "credit_pct_max": float(g["credit_pct"].max()),
            "win_rate":      float((g["outcome"] == "win").mean()),
            "avg_net_pnl":   float(g["net_pnl_usd"].mean()),
        })
    out.sort(key=lambda r: r["bucket"])
    return {"buckets": out}


# ── Expiry-grid aggregation (contract type × IV band × Δ) ────────────────────
#
# Classifies each trade's expiry by Delta's contract type at entry time:
#   current      — first daily ahead (DTE < 1.5)
#   next         — second daily (DTE 1.5–2.5)
#   next_to_next — third daily (DTE 2.5–3.5)
#   weekly       — next Friday weekly (DTE 3.5–9.5)
#   biweekly     — Friday weekly two weeks out (DTE 9.5–16.5)
#   three_week   — DTE 16.5–35 NOT last-Friday-of-month
#   monthly      — DTE 17–35 AND last-Friday-of-month
#   bimonthly    — DTE 35–65
#   quarterly    — DTE 65+
# IV bucket = THIS-EXPIRY ATM IV at entry, computed as
# avg(call_entry_iv, put_entry_iv) of the Δ=0.50 row of that (entry × expiry).

_CONTRACT_ORDER = ['current', 'next', 'next_to_next', 'weekly', 'biweekly',
                   'three_week', 'monthly', 'bimonthly', 'quarterly']
_IV_BANDS  = [0, 30, 40, 50, 60, 70, 80, 90, 100, 999]
_IV_LABELS = ['<30', '30-40', '40-50', '50-60', '60-70', '70-80', '80-90', '90-100', '100+']


def _classify_contract_type(entry_ts: int, expiry_date: str) -> str:
    """Mirror the classifier used in the offline analysis script. Pure
    function of (entry_ts, expiry_date) — no chain lookup."""
    try:
        entry_dt = datetime.fromtimestamp(int(entry_ts), tz=timezone.utc)
        exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d").replace(
            hour=12, tzinfo=timezone.utc)
    except Exception:
        return "unknown"
    dte = (exp_dt - entry_dt).total_seconds() / 86400.0
    if dte < 1.5:    return 'current'
    if dte < 2.5:    return 'next'
    if dte < 3.5:    return 'next_to_next'
    if dte < 9.5:    return 'weekly'
    if dte < 16.5:   return 'biweekly'
    if dte < 35:
        # Last-Friday-of-month detector
        if exp_dt.weekday() == 4 and (exp_dt + timedelta(days=7)).month != exp_dt.month:
            return 'monthly'
        return 'three_week'
    if dte < 65:     return 'bimonthly'
    return 'quarterly'


def _enrich_for_expiry_grid(df: pd.DataFrame) -> pd.DataFrame:
    """Add contract_type + this_expiry_atm_iv_pct + iv_band columns."""
    out = df.copy()
    out['contract_type'] = [
        _classify_contract_type(ts, ed)
        for ts, ed in zip(out['entry_ts'].astype(int), out['expiry_date'])
    ]
    # this-expiry ATM IV proxy = avg of (call_iv + put_iv)/2 from the Δ=0.50
    # trade of that (entry, expiry) cell, expressed in % (so 50 = 50%).
    atm = out[out['target_delta'] == 0.50][
        ['entry_ts', 'expiry_date', 'call_entry_iv', 'put_entry_iv']
    ].copy()
    atm['this_expiry_atm_iv_pct'] = (atm['call_entry_iv'] + atm['put_entry_iv']) * 50
    atm = atm[['entry_ts', 'expiry_date', 'this_expiry_atm_iv_pct']]
    out = out.merge(atm, on=['entry_ts', 'expiry_date'], how='left')
    out['iv_band'] = pd.cut(
        out['this_expiry_atm_iv_pct'],
        bins=_IV_BANDS, labels=_IV_LABELS, right=False,
    ).astype(str)
    return out


@router.get("/expiry_grid")
def expiry_grid(
    contract_types: Optional[str] = Query(
        None, description="Comma-separated subset, e.g. current,next,weekly"),
    expiries: Optional[str] = Query(
        None, description="Comma-separated YYYY-MM-DD expiry dates to restrict to"),
    min_n: int = Query(0, ge=0, description="Drop cells with fewer than N trades"),
):
    """Full (contract_type × IV band × Δ) aggregation for the M4 dataset.

    Each cell carries:
      - n trades, win rate, sl rate
      - max_mtm: avg / best (most positive)
      - min_mtm: avg / worst (most negative)
      - gross_pnl: avg / total
      - net_pnl:   avg / total
      - cost decomposition: round-trip slip + brokerage avg + 50/50 entry/exit estimate
      - margin used avg, credit % avg
    """
    df = _enrich_for_expiry_grid(_load_trades())

    keep = set(contract_types.split(",")) if contract_types else set(_CONTRACT_ORDER)
    df = df[df['contract_type'].isin(keep)]
    if expiries:
        wanted = {e.strip() for e in expiries.split(",") if e.strip()}
        df = df[df['expiry_date'].astype(str).isin(wanted)]
    if df.empty:
        return {"rows": [], "contract_order": _CONTRACT_ORDER, "iv_bands": _IV_LABELS}

    rows = []
    for (ct, iv, td), g in df.groupby(
        ['contract_type', 'iv_band', 'target_delta'], observed=True,
    ):
        n = int(len(g))
        if n < min_n:
            continue
        slip_total = float(g['slippage_usd'].mean())
        brk_total  = float(g['brokerage_usd'].mean())
        sl_mask    = g['exit_reason'].isin(['SL', 'LegSL'])
        sl = g[sl_mask]
        # Winners vs losers split — separate the two distributions
        wins   = g[g['outcome'] == 'win']
        losses = g[g['outcome'] == 'loss']
        rows.append({
            "contract_type": str(ct),
            "iv_band":       str(iv),
            "delta_target":  round(float(td), 2),
            "n":             n,
            "win_rate":      float((g['outcome'] == 'win').mean()),
            "sl_rate":       float(sl_mask.mean()),
            # SL-only stats: when SL triggered, what was the MTM at trigger
            # (= min_mtm) and the realized net P&L. Counts/avgs/totals.
            "n_sl":          int(len(sl)),
            "sl_mtm_avg":    float(sl['min_mtm_usd'].mean()) if len(sl) else 0.0,
            "sl_mtm_total":  float(sl['min_mtm_usd'].sum()),
            "sl_net_avg":    float(sl['net_pnl_usd'].mean()) if len(sl) else 0.0,
            "sl_net_total":  float(sl['net_pnl_usd'].sum()),
            # ── Winners-only stats ────────────────────────────────────────
            # worst_mtm = deepest dip among winners (recovered to a win)
            # best_win  = biggest single winning trade
            # avg_win   = mean win
            # lowest_win = smallest winning trade (barely-positive)
            "n_wins":             int(len(wins)),
            "win_min_mtm_worst":  float(wins['min_mtm_usd'].min())  if len(wins) else 0.0,
            "win_min_mtm_avg":    float(wins['min_mtm_usd'].mean()) if len(wins) else 0.0,
            "win_net_best":       float(wins['net_pnl_usd'].max())  if len(wins) else 0.0,
            "win_net_avg":        float(wins['net_pnl_usd'].mean()) if len(wins) else 0.0,
            "win_net_lowest":     float(wins['net_pnl_usd'].min())  if len(wins) else 0.0,
            # ── Losers-only stats ─────────────────────────────────────────
            # best_mtm  = highest peak among losers (came close to winning, turned down)
            # worst_loss = biggest single losing trade (most negative)
            # avg_loss   = mean loss
            # lowest_loss = smallest-magnitude loss (closest to break-even)
            "n_losses":            int(len(losses)),
            "loss_max_mtm_best":   float(losses['max_mtm_usd'].max())  if len(losses) else 0.0,
            "loss_max_mtm_avg":    float(losses['max_mtm_usd'].mean()) if len(losses) else 0.0,
            "loss_net_worst":      float(losses['net_pnl_usd'].min())  if len(losses) else 0.0,
            "loss_net_avg":        float(losses['net_pnl_usd'].mean()) if len(losses) else 0.0,
            "loss_net_lowest":     float(losses['net_pnl_usd'].max())  if len(losses) else 0.0,
            # Also expose loser MAE (deepest dip during loss, useful with best_mtm)
            "loss_min_mtm_worst":  float(losses['min_mtm_usd'].min())  if len(losses) else 0.0,
            "loss_min_mtm_avg":    float(losses['min_mtm_usd'].mean()) if len(losses) else 0.0,
            # MTM ($)
            "max_mtm_avg":   float(g['max_mtm_usd'].mean()),
            "max_mtm_best":  float(g['max_mtm_usd'].max()),
            "min_mtm_avg":   float(g['min_mtm_usd'].mean()),
            "min_mtm_worst": float(g['min_mtm_usd'].min()),
            # Gross P&L ($)
            "gross_pnl_avg": float(g['gross_pnl_usd'].mean()),
            "gross_pnl_sum": float(g['gross_pnl_usd'].sum()),
            # Net P&L ($)
            "net_pnl_avg":   float(g['net_pnl_usd'].mean()),
            "net_pnl_sum":   float(g['net_pnl_usd'].sum()),
            # Costs (round trip avg + 50/50 estimate per side, since the M4
            # parquet stores costs as round-trip totals)
            "slippage_avg_total":     slip_total,
            "slippage_avg_per_side":  slip_total / 2.0,
            "brokerage_avg_total":    brk_total,
            "brokerage_avg_per_side": brk_total / 2.0,
            "cost_avg_total":         slip_total + brk_total,
            # Other useful aggregates
            "credit_pct_avg":   float(g['credit_pct'].mean()),
            "credit_usd_avg":   float(g['credit_usd'].mean()),
            "margin_avg":       float(g['margin_used_usd'].mean()),
            "this_expiry_atm_iv_avg_pct":
                float(g['this_expiry_atm_iv_pct'].mean()),
            "dte_avg":          float(g['dte_days'].mean()),
        })

    rows.sort(key=lambda r: (
        _CONTRACT_ORDER.index(r["contract_type"]) if r["contract_type"] in _CONTRACT_ORDER else 99,
        _IV_LABELS.index(r["iv_band"]) if r["iv_band"] in _IV_LABELS else 99,
        r["delta_target"],
    ))
    return {
        "n_rows": len(rows),
        "contract_order": _CONTRACT_ORDER,
        "iv_bands": _IV_LABELS,
        "rows": rows,
    }


@router.get("/expiry_list")
def expiry_list():
    """All unique expiry dates in the M4 dataset, classified by contract_type
    using the entry-friday closest to that expiry. Sorted desc by date.

    Used to populate the expiry-search dropdown on the M6 expiry-grid table.
    """
    df = _enrich_for_expiry_grid(_load_trades())
    # For each (expiry_date), pick the most-recent entry_ts to assign a
    # canonical contract_type label (older entries on same expiry could be
    # "weekly"; closer ones could roll to "current").
    out = []
    for exp, sub in df.groupby('expiry_date'):
        latest = sub.sort_values('entry_ts').iloc[-1]
        out.append({
            "expiry": str(exp),
            "contract_type": str(latest['contract_type']),
            "n_trades": int(len(sub)),
            "n_entries": int(sub['entry_ts'].nunique()),
            "atm_iv_avg_pct": float(sub['this_expiry_atm_iv_pct'].mean()) if sub['this_expiry_atm_iv_pct'].notna().any() else None,
        })
    out.sort(key=lambda r: r["expiry"], reverse=True)
    return {"expiries": out}


@router.get("/expiry_winners")
def expiry_winners(
    min_n: int = Query(2, ge=1, description="Min trades per cell to qualify"),
):
    """For each (contract_type × IV band), the best Δ by avg net P&L.

    Drives the "Best (IV × Δ) winner per contract type" summary panel.
    """
    df = _enrich_for_expiry_grid(_load_trades())
    rows = []
    for (ct, iv), g in df.groupby(['contract_type', 'iv_band'], observed=True):
        cells = []
        for td, gd in g.groupby('target_delta', observed=True):
            n = int(len(gd))
            if n < min_n: continue
            cells.append({
                "delta_target": round(float(td), 2),
                "n":            n,
                "avg_net":      float(gd['net_pnl_usd'].mean()),
                "win_rate":     float((gd['outcome'] == 'win').mean()),
                "total_net":    float(gd['net_pnl_usd'].sum()),
            })
        if not cells: continue
        cells.sort(key=lambda c: c["avg_net"], reverse=True)
        best = cells[0]
        worst = cells[-1]
        rows.append({
            "contract_type": str(ct),
            "iv_band":       str(iv),
            "n_cells":       len(cells),
            "best":          best,
            "worst":         worst,
        })
    rows.sort(key=lambda r: (
        _CONTRACT_ORDER.index(r["contract_type"]) if r["contract_type"] in _CONTRACT_ORDER else 99,
        _IV_LABELS.index(r["iv_band"]) if r["iv_band"] in _IV_LABELS else 99,
    ))
    return {"rows": rows}


@router.get("/contract_type_summary")
def contract_type_summary():
    """One-row-per-contract-type summary (top of the new dashboard page)."""
    df = _enrich_for_expiry_grid(_load_trades())
    rows = []
    for ct in _CONTRACT_ORDER:
        sub = df[df['contract_type'] == ct]
        if sub.empty: continue
        sl_mask = sub['exit_reason'].isin(['SL', 'LegSL'])
        sl = sub[sl_mask]
        wins   = sub[sub['outcome'] == 'win']
        losses = sub[sub['outcome'] != 'win']
        rows.append({
            "contract_type": ct,
            "n_trades":      int(len(sub)),
            "n_fridays":     int(sub['entry_date_ist'].nunique()),
            "n_expiries":    int(sub['expiry_date'].nunique()),
            "avg_dte":       float(sub['dte_days'].mean()),
            "win_rate":      float((sub['outcome'] == 'win').mean()),
            "n_wins":        int(len(wins)),
            "n_losses":      int(len(losses)),
            "sl_rate":       float(sl_mask.mean()),
            "n_sl":          int(len(sl)),
            "sl_mtm_avg":    float(sl['min_mtm_usd'].mean()) if len(sl) else 0.0,
            "sl_mtm_total":  float(sl['min_mtm_usd'].sum()),
            "sl_net_avg":    float(sl['net_pnl_usd'].mean()) if len(sl) else 0.0,
            "sl_net_total":  float(sl['net_pnl_usd'].sum()),
            "avg_net_pnl":   float(sub['net_pnl_usd'].mean()),
            "avg_net_win":   float(wins['net_pnl_usd'].mean()) if len(wins) else 0.0,
            "avg_net_loss":  float(losses['net_pnl_usd'].mean()) if len(losses) else 0.0,
            "total_net_pnl": float(sub['net_pnl_usd'].sum()),
            "best_net_pnl":  float(sub['net_pnl_usd'].max()),
            "worst_net_pnl": float(sub['net_pnl_usd'].min()),
            "avg_gross_pnl": float(sub['gross_pnl_usd'].mean()),
            "avg_max_mtm":   float(sub['max_mtm_usd'].mean()),
            "best_max_mtm":  float(sub['max_mtm_usd'].max()),
            "avg_min_mtm":   float(sub['min_mtm_usd'].mean()),
            "worst_min_mtm": float(sub['min_mtm_usd'].min()),
            "avg_cost":      float(sub['slippage_usd'].mean() + sub['brokerage_usd'].mean()),
        })
    return {"rows": rows}


# ── Attribution endpoints (winners vs losers, per-Friday best, win freq) ─────
#
# These all require m4_trades_enriched.parquet (produced by
# scripts/backfill_m4_enriched.py). The IV-premium decomposition + per-leg
# Greeks fields are only present in the enriched parquet; if the loader
# falls back to plain m4_trades.parquet those columns simply won't appear
# in the response.

# Numeric indicators surfaced in the winners-vs-losers comparison.
# Grouped by category for the frontend's table-section headers.
_ATTR_INDICATORS: list[tuple[str, str, str]] = [
    # (column, display_label, category)
    # IV term structure
    ("ctx_atm_iv_7d",          "ATM IV 7d",            "IV"),
    ("ctx_atm_iv_14d",         "ATM IV 14d",           "IV"),
    ("ctx_atm_iv_30d",         "ATM IV 30d",           "IV"),
    ("ctx_atm_iv_60d",         "ATM IV 60d",           "IV"),
    ("ctx_ivp_atm_7d_90d",     "IVP 7d/90d (rank)",    "IV"),
    ("ctx_ivp_4h",             "IVP 4h",               "IV"),
    # Realized vol + spread
    ("ctx_rv_7d",              "RV 7d",                "RV/VRP"),
    ("ctx_rv_14d",             "RV 14d",               "RV/VRP"),
    ("ctx_rv_30d",             "RV 30d",               "RV/VRP"),
    ("ctx_iv_rv_spread_7d",    "IV-RV spread 7d",      "RV/VRP"),
    ("ctx_iv_rv_spread_30d",   "IV-RV spread 30d",     "RV/VRP"),
    ("ctx_iv_rv_ratio_7d",     "IV/RV ratio 7d",       "RV/VRP"),
    ("ctx_vrp_pct_7d",         "VRP % 7d",             "RV/VRP"),
    ("ctx_rvp_4h",             "RVP 4h",               "RV/VRP"),
    # Skew / smile / term
    ("ctx_risk_reversal_25d",  "RR 25d",               "Skew/Term"),
    ("ctx_butterfly_25d",      "Butterfly 25d",        "Skew/Term"),
    ("ctx_wing_atm_ratio",     "Wing/ATM ratio",       "Skew/Term"),
    ("ctx_term_slope_7_30",    "Term slope 7→30",      "Skew/Term"),
    # Spot regime
    ("ctx_adx_14_4h",          "ADX 14 (4h)",          "Spot regime"),
    ("ctx_atr_pct_4h",         "ATR % (4h)",           "Spot regime"),
    # Order book / GEX
    ("ctx_pcr_oi",             "PCR OI",               "GEX/Flow"),
    ("ctx_total_gex",          "Total GEX",            "GEX/Flow"),
    # Premium structure (the user's "credit% baseline & median IV" set)
    ("credit_pct",             "Credit %",             "Premium"),
    ("credit_pct_normalized",  "Credit % / √DTE",      "Premium"),
    ("fair_credit_at_ivp",     "Fair credit @ IVP",    "Premium"),
    ("structural_credit_pct",  "Structural credit %",  "Premium"),
    ("iv_regime_premium_pct",  "IV regime premium %",  "Premium"),
    ("excess_over_fair_pct",   "Excess over fair %",   "Premium"),
    # Greeks ratios (the user's "IV vs theta" question)
    ("theta_per_vega_call",    "θ/ν call",             "Greeks"),
    ("theta_per_vega_put",     "θ/ν put",              "Greeks"),
    ("theta_per_vega_combined","θ/ν combined",         "Greeks"),
]

_ATTR_DELTAS = [0.05, 0.10, 0.15, 0.25, 0.30, 0.50]


def _classify_contract_for(df: pd.DataFrame) -> pd.DataFrame:
    """Add contract_type to the trades DF if not already present.
    Reuses the same classifier as the expiry grid."""
    if "contract_type" in df.columns:
        return df
    out = df.copy()
    out["contract_type"] = [
        _classify_contract_type(ts, ed)
        for ts, ed in zip(out["entry_ts"].astype(int), out["expiry_date"])
    ]
    return out


@router.get("/winners_vs_losers")
def winners_vs_losers(
    delta: float = Query(0.30, description="Target delta to filter to"),
    contract_types: Optional[str] = Query(
        None, description="Comma-separated subset; defaults to all 9"),
    discriminate_sigma: float = Query(
        0.5, ge=0.0, le=3.0,
        description="Effect-size cutoff for discriminating flag (|gap|/σ)"),
):
    """For each contract type, compare avg(indicator) for wins vs losses.

    The `discriminating` flag is set when |gap| > N·σ where σ is the
    indicator's overall std across the full dataset (cheap effect-size
    proxy — not a t-test, but enough for surfacing in the dashboard).
    """
    df = _classify_contract_for(_load_trades())
    df = df[df["target_delta"] == delta]
    if df.empty:
        return {"delta": delta, "indicators": [], "rows": []}

    keep = (set(contract_types.split(",")) if contract_types
            else set(_CONTRACT_ORDER))
    df = df[df["contract_type"].isin(keep)]

    # Pre-compute σ per indicator across the full dataset for the
    # effect-size cutoff. Using the full dataset (not just this Δ) gives
    # a stable reference scale.
    full = _load_trades()
    sigmas: dict[str, float] = {}
    for col, _label, _cat in _ATTR_INDICATORS:
        if col in full.columns:
            sigmas[col] = float(full[col].std()) if full[col].notna().any() else 0.0

    indicator_meta = [
        {"column": col, "label": label, "category": cat}
        for col, label, cat in _ATTR_INDICATORS if col in df.columns
    ]

    rows = []
    for ct in _CONTRACT_ORDER:
        if ct not in keep: continue
        sub = df[df["contract_type"] == ct]
        if sub.empty: continue
        wins   = sub[sub["outcome"] == "win"]
        losses = sub[sub["outcome"] != "win"]
        n_win, n_loss = len(wins), len(losses)
        if n_win == 0 and n_loss == 0:
            continue

        ind_results: dict[str, dict] = {}
        for col, _label, _cat in _ATTR_INDICATORS:
            if col not in sub.columns:
                continue
            avg_win  = float(wins[col].mean())   if n_win  else float("nan")
            avg_loss = float(losses[col].mean()) if n_loss else float("nan")
            gap = avg_win - avg_loss if (
                np.isfinite(avg_win) and np.isfinite(avg_loss)) else float("nan")
            sigma = sigmas.get(col, 0.0)
            discriminating = bool(
                np.isfinite(gap) and sigma > 0 and
                abs(gap) > discriminate_sigma * sigma
            )
            ind_results[col] = {
                "avg_win":  None if not np.isfinite(avg_win)  else round(avg_win, 6),
                "avg_loss": None if not np.isfinite(avg_loss) else round(avg_loss, 6),
                "gap":      None if not np.isfinite(gap)      else round(gap, 6),
                "sigma":    round(sigma, 6),
                "discriminating": discriminating,
            }

        rows.append({
            "contract_type": ct,
            "n_win":  n_win,
            "n_loss": n_loss,
            "win_rate": round(n_win / max(n_win + n_loss, 1), 4),
            "avg_net_win":  float(wins["net_pnl_usd"].mean()) if n_win else 0.0,
            "avg_net_loss": float(losses["net_pnl_usd"].mean()) if n_loss else 0.0,
            "indicators": ind_results,
        })

    return {
        "delta": delta,
        "indicators": indicator_meta,
        "rows": rows,
    }


@router.get("/per_friday_best")
def per_friday_best(
    delta: float = Query(0.30),
    n_deciding: int = Query(3, ge=1, le=10,
                            description="How many top deciding indicators to surface"),
):
    """For each Friday, return the contract type that produced the best
    net P&L at the given Δ, plus the runner-up, the worst loser, and the
    top-N indicators whose values most differ between winner and loser
    (normalized by the indicator's overall std)."""
    df = _classify_contract_for(_load_trades())
    df = df[df["target_delta"] == delta]
    if df.empty:
        return {"delta": delta, "rows": []}

    full = _load_trades()
    sigmas: dict[str, float] = {}
    for col, _label, _cat in _ATTR_INDICATORS:
        if col in full.columns:
            s = float(full[col].std()) if full[col].notna().any() else 0.0
            sigmas[col] = s

    def _trade_summary(row: pd.Series) -> dict:
        out = {
            "contract_type": str(row["contract_type"]),
            "expiry_date":   str(row["expiry_date"]),
            "net_pnl_usd":   float(row["net_pnl_usd"]),
            "outcome":       str(row["outcome"]),
            "credit_pct":    float(row["credit_pct"])
                             if pd.notna(row["credit_pct"]) else None,
            "indicators":    {},
        }
        for col, _label, _cat in _ATTR_INDICATORS:
            if col in row.index and pd.notna(row[col]):
                out["indicators"][col] = round(float(row[col]), 6)
        return out

    rows = []
    for friday, g in df.groupby("entry_date_ist"):
        if len(g) < 2:
            # Single contract live on this Friday — no comparison possible
            best = g.iloc[0]
            rows.append({
                "entry_date_ist": str(friday),
                "n_contracts": 1,
                "winner":     _trade_summary(best),
                "runner_up":  None,
                "loser":      None,
                "deciding_indicators": [],
            })
            continue
        gs = g.sort_values("net_pnl_usd", ascending=False)
        winner = gs.iloc[0]
        loser  = gs.iloc[-1]
        runner = gs.iloc[1] if len(gs) >= 2 else None

        # Rank indicators by |winner - loser| / σ
        scored: list[tuple[str, float, float, float, float]] = []
        for col, _label, _cat in _ATTR_INDICATORS:
            if col not in winner.index or col not in loser.index:
                continue
            w_v, l_v = winner[col], loser[col]
            if pd.isna(w_v) or pd.isna(l_v):
                continue
            sigma = sigmas.get(col, 0.0)
            if sigma <= 0:
                continue
            score = abs(float(w_v) - float(l_v)) / sigma
            scored.append((col, float(w_v), float(l_v), score, sigma))
        scored.sort(key=lambda t: t[3], reverse=True)
        deciders = [
            {
                "column":         col,
                "winner_value":   round(w_v, 6),
                "loser_value":    round(l_v, 6),
                "gap":            round(w_v - l_v, 6),
                "score_sigma":    round(score, 4),
            }
            for col, w_v, l_v, score, _sigma in scored[:n_deciding]
        ]

        rows.append({
            "entry_date_ist": str(friday),
            "n_contracts":    int(len(g)),
            "winner":     _trade_summary(winner),
            "runner_up":  _trade_summary(runner) if runner is not None else None,
            "loser":      _trade_summary(loser),
            "deciding_indicators": deciders,
        })

    rows.sort(key=lambda r: r["entry_date_ist"], reverse=True)
    return {
        "delta": delta,
        "n_fridays": len(rows),
        "rows": rows,
    }


@router.get("/win_frequency")
def win_frequency(
    delta: float = Query(0.30),
):
    """For each contract type, the count of Fridays it produced the best
    net P&L at the given Δ. Sums to n_fridays_with_multiple_contracts."""
    df = _classify_contract_for(_load_trades())
    df = df[df["target_delta"] == delta]
    if df.empty:
        return {"delta": delta, "n_fridays": 0, "rows": []}

    n_fridays = int(df["entry_date_ist"].nunique())

    counts: dict[str, dict] = {ct: {"n_wins": 0, "sum_net_when_winning": 0.0,
                                     "n_appears": 0}
                                for ct in _CONTRACT_ORDER}
    for friday, g in df.groupby("entry_date_ist"):
        gs = g.sort_values("net_pnl_usd", ascending=False)
        winner = gs.iloc[0]
        ct = str(winner["contract_type"])
        if ct in counts:
            counts[ct]["n_wins"] += 1
            counts[ct]["sum_net_when_winning"] += float(winner["net_pnl_usd"])
        for ct_appears in g["contract_type"].astype(str).unique():
            if ct_appears in counts:
                counts[ct_appears]["n_appears"] += 1

    rows = []
    for ct in _CONTRACT_ORDER:
        c = counts[ct]
        if c["n_appears"] == 0:
            continue
        avg_net = (c["sum_net_when_winning"] / c["n_wins"]) if c["n_wins"] else 0.0
        rows.append({
            "contract_type":      ct,
            "n_wins":             c["n_wins"],
            "n_appears":          c["n_appears"],
            "win_frequency":      round(c["n_wins"] / max(n_fridays, 1), 4),
            "appear_frequency":   round(c["n_appears"] / max(n_fridays, 1), 4),
            "avg_net_when_winning": round(avg_net, 4),
        })
    rows.sort(key=lambda r: r["n_wins"], reverse=True)
    return {
        "delta": delta,
        "n_fridays": n_fridays,
        "rows": rows,
    }

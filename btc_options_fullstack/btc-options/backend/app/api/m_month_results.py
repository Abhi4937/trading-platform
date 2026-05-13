"""M-Month dashboard API — monthly + bimonthly + last-Fri-rolling strangle sweep.

Sibling to m7_results.py. Backed by:
  /home/abhis/btc-data/derived/m_month/m_month_trades.parquet
  /home/abhis/btc-data/derived/m_month/m_month_paths/entry_month=*/part.parquet

Stage 1 scope: /meta, /summary, /iv_band_summary, /missed_sessions. The
heavy drilldown endpoints (losses_distribution, trade_diagnostic,
leg_attribution, cell_winners_vs_losers, cell_worst_fridays) live in
m7_results and can be re-pointed at the m_month parquet via a separate
follow-up.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import duckdb
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
log = logging.getLogger(__name__)

M_MONTH_BASE_DIR = "/home/abhis/btc-data/derived/m_month"
TRADES_PATH = os.path.join(M_MONTH_BASE_DIR, "m_month_trades.parquet")
PATHS_GLOB = os.path.join(M_MONTH_BASE_DIR, "m_month_paths/entry_month=*/*.parquet")

_TRADES_DF: Optional[pd.DataFrame] = None
_TRADES_MTIME: float = 0.0

VALID_CYCLES = ("monthly", "bimonthly", "lastfri_monthly", "lastfri_bimonthly")


def _load_trades() -> pd.DataFrame:
    """Read m_month_trades.parquet with mtime-based cache."""
    global _TRADES_DF, _TRADES_MTIME
    if not os.path.exists(TRADES_PATH):
        raise HTTPException(
            status_code=503,
            detail=f"m_month_trades.parquet missing under {M_MONTH_BASE_DIR}; "
                   f"run `python -m app.analytics.m_month_batch_backtester` first.",
        )
    mtime = os.path.getmtime(TRADES_PATH)
    if _TRADES_DF is None or mtime != _TRADES_MTIME:
        _TRADES_DF = pd.read_parquet(TRADES_PATH)
        _TRADES_MTIME = mtime
        log.info("M-Month trades reloaded: %d rows from %s", len(_TRADES_DF), TRADES_PATH)
    return _TRADES_DF


def _apply_cycle_filter(df: pd.DataFrame, trade_cycle: Optional[str]) -> pd.DataFrame:
    if not trade_cycle or trade_cycle == "all":
        return df
    return df[df["trade_cycle"] == trade_cycle]


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/meta")
def get_meta() -> dict:
    """Metadata for the dashboard's initial load."""
    try:
        df = _load_trades()
    except HTTPException:
        return {
            "ready": False,
            "cycles": list(VALID_CYCLES),
            "deltas": [],
            "iv_bands": [],
            "entry_months": [],
            "n_trades": 0,
        }

    return {
        "ready": True,
        "cycles": list(VALID_CYCLES),
        "deltas": sorted(df["delta_target"].dropna().unique().tolist()),
        "iv_bands": sorted(df["entry_atm_iv_band"].dropna().unique().tolist()),
        "entry_months": sorted(df["entry_month"].dropna().unique().tolist()),
        "entry_hours": sorted(df["entry_hour_ist"].dropna().unique().tolist()),
        "expiry_dates": sorted(df["expiry_date"].dropna().unique().tolist()),
        "n_trades": int(len(df)),
        "first_anchor_date": str(df["anchor_date_ist"].min()) if len(df) else None,
        "last_anchor_date": str(df["anchor_date_ist"].max()) if len(df) else None,
    }


@router.get("/summary")
def get_summary(
    trade_cycle: Optional[str] = Query(None,
        description="monthly / bimonthly / lastfri_rolling / all"),
) -> dict:
    """Headline KPIs for the dashboard strip — entry-side only at stage 1.

    Exit-side metrics (win rate, avg net P&L) require an exit rule and live in
    the best_combo / iv_band_summary endpoints in a follow-up.
    """
    try:
        df = _load_trades()
    except HTTPException:
        return {
            "ready": False,
            "n_trades": 0,
            "n_anchors": 0,
            "n_cycles": 0,
            "trade_cycle": trade_cycle or "all",
        }
    df = _apply_cycle_filter(df, trade_cycle)
    return {
        "ready": True,
        "trade_cycle": trade_cycle or "all",
        "n_trades": int(len(df)),
        "n_anchors": int(df["anchor_date_ist"].nunique()),
        "n_cycles": int(df["trade_cycle"].nunique()),
        "avg_credit_usd": float(df["credit_usd"].mean()) if len(df) else 0.0,
        "avg_margin_usd": float(df["margin_used_usd_at_entry"].mean()) if len(df) else 0.0,
        "avg_dte_days": float(df["dte_days"].mean()) if len(df) else 0.0,
    }


@router.get("/trades")
def get_trades(
    trade_cycle: Optional[str] = Query(None),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
) -> dict:
    """Paginated trade-entry rows (no exit derivation)."""
    df = _load_trades()
    df = _apply_cycle_filter(df, trade_cycle)
    total = len(df)
    slc = df.iloc[offset:offset + limit]
    rows: list[dict] = []
    for _, r in slc.iterrows():
        row = {k: (None if pd.isna(v) else v) for k, v in r.to_dict().items()}
        # Cast numpy types to plain Python for JSON serialization
        for k, v in row.items():
            if isinstance(v, (np.integer,)):
                row[k] = int(v)
            elif isinstance(v, (np.floating,)):
                row[k] = float(v) if not np.isnan(v) else None
        rows.append(row)
    return {"total": total, "rows": rows}


@router.get("/iv_band_summary")
def get_iv_band_summary(
    trade_cycle: Optional[str] = Query(None),
) -> dict:
    """Per-IV-band aggregate, entry-side only at stage 1.

    Stage 2 will expand to apply an exit rule and compute win-rate / avg-net etc.
    """
    df = _load_trades()
    df = _apply_cycle_filter(df, trade_cycle)
    if df.empty:
        return {"rows": [], "metric": "n_trades"}

    grouped = df.groupby("entry_atm_iv_band", dropna=False).agg(
        n_trades=("trade_id", "count"),
        avg_credit_usd=("credit_usd", "mean"),
        avg_margin_usd=("margin_used_usd_at_entry", "mean"),
        avg_dte_days=("dte_days", "mean"),
    ).reset_index()

    rows: list[dict] = []
    for _, r in grouped.iterrows():
        rows.append({
            "iv_band": str(r["entry_atm_iv_band"]),
            "n_trades": int(r["n_trades"]),
            "avg_credit_usd": float(r["avg_credit_usd"]) if pd.notna(r["avg_credit_usd"]) else 0.0,
            "avg_margin_usd": float(r["avg_margin_usd"]) if pd.notna(r["avg_margin_usd"]) else 0.0,
            "avg_dte_days": float(r["avg_dte_days"]) if pd.notna(r["avg_dte_days"]) else 0.0,
        })
    rows.sort(key=lambda x: x["iv_band"])
    return {"rows": rows, "metric": "n_trades"}


@router.get("/trade_diagnostic")
def get_trade_diagnostic(
    trade_id: int = Query(..., description="Per-trade ID from /trades"),
    bar_step: int = Query(1, ge=1, le=60,
        description="Sample every Nth path bar (1 = every minute, 5 = every 5 min). "
                    "Use 5+ for long-DTE trades to keep payload small."),
) -> dict:
    """Per-trade diagnostic — header from the trades parquet + the full
    Greeks/spot/MTM trajectory from the path parquet.

    Returns identity + per-bar arrays for: ts, spot, call_mark, put_mark,
    total_premium, call_delta, call_gamma, call_theta, call_vega, put_*
    (same), net_delta/gamma/theta/vega, theta_per_vega_combined, atm_iv_now,
    gross_pnl_usd, net_pnl_unwind_usd, pnl_pct_of_credit, pnl_pct_of_margin.
    """
    trades = _load_trades()
    row = trades[trades["trade_id"] == trade_id]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"trade_id={trade_id} not found")
    r = row.iloc[0].to_dict()

    # Identity columns the dashboard wants to display
    identity_cols = [
        "trade_id", "trade_cycle", "entry_month", "anchor_date_ist",
        "entry_ts_utc", "entry_ts_requested_utc", "entry_ts_actual_utc",
        "wait_minutes", "match_quality", "entry_dow", "entry_hour_ist",
        "expiry_date", "expiry_variant", "dte_days", "dte_hours_at_entry",
        "delta_target", "is_straddle", "quantity_lots",
        "call_strike", "put_strike",
        "call_entry_mark", "put_entry_mark",
        "call_entry_iv", "put_entry_iv",
        "call_entry_delta", "put_entry_delta",
        "call_entry_gamma", "put_entry_gamma",
        "call_entry_theta", "put_entry_theta",
        "call_entry_vega", "put_entry_vega",
        "theta_per_vega_combined", "entry_net_delta", "entry_net_gamma",
        "entry_net_theta", "entry_net_vega",
        "total_credit_usd_per_btc", "credit_usd", "credit_pct_of_spot",
        "spot_at_entry", "entry_atm_iv", "entry_atm_iv_pct", "entry_atm_iv_band",
        "total_entry_cost_usd", "margin_used_usd_at_entry",
        "n_path_rows", "path_first_ts", "path_last_ts",
    ]
    identity: dict = {}
    for c in identity_cols:
        v = r.get(c)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            identity[c] = None
        elif isinstance(v, (np.integer,)):
            identity[c] = int(v)
        elif isinstance(v, (np.floating,)):
            identity[c] = float(v)
        else:
            identity[c] = v

    # Pull the per-bar trajectory from the path parquet
    if not os.path.exists(os.path.dirname(PATHS_GLOB.split("entry_month=*")[0])):
        return {"identity": identity, "path": None,
                "error": "no path data on disk"}

    bar_cols = [
        "ts", "minute_offset", "spot",
        "call_mark", "put_mark", "total_premium",
        "call_iv", "put_iv", "atm_iv_now",
        "call_delta", "call_gamma", "call_theta", "call_vega",
        "put_delta", "put_gamma", "put_theta", "put_vega",
        "net_delta", "net_gamma", "net_theta", "net_vega",
        "theta_per_vega_combined",
        "gross_pnl_usd", "net_pnl_unwind_usd",
        "pnl_pct_of_credit", "pnl_pct_of_margin",
    ]
    conn = duckdb.connect()
    try:
        q = f"""
        SELECT {', '.join(bar_cols)}
        FROM read_parquet('{PATHS_GLOB}', hive_partitioning=true)
        WHERE trade_id = {int(trade_id)}
          AND minute_offset % {int(bar_step)} = 0
        ORDER BY ts ASC
        """
        df = conn.execute(q).df()
    except Exception as e:
        log.exception(f"trade_diagnostic path query failed for {trade_id}: {e}")
        return {"identity": identity, "path": None, "error": str(e)}
    finally:
        conn.close()

    if df.empty:
        return {"identity": identity, "path": None,
                "error": "no path bars found for this trade_id"}

    # Build per-column arrays (more compact than per-row dicts)
    path: dict[str, list] = {}
    for col in bar_cols:
        series = df[col]
        if pd.api.types.is_integer_dtype(series):
            path[col] = [int(v) if not pd.isna(v) else None for v in series.tolist()]
        elif pd.api.types.is_float_dtype(series):
            path[col] = [None if pd.isna(v) else float(v) for v in series.tolist()]
        else:
            path[col] = series.tolist()

    return {
        "identity": identity,
        "path": path,
        "bar_step_min": bar_step,
        "n_bars_returned": int(len(df)),
    }


@router.get("/missed_sessions")
def get_missed_sessions(
    trade_cycle: Optional[str] = Query(None),
) -> dict:
    """List of anchor dates with NO trades (e.g. because no qualifying strikes)."""
    df = _load_trades()
    df = _apply_cycle_filter(df, trade_cycle)
    # Build the expected anchor set from the trades' month range
    if df.empty:
        return {"missed": [], "total_anchors_expected": 0}
    anchors_with_trades = set(df["anchor_date_ist"].unique())
    months = sorted(df["entry_month"].unique())
    expected: set[str] = set()
    for em in months:
        year, month = (int(x) for x in em.split("-"))
        # Add the first-Mon and last-Fri of each month present in data
        from datetime import date as _date, timedelta as _td
        fm = _date(year, month, 1)
        while fm.weekday() != 0:
            fm += _td(days=1)
        expected.add(fm.isoformat())
        if month == 12:
            lf_d = _date(year + 1, 1, 1) - _td(days=1)
        else:
            lf_d = _date(year, month + 1, 1) - _td(days=1)
        while lf_d.weekday() != 4:
            lf_d -= _td(days=1)
        expected.add(lf_d.isoformat())
    missed = sorted(expected - anchors_with_trades)
    return {
        "missed": [{"anchor_date": d, "reason": "no_qualifying_strikes_or_data"} for d in missed],
        "total_anchors_expected": len(expected),
        "total_anchors_with_trades": len(anchors_with_trades),
    }

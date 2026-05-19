"""M9 dashboard API — Friday-entry weekly + biweekly strangle sweep.

Sibling to m_month_results.py. Backed by:
  /home/abhis/btc-data/derived/m9_friday_weekly/m9_trades.parquet
  /home/abhis/btc-data/derived/m9_friday_weekly/m9_paths/expiry_type=*/entry_yyyymm=*/*.parquet

Stage 1 scope: /meta, /summary, /trades, /iv_band_summary, /missed_sessions,
/trade_diagnostic. The full M7-style drilldown surface (loss_distribution,
leg_attribution, cell_winners_vs_losers, etc.) lands in Stage 2.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import duckdb
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
log = logging.getLogger(__name__)

M9_BASE_DIR = "/home/abhis/btc-data/derived/m9_friday_weekly"
TRADES_PATH = os.path.join(M9_BASE_DIR, "m9_trades.parquet")
PATHS_GLOB = os.path.join(M9_BASE_DIR, "m9_paths/expiry_type=*/entry_yyyymm=*/*.parquet")

_TRADES_DF: Optional[pd.DataFrame] = None
_TRADES_MTIME: float = 0.0

VALID_EXPIRY_TYPES = ("weekly", "biweekly")


def _load_trades() -> pd.DataFrame:
    """Read m9_trades.parquet with mtime-based cache."""
    global _TRADES_DF, _TRADES_MTIME
    if not os.path.exists(TRADES_PATH):
        raise HTTPException(
            status_code=503,
            detail=f"m9_trades.parquet missing under {M9_BASE_DIR}; "
                   f"run `python -m app.analytics.m9_friday_weekly_backtester` first.",
        )
    mtime = os.path.getmtime(TRADES_PATH)
    if _TRADES_DF is None or mtime != _TRADES_MTIME:
        _TRADES_DF = pd.read_parquet(TRADES_PATH)
        _TRADES_MTIME = mtime
        log.info("M9 trades reloaded: %d rows from %s", len(_TRADES_DF), TRADES_PATH)
    return _TRADES_DF


def _apply_expiry_type_filter(df: pd.DataFrame, expiry_type: Optional[str]) -> pd.DataFrame:
    if not expiry_type or expiry_type == "all":
        return df
    return df[df["expiry_type"] == expiry_type]


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/meta")
def get_meta() -> dict:
    """Metadata for dashboard initial load."""
    try:
        df = _load_trades()
    except HTTPException:
        return {
            "ready": False,
            "expiry_types": list(VALID_EXPIRY_TYPES),
            "deltas": [],
            "iv_bands": [],
            "entry_yyyymms": [],
            "n_trades": 0,
        }

    def _uniq(col: str) -> list:
        if col not in df.columns:
            return []
        s = df[col].dropna().unique().tolist()
        try:
            return sorted(s)
        except TypeError:
            return sorted([str(v) for v in s])

    return {
        "ready": True,
        "expiry_types": list(VALID_EXPIRY_TYPES),
        "deltas": _uniq("delta_target"),
        "iv_bands": _uniq("entry_atm_iv_band"),
        "entry_yyyymms": _uniq("entry_yyyymm"),
        "entry_hours": _uniq("entry_hour_ist"),
        "entry_minutes": _uniq("entry_minute_ist"),
        "entry_time_labels": _uniq("entry_time_label"),
        "entry_fridays": _uniq("entry_friday_ist"),
        "expiry_dates": _uniq("expiry_date"),
        "dte_buckets": _uniq("dte_bucket"),
        "ivp_buckets": _uniq("ivp_bucket"),
        "n_trades": int(len(df)),
        "first_friday": str(df["entry_friday_ist"].min()) if len(df) else None,
        "last_friday": str(df["entry_friday_ist"].max()) if len(df) else None,
    }


@router.get("/summary")
def get_summary(
    expiry_type: Optional[str] = Query(None,
        description="weekly / biweekly / all"),
) -> dict:
    """Headline KPIs strip — entry-side stats."""
    try:
        df = _load_trades()
    except HTTPException:
        return {
            "ready": False,
            "n_trades": 0,
            "n_fridays": 0,
            "expiry_type": expiry_type or "all",
        }
    df = _apply_expiry_type_filter(df, expiry_type)
    return {
        "ready": True,
        "expiry_type": expiry_type or "all",
        "n_trades": int(len(df)),
        "n_fridays": int(df["entry_friday_ist"].nunique()),
        "n_expiry_types": int(df["expiry_type"].nunique()),
        "avg_credit_usd": float(df["credit_usd"].mean()) if len(df) else 0.0,
        "avg_margin_usd": float(df["margin_used_usd_at_entry"].mean()) if len(df) else 0.0,
        "avg_dte_days": float(df["dte_days"].mean()) if len(df) else 0.0,
    }


@router.get("/trades")
def get_trades(
    expiry_type: Optional[str] = Query(None),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
) -> dict:
    """Paginated trade-entry rows (no exit derivation)."""
    df = _load_trades()
    df = _apply_expiry_type_filter(df, expiry_type)
    total = len(df)
    slc = df.iloc[offset:offset + limit]
    rows: list[dict] = []
    for _, r in slc.iterrows():
        row = {k: (None if pd.isna(v) else v) for k, v in r.to_dict().items()}
        for k, v in row.items():
            if isinstance(v, (np.integer,)):
                row[k] = int(v)
            elif isinstance(v, (np.floating,)):
                row[k] = float(v) if not np.isnan(v) else None
        rows.append(row)
    return {"total": total, "rows": rows}


@router.get("/iv_band_summary")
def get_iv_band_summary(
    expiry_type: Optional[str] = Query(None),
) -> dict:
    """Per-IV-band aggregate, entry-side only."""
    df = _load_trades()
    df = _apply_expiry_type_filter(df, expiry_type)
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
    bar_step: int = Query(1, ge=1, le=60),
) -> dict:
    """Per-trade diagnostic — header + Greeks/spot/MTM trajectory."""
    trades = _load_trades()
    row = trades[trades["trade_id"] == trade_id]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"trade_id={trade_id} not found")
    r = row.iloc[0].to_dict()

    identity_cols = [
        "trade_id", "expiry_type", "entry_friday_ist", "entry_yyyymm",
        "entry_ts_utc", "entry_ts_requested_utc", "entry_ts_actual_utc",
        "wait_minutes", "match_quality",
        "entry_hour_ist", "entry_minute_ist", "entry_time_label",
        "expiry_date", "dte_days", "dte_hours_at_entry",
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

    if not os.path.exists(os.path.dirname(PATHS_GLOB.split("expiry_type=*")[0])):
        return {"identity": identity, "path": None, "error": "no path data on disk"}

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
    expiry_type: Optional[str] = Query(None),
) -> dict:
    """List of Fridays with NO trades for an expiry_type."""
    df = _load_trades()
    df = _apply_expiry_type_filter(df, expiry_type)
    if df.empty:
        return {"missed": [], "total_fridays_expected": 0}
    # Expected universe = every Friday present in the data range for ANY
    # expiry_type; missed = those without rows under the active filter.
    all_df = _load_trades()
    expected = set(all_df["entry_friday_ist"].unique())
    have = set(df["entry_friday_ist"].unique())
    missed = sorted(expected - have)
    return {
        "missed": [{"entry_friday_ist": d, "reason": "no_qualifying_strikes_or_data"} for d in missed],
        "total_fridays_expected": len(expected),
        "total_fridays_with_trades": len(have),
    }

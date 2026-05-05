"""M7 dashboard API — Friday→Saturday strangle/straddle sweep results.

Endpoints:
  GET /api/v1/m7/summary                        — counts/win-rate/avg net (any filter)
  GET /api/v1/m7/trades                         — paginated entry rows (filterable)
  GET /api/v1/m7/path?trade_id=...              — full 1m path for one trade
  GET /api/v1/m7/aggregate?dimensions=&metric=  — group-by reductions, optional exit rule
  GET /api/v1/m7/heatmap                        — entry-time × exit-time matrix
  GET /api/v1/m7/iv_band_summary                — best (entry/exit/expiry/delta/rule) per IV band
  GET /api/v1/m7/cost_breakdown?trade_id=...    — per-leg slippage + brokerage detail
  GET /api/v1/m7/best_combo                     — top-N combos by metric

Backed by:
  /home/abhis/btc-data/derived/m7/m7_trades.parquet            (entry context only)
  /home/abhis/btc-data/derived/m7/m7_paths/friday_date=*/...   (1m paths, partitioned)

The simulator produces no exit columns — every exit outcome is derived here from
the saved 1m path via DuckDB, given an exit_rule (fixed_time / max_profit_pct /
margin_pct / premium_sl_pct, with Sat 17:30 IST hard cap).
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Optional

import duckdb
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
log = logging.getLogger(__name__)

M7_BASE_DIR = "/home/abhis/btc-data/derived/m7"
TRADES_PATH = os.path.join(M7_BASE_DIR, "m7_trades.parquet")
TRADES_ENRICHED_PATH = os.path.join(M7_BASE_DIR, "m7_trades_enriched.parquet")
PATHS_GLOB = os.path.join(M7_BASE_DIR, "m7_paths/friday_date=*/part.parquet")

# Lazy module-level cache. Trades fits in RAM; paths queried via DuckDB.
_TRADES_DF: Optional[pd.DataFrame] = None


def _load_trades() -> pd.DataFrame:
    """Prefer m7_trades_enriched.parquet (with calibration_v2 join columns)
    when present; fall back to plain m7_trades.parquet."""
    global _TRADES_DF
    if _TRADES_DF is None:
        if os.path.exists(TRADES_ENRICHED_PATH):
            _TRADES_DF = pd.read_parquet(TRADES_ENRICHED_PATH)
        elif os.path.exists(TRADES_PATH):
            _TRADES_DF = pd.read_parquet(TRADES_PATH)
        else:
            raise HTTPException(
                status_code=503,
                detail=f"m7_trades.parquet missing under {M7_BASE_DIR}; "
                       f"run `python -m app.analytics.m7_batch_backtester` first.",
            )
    return _TRADES_DF


def _duckdb_conn() -> duckdb.DuckDBPyConnection:
    """Per-call DuckDB connection (cheap)."""
    return duckdb.connect()


# ── Filters ───────────────────────────────────────────────────────────────────

_TRADE_FILTER_COLS = {
    "delta_target": float,
    "is_straddle": bool,
    "expiry_date": str,
    "entry_atm_iv_band": str,
    "entry_hour_ist": int,
    "dte_bucket": str,
    "spot_bucket": str,
    "ivp_bucket": str,
    "ctx_pattern": str,
    "ctx_gex_regime": str,
    "friday_date_ist": str,
}


def _coerce_value(raw: str, t: type):
    if t is bool:
        return raw.lower() in ("true", "1", "yes")
    if t is int:
        return int(raw)
    if t is float:
        return float(raw)
    return raw


def _apply_filters(df: pd.DataFrame, filters: dict[str, Optional[str]]) -> pd.DataFrame:
    out = df
    for col, raw in (filters or {}).items():
        if col not in _TRADE_FILTER_COLS or raw is None or raw == "":
            continue
        t = _TRADE_FILTER_COLS[col]
        vals = [_coerce_value(v.strip(), t) for v in str(raw).split(",") if v.strip()]
        if not vals:
            continue
        if col not in out.columns:
            continue
        out = out[out[col].isin(vals)]
    return out


def _query_filters(
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
) -> dict:
    return {
        "delta_target": delta_target,
        "is_straddle": is_straddle,
        "expiry_date": expiry_date,
        "entry_atm_iv_band": entry_atm_iv_band,
        "entry_hour_ist": entry_hour_ist,
        "dte_bucket": dte_bucket,
        "spot_bucket": spot_bucket,
        "ivp_bucket": ivp_bucket,
        "ctx_pattern": ctx_pattern,
        "ctx_gex_regime": ctx_gex_regime,
        "friday_date_ist": friday_date_ist,
    }


# ── Exit rule derivation ──────────────────────────────────────────────────────

def _parse_exit_rule(exit_rule: Optional[str]) -> dict:
    """Parse exit_rule from JSON query param.

    Shape: {
      "fixed_exit_ts": <unix> | null,        # if set, ignore everything else
      "max_profit_pct":     <0..100> | null, # exit if pnl_pct_of_credit >= this
      "margin_target_pct":  <0..100> | null, # exit if pnl_pct_of_margin >= this
      "premium_sl_pct":     <50..200> | null # exit if leg mark >= entry × (1+pct/100)
      # Hard cap = Sat 17:30 IST is always applied (= last path row).
    }
    """
    if not exit_rule:
        return {}
    try:
        d = json.loads(exit_rule)
        if not isinstance(d, dict):
            return {}
        return d
    except Exception:
        raise HTTPException(status_code=400, detail="exit_rule must be JSON object")


def _exit_rule_sql_predicate(rule: dict) -> str:
    """Build SQL predicate fragment that fires when ANY rule triggers.

    Returns a fragment usable inside a CASE WHEN (...) THEN ts.
    Empty rule → empty string (no rule triggers; only hard cap applies).
    """
    parts = []
    if rule.get("max_profit_pct") is not None:
        parts.append(f"p.pnl_pct_of_credit >= {float(rule['max_profit_pct'])}")
    if rule.get("margin_target_pct") is not None:
        parts.append(f"p.pnl_pct_of_margin >= {float(rule['margin_target_pct'])}")
    if rule.get("premium_sl_pct") is not None:
        pct = float(rule['premium_sl_pct'])
        # SELL premium: fires if either leg's mark >= entry_mark × (1 + pct/100)
        # entry marks are joined via t.call_entry_mark / t.put_entry_mark
        mult = 1.0 + pct / 100.0
        parts.append(
            f"(p.call_mark >= t.call_entry_mark * {mult} "
            f"OR p.put_mark >= t.put_entry_mark * {mult})"
        )
    if not parts:
        return ""
    return " OR ".join(parts)


def _derive_exits(filters: dict, exit_rule: dict) -> pd.DataFrame:
    """For every trade matching `filters`, derive the exit outcome under `exit_rule`.

    Returns a DataFrame: one row per trade, with trade-level filter context
    columns + [exit_ts, exit_reason, gross_pnl_usd, net_pnl_estimate_usd,
    pnl_pct_of_credit, pnl_pct_of_margin, exit_call_mark, exit_put_mark, exit_spot].
    """
    trades = _apply_filters(_load_trades(), filters)
    if trades.empty:
        return pd.DataFrame()

    # If a fixed exit time is given, simply look up that row.
    if exit_rule.get("fixed_exit_ts") is not None:
        fix_ts = int(exit_rule["fixed_exit_ts"])
        # Same fixed_exit_ts for all trades → query path for that ts per trade
        sql = f"""
        SELECT p.trade_id, p.ts AS exit_ts, p.spot AS exit_spot,
               p.call_mark AS exit_call_mark, p.put_mark AS exit_put_mark,
               p.gross_pnl_usd, p.pnl_pct_of_credit, p.pnl_pct_of_margin,
               'fixed_time' AS exit_reason
        FROM read_parquet('{PATHS_GLOB}', hive_partitioning=true) p
        WHERE p.ts = {fix_ts}
        """
        conn = _duckdb_conn()
        exits = conn.execute(sql).df()
        conn.close()
    else:
        pred = _exit_rule_sql_predicate(exit_rule)
        # Trades-side projection (just what we need for joins + entry refs)
        meta_sql = """
        SELECT trade_id, call_entry_mark, put_entry_mark
        FROM read_parquet('{trades_path}')
        """.format(trades_path=TRADES_PATH if not os.path.exists(TRADES_ENRICHED_PATH) else TRADES_ENRICHED_PATH)

        if pred:
            triggers_sql = f"""
            WITH t AS ({meta_sql})
            SELECT p.trade_id,
                   MIN(p.ts) AS first_trigger_ts
            FROM read_parquet('{PATHS_GLOB}', hive_partitioning=true) p
            JOIN t ON p.trade_id = t.trade_id
            WHERE {pred}
            GROUP BY p.trade_id
            """
        else:
            # No rules → all trades exit at the hard cap (last path row)
            triggers_sql = "SELECT CAST(NULL AS BIGINT) AS trade_id, CAST(NULL AS BIGINT) AS first_trigger_ts WHERE 1=0"

        # Hard-cap = last path row per trade. Compute once per query.
        hardcap_sql = f"""
        SELECT trade_id, MAX(ts) AS hard_cap_ts
        FROM read_parquet('{PATHS_GLOB}', hive_partitioning=true)
        GROUP BY trade_id
        """

        sql = f"""
        WITH triggers AS ({triggers_sql}),
             hard_caps AS ({hardcap_sql}),
             chosen AS (
               SELECT h.trade_id,
                      COALESCE(tr.first_trigger_ts, h.hard_cap_ts) AS exit_ts,
                      CASE WHEN tr.first_trigger_ts IS NOT NULL THEN 'rule_trigger'
                           ELSE 'hard_cap' END AS exit_reason
               FROM hard_caps h LEFT JOIN triggers tr USING (trade_id)
             )
        SELECT c.trade_id, c.exit_ts, c.exit_reason,
               p.spot AS exit_spot, p.call_mark AS exit_call_mark,
               p.put_mark AS exit_put_mark,
               p.gross_pnl_usd, p.pnl_pct_of_credit, p.pnl_pct_of_margin
        FROM chosen c
        JOIN read_parquet('{PATHS_GLOB}', hive_partitioning=true) p
          ON p.trade_id = c.trade_id AND p.ts = c.exit_ts
        """
        conn = _duckdb_conn()
        exits = conn.execute(sql).df()
        conn.close()

    if exits.empty:
        return pd.DataFrame()

    # Restrict exits to filtered trades & merge entry-context columns
    keep_trade_cols = [
        "trade_id", "friday_date_ist", "entry_hour_ist", "entry_time_label",
        "expiry_date", "delta_target", "is_straddle",
        "entry_atm_iv", "entry_atm_iv_pct", "entry_atm_iv_band",
        "spot_at_entry", "credit_usd", "total_entry_cost_usd",
        "margin_used_usd_at_entry", "dte_bucket", "spot_bucket",
        "ivp_bucket", "ctx_pattern", "ctx_gex_regime", "dte_days",
    ]
    keep_trade_cols = [c for c in keep_trade_cols if c in trades.columns]
    merged = trades[keep_trade_cols].merge(exits, on="trade_id", how="inner")

    # Net P&L estimate: gross - 2× entry costs (rough round-trip approximation;
    # use /cost_breakdown for the exact per-leg slippage at exit ts).
    merged["net_pnl_estimate_usd"] = (
        merged["gross_pnl_usd"] - 2.0 * merged["total_entry_cost_usd"]
    )
    merged["is_win"] = merged["net_pnl_estimate_usd"] > 0

    return merged


# ── NaN → None for JSON ───────────────────────────────────────────────────────

def _to_records(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    out: list[dict] = []
    for rec in df.replace({np.nan: None, pd.NaT: None}).to_dict(orient="records"):
        # trade_id is uint64-ish; coerce to str for JSON safety
        if "trade_id" in rec and rec["trade_id"] is not None:
            rec["trade_id"] = str(rec["trade_id"])
        out.append(rec)
    return out


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/summary")
def get_summary(
    exit_rule: Optional[str] = None,
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
):
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"n_trades": 0, "n_wins": 0, "win_rate": 0.0,
                "avg_net_pnl_usd": 0.0, "total_net_pnl_usd": 0.0,
                "avg_credit_usd": 0.0, "avg_margin_usd": 0.0,
                "exit_reason_counts": {}}
    n = len(derived)
    n_wins = int(derived["is_win"].sum())
    return {
        "n_trades": n,
        "n_wins": n_wins,
        "win_rate": round(n_wins / n, 4),
        "avg_net_pnl_usd": round(float(derived["net_pnl_estimate_usd"].mean()), 4),
        "total_net_pnl_usd": round(float(derived["net_pnl_estimate_usd"].sum()), 4),
        "avg_gross_pnl_usd": round(float(derived["gross_pnl_usd"].mean()), 4),
        "avg_credit_usd": round(float(derived["credit_usd"].mean()), 4),
        "avg_margin_usd": round(float(derived["margin_used_usd_at_entry"].dropna().mean() or 0.0), 2),
        "exit_reason_counts": derived["exit_reason"].value_counts().to_dict(),
    }


@router.get("/trades")
def get_trades(
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    sort_by: str = "friday_date_ist",
    sort_dir: str = "asc",
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
):
    df = _apply_filters(_load_trades(), {
        "delta_target": delta_target, "is_straddle": is_straddle,
        "expiry_date": expiry_date, "entry_atm_iv_band": entry_atm_iv_band,
        "entry_hour_ist": entry_hour_ist, "friday_date_ist": friday_date_ist,
    })
    if sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=(sort_dir == "asc"))
    total = len(df)
    page = df.iloc[offset:offset + limit]
    return {"total": total, "offset": offset, "limit": limit,
            "rows": _to_records(page)}


@router.get("/path")
def get_path(trade_id: str = Query(...)):
    """Return the full 1m path for one trade."""
    try:
        tid = int(trade_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="trade_id must be int")
    sql = f"""
    SELECT * FROM read_parquet('{PATHS_GLOB}', hive_partitioning=true)
    WHERE trade_id = {tid}
    ORDER BY ts ASC
    """
    conn = _duckdb_conn()
    df = conn.execute(sql).df()
    conn.close()
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No path for trade_id={trade_id}")
    return {"trade_id": trade_id, "n_rows": len(df), "rows": _to_records(df)}


@router.get("/aggregate")
def get_aggregate(
    dimensions: str = Query(..., description="Comma-separated dim cols, e.g. delta_target,entry_atm_iv_band"),
    metric: str = Query("avg_net_pnl", description="avg_net_pnl|win_rate|count|sum_net_pnl|avg_credit"),
    exit_rule: Optional[str] = None,
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
):
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              None, ivp_bucket, ctx_pattern, None,
                              friday_date_ist)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"rows": [], "metric": metric, "dimensions": dimensions.split(",")}

    dims = [d.strip() for d in dimensions.split(",") if d.strip()]
    for d in dims:
        if d not in derived.columns:
            raise HTTPException(status_code=400, detail=f"Unknown dimension: {d}")

    grp = derived.groupby(dims, dropna=False)
    if metric == "count":
        out = grp.size().reset_index(name="value")
    elif metric == "win_rate":
        out = grp["is_win"].mean().round(4).reset_index(name="value")
    elif metric == "avg_net_pnl":
        out = grp["net_pnl_estimate_usd"].mean().round(4).reset_index(name="value")
    elif metric == "sum_net_pnl":
        out = grp["net_pnl_estimate_usd"].sum().round(4).reset_index(name="value")
    elif metric == "avg_gross_pnl":
        out = grp["gross_pnl_usd"].mean().round(4).reset_index(name="value")
    elif metric == "avg_credit":
        out = grp["credit_usd"].mean().round(4).reset_index(name="value")
    elif metric == "avg_margin":
        out = grp["margin_used_usd_at_entry"].mean().round(2).reset_index(name="value")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown metric: {metric}")
    out["n_trades"] = grp.size().values
    return {"rows": _to_records(out), "metric": metric, "dimensions": dims}


@router.get("/heatmap")
def get_heatmap(
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
    delta_target: Optional[str] = None,
    expiry_date: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
):
    """Entry-time × Friday heatmap (one cell per friday_date × entry_hour)."""
    filters = _query_filters(delta_target, None, expiry_date,
                              entry_atm_iv_band, None, None, None, None, None,
                              None, None)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"rows": []}
    grp = derived.groupby(["entry_hour_ist", "friday_date_ist"], dropna=False)
    if metric == "win_rate":
        agg = grp["is_win"].mean().round(4)
    elif metric == "avg_net_pnl":
        agg = grp["net_pnl_estimate_usd"].mean().round(4)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown metric: {metric}")
    out = agg.reset_index(name="value")
    out["n_trades"] = grp.size().values
    return {"rows": _to_records(out), "metric": metric}


@router.get("/iv_band_summary")
def get_iv_band_summary(
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
):
    """For each IV band, find the best (entry_hour, expiry, delta) combo
    by the chosen metric. Headline 'answer the question' table."""
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits({}, rule)
    if derived.empty:
        return {"rows": []}

    dims = ["entry_atm_iv_band", "entry_hour_ist", "expiry_date", "delta_target"]
    grp = derived.groupby(dims, dropna=False)
    if metric == "win_rate":
        score = grp["is_win"].mean()
    elif metric == "avg_net_pnl":
        score = grp["net_pnl_estimate_usd"].mean()
    elif metric == "sum_net_pnl":
        score = grp["net_pnl_estimate_usd"].sum()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown metric: {metric}")
    n = grp.size()
    df = pd.DataFrame({"score": score, "n_trades": n}).reset_index()
    # Best combo per IV band (require >= 3 trades for stability)
    df_stable = df[df["n_trades"] >= 3]
    if df_stable.empty:
        df_stable = df
    idx = df_stable.groupby("entry_atm_iv_band", dropna=False)["score"].idxmax()
    best = df_stable.loc[idx].reset_index(drop=True)
    best["score"] = best["score"].round(4)
    return {"rows": _to_records(best), "metric": metric}


@router.get("/best_combo")
def get_best_combo(
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
    top_n: int = Query(20, ge=1, le=200),
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
):
    """Top-N (entry_hour × expiry × delta) combos by metric, given exit rule."""
    filters = _query_filters(delta_target, is_straddle, None, entry_atm_iv_band,
                              None, None, None, None, None, None, None)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"rows": []}

    dims = ["entry_hour_ist", "expiry_date", "delta_target"]
    grp = derived.groupby(dims, dropna=False)
    if metric == "win_rate":
        score = grp["is_win"].mean()
    elif metric == "avg_net_pnl":
        score = grp["net_pnl_estimate_usd"].mean()
    elif metric == "sum_net_pnl":
        score = grp["net_pnl_estimate_usd"].sum()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown metric: {metric}")
    df = pd.DataFrame({"score": score, "n_trades": grp.size()}).reset_index()
    df = df[df["n_trades"] >= 3]
    df = df.sort_values("score", ascending=False).head(top_n)
    df["score"] = df["score"].round(4)
    return {"rows": _to_records(df), "metric": metric}


@router.get("/cost_breakdown")
def get_cost_breakdown(trade_id: str = Query(...)):
    """Per-leg entry cost decomposition for one trade."""
    try:
        tid = int(trade_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="trade_id must be int")
    df = _load_trades()
    row = df[df["trade_id"] == tid]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"trade_id={trade_id} not found")
    r = row.iloc[0]
    return {
        "trade_id": trade_id,
        "entry_slippage_call_usd": float(r.get("entry_slippage_call_usd", 0)),
        "entry_slippage_put_usd":  float(r.get("entry_slippage_put_usd", 0)),
        "entry_brokerage_call_usd": float(r.get("entry_brokerage_call_usd", 0)),
        "entry_brokerage_put_usd":  float(r.get("entry_brokerage_put_usd", 0)),
        "total_entry_cost_usd": float(r.get("total_entry_cost_usd", 0)),
        "credit_usd": float(r.get("credit_usd", 0)),
        "margin_used_usd_at_entry": (
            float(r["margin_used_usd_at_entry"])
            if pd.notna(r.get("margin_used_usd_at_entry")) else None
        ),
    }


@router.get("/meta")
def get_meta():
    """Return the universe of dimension values for filter dropdowns."""
    df = _load_trades()
    return {
        "n_trades_total": len(df),
        "fridays": sorted(df["friday_date_ist"].dropna().unique().tolist()),
        "expiries": sorted(df["expiry_date"].dropna().unique().tolist()),
        "deltas": sorted(df["delta_target"].dropna().unique().tolist()),
        "entry_hours": sorted(df["entry_hour_ist"].dropna().unique().tolist()),
        "iv_bands": sorted(df["entry_atm_iv_band"].dropna().unique().tolist()),
        "dte_buckets": sorted(df["dte_bucket"].dropna().unique().tolist()),
        "ivp_buckets": sorted(df["ivp_bucket"].dropna().unique().tolist()),
        "patterns": sorted(df["ctx_pattern"].dropna().unique().tolist())
                    if "ctx_pattern" in df.columns else [],
        "gex_regimes": sorted(df["ctx_gex_regime"].dropna().unique().tolist())
                       if "ctx_gex_regime" in df.columns else [],
    }

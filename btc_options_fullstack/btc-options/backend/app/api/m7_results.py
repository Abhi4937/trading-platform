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

# Lazy module-level cache — auto-reloads when the parquet file changes on disk.
_TRADES_DF: Optional[pd.DataFrame] = None
_TRADES_MTIME: float = 0.0

# Exit derivation cache — keyed by (exit_rule json, trades_mtime).
# Each entry is the FULL merged DataFrame (all trades, no filters) for that exit_rule.
# Filtering happens in pandas on the cached frame, avoiding repeat DuckDB scans.
_EXIT_CACHE: dict[tuple[str, float], pd.DataFrame] = {}


def _load_trades() -> pd.DataFrame:
    """Prefer m7_trades_enriched.parquet (with calibration_v2 join columns)
    when present; fall back to plain m7_trades.parquet.
    Re-reads from disk whenever the file's mtime changes (backfill writes
    incremental snapshots every 5 Fridays)."""
    global _TRADES_DF, _TRADES_MTIME
    path = TRADES_ENRICHED_PATH if os.path.exists(TRADES_ENRICHED_PATH) else TRADES_PATH
    if not os.path.exists(path):
        raise HTTPException(
            status_code=503,
            detail=f"m7_trades.parquet missing under {M7_BASE_DIR}; "
                   f"run `python -m app.analytics.m7_batch_backtester` first.",
        )
    mtime = os.path.getmtime(path)
    if _TRADES_DF is None or mtime != _TRADES_MTIME:
        # Trades changed → exit-derivation cache is stale
        _EXIT_CACHE.clear()
        _TRADES_DF = pd.read_parquet(path)
        # Derive expiry_bucket from dte_days (not stored by backtester)
        if "expiry_bucket" not in _TRADES_DF.columns and "dte_days" in _TRADES_DF.columns:
            _TRADES_DF["expiry_bucket"] = pd.cut(
                _TRADES_DF["dte_days"],
                bins=[0, 1.5, 2.5, 5, 10, 20, 45, float("inf")],
                labels=["current (Sat)", "next (Sun)", "next_to_next (Mon)",
                        "weekly (7d)", "biweekly (14d)", "monthly (30d)", "quarterly"],
            ).astype(str)
        _add_entry_skew_columns(_TRADES_DF)
        _TRADES_MTIME = mtime
        log.info("M7 trades reloaded: %d rows from %s", len(_TRADES_DF), path)
    return _TRADES_DF


def _add_entry_skew_columns(df: pd.DataFrame) -> None:
    """Add entry-time skew columns + bucketed versions in-place.

    Sign conventions (call − put everywhere):
      delta_skew      = |call_entry_delta| − |put_entry_delta|
                        positive  → call is closer to ATM (CE leg has more directional risk)
                        negative  → put is closer to ATM
      iv_skew_pct     = (call_entry_iv − put_entry_iv) × 100, in IV percentage points
                        positive  → call IV richer than put IV (rare; usually negative for BTC)
                        negative  → put IV richer (typical "negative skew")
      premium_skew_usd = call_entry_mark − put_entry_mark
      premium_skew_pct = premium_skew_usd / mean(call_entry_mark, put_entry_mark)
                        ∈ [-2, 2] roughly. Independent of absolute premium scale.

    Buckets are coarse so heatmap cells have enough trades; the raw cols are
    available for filtering at finer granularity if needed later.
    """
    needed = {"call_entry_delta", "put_entry_delta",
              "call_entry_iv", "put_entry_iv",
              "call_entry_mark", "put_entry_mark"}
    if not needed.issubset(df.columns):
        return  # M7 trades parquet missing per-leg cols; skew cols left absent

    df["delta_skew"] = df["call_entry_delta"].abs() - df["put_entry_delta"].abs()
    df["iv_skew_pct"] = (df["call_entry_iv"] - df["put_entry_iv"]) * 100.0
    df["premium_skew_usd"] = df["call_entry_mark"] - df["put_entry_mark"]
    avg_mark = (df["call_entry_mark"] + df["put_entry_mark"]) / 2.0
    df["premium_skew_pct"] = (df["premium_skew_usd"] / avg_mark).where(avg_mark > 0)

    # Coarse buckets keyed off the signed diff; "call_richer" means the CE leg
    # carries more (delta / IV / premium) than the PE leg.
    df["delta_skew_bucket"] = pd.cut(
        df["delta_skew"],
        bins=[-1.0, -0.05, -0.02, 0.02, 0.05, 1.0],
        labels=["put_richer_strong", "put_richer", "balanced", "call_richer", "call_richer_strong"],
    ).astype(str)
    df["iv_skew_bucket"] = pd.cut(
        df["iv_skew_pct"],
        bins=[-1000, -5, -2, 2, 5, 1000],
        labels=["put_iv_strong", "put_iv", "balanced", "call_iv", "call_iv_strong"],
    ).astype(str)
    df["premium_skew_bucket"] = pd.cut(
        df["premium_skew_pct"],
        bins=[-3, -0.30, -0.10, 0.10, 0.30, 3],
        labels=["put_premium_strong", "put_premium", "balanced", "call_premium", "call_premium_strong"],
    ).astype(str)


def _duckdb_conn() -> duckdb.DuckDBPyConnection:
    """Per-call DuckDB connection (cheap)."""
    return duckdb.connect()


# ── Filters ───────────────────────────────────────────────────────────────────

_TRADE_FILTER_COLS = {
    "delta_target": float,
    "is_straddle": bool,
    "expiry_date": str,
    "expiry_bucket": str,
    "entry_atm_iv_band": str,
    "entry_hour_ist": int,
    "dte_bucket": str,
    "spot_bucket": str,
    "ivp_bucket": str,
    "ctx_pattern": str,
    "ctx_gex_regime": str,
    "friday_date_ist": str,
    "iv_skew_bucket": str,
    "delta_skew_bucket": str,
    "premium_skew_bucket": str,
    "leg_winner": str,
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
    expiry_bucket: Optional[str] = None,
    iv_skew_bucket: Optional[str] = None,
    delta_skew_bucket: Optional[str] = None,
    premium_skew_bucket: Optional[str] = None,
    leg_winner: Optional[str] = None,
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
        "expiry_bucket": expiry_bucket,
        "iv_skew_bucket": iv_skew_bucket,
        "delta_skew_bucket": delta_skew_bucket,
        "premium_skew_bucket": premium_skew_bucket,
        "leg_winner": leg_winner,
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

    margin_target_pct and max_profit_pct are evaluated AFTER deducting entry
    slippage from gross P&L — so "10% on margin" means the trader's on-screen
    P&L (after the entry slippage hit) reaches 10% of margin, not the raw
    mid-mark gross. Entry brokerage is NOT subtracted here (it's a flat fee
    that doesn't change the option's mark on screen).
    """
    parts = []
    # gross_pnl after entry slippage. References t.* — caller must ensure
    # the trades projection includes entry_slippage_call_usd / put_usd.
    pnl_after_slip = (
        "(p.gross_pnl_usd - t.entry_slippage_call_usd - t.entry_slippage_put_usd)"
    )
    if rule.get("max_profit_pct") is not None:
        pct = float(rule['max_profit_pct'])
        parts.append(
            f"({pnl_after_slip} >= t.credit_usd * {pct / 100.0} AND t.credit_usd > 0)"
        )
    if rule.get("margin_target_pct") is not None:
        pct = float(rule['margin_target_pct'])
        parts.append(
            f"({pnl_after_slip} >= t.margin_used_usd_at_entry * {pct / 100.0} "
            f"AND t.margin_used_usd_at_entry > 0)"
        )
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

    Cached: the expensive DuckDB scan is computed once per `exit_rule` for ALL
    trades, then filtered in pandas. Different exit_rules get separate cache
    entries; first call for each new rule is slow (~5–15s), subsequent calls
    with same rule + any filter combo are instant.
    """
    # Ensure trades cache is fresh before computing the cache key.
    _load_trades()
    rule_key = (json.dumps(exit_rule or {}, sort_keys=True), _TRADES_MTIME)
    full = _EXIT_CACHE.get(rule_key)
    if full is None:
        full = _compute_all_exits(exit_rule)
        _EXIT_CACHE[rule_key] = full
        log.info("M7 exit cache populated for rule=%s (%d trades)",
                 rule_key[0][:80], len(full))
    if full.empty:
        return full
    return _apply_filters(full, filters)


def _compute_all_exits(exit_rule: dict) -> pd.DataFrame:
    """Compute exit outcomes for ALL trades under `exit_rule` (no filters).
    This is the expensive DuckDB scan. Result is cached by `_derive_exits`.

    Returns a DataFrame with all trade-level context columns + exit columns:
    [exit_ts, exit_reason, gross_pnl_usd, net_pnl_estimate_usd,
    pnl_pct_of_credit, pnl_pct_of_margin, exit_call_mark, exit_put_mark, exit_spot].
    """
    trades = _load_trades()
    if trades.empty:
        return pd.DataFrame()

    # fixed_exit_hour_ist: per-trade Saturday exit at a given IST hour.
    # Formula: target_ts = friday_midnight_utc + 86400 + hour*3600 - 19800
    # (86400 = one day, -19800 = IST offset 5.5h, so Sat H:MM IST → Sat (H-5.5)h UTC)
    #
    # When rule-based exits (premium_sl_pct / max_profit_pct / margin_target_pct)
    # are also set, they fire FIRST if their trigger ts is at or before the
    # fixed hour ts; otherwise we fall back to the fixed hour. This makes
    # "Exit hour" act as a hard cap rather than an absolute override.
    if exit_rule.get("fixed_exit_hour_ist") is not None:
        hour = float(exit_rule["fixed_exit_hour_ist"])
        offset_secs = int(86400 + hour * 3600 - 19800)
        t = trades.copy()
        t["_target_ts"] = t["friday_date_ist"].map(
            lambda d: int(pd.Timestamp(str(d), tz="UTC").timestamp()) + offset_secs
        )
        conn = _duckdb_conn()
        conn.register("_trade_targets", t[["trade_id", "_target_ts"]])

        # Per-trade fixed-hour exit ts (last path row at or before the hour)
        hour_sql = f"""
        SELECT p.trade_id, MAX(p.ts) AS hour_exit_ts
        FROM read_parquet('{PATHS_GLOB}', hive_partitioning=true) p
        JOIN _trade_targets ft ON p.trade_id = ft.trade_id
        WHERE p.ts <= ft._target_ts
        GROUP BY p.trade_id
        """

        # Per-trade rule-trigger ts (first ts where SL/profit/margin fires,
        # bounded to the hour cap). Empty if no rules set.
        pred = _exit_rule_sql_predicate(exit_rule)
        if pred:
            trades_path = (TRADES_ENRICHED_PATH
                           if os.path.exists(TRADES_ENRICHED_PATH) else TRADES_PATH)
            triggers_sql = f"""
            SELECT p.trade_id, MIN(p.ts) AS rule_ts
            FROM read_parquet('{PATHS_GLOB}', hive_partitioning=true) p
            JOIN read_parquet('{trades_path}') t ON p.trade_id = t.trade_id
            JOIN _trade_targets ft ON p.trade_id = ft.trade_id
            WHERE p.ts <= ft._target_ts AND ({pred})
            GROUP BY p.trade_id
            """
        else:
            triggers_sql = "SELECT CAST(NULL AS BIGINT) AS trade_id, CAST(NULL AS BIGINT) AS rule_ts WHERE 1=0"

        sql = f"""
        WITH hour_exits AS ({hour_sql}),
             rule_exits AS ({triggers_sql}),
             chosen AS (
               SELECT h.trade_id,
                      COALESCE(LEAST(r.rule_ts, h.hour_exit_ts), h.hour_exit_ts) AS exit_ts,
                      CASE WHEN r.rule_ts IS NOT NULL AND r.rule_ts <= h.hour_exit_ts
                           THEN 'rule_trigger' ELSE 'fixed_hour_ist' END AS exit_reason
               FROM hour_exits h LEFT JOIN rule_exits r USING (trade_id)
             )
        SELECT c.trade_id, c.exit_ts, c.exit_reason,
               p.spot AS exit_spot, p.call_mark AS exit_call_mark,
               p.put_mark AS exit_put_mark,
               p.gross_pnl_usd, p.pnl_pct_of_credit, p.pnl_pct_of_margin
        FROM chosen c
        JOIN read_parquet('{PATHS_GLOB}', hive_partitioning=true) p
          ON p.trade_id = c.trade_id AND p.ts = c.exit_ts
        """
        exits = conn.execute(sql).df()
        conn.close()

    # If a fixed exit timestamp is given, simply look up that row.
    elif exit_rule.get("fixed_exit_ts") is not None:
        fix_ts = int(exit_rule["fixed_exit_ts"])
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
        # Trades-side projection: marks for premium_sl, slippage + credit + margin
        # for the entry-slippage-adjusted gross-vs-target comparison.
        meta_sql = """
        SELECT trade_id, call_entry_mark, put_entry_mark,
               entry_slippage_call_usd, entry_slippage_put_usd,
               credit_usd, margin_used_usd_at_entry
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
        "trade_id", "friday_date_ist", "entry_ts_utc",
        "entry_hour_ist", "entry_time_label",
        "expiry_date", "delta_target", "is_straddle",
        "entry_atm_iv", "entry_atm_iv_pct", "entry_atm_iv_band",
        "ctx_atm_iv_7d",  # Friday-level reference IV (per M3, 7d-tenor)
        "spot_at_entry", "credit_usd", "total_entry_cost_usd",
        "entry_slippage_call_usd", "entry_slippage_put_usd",  # for MTM (slip-only)
        "margin_used_usd_at_entry", "dte_bucket", "expiry_bucket", "spot_bucket",
        "ivp_bucket", "ctx_pattern", "ctx_gex_regime", "dte_days",
        "call_strike", "put_strike", "quantity_lots",
        # Per-leg entry data — needed for Chunk 1 (leg attribution + skew analysis)
        "call_entry_mark", "put_entry_mark",
        "call_entry_iv",   "put_entry_iv",
        "call_entry_delta","put_entry_delta",
        # Skew cols (derived in _add_entry_skew_columns at load time)
        "delta_skew", "iv_skew_pct", "premium_skew_usd", "premium_skew_pct",
        "iv_skew_bucket", "delta_skew_bucket", "premium_skew_bucket",
    ]
    keep_trade_cols = [c for c in keep_trade_cols if c in trades.columns]
    merged = trades[keep_trade_cols].merge(exits, on="trade_id", how="inner")

    # Per-trade peak/trough gross P&L during the trade's ACTUAL hold period
    # (entry → exit_ts), NOT over the full path to Sat 17:30. This matters
    # when an SL/profit/margin rule (or a fixed exit hour) closes the trade
    # early — the path keeps simulating after but the trader exited.
    #
    # Also computes per-leg max/min MTM during the same window. Per-leg MTM
    # at minute m: leg_pnl_at_m = (entry_mark − path_mark_at_m) × qty × 0.001.
    # Marks are already USD-per-BTC (not USD-per-contract); CONTRACT_VALUE
    # = 0.001 BTC/contract converts to USD per leg. This matches the
    # m7_batch_backtester gross_pnl formula at line 680 of m7_batch_backtester.py.
    # The trades-side cols call_entry_mark / put_entry_mark / quantity_lots
    # are passed in via the registered DataFrame so DuckDB can evaluate the
    # formula in-SQL.
    leg_meta_cols = ["trade_id", "exit_ts",
                     "call_entry_mark", "put_entry_mark", "quantity_lots"]
    have_leg_meta = all(c in merged.columns for c in leg_meta_cols)
    conn = _duckdb_conn()
    if have_leg_meta:
        conn.register("_trade_exits",
                      merged[leg_meta_cols].rename(columns={
                          "exit_ts":         "_exit_ts",
                          "call_entry_mark": "_c_entry",
                          "put_entry_mark":  "_p_entry",
                          "quantity_lots":   "_qty",
                      }))
        mtm_sql = f"""
        SELECT p.trade_id,
               MAX(p.gross_pnl_usd) AS max_gross_pnl_usd,
               MIN(p.gross_pnl_usd) AS min_gross_pnl_usd,
               arg_max(p.ts, p.gross_pnl_usd) AS ts_at_max_mtm,
               arg_min(p.ts, p.gross_pnl_usd) AS ts_at_min_mtm,
               MAX( (e._c_entry - p.call_mark) * e._qty * 0.001 )
                   AS call_leg_max_mtm_usd,
               MIN( (e._c_entry - p.call_mark) * e._qty * 0.001 )
                   AS call_leg_min_mtm_usd,
               MAX( (e._p_entry - p.put_mark)  * e._qty * 0.001 )
                   AS put_leg_max_mtm_usd,
               MIN( (e._p_entry - p.put_mark)  * e._qty * 0.001 )
                   AS put_leg_min_mtm_usd
        FROM read_parquet('{PATHS_GLOB}', hive_partitioning=true) p
        JOIN _trade_exits e ON p.trade_id = e.trade_id
        WHERE p.ts <= e._exit_ts
        GROUP BY p.trade_id
        """
    else:
        # Fallback for old schemas without per-leg entry marks
        conn.register("_trade_exits",
                      merged[["trade_id", "exit_ts"]].rename(columns={"exit_ts": "_exit_ts"}))
        mtm_sql = f"""
        SELECT p.trade_id,
               MAX(p.gross_pnl_usd) AS max_gross_pnl_usd,
               MIN(p.gross_pnl_usd) AS min_gross_pnl_usd,
               arg_max(p.ts, p.gross_pnl_usd) AS ts_at_max_mtm,
               arg_min(p.ts, p.gross_pnl_usd) AS ts_at_min_mtm
        FROM read_parquet('{PATHS_GLOB}', hive_partitioning=true) p
        JOIN _trade_exits e ON p.trade_id = e.trade_id
        WHERE p.ts <= e._exit_ts
        GROUP BY p.trade_id
        """
    mtm_df = conn.execute(mtm_sql).df()
    conn.close()
    merged = merged.merge(mtm_df, on="trade_id", how="left")

    # Per-leg P&L at exit time (Chunk 1: leg attribution).
    # SELL premium → leg_pnl = (entry_mark − exit_mark) × qty × 0.001.
    # Marks are USD-per-BTC, contract size = 0.001 BTC; product is USD.
    # Matches m7_batch_backtester's `gross_pnl` formula. Sum of per-leg
    # equals path's gross_pnl_usd within FP rounding (validated as Chunk 1
    # historical-validation gate).
    if have_leg_meta and "exit_call_mark" in merged.columns:
        merged["call_leg_pnl_usd"] = (
            (merged["call_entry_mark"] - merged["exit_call_mark"])
            * merged["quantity_lots"] * 0.001
        )
        merged["put_leg_pnl_usd"] = (
            (merged["put_entry_mark"] - merged["exit_put_mark"])
            * merged["quantity_lots"] * 0.001
        )
        merged["leg_pnl_diff_usd"] = (
            merged["call_leg_pnl_usd"] - merged["put_leg_pnl_usd"]
        )
        # leg_winner: which leg(s) ended in profit. SELL strangle → a leg "wins"
        # when its premium decayed (entry_mark > exit_mark, leg_pnl > 0).
        c_pos = merged["call_leg_pnl_usd"] > 0
        p_pos = merged["put_leg_pnl_usd"] > 0
        merged["leg_winner"] = np.select(
            [c_pos & p_pos, c_pos & ~p_pos, ~c_pos & p_pos],
            ["both", "call_only", "put_only"],
            default="neither",
        )
        # Boolean indicator columns for share metrics. Pandas 2.x excludes
        # grouping columns from subframes inside .apply(), which broke
        # `(g["leg_winner"] == X).mean()` when leg_winner was the group axis.
        # Boolean cols aggregated via `mean()` work in all groupby contexts.
        merged["_is_both"]      = (c_pos & p_pos).astype(float)
        merged["_is_call_only"] = (c_pos & ~p_pos).astype(float)
        merged["_is_put_only"]  = (~c_pos & p_pos).astype(float)
        merged["_is_neither"]   = (~c_pos & ~p_pos).astype(float)

    # Compute EXACT exit-side slippage + brokerage per leg using exit marks
    # (no longer approximating exit costs as equal to entry costs).
    _add_exit_costs(merged)

    # Net P&L = gross - (entry_costs + exit_costs), each computed exactly.
    merged["net_pnl_estimate_usd"] = (
        merged["gross_pnl_usd"]
        - merged["total_entry_cost_usd"]
        - merged["total_exit_cost_usd"]
    )
    merged["is_win"] = merged["net_pnl_estimate_usd"] > 0

    # MTM (peak / trough / exit-time) reflects what's on the option mark
    # while the position is open: subtract ONLY entry slippage. Entry
    # brokerage is a flat fee charged at trade time; it doesn't change the
    # mark itself, so we don't bake it into MTM. Same with exit costs (not
    # paid until you actually close). `net_pnl_estimate_usd` (below) is the
    # fully-realized number that subtracts all four cost components.
    entry_slip = (
        merged.get("entry_slippage_call_usd", 0)
        + merged.get("entry_slippage_put_usd", 0)
    )
    merged["max_mtm_usd"] = merged["max_gross_pnl_usd"] - entry_slip
    merged["min_mtm_usd"] = merged["min_gross_pnl_usd"] - entry_slip
    # Relative time of peak/trough within the (entry_ts → exit_ts) window.
    # 0 = at entry, 1 = at exit. Lets us plot "when the best/worst moment
    # happened" on a normalized axis so trades of different durations align.
    if "entry_ts_utc" in merged.columns:
        duration = (merged["exit_ts"] - merged["entry_ts_utc"]).astype(float)
        valid = duration > 0
        merged["rel_time_max_mtm"] = (
            (merged["ts_at_max_mtm"] - merged["entry_ts_utc"]) / duration
        ).where(valid).clip(lower=0.0, upper=1.0)
        merged["rel_time_min_mtm"] = (
            (merged["ts_at_min_mtm"] - merged["entry_ts_utc"]) / duration
        ).where(valid).clip(lower=0.0, upper=1.0)
    # Exit-time MTM = the on-screen P&L at the moment of exit, same convention
    # as max/min MTM (only entry slippage subtracted). This is what shows on
    # the option mark right before you click "close".
    merged["exit_mtm_usd"] = merged["gross_pnl_usd"] - entry_slip

    # Per-trade ratios (kept as decimal — UI multiplies by 100 for display).
    margin = merged["margin_used_usd_at_entry"]
    credit = merged["credit_usd"]
    merged["pct_return_on_margin"] = (
        merged["net_pnl_estimate_usd"] / margin
    ).where(margin > 0)
    merged["pct_return_on_credit"] = (
        merged["net_pnl_estimate_usd"] / credit
    ).where(credit > 0)

    return merged


def _add_exit_costs(df: pd.DataFrame) -> None:
    """Add exit-side slippage + brokerage columns in-place using per-trade
    exit marks. Mirrors the cost config used by m7_batch_backtester at entry:
    slippage smart mode, brokerage rate=offer, no referral."""
    from app.services.costs import (
        slippage_dollars_per_side, compute_brokerage_one_side,
    )

    def _row_exit_cost(r):
        spot = float(r["exit_spot"])
        ts = int(r["exit_ts"])
        qty = int(r.get("quantity_lots", 100) or 100)

        c_mark = float(r["exit_call_mark"]); c_strike = float(r["call_strike"])
        p_mark = float(r["exit_put_mark"]);  p_strike = float(r["put_strike"])

        c_slip = slippage_dollars_per_side(
            True, "smart", 5.0, 1.0, spot, c_mark, c_strike, True, qty, ts)
        p_slip = slippage_dollars_per_side(
            True, "smart", 5.0, 1.0, spot, p_mark, p_strike, False, qty, ts)
        c_brk = compute_brokerage_one_side(spot, c_mark, qty, "offer", False)
        p_brk = compute_brokerage_one_side(spot, p_mark, qty, "offer", False)
        return c_slip, p_slip, c_brk, p_brk

    costs = df.apply(_row_exit_cost, axis=1, result_type="expand")
    df["exit_slippage_call_usd"]  = costs[0].astype(float)
    df["exit_slippage_put_usd"]   = costs[1].astype(float)
    df["exit_brokerage_call_usd"] = costs[2].astype(float)
    df["exit_brokerage_put_usd"]  = costs[3].astype(float)
    df["total_exit_cost_usd"] = (
        df["exit_slippage_call_usd"] + df["exit_slippage_put_usd"]
        + df["exit_brokerage_call_usd"] + df["exit_brokerage_put_usd"]
    )


# ── Metric scoring ────────────────────────────────────────────────────────────

# Metrics that operate on a subset (winners-only or losers-only). The lambdas
# get the full sub-DataFrame for each group and return a single number.
_WINNER_METRICS = {
    "avg_win_usd":            lambda g: g.loc[g["is_win"], "net_pnl_estimate_usd"].mean(),
    "max_win_usd":            lambda g: g.loc[g["is_win"], "net_pnl_estimate_usd"].max(),
    "avg_max_mtm_winners":    lambda g: g.loc[g["is_win"], "max_mtm_usd"].mean(),
    "avg_min_mtm_winners":    lambda g: g.loc[g["is_win"], "min_mtm_usd"].mean(),
    "max_mtm_winners":        lambda g: g.loc[g["is_win"], "max_mtm_usd"].max(),
    "min_mtm_winners":        lambda g: g.loc[g["is_win"], "min_mtm_usd"].min(),
    # Exit-time MTM among winners only (gross − entry costs only)
    "avg_win_mtm":            lambda g: g.loc[g["is_win"], "exit_mtm_usd"].mean(),
    "largest_win_mtm":        lambda g: g.loc[g["is_win"], "exit_mtm_usd"].max(),
    # Per-trade ROI restricted to winners (per-trade ratio, then mean)
    "avg_pct_return_on_margin_winners": lambda g: g.loc[g["is_win"], "pct_return_on_margin"].mean(),
    "avg_pct_return_on_credit_winners": lambda g: g.loc[g["is_win"], "pct_return_on_credit"].mean(),
}
_LOSER_METRICS = {
    "avg_loss_usd":           lambda g: g.loc[~g["is_win"], "net_pnl_estimate_usd"].mean(),
    "max_loss_usd":           lambda g: g.loc[~g["is_win"], "net_pnl_estimate_usd"].min(),
    "avg_max_mtm_losers":     lambda g: g.loc[~g["is_win"], "max_mtm_usd"].mean(),
    "avg_min_mtm_losers":     lambda g: g.loc[~g["is_win"], "min_mtm_usd"].mean(),
    "max_mtm_losers":         lambda g: g.loc[~g["is_win"], "max_mtm_usd"].max(),
    "min_mtm_losers":         lambda g: g.loc[~g["is_win"], "min_mtm_usd"].min(),
    # Exit-time MTM among losers only
    "avg_loss_mtm":           lambda g: g.loc[~g["is_win"], "exit_mtm_usd"].mean(),
    "largest_loss_mtm":       lambda g: g.loc[~g["is_win"], "exit_mtm_usd"].min(),
}
# Metrics that are simple column aggregations (mean / sum / size on a column).
_SIMPLE_METRICS = {
    "count":                       ("__size__",                    "size"),
    "win_rate":                    ("is_win",                      "mean"),
    "avg_net_pnl":                 ("net_pnl_estimate_usd",        "mean"),
    "sum_net_pnl":                 ("net_pnl_estimate_usd",        "sum"),
    "avg_gross_pnl":               ("gross_pnl_usd",               "mean"),
    "avg_credit":                  ("credit_usd",                  "mean"),
    "avg_max_theoretical_pnl":     ("credit_usd",                  "mean"),  # alias
    "avg_margin":                  ("margin_used_usd_at_entry",    "mean"),
    "avg_pct_return_on_margin":    ("pct_return_on_margin",        "mean"),
    "avg_pct_return_on_credit":    ("pct_return_on_credit",        "mean"),
    # Exit-time MTM overall (on-screen P&L at exit, only entry costs subtracted)
    "avg_exit_mtm":                ("exit_mtm_usd",                "mean"),
    # Per-leg P&L (Chunk 1 — leg attribution)
    "avg_call_leg_pnl":            ("call_leg_pnl_usd",            "mean"),
    "avg_put_leg_pnl":             ("put_leg_pnl_usd",             "mean"),
    "avg_leg_pnl_diff":            ("leg_pnl_diff_usd",            "mean"),
    "avg_call_leg_max_mtm":        ("call_leg_max_mtm_usd",        "mean"),
    "avg_call_leg_min_mtm":        ("call_leg_min_mtm_usd",        "mean"),
    "avg_put_leg_max_mtm":         ("put_leg_max_mtm_usd",         "mean"),
    "avg_put_leg_min_mtm":         ("put_leg_min_mtm_usd",         "mean"),
    # Skew (entry-time, useful as group-by aggregate)
    "avg_iv_skew_pct":             ("iv_skew_pct",                 "mean"),
    "avg_delta_skew":              ("delta_skew",                  "mean"),
    "avg_premium_skew_usd":        ("premium_skew_usd",            "mean"),
    # Leg-winner outcome shares (Chunk 1) — boolean cols + mean works in all
    # groupby contexts (whether or not leg_winner is itself a group axis).
    "both_share":                  ("_is_both",                    "mean"),
    "call_only_share":             ("_is_call_only",               "mean"),
    "put_only_share":              ("_is_put_only",                "mean"),
    "neither_share":               ("_is_neither",                 "mean"),
}

# Special-case metrics that don't fit the simple "column + agg" pattern.
def _count_rule_trigger(g): return int((g["exit_reason"] == "rule_trigger").sum())
def _count_hard_cap(g):     return int((g["exit_reason"] == "hard_cap").sum())
def _count_losses(g):       return int((~g["is_win"]).sum())
def _count_wins(g):         return int(g["is_win"].sum())

def _max_run_length(mask: pd.Series) -> int:
    """Max consecutive run of True values in a boolean Series."""
    mask = mask.astype(bool).reset_index(drop=True)
    if mask.empty:
        return 0
    # Group consecutive identical values; sum True-runs only.
    runs = (mask != mask.shift()).cumsum()
    run_sums = mask.groupby(runs).sum()
    return int(run_sums.max()) if not run_sums.empty else 0

def _max_consecutive_losses(g):
    # Order by Friday so streaks are chronological
    s = g.sort_values("friday_date_ist", kind="stable")["is_win"]
    return _max_run_length(~s.astype(bool))

def _max_consecutive_sl_hits(g):
    s = g.sort_values("friday_date_ist", kind="stable")["exit_reason"]
    return _max_run_length(s == "rule_trigger")

def _max_consecutive_wins(g):
    s = g.sort_values("friday_date_ist", kind="stable")["is_win"]
    return _max_run_length(s.astype(bool))

def _n_winners_below_avg_min_mtm(g):
    """Count of winning trades whose min_mtm_usd dipped below the group's
    avg_min_mtm_winners. I.e. winners that endured a deeper drawdown than
    typical for that combo before recovering into profit."""
    winners = g.loc[g["is_win"]]
    if winners.empty:
        return 0
    avg_min = winners["min_mtm_usd"].mean()
    if pd.isna(avg_min):
        return 0
    return int((winners["min_mtm_usd"] < avg_min).sum())

def _n_losers_above_avg_max_mtm(g):
    """Count of losing trades whose max_mtm_usd rose above the group's
    avg_max_mtm_losers. I.e. losers that had a higher peak P&L than typical
    before turning into losses (missed exit opportunity)."""
    losers = g.loc[~g["is_win"]]
    if losers.empty:
        return 0
    avg_max = losers["max_mtm_usd"].mean()
    if pd.isna(avg_max):
        return 0
    return int((losers["max_mtm_usd"] > avg_max).sum())

_SPECIAL_METRICS = {
    "n_rule_trigger": _count_rule_trigger,  # # of trades that hit any rule (SL/max-profit/margin)
    "n_hard_cap":     _count_hard_cap,      # # of trades that exited at Sat 17:30 (no rule fired)
    "n_losses":       _count_losses,        # # of losing trades
    "n_wins":         _count_wins,          # # of winning trades
    "max_consec_losses":  _max_consecutive_losses,   # longest streak of losing trades (chronological)
    "max_consec_wins":    _max_consecutive_wins,     # longest streak of winning trades
    "max_consec_sl_hits": _max_consecutive_sl_hits,  # longest streak of rule-triggered (SL) exits
    "n_winners_below_avg_min_mtm": _n_winners_below_avg_min_mtm,  # winners w/ worse-than-avg drawdown
    "n_losers_above_avg_max_mtm":  _n_losers_above_avg_max_mtm,   # losers w/ better-than-avg peak
}

ALL_METRICS = (list(_SIMPLE_METRICS) + list(_WINNER_METRICS)
               + list(_LOSER_METRICS) + list(_SPECIAL_METRICS))


def _metric_score(grouped, metric: str) -> pd.Series:
    """Compute `metric` on a DataFrameGroupBy. Returns a Series indexed by
    group key, suitable for downstream `.idxmax()` / DataFrame assembly."""
    if metric in _SIMPLE_METRICS:
        col, op = _SIMPLE_METRICS[metric]
        if op == "size":
            return grouped.size()
        return getattr(grouped[col], op)()
    if metric in _WINNER_METRICS:
        return grouped.apply(_WINNER_METRICS[metric])
    if metric in _LOSER_METRICS:
        return grouped.apply(_LOSER_METRICS[metric])
    if metric in _SPECIAL_METRICS:
        return grouped.apply(_SPECIAL_METRICS[metric])
    raise HTTPException(status_code=400,
                        detail=f"Unknown metric: {metric}. "
                               f"Valid: {sorted(ALL_METRICS)}")


def _round_score(metric: str, val: float) -> float:
    """Round a metric value to a sensible precision for the wire."""
    if val is None or (isinstance(val, float) and (val != val)):
        return val
    if metric in ("win_rate", "avg_pct_return_on_margin", "avg_pct_return_on_credit",
                  "avg_pct_return_on_margin_winners", "avg_pct_return_on_credit_winners") \
            or metric.endswith("_share"):  # leg-winner outcome shares
        return round(float(val), 6)
    if metric in ("count", "n_rule_trigger", "n_hard_cap", "n_losses", "n_wins",
                  "max_consec_losses", "max_consec_wins", "max_consec_sl_hits",
                  "n_winners_below_avg_min_mtm", "n_losers_above_avg_max_mtm"):
        return int(val)
    return round(float(val), 4)


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
    expiry_bucket: Optional[str] = None,
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
                              ctx_gex_regime, friday_date_ist, expiry_bucket)
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
    expiry_bucket: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
):
    df = _apply_filters(_load_trades(), {
        "delta_target": delta_target, "is_straddle": is_straddle,
        "expiry_date": expiry_date, "expiry_bucket": expiry_bucket,
        "entry_atm_iv_band": entry_atm_iv_band,
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
    expiry_bucket: Optional[str] = None,
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
                              friday_date_ist, expiry_bucket)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"rows": [], "metric": metric, "dimensions": dimensions.split(",")}

    dims = [d.strip() for d in dimensions.split(",") if d.strip()]
    for d in dims:
        if d not in derived.columns:
            raise HTTPException(status_code=400, detail=f"Unknown dimension: {d}")

    grp = derived.groupby(dims, dropna=False)
    score = _metric_score(grp, metric)
    out = score.reset_index(name="value")
    out["value"] = out["value"].apply(lambda v: _round_score(metric, v))
    out["n_trades"] = grp.size().values
    return {"rows": _to_records(out), "metric": metric, "dimensions": dims}


@router.get("/heatmap")
def get_heatmap(
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
    delta_target: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
):
    """Entry-time × Friday heatmap (one cell per friday_date × entry_hour)."""
    filters = _query_filters(delta_target, None, expiry_date,
                              entry_atm_iv_band, None, None, None, None, None,
                              None, None, expiry_bucket)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"rows": []}
    grp = derived.groupby(["entry_hour_ist", "friday_date_ist"], dropna=False)
    score = _metric_score(grp, metric)
    out = score.reset_index(name="value")
    out["value"] = out["value"].apply(lambda v: _round_score(metric, v))
    out["n_trades"] = grp.size().values
    return {"rows": _to_records(out), "metric": metric}


@router.get("/missed_fridays")
def get_missed_fridays(
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
):
    """For each Friday NOT represented in any of the 10 IV-band best cells
    (under the same filters + exit rule), return that Friday's own best combo.

    Same logic as /iv_band_summary for picking best cells, then identifies
    orphan Fridays and reports each one's top trade.
    """
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist, expiry_bucket)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"rows": [], "n_missed": 0, "n_total_fridays": 0}

    # Step 1: replicate /iv_band_summary best-cell selection
    dims = ["entry_atm_iv_band", "entry_hour_ist", "expiry_bucket", "delta_target"]
    grp = derived.groupby(dims, dropna=False)
    score = _metric_score(grp, metric)
    n = grp.size()
    df = pd.DataFrame({"score": score, "n_trades": n}).reset_index()

    df_valid = df.dropna(subset=["score"])
    strict = df_valid[df_valid["n_trades"] >= 3]
    strict_idx = strict.groupby("entry_atm_iv_band", dropna=False)["score"].idxmax() if not strict.empty else pd.Index([])
    strict_best = strict.loc[strict_idx] if len(strict_idx) else strict.iloc[0:0]
    covered = set(strict_best["entry_atm_iv_band"].dropna().tolist())
    fallback = df_valid[~df_valid["entry_atm_iv_band"].isin(covered)]
    if not fallback.empty:
        fb_idx = fallback.groupby("entry_atm_iv_band", dropna=False)["score"].idxmax()
        fallback_best = fallback.loc[fb_idx]
        best_cells = pd.concat([strict_best, fallback_best], ignore_index=True)
    else:
        best_cells = strict_best.reset_index(drop=True)

    # Step 2: find Fridays represented in any best cell
    matched_fridays = set()
    for _, c in best_cells.iterrows():
        cell_trades = derived[
            (derived["entry_atm_iv_band"] == c["entry_atm_iv_band"]) &
            (derived["entry_hour_ist"] == c["entry_hour_ist"]) &
            (derived["expiry_bucket"] == c["expiry_bucket"]) &
            (derived["delta_target"] == c["delta_target"])
        ]
        matched_fridays.update(cell_trades["friday_date_ist"].astype(str).unique())

    all_fridays = set(derived["friday_date_ist"].astype(str).unique())
    missed_fridays = sorted(all_fridays - matched_fridays)

    # Step 3: for each missed Friday, find its best combo by net_pnl_estimate_usd
    if not missed_fridays:
        return {"rows": [], "n_missed": 0,
                "n_total_fridays": len(all_fridays),
                "n_matched": len(matched_fridays)}

    missed_df = derived[derived["friday_date_ist"].astype(str).isin(missed_fridays)].copy()
    # Pick the row with max net_pnl_estimate_usd per Friday
    idx = missed_df.groupby("friday_date_ist")["net_pnl_estimate_usd"].idxmax()
    best_per_friday = missed_df.loc[idx].copy()

    # Sort by Friday date
    best_per_friday = best_per_friday.sort_values("friday_date_ist").reset_index(drop=True)

    # Project the columns we want to return
    keep_cols = [
        "friday_date_ist", "entry_hour_ist", "expiry_bucket", "delta_target",
        "entry_atm_iv_band", "entry_atm_iv_pct",
        "credit_usd", "margin_used_usd_at_entry",
        "net_pnl_estimate_usd", "gross_pnl_usd",
        "max_mtm_usd", "min_mtm_usd", "exit_mtm_usd",
        "is_win", "exit_reason",
    ]
    keep_cols = [c for c in keep_cols if c in best_per_friday.columns]
    out = best_per_friday[keep_cols].copy()

    # Round numeric cols
    for c in ["entry_atm_iv_pct", "credit_usd", "margin_used_usd_at_entry",
              "net_pnl_estimate_usd", "gross_pnl_usd",
              "max_mtm_usd", "min_mtm_usd", "exit_mtm_usd"]:
        if c in out.columns:
            out[c] = out[c].apply(
                lambda v: round(float(v), 2) if pd.notna(v) else None)

    return {
        "rows": _to_records(out),
        "n_missed": len(missed_fridays),
        "n_total_fridays": len(all_fridays),
        "n_matched": len(matched_fridays),
    }


@router.get("/iv_band_summary")
def get_iv_band_summary(
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
):
    """For each IV band, find the best (entry_hour, expiry, delta) combo
    by the chosen metric. Headline 'answer the question' table."""
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist, expiry_bucket)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"rows": []}

    dims = ["entry_atm_iv_band", "entry_hour_ist", "expiry_bucket", "delta_target"]
    grp = derived.groupby(dims, dropna=False)
    score = _metric_score(grp, metric)
    n = grp.size()
    df = pd.DataFrame({"score": score, "n_trades": n}).reset_index()

    # Best combo per IV band — prefer n>=3 for stability, but fall back
    # per-band so every band that has any data shows up.
    # Drop NaN scores (e.g. winner/loser-only metrics where the band has 0 of those),
    # otherwise pandas .idxmax() raises on all-NA groups.
    df_valid = df.dropna(subset=["score"])
    strict = df_valid[df_valid["n_trades"] >= 3]
    strict_idx = strict.groupby("entry_atm_iv_band", dropna=False)["score"].idxmax() if not strict.empty else pd.Index([])
    strict_best = strict.loc[strict_idx] if len(strict_idx) else strict.iloc[0:0]
    covered = set(strict_best["entry_atm_iv_band"].dropna().tolist())
    fallback = df_valid[~df_valid["entry_atm_iv_band"].isin(covered)]
    if not fallback.empty:
        fb_idx = fallback.groupby("entry_atm_iv_band", dropna=False)["score"].idxmax()
        fallback_best = fallback.loc[fb_idx]
        best = pd.concat([strict_best, fallback_best], ignore_index=True)
    else:
        best = strict_best.reset_index(drop=True)

    # Look up additional metrics for the same chosen combos. For each picked
    # row, restrict `derived` to the same (iv_band, entry_hour, expiry, delta)
    # and compute every "extra" metric on that single sub-group.
    EXTRA_METRICS = [
        "avg_net_pnl", "win_rate",
        "avg_loss_usd", "avg_win_usd",
        "avg_credit", "avg_margin",
        "avg_pct_return_on_margin", "avg_pct_return_on_credit",
        "avg_pct_return_on_margin_winners", "avg_pct_return_on_credit_winners",
        # Winners-only MTM stats
        "avg_max_mtm_winners", "avg_min_mtm_winners",
        "max_mtm_winners", "min_mtm_winners",
        # Losers-only MTM stats
        "avg_max_mtm_losers", "avg_min_mtm_losers",
        "max_mtm_losers", "min_mtm_losers",
        # Net P&L extremes
        "max_loss_usd", "max_win_usd",
        # Exit-time MTM (entry costs only, no exit costs)
        "avg_exit_mtm",
        "avg_win_mtm", "largest_win_mtm",
        "avg_loss_mtm", "largest_loss_mtm",
        # Counts
        "n_rule_trigger", "n_hard_cap", "n_losses", "n_wins",
        # Streaks (chronological by friday_date_ist)
        "max_consec_losses", "max_consec_wins", "max_consec_sl_hits",
        # Outlier counts vs group-average MTM
        "n_winners_below_avg_min_mtm", "n_losers_above_avg_max_mtm",
    ]
    for m in EXTRA_METRICS:
        col = f"_{m}"
        best[col] = float("nan")
    for i, row in best.iterrows():
        sub = derived[
            (derived["entry_atm_iv_band"] == row["entry_atm_iv_band"]) &
            (derived["entry_hour_ist"] == row["entry_hour_ist"]) &
            (derived["expiry_bucket"] == row["expiry_bucket"]) &
            (derived["delta_target"] == row["delta_target"])
        ]
        if sub.empty:
            continue
        sub_grp = sub.groupby(lambda _: 0)  # single group
        for m in EXTRA_METRICS:
            try:
                val = float(_metric_score(sub_grp, m).iloc[0])
            except Exception:
                val = float("nan")
            best.at[i, f"_{m}"] = val
    # Rename the extra columns to public names (drop the leading underscore).
    best = best.rename(columns={f"_{m}": m for m in EXTRA_METRICS})

    # Sort IV bands in natural order (0-20, 20-30, …, 100+)
    def _band_sort_key(b):
        if b == "100+": return 1000
        try: return int(str(b).split("-")[0])
        except (ValueError, AttributeError): return 9999
    best = best.sort_values("entry_atm_iv_band",
                            key=lambda s: s.map(_band_sort_key)).reset_index(drop=True)
    best["score"] = best["score"].apply(lambda v: _round_score(metric, v))
    for m in EXTRA_METRICS:
        if m in best.columns:
            best[m] = best[m].apply(lambda v: _round_score(m, v))
    return {"rows": _to_records(best), "metric": metric}


@router.get("/best_combo_markers")
def get_best_combo_markers(
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
):
    """For each IV band's best (entry_hour × expiry_bucket × delta) combo,
    return per-trade path-marker rows for the path-markers chart:
    relative time of max/min MTM, the MTM values themselves, win/loss, and
    exit reason. Best-combo selection mirrors /iv_band_summary."""
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist, expiry_bucket)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"metric": metric, "bands": []}

    # Step 1: replicate /iv_band_summary best-cell selection
    dims = ["entry_atm_iv_band", "entry_hour_ist", "expiry_bucket", "delta_target"]
    grp = derived.groupby(dims, dropna=False)
    score = _metric_score(grp, metric)
    n = grp.size()
    df = pd.DataFrame({"score": score, "n_trades": n}).reset_index()

    df_valid = df.dropna(subset=["score"])
    strict = df_valid[df_valid["n_trades"] >= 3]
    strict_idx = strict.groupby("entry_atm_iv_band", dropna=False)["score"].idxmax() if not strict.empty else pd.Index([])
    strict_best = strict.loc[strict_idx] if len(strict_idx) else strict.iloc[0:0]
    covered = set(strict_best["entry_atm_iv_band"].dropna().tolist())
    fallback = df_valid[~df_valid["entry_atm_iv_band"].isin(covered)]
    if not fallback.empty:
        fb_idx = fallback.groupby("entry_atm_iv_band", dropna=False)["score"].idxmax()
        fallback_best = fallback.loc[fb_idx]
        best = pd.concat([strict_best, fallback_best], ignore_index=True)
    else:
        best = strict_best.reset_index(drop=True)

    # Sort IV bands in natural order so the chart panels appear top-to-bottom
    # in the same order as the headline IV-band table.
    def _band_sort_key(b):
        if b == "100+": return 1000
        try: return int(str(b).split("-")[0])
        except (ValueError, AttributeError): return 9999
    best = best.sort_values("entry_atm_iv_band",
                            key=lambda s: s.map(_band_sort_key)).reset_index(drop=True)

    # Step 2: for each chosen combo, project per-trade marker rows
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
            (derived["entry_atm_iv_band"] == row["entry_atm_iv_band"]) &
            (derived["entry_hour_ist"] == row["entry_hour_ist"]) &
            (derived["expiry_bucket"] == row["expiry_bucket"]) &
            (derived["delta_target"] == row["delta_target"])
        ]
        if sub.empty:
            continue
        n_wins = int(sub["is_win"].sum()) if "is_win" in sub.columns else 0
        n_losses = int((~sub["is_win"].astype(bool)).sum()) if "is_win" in sub.columns else 0
        # Round numeric fields for wire-friendliness
        sub_proj = sub[keep_cols].copy()
        for c in ("rel_time_max_mtm", "rel_time_min_mtm"):
            if c in sub_proj.columns:
                sub_proj[c] = sub_proj[c].apply(
                    lambda v: round(float(v), 4) if pd.notna(v) else None)
        for c in ("max_mtm_usd", "min_mtm_usd", "exit_mtm_usd", "net_pnl_estimate_usd"):
            if c in sub_proj.columns:
                sub_proj[c] = sub_proj[c].apply(
                    lambda v: round(float(v), 2) if pd.notna(v) else None)
        # Sort trades chronologically inside each band so tooltip / hover lookup
        # is predictable.
        sub_proj = sub_proj.sort_values("friday_date_ist").reset_index(drop=True)

        bands_out.append({
            "entry_atm_iv_band": row["entry_atm_iv_band"],
            "entry_hour_ist": int(row["entry_hour_ist"]),
            "expiry_bucket": row["expiry_bucket"],
            "delta_target": float(row["delta_target"]),
            "n_trades": int(row["n_trades"]),
            "n_wins": n_wins,
            "n_losses": n_losses,
            "trades": _to_records(sub_proj),
        })

    return {"metric": metric, "bands": bands_out}


@router.get("/best_combo")
def get_best_combo(
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
    top_n: int = Query(20, ge=1, le=200),
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
):
    """Top-N (entry_hour × expiry_bucket × delta) combos by metric, given exit rule."""
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist, expiry_bucket)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"rows": []}

    dims = ["entry_hour_ist", "expiry_bucket", "delta_target"]
    grp = derived.groupby(dims, dropna=False)
    score = _metric_score(grp, metric)
    df = pd.DataFrame({"score": score, "n_trades": grp.size()}).reset_index()
    df = df[df["n_trades"] >= 3]
    df = df.sort_values("score", ascending=False).head(top_n)
    df["score"] = df["score"].apply(lambda v: _round_score(metric, v))
    return {"rows": _to_records(df), "metric": metric}


@router.get("/leg_attribution")
def get_leg_attribution(
    exit_rule: Optional[str] = None,
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
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
    sort_by: str = "friday_date_ist",
    sort_dir: str = "desc",
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    """Per-trade leg-level breakdown — Chunk 1 of the Trade Copilot plan.

    For each trade matching `filters` under `exit_rule`, return:
      - skew at entry (delta / IV / premium)
      - per-leg P&L at exit (call_leg_pnl_usd + put_leg_pnl_usd ≡ gross_pnl_usd)
      - per-leg max/min MTM during the hold
      - leg_winner classification (both / call_only / put_only / neither)

    The frontend table renders CE/PE as two stacked rows per trade with
    shared skew + classification cells, so a glance shows which leg paid
    and which one dragged the position.
    """
    filters = _query_filters(
        delta_target, is_straddle, expiry_date,
        entry_atm_iv_band, entry_hour_ist, dte_bucket,
        spot_bucket, ivp_bucket, ctx_pattern, ctx_gex_regime,
        friday_date_ist, expiry_bucket,
        iv_skew_bucket, delta_skew_bucket, premium_skew_bucket, leg_winner,
    )
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"total": 0, "rows": [], "offset": offset, "limit": limit}

    keep_cols = [
        "trade_id", "friday_date_ist", "entry_hour_ist", "entry_time_label",
        "expiry_date", "expiry_bucket", "delta_target", "is_straddle",
        "entry_atm_iv_band", "entry_atm_iv_pct",
        # Per-leg entry context
        "call_strike", "put_strike", "quantity_lots",
        "call_entry_mark", "put_entry_mark",
        "call_entry_iv",   "put_entry_iv",
        "call_entry_delta","put_entry_delta",
        # Per-leg exit context
        "exit_call_mark", "exit_put_mark", "exit_spot",
        # Per-leg outcomes
        "call_leg_pnl_usd", "put_leg_pnl_usd", "leg_pnl_diff_usd",
        "call_leg_max_mtm_usd", "call_leg_min_mtm_usd",
        "put_leg_max_mtm_usd",  "put_leg_min_mtm_usd",
        "leg_winner",
        # Skew
        "delta_skew", "iv_skew_pct", "premium_skew_usd", "premium_skew_pct",
        "iv_skew_bucket", "delta_skew_bucket", "premium_skew_bucket",
        # Position-level outcomes
        "credit_usd", "margin_used_usd_at_entry", "total_entry_cost_usd",
        "total_exit_cost_usd",
        "gross_pnl_usd", "net_pnl_estimate_usd",
        "max_mtm_usd", "min_mtm_usd", "exit_mtm_usd",
        "is_win", "exit_reason", "exit_ts",
    ]
    keep_cols = [c for c in keep_cols if c in derived.columns]
    df = derived[keep_cols].copy()

    if sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=(sort_dir == "asc"),
                            kind="stable")

    total = len(df)
    page = df.iloc[offset:offset + limit].copy()

    # Round numeric cols for wire-friendliness
    for c in ("call_entry_mark", "put_entry_mark",
              "call_entry_iv",   "put_entry_iv",
              "call_entry_delta","put_entry_delta",
              "exit_call_mark",  "exit_put_mark",
              "delta_skew", "iv_skew_pct",
              "premium_skew_usd", "premium_skew_pct"):
        if c in page.columns:
            page[c] = page[c].apply(
                lambda v: round(float(v), 6) if pd.notna(v) else None)
    for c in ("call_leg_pnl_usd", "put_leg_pnl_usd", "leg_pnl_diff_usd",
              "call_leg_max_mtm_usd", "call_leg_min_mtm_usd",
              "put_leg_max_mtm_usd",  "put_leg_min_mtm_usd",
              "credit_usd", "margin_used_usd_at_entry",
              "total_entry_cost_usd", "total_exit_cost_usd",
              "gross_pnl_usd", "net_pnl_estimate_usd",
              "max_mtm_usd", "min_mtm_usd", "exit_mtm_usd",
              "exit_spot", "entry_atm_iv_pct"):
        if c in page.columns:
            page[c] = page[c].apply(
                lambda v: round(float(v), 2) if pd.notna(v) else None)

    return {
        "total": total, "offset": offset, "limit": limit,
        "rows": _to_records(page),
    }


@router.get("/leg_skew_heatmap")
def get_leg_skew_heatmap(
    exit_rule: Optional[str] = None,
    metric: str = "win_rate",
    row_key: str = "iv_skew_bucket",
    col_key: str = "delta_skew_bucket",
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
):
    """2D heatmap on the leg-attribution dataset.

    `row_key` / `col_key` accept any of:
      - iv_skew_bucket / delta_skew_bucket / premium_skew_bucket
      - leg_winner
      - delta_target / entry_hour_ist / entry_atm_iv_band / expiry_bucket / dte_bucket
      - friday_date_ist (use cautiously — 121 cells)

    `metric` accepts any name in ALL_METRICS, including the new
    leg-aware ones (avg_call_leg_pnl, avg_put_leg_pnl, both_share, etc.).
    """
    filters = _query_filters(
        delta_target, is_straddle, expiry_date,
        entry_atm_iv_band, entry_hour_ist, dte_bucket,
        None, ivp_bucket, ctx_pattern, None,
        friday_date_ist, expiry_bucket,
    )
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {"rows": [], "metric": metric,
                "row_key": row_key, "col_key": col_key}

    for k in (row_key, col_key):
        if k not in derived.columns:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown axis: {k}. Available: "
                       f"{sorted(c for c in derived.columns if c.endswith('_bucket') or c == 'leg_winner')}",
            )

    grp = derived.groupby([row_key, col_key], dropna=False)
    score = _metric_score(grp, metric)
    out = score.reset_index(name="value")
    out["value"] = out["value"].apply(lambda v: _round_score(metric, v))
    out["n_trades"] = grp.size().values
    return {
        "rows": _to_records(out),
        "metric": metric,
        "row_key": row_key,
        "col_key": col_key,
    }


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
    # Stable bucket order (left → right = put-leaning → call-leaning) so
    # heatmap axes always sort the same way regardless of which buckets
    # are populated in the current dataset.
    SKEW_ORDER = {
        "delta_skew_bucket": ["put_richer_strong", "put_richer", "balanced",
                              "call_richer", "call_richer_strong"],
        "iv_skew_bucket": ["put_iv_strong", "put_iv", "balanced", "call_iv",
                           "call_iv_strong"],
        "premium_skew_bucket": ["put_premium_strong", "put_premium", "balanced",
                                "call_premium", "call_premium_strong"],
    }
    def _ordered(col: str) -> list:
        if col not in df.columns:
            return []
        present = set(df[col].dropna().unique().tolist())
        return [b for b in SKEW_ORDER[col] if b in present]
    return {
        "n_trades_total": len(df),
        "fridays": sorted(df["friday_date_ist"].dropna().unique().tolist()),
        "expiries": sorted(df["expiry_date"].dropna().unique().tolist()),
        "expiry_buckets": sorted(df["expiry_bucket"].dropna().unique().tolist())
                          if "expiry_bucket" in df.columns else [],
        "deltas": sorted(df["delta_target"].dropna().unique().tolist()),
        "entry_hours": sorted(df["entry_hour_ist"].dropna().unique().tolist()),
        "iv_bands": sorted(df["entry_atm_iv_band"].dropna().unique().tolist()),
        "dte_buckets": sorted(df["dte_bucket"].dropna().unique().tolist()),
        "ivp_buckets": sorted(df["ivp_bucket"].dropna().unique().tolist()),
        "patterns": sorted(df["ctx_pattern"].dropna().unique().tolist())
                    if "ctx_pattern" in df.columns else [],
        "gex_regimes": sorted(df["ctx_gex_regime"].dropna().unique().tolist())
                       if "ctx_gex_regime" in df.columns else [],
        # Chunk 1 — leg attribution: skew bucket universes
        "delta_skew_buckets":   _ordered("delta_skew_bucket"),
        "iv_skew_buckets":      _ordered("iv_skew_bucket"),
        "premium_skew_buckets": _ordered("premium_skew_bucket"),
        # leg_winner classes available post-derivation; static enum
        "leg_winners": ["both", "call_only", "put_only", "neither"],
    }

"""M9 best-combo per-IV-band ranker — Friday weekly/biweekly version.

Mirror of m_month_best_combo.py with these differences:

- Path glob: hive-partitioned by `expiry_type` + `entry_yyyymm`
- Hold-duration menu: 1d/2d/3d/4d/5d/6d (max walk is 6 days) + natural
- Group keys: (expiry_type, iv_band, delta_target, entry_hour_ist,
  entry_minute_ist, hold_duration) — replaces M-Month's
  (trade_cycle, dow, hour, hold_duration)

Composite exit rule: whichever of (premium_sl, max_profit, margin_target,
fixed_hold_duration) fires first. DuckDB CTE uses arg_min(triggered_by, ts)
so exit_reason matches the EARLIEST triggering bar.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import duckdb
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.api.m9_friday_weekly_results import (
    M9_BASE_DIR,
    PATHS_GLOB,
    TRADES_PATH,
    VALID_EXPIRY_TYPES,
    _load_trades,
)

router = APIRouter()
log = logging.getLogger(__name__)

# Hold-duration label → minutes from entry. `None` = natural (full 6-day cap).
HOLD_DURATIONS: dict[str, Optional[int]] = {
    "natural": None,
    "1d": 1 * 1440,
    "2d": 2 * 1440,
    "3d": 3 * 1440,
    "4d": 4 * 1440,
    "5d": 5 * 1440,
    "6d": 6 * 1440,
}

_EXIT_PNL_CACHE: dict[tuple, pd.DataFrame] = {}
_EXIT_PNL_LOCK = threading.Lock()
_EXIT_PNL_CACHE_MAX = 128


def _empty_exit_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "trade_id": pd.Series(dtype="int64"),
        "exit_gross_pnl_usd": pd.Series(dtype="float64"),
        "max_mtm_usd": pd.Series(dtype="float64"),
        "min_mtm_usd": pd.Series(dtype="float64"),
        "n_path_bars": pd.Series(dtype="int64"),
        "exit_ts": pd.Series(dtype="int64"),
        "realized_hold_hours": pd.Series(dtype="float64"),
        "exit_reason": pd.Series(dtype="object"),
    })


def _compute_exit_pnl(
    hold_duration: str = "natural",
    premium_sl_pct: Optional[float] = None,
    max_profit_pct: Optional[float] = None,
    margin_target_pct: Optional[float] = None,
) -> pd.DataFrame:
    """Per-trade exit metrics under a composite exit rule. See m_month version."""
    if hold_duration not in HOLD_DURATIONS:
        raise ValueError(f"unknown hold_duration: {hold_duration}")

    def _norm(v):
        if v is None or (isinstance(v, (int, float)) and v <= 0):
            return None
        return round(float(v), 4)

    premium_sl_pct = _norm(premium_sl_pct)
    max_profit_pct = _norm(max_profit_pct)
    margin_target_pct = _norm(margin_target_pct)

    trades_mtime = os.path.getmtime(TRADES_PATH)
    key = (trades_mtime, hold_duration, premium_sl_pct, max_profit_pct, margin_target_pct)
    cached = _EXIT_PNL_CACHE.get(key)
    if cached is not None:
        return cached

    with _EXIT_PNL_LOCK:
        cached = _EXIT_PNL_CACHE.get(key)
        if cached is not None:
            return cached

        paths_root = PATHS_GLOB.split("expiry_type=*")[0]
        if not os.path.exists(os.path.dirname(paths_root)):
            empty = _empty_exit_frame()
            _EXIT_PNL_CACHE[key] = empty
            return empty

        trades_df = _load_trades()
        per_trade_cols = ["trade_id", "entry_ts_utc",
                          "total_credit_usd_per_btc", "credit_usd",
                          "margin_used_usd_at_entry"]
        params = trades_df[per_trade_cols].copy()
        params["entry_ts_utc"] = params["entry_ts_utc"].astype("int64")
        params["entry_premium"] = params["total_credit_usd_per_btc"].astype("float64")
        params["credit_usd"] = params["credit_usd"].astype("float64")
        params["margin_used_usd_at_entry"] = params["margin_used_usd_at_entry"].astype("float64")

        if premium_sl_pct is not None and premium_sl_pct > 0:
            params["sl_premium_threshold"] = params["entry_premium"] * (1.0 + premium_sl_pct / 100.0)
        else:
            params["sl_premium_threshold"] = np.nan
        if max_profit_pct is not None and max_profit_pct > 0:
            params["max_profit_pnl"] = params["credit_usd"] * (max_profit_pct / 100.0)
        else:
            params["max_profit_pnl"] = np.nan
        if margin_target_pct is not None and margin_target_pct > 0:
            params["margin_target_pnl"] = params["margin_used_usd_at_entry"] * (margin_target_pct / 100.0)
        else:
            params["margin_target_pnl"] = np.nan

        duration_min = HOLD_DURATIONS[hold_duration]
        duration_sec = duration_min * 60 if duration_min is not None else None

        conn = duckdb.connect()
        try:
            conn.register("params", params[[
                "trade_id", "entry_ts_utc",
                "sl_premium_threshold", "max_profit_pnl", "margin_target_pnl",
            ]])

            q = f"""
            WITH bars AS (
              SELECT pp.trade_id, pp.ts, pp.gross_pnl_usd, pp.total_premium,
                     p.entry_ts_utc, p.sl_premium_threshold,
                     p.max_profit_pnl, p.margin_target_pnl,
                     CASE
                       WHEN p.sl_premium_threshold IS NOT NULL
                            AND pp.total_premium >= p.sl_premium_threshold THEN 'premium_sl'
                       WHEN p.max_profit_pnl IS NOT NULL
                            AND pp.gross_pnl_usd >= p.max_profit_pnl THEN 'max_profit'
                       WHEN p.margin_target_pnl IS NOT NULL
                            AND pp.gross_pnl_usd >= p.margin_target_pnl THEN 'margin_target'
                       {f"WHEN pp.ts >= p.entry_ts_utc + {int(duration_sec)} THEN 'fixed_hold_duration'" if duration_sec is not None else ""}
                       ELSE NULL
                     END AS triggered_by
              FROM read_parquet('{PATHS_GLOB}', hive_partitioning=true) pp
              JOIN params p ON pp.trade_id = p.trade_id
            ),
            agg AS (
              SELECT trade_id,
                     MAX(gross_pnl_usd) AS max_mtm_usd,
                     MIN(gross_pnl_usd) AS min_mtm_usd,
                     COUNT(*) AS n_path_bars,
                     MAX(ts) AS last_ts,
                     MAX(entry_ts_utc) AS entry_ts_utc
              FROM bars
              GROUP BY trade_id
            ),
            first_trigger AS (
              SELECT trade_id, MIN(ts) AS trigger_ts,
                     arg_min(triggered_by, ts) AS trigger_kind
              FROM bars
              WHERE triggered_by IS NOT NULL
              GROUP BY trade_id
            ),
            exit_ts_per_trade AS (
              SELECT a.trade_id,
                     COALESCE(ft.trigger_ts, a.last_ts) AS exit_ts,
                     COALESCE(ft.trigger_kind, 'natural') AS exit_reason
              FROM agg a
              LEFT JOIN first_trigger ft ON ft.trade_id = a.trade_id
            ),
            exit_pnl AS (
              SELECT b.trade_id, b.ts, b.gross_pnl_usd
              FROM bars b
              JOIN exit_ts_per_trade ex
                ON b.trade_id = ex.trade_id AND b.ts = ex.exit_ts
            ),
            window_agg AS (
              SELECT b.trade_id,
                     MAX(b.gross_pnl_usd) AS max_mtm_window,
                     MIN(b.gross_pnl_usd) AS min_mtm_window,
                     COUNT(*) AS n_path_bars
              FROM bars b
              JOIN exit_ts_per_trade ex
                ON b.trade_id = ex.trade_id AND b.ts <= ex.exit_ts
              GROUP BY b.trade_id
            )
            SELECT ex.trade_id,
                   ep.gross_pnl_usd AS exit_gross_pnl_usd,
                   wa.max_mtm_window AS max_mtm_usd,
                   wa.min_mtm_window AS min_mtm_usd,
                   wa.n_path_bars,
                   ex.exit_ts,
                   (ex.exit_ts - a.entry_ts_utc) / 3600.0 AS realized_hold_hours,
                   ex.exit_reason
            FROM exit_ts_per_trade ex
            JOIN agg a ON a.trade_id = ex.trade_id
            JOIN exit_pnl ep ON ep.trade_id = ex.trade_id
            JOIN window_agg wa ON wa.trade_id = ex.trade_id
            """
            df = conn.execute(q).df()
        except Exception as e:
            log.exception(f"M9 exit-PnL aggregation failed for key={key}: {e}")
            df = _empty_exit_frame()
        finally:
            conn.close()

        if len(_EXIT_PNL_CACHE) >= _EXIT_PNL_CACHE_MAX:
            for k in list(_EXIT_PNL_CACHE.keys()):
                if k[0] != trades_mtime:
                    _EXIT_PNL_CACHE.pop(k, None)
            while len(_EXIT_PNL_CACHE) >= _EXIT_PNL_CACHE_MAX:
                first_key = next(iter(_EXIT_PNL_CACHE))
                _EXIT_PNL_CACHE.pop(first_key, None)
        _EXIT_PNL_CACHE[key] = df
        log.info(f"M9 exit-PnL computed for {key[1:]} → {len(df):,} trades")
        return df


def _merged_trades_with_exits(
    hold_duration: str = "natural",
    premium_sl_pct: Optional[float] = None,
    max_profit_pct: Optional[float] = None,
    margin_target_pct: Optional[float] = None,
) -> pd.DataFrame:
    trades = _load_trades().copy()
    exits = _compute_exit_pnl(hold_duration, premium_sl_pct, max_profit_pct,
                              margin_target_pct)
    if exits.empty:
        trades["exit_gross_pnl_usd"] = float("nan")
        trades["max_mtm_usd"] = float("nan")
        trades["min_mtm_usd"] = float("nan")
        trades["n_path_bars"] = 0
        trades["exit_ts"] = pd.NA
        trades["realized_hold_hours"] = float("nan")
        trades["exit_reason"] = "no_path_data"
    else:
        trades = trades.merge(exits, on="trade_id", how="left")

    trades["hold_duration"] = hold_duration
    trades["rule_premium_sl_pct"] = premium_sl_pct
    trades["rule_max_profit_pct"] = max_profit_pct
    trades["rule_margin_target_pct"] = margin_target_pct
    trades["net_pnl_estimate_usd"] = trades["exit_gross_pnl_usd"] - trades["total_entry_cost_usd"]
    trades["is_win"] = trades["net_pnl_estimate_usd"] > 0
    trades["pct_return_on_credit"] = np.where(
        trades["credit_usd"] > 0,
        (trades["net_pnl_estimate_usd"] / trades["credit_usd"]) * 100.0,
        np.nan,
    )
    trades["pct_return_on_margin"] = np.where(
        trades["margin_used_usd_at_entry"] > 0,
        (trades["net_pnl_estimate_usd"] / trades["margin_used_usd_at_entry"]) * 100.0,
        np.nan,
    )
    return trades


_PRIMARY_METRICS = {
    "avg_net_pnl":      ("net_pnl_estimate_usd",  "mean", "max"),
    "total_net_pnl":    ("net_pnl_estimate_usd",  "sum",  "max"),
    "win_rate":         ("is_win",                "mean", "max"),
    "avg_credit_usd":   ("credit_usd",            "mean", "max"),
    "avg_margin_usd":   ("margin_used_usd_at_entry", "mean", "min"),
    "avg_pct_return_on_credit": ("pct_return_on_credit", "mean", "max"),
    "avg_pct_return_on_margin": ("pct_return_on_margin", "mean", "max"),
    "n_trades":         ("trade_id",              "count", "max"),
}


def _aggregate_grid(df: pd.DataFrame) -> pd.DataFrame:
    """Group by (expiry_type, iv_band, delta, hour, minute, hold_duration)."""
    keys = ["expiry_type", "entry_atm_iv_band",
            "delta_target", "entry_hour_ist", "entry_minute_ist",
            "hold_duration"]
    if df.empty:
        return pd.DataFrame(columns=keys)

    grouped = df.groupby(keys, dropna=False).agg(
        n_trades=("trade_id", "count"),
        n_wins=("is_win", "sum"),
        n_losses=("is_win", lambda s: int((~s.astype(bool)).sum())),
        win_rate=("is_win", "mean"),
        avg_net_pnl=("net_pnl_estimate_usd", "mean"),
        total_net_pnl=("net_pnl_estimate_usd", "sum"),
        avg_credit_usd=("credit_usd", "mean"),
        avg_margin_usd=("margin_used_usd_at_entry", "mean"),
        avg_dte_days=("dte_days", "mean"),
        avg_pct_return_on_credit=("pct_return_on_credit", "mean"),
        avg_pct_return_on_margin=("pct_return_on_margin", "mean"),
        avg_max_mtm=("max_mtm_usd", "mean"),
        avg_min_mtm=("min_mtm_usd", "mean"),
        min_mtm_usd=("min_mtm_usd", "min"),
        max_mtm_usd=("max_mtm_usd", "max"),
        avg_realized_hold_hours=("realized_hold_hours", "mean"),
        avg_wait_minutes=("wait_minutes", "mean"),
        avg_match_quality=("match_quality", "mean"),
        n_premium_sl=("exit_reason", lambda s: int((s == "premium_sl").sum())),
        n_max_profit=("exit_reason", lambda s: int((s == "max_profit").sum())),
        n_margin_target=("exit_reason", lambda s: int((s == "margin_target").sum())),
        n_fixed_hold=("exit_reason", lambda s: int((s == "fixed_hold_duration").sum())),
        n_natural=("exit_reason", lambda s: int((s == "natural").sum())),
    ).reset_index()
    grouped.rename(columns={"entry_atm_iv_band": "iv_band"}, inplace=True)
    return grouped


def _pick_best_per_band(grid: pd.DataFrame, primary: str,
                        secondary: Optional[str] = None) -> pd.DataFrame:
    if grid.empty or primary not in grid.columns:
        return grid
    direction = _PRIMARY_METRICS.get(primary, (None, None, "max"))[2]
    ascending = direction == "min"
    sort_cols = [primary]
    sort_asc = [ascending]
    if secondary and secondary in grid.columns:
        sort_cols.append(secondary)
        sec_dir = _PRIMARY_METRICS.get(secondary, (None, None, "max"))[2]
        sort_asc.append(sec_dir == "min")
    g = grid.sort_values(sort_cols, ascending=sort_asc, kind="stable")
    best = g.groupby("iv_band", as_index=False, sort=False).head(1)
    return best


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/iv_band_best_combo")
def get_iv_band_best_combo(
    expiry_type: Optional[str] = Query(None,
        description="weekly / biweekly / all"),
    hold_duration: str = Query("natural",
        description=f"Hold-duration anchor: one of {sorted(HOLD_DURATIONS)}"),
    premium_sl_pct: Optional[float] = Query(None, ge=0,
        description="Premium SL %: exit when total_premium ≥ entry × (1 + X%)"),
    max_profit_pct: Optional[float] = Query(None, ge=0,
        description="Max profit % of credit: exit when gross_pnl ≥ credit × X%"),
    margin_target_pct: Optional[float] = Query(None, ge=0,
        description="Margin target %: exit when gross_pnl ≥ margin × X%"),
    iv_band: Optional[str] = Query(None, description="Comma-sep IV bands"),
    delta_target: Optional[str] = Query(None, description="Comma-sep delta targets"),
    entry_hour: Optional[str] = Query(None, description="Comma-sep entry hours (IST)"),
    entry_minute: Optional[str] = Query(None, description="Comma-sep entry minutes (IST)"),
    entry_friday: Optional[str] = Query(None, description="Comma-sep entry Fridays (ISO)"),
    dte_bucket: Optional[str] = Query(None, description="Comma-sep DTE buckets"),
    ivp_bucket: Optional[str] = Query(None, description="Comma-sep IVP buckets"),
    primary: str = Query("avg_net_pnl"),
    secondary: Optional[str] = Query(None),
    include_grid: bool = Query(False),
) -> dict:
    """Per-IV-band best combo + optional full grid."""
    if primary not in _PRIMARY_METRICS:
        raise HTTPException(status_code=400,
            detail=f"unknown primary metric: {primary}. valid: {sorted(_PRIMARY_METRICS)}")
    if hold_duration not in HOLD_DURATIONS:
        raise HTTPException(status_code=400,
            detail=f"unknown hold_duration: {hold_duration}. valid: {sorted(HOLD_DURATIONS)}")

    df = _merged_trades_with_exits(
        hold_duration=hold_duration,
        premium_sl_pct=premium_sl_pct,
        max_profit_pct=max_profit_pct,
        margin_target_pct=margin_target_pct,
    )

    if expiry_type and expiry_type != "all":
        df = df[df["expiry_type"] == expiry_type]

    def _csv_filter(df_in: pd.DataFrame, col: str, raw: Optional[str], cast=str) -> pd.DataFrame:
        if not raw or col not in df_in.columns:
            return df_in
        vals: list = []
        for v in raw.split(","):
            v = v.strip()
            if not v:
                continue
            try:
                vals.append(cast(v))
            except Exception:
                vals.append(v)
        if not vals:
            return df_in
        return df_in[df_in[col].isin(vals)]

    df = _csv_filter(df, "entry_atm_iv_band", iv_band, str)
    df = _csv_filter(df, "delta_target", delta_target, float)
    df = _csv_filter(df, "entry_hour_ist", entry_hour, int)
    df = _csv_filter(df, "entry_minute_ist", entry_minute, int)
    df = _csv_filter(df, "entry_friday_ist", entry_friday, str)
    df = _csv_filter(df, "dte_bucket", dte_bucket, str)
    df = _csv_filter(df, "ivp_bucket", ivp_bucket, str)

    grid = _aggregate_grid(df)
    best = _pick_best_per_band(grid, primary, secondary)

    def _to_rows(d: pd.DataFrame) -> list[dict]:
        out: list[dict] = []
        for _, r in d.iterrows():
            row: dict = {}
            for k, v in r.to_dict().items():
                if pd.isna(v):
                    row[k] = None
                elif isinstance(v, (np.integer,)):
                    row[k] = int(v)
                elif isinstance(v, (np.floating,)):
                    row[k] = float(v)
                else:
                    row[k] = v
            out.append(row)
        return out

    return {
        "primary": primary,
        "secondary": secondary,
        "expiry_type": expiry_type or "all",
        "hold_duration": hold_duration,
        "premium_sl_pct": premium_sl_pct,
        "max_profit_pct": max_profit_pct,
        "margin_target_pct": margin_target_pct,
        "filters": {
            "iv_band": iv_band, "delta_target": delta_target,
            "entry_hour": entry_hour, "entry_minute": entry_minute,
            "entry_friday": entry_friday,
            "dte_bucket": dte_bucket, "ivp_bucket": ivp_bucket,
        },
        "n_trades_after_filters": int(len(df)),
        "n_cells_in_grid": int(len(grid)),
        "best_per_band": _to_rows(best),
        **({"grid": _to_rows(grid)} if include_grid else {}),
    }


@router.get("/available_primary_metrics")
def get_available_primary_metrics() -> dict:
    """Primary metric keys, hold-duration labels, and standard rule-param menus."""
    return {
        "primaries": sorted(_PRIMARY_METRICS.keys()),
        "directions": {k: v[2] for k, v in _PRIMARY_METRICS.items()},
        "hold_durations": list(HOLD_DURATIONS.keys()),
        "premium_sl_pct_menu":   [50, 75, 100, 150, 200],
        "max_profit_pct_menu":   [10, 15, 20, 25, 30, 40, 50, 60, 70, 80],
        "margin_target_pct_menu": [5, 7, 10, 12, 15, 20, 25, 30, 40, 50],
    }

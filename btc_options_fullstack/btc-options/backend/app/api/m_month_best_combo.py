"""M-Month best-combo per-IV-band ranker.

Derives exit P&L per trade by scanning the path parquet for the first bar
where ANY active exit-rule trigger fires. Rule types:

  - **Natural** (hard-cap): exit at last path bar
  - **Fixed hold duration**: exit at entry + N (3d, 5d, 1w, 2w, ..., 8w)
  - **Premium SL %**: exit when total_premium ≥ entry_premium × (1 + SL%)
  - **Max profit %**: exit when gross_pnl ≥ credit × X%
  - **Margin target %**: exit when gross_pnl ≥ margin × X%

When multiple rules are active, whichever fires first wins. Every duration
is clipped to the cycle's natural expiry. Composite of these gives the
96-rule menu (3 SL × 32 sub-configs) M7-style.
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

from app.api.m_month_results import (
    M_MONTH_BASE_DIR,
    PATHS_GLOB,
    TRADES_PATH,
    VALID_CYCLES,
    _load_trades,
)

router = APIRouter()
log = logging.getLogger(__name__)

# Hold-duration label → minutes from entry. `None` = natural (last path bar).
HOLD_DURATIONS: dict[str, Optional[int]] = {
    "natural": None,
    "3d": 3 * 1440,
    "5d": 5 * 1440,
    "1w": 7 * 1440,
    "2w": 14 * 1440,
    "3w": 21 * 1440,
    "4w": 28 * 1440,
    "5w": 35 * 1440,
    "6w": 42 * 1440,
    "7w": 49 * 1440,
    "8w": 56 * 1440,
}

# Cache: keyed by (trades_mtime, hold_duration, sl_pct, max_profit_pct,
# margin_target_pct) → per-trade exit-PnL DataFrame.
_EXIT_PNL_CACHE: dict[tuple, pd.DataFrame] = {}
_EXIT_PNL_LOCK = threading.Lock()
_EXIT_PNL_CACHE_MAX = 128  # evict LRU when full


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
    """Per-trade exit metrics under a composite exit rule.

    Each trade exits at the FIRST bar where ANY active rule fires:
      - Premium SL: total_premium ≥ entry_premium × (1 + sl_pct/100)
      - Max profit: gross_pnl ≥ credit × max_profit_pct/100
      - Margin target: gross_pnl ≥ margin × margin_target_pct/100
      - Fixed hold duration: ts ≥ entry_ts + hold_duration_seconds
    If no rule fires, falls back to natural last-bar exit (hard_cap).

    Returns DataFrame: trade_id, exit_gross_pnl_usd, max_mtm_usd,
    min_mtm_usd, n_path_bars, exit_ts, realized_hold_hours, exit_reason.
    """
    if hold_duration not in HOLD_DURATIONS:
        raise ValueError(f"unknown hold_duration: {hold_duration}")

    # Normalize rule params: treat 0 / None / falsy as "rule inactive", and
    # round floats to 4dp to make int-vs-float and 50.0-vs-50 hit the same cache key.
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

        if not os.path.exists(os.path.dirname(PATHS_GLOB.split("entry_month=*")[0])):
            empty = _empty_exit_frame()
            _EXIT_PNL_CACHE[key] = empty
            return empty

        trades_df = _load_trades()
        # Per-trade rule parameters (entry premium, credit, margin, entry ts)
        per_trade_cols = ["trade_id", "entry_ts_utc",
                          "total_credit_usd_per_btc", "credit_usd",
                          "margin_used_usd_at_entry"]
        params = trades_df[per_trade_cols].copy()
        params["entry_ts_utc"] = params["entry_ts_utc"].astype("int64")
        params["entry_premium"] = params["total_credit_usd_per_btc"].astype("float64")
        params["credit_usd"] = params["credit_usd"].astype("float64")
        params["margin_used_usd_at_entry"] = params["margin_used_usd_at_entry"].astype("float64")

        # Compute per-trade trigger thresholds (NaN if rule inactive)
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
            # The query: for every path bar, mark whether each active rule has
            # triggered AT-OR-BEFORE that bar; the earliest such bar per trade
            # is the exit. We use COALESCE to gracefully skip inactive rules.
            hold_clause = ""
            if duration_sec is not None:
                hold_clause = f"OR (pp.ts >= p.entry_ts_utc + {int(duration_sec)})"

            q = f"""
            WITH bars AS (
              SELECT pp.trade_id, pp.ts, pp.gross_pnl_usd, pp.total_premium,
                     p.entry_ts_utc, p.sl_premium_threshold,
                     p.max_profit_pnl, p.margin_target_pnl,
                     -- Which rule triggered (NULL if none)
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
            -- arg_min ensures trigger_kind corresponds to the EARLIEST triggering bar,
            -- not an arbitrary one (DuckDB's ANY_VALUE would pick any row in the group).
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
            -- Bar-window stats up to exit (max/min MTM seen prior to exit)
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
            log.exception(f"Exit-PnL aggregation failed for key={key}: {e}")
            df = _empty_exit_frame()
        finally:
            conn.close()

        # LRU eviction
        if len(_EXIT_PNL_CACHE) >= _EXIT_PNL_CACHE_MAX:
            # Drop entries with different mtime first (always stale)
            for k in list(_EXIT_PNL_CACHE.keys()):
                if k[0] != trades_mtime:
                    _EXIT_PNL_CACHE.pop(k, None)
            # Still full? drop oldest insertion order entries
            while len(_EXIT_PNL_CACHE) >= _EXIT_PNL_CACHE_MAX:
                first_key = next(iter(_EXIT_PNL_CACHE))
                _EXIT_PNL_CACHE.pop(first_key, None)
        _EXIT_PNL_CACHE[key] = df
        log.info(f"M-Month exit-PnL computed for {key[1:]} → {len(df):,} trades")
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
    """Group by (cycle, iv_band, delta, dow, hour, hold_duration) and compute metrics."""
    keys = ["trade_cycle", "entry_atm_iv_band",
            "delta_target", "entry_dow", "entry_hour_ist",
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
    trade_cycle: Optional[str] = Query(None,
        description="monthly / bimonthly / lastfri_monthly / lastfri_bimonthly / all"),
    hold_duration: str = Query("natural",
        description=f"Hold-duration anchor: one of {sorted(HOLD_DURATIONS)}"),
    premium_sl_pct: Optional[float] = Query(None, ge=0,
        description="Premium SL %: exit when total_premium ≥ entry × (1 + X%). e.g. 50 / 75 / 100."),
    max_profit_pct: Optional[float] = Query(None, ge=0,
        description="Max profit % of credit: exit when gross_pnl ≥ credit × X%. e.g. 10..80."),
    margin_target_pct: Optional[float] = Query(None, ge=0,
        description="Margin target %: exit when gross_pnl ≥ margin × X%. e.g. 5..50."),
    primary: str = Query("avg_net_pnl"),
    secondary: Optional[str] = Query(None),
    include_grid: bool = Query(False),
) -> dict:
    """Return per-IV-band best combo + optionally the full grid.

    Composite exit rule: whichever of (premium_sl, max_profit, margin_target,
    fixed_hold_duration) fires first. Each rule is optional; set premium_sl_pct
    + max_profit_pct simultaneously to model "exit at first SL hit OR first
    25% profit". `hold_duration="natural"` means no time-based clip.
    """
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

    if trade_cycle and trade_cycle != "all":
        df = df[df["trade_cycle"] == trade_cycle]

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
        "trade_cycle": trade_cycle or "all",
        "hold_duration": hold_duration,
        "premium_sl_pct": premium_sl_pct,
        "max_profit_pct": max_profit_pct,
        "margin_target_pct": margin_target_pct,
        "n_cells_in_grid": int(len(grid)),
        "best_per_band": _to_rows(best),
        **({"grid": _to_rows(grid)} if include_grid else {}),
    }


@router.get("/available_primary_metrics")
def get_available_primary_metrics() -> dict:
    """List of primary-metric keys, hold-duration labels, and standard
    rule-parameter menus for UI dropdowns."""
    return {
        "primaries": sorted(_PRIMARY_METRICS.keys()),
        "directions": {k: v[2] for k, v in _PRIMARY_METRICS.items()},
        "hold_durations": list(HOLD_DURATIONS.keys()),
        "premium_sl_pct_menu":   [50, 75, 100, 150, 200],
        "max_profit_pct_menu":   [10, 15, 20, 25, 30, 40, 50, 60, 70, 80],
        "margin_target_pct_menu": [5, 7, 10, 12, 15, 20, 25, 30, 40, 50],
    }

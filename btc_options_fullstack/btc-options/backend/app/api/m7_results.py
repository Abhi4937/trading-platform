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

import glob
import hashlib
import json
import logging
import math
import os
import shutil
import tempfile
import threading
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

# Joint delta+price-matched dataset — parallel parquet written by
# m7_batch_backtester_joint. No enriched variant for v1; falls back gracefully
# when missing (loaders raise 503 with a clear message).
TRADES_PATH_PRICE_MATCHED = os.path.join(M7_BASE_DIR, "m7_trades_price_matched.parquet")
PATHS_GLOB_PRICE_MATCHED = os.path.join(M7_BASE_DIR,
                                         "m7_paths_price_matched/friday_date=*/part.parquet")


def _trades_path_for_dataset(dataset: str) -> str:
    """Resolve the on-disk trades parquet for the requested dataset.
    For delta_match: prefer enriched when it is at least as fresh as plain
    (mtime enriched ≥ mtime plain). After a backtester --append run the plain
    file is newer → we fall back to it automatically until re-enrichment runs.
    For price_match: single canonical path (no enriched variant for v1).
    """
    if dataset == "price_match":
        return TRADES_PATH_PRICE_MATCHED
    if os.path.exists(TRADES_ENRICHED_PATH):
        enriched_mtime = os.path.getmtime(TRADES_ENRICHED_PATH)
        plain_mtime = os.path.getmtime(TRADES_PATH) if os.path.exists(TRADES_PATH) else 0.0
        if enriched_mtime >= plain_mtime:
            return TRADES_ENRICHED_PATH
    return TRADES_PATH


PATHS_FLAT = os.path.join(M7_BASE_DIR, "m7_paths_flat.parquet")
PATHS_FLAT_PRICE_MATCHED = os.path.join(M7_BASE_DIR, "m7_paths_price_matched_flat.parquet")


def _paths_glob_for_dataset(dataset: str) -> str:
    if dataset == "price_match":
        flat = PATHS_FLAT_PRICE_MATCHED
        return flat if os.path.exists(flat) else PATHS_GLOB_PRICE_MATCHED
    flat = PATHS_FLAT
    return flat if os.path.exists(flat) else PATHS_GLOB


# Lazy module-level cache — auto-reloads when the parquet file changes on disk.
# Keyed by dataset name so delta_match and price_match warm independently.
_TRADES_BY_DATASET: dict[str, tuple[Optional[pd.DataFrame], float]] = {
    "delta_match": (None, 0.0),
    "price_match": (None, 0.0),
}

# Back-compat aliases — code paths that still reference _TRADES_DF / _TRADES_MTIME
# without a dataset arg implicitly mean "delta_match" (the only dataset before
# this refactor). The warmup_rule_async path uses these too.
_TRADES_DF: Optional[pd.DataFrame] = None
_TRADES_MTIME: float = 0.0

# Exit derivation cache — keyed by (dataset, exit_rule json, trades_mtime).
# Each entry is the FULL merged DataFrame (all trades, no filters) for that
# (dataset, exit_rule). Filtering happens in pandas on the cached frame.
_EXIT_CACHE: dict[tuple[str, str, float], pd.DataFrame] = {}

# Serialise concurrent DuckDB scans across threads. The warmup thread in
# `m7_best_combo` runs full-path scans in the background; if a request thread
# starts another scan over the same parquets at the same time DuckDB can
# C++-terminate ("terminate called without an active exception"). Holding
# this lock around `_compute_all_exits` ensures one big scan at a time.
# Cache hits in `_derive_exits` skip the lock entirely.
_EXIT_COMPUTE_LOCK = threading.Lock()

# On-disk L2 for `_EXIT_CACHE`. Each (dataset, exit_rule) pair persists as
# one parquet under exit_cache/<dataset>/<sha1(canonical_rule_json)>.parquet.
# A backend restart pre-populated this directory once means subsequent cold
# starts read parquets (~150ms each) instead of running DuckDB scans over the
# 1m path glob (~5-15s each). Wiped automatically when trades parquet mtime
# changes (see _load_trades). Set M7_EXIT_CACHE_DISK=0 to disable.
_EXIT_CACHE_DIR = os.path.join(M7_BASE_DIR, "exit_cache")
_EXIT_CACHE_DISK_ENABLED = os.environ.get("M7_EXIT_CACHE_DISK", "1") != "0"

# Query-time trade-pool filters — applied once at _load_trades() so all
# downstream (exit_cache, coverage, pivot, endpoints) see only kept rows.
# Drop biweekly/monthly/quarterly expiry buckets and low-delta entries.
_KEPT_EXPIRY_BUCKETS = frozenset({
    "current (Sat)", "next (Sun)", "next_to_next (Mon)", "weekly (7d)"
})
_KEPT_DELTAS = frozenset({0.25, 0.30, 0.40, 0.50})


def _rule_cache_path(dataset: str, exit_rule: dict) -> str:
    """Return the on-disk parquet path for an (exit_rule) under `dataset`.

    Key is sha1 of the canonicalized rule JSON so the same rule maps to the
    same file across runs and across machines.
    """
    canonical = json.dumps(exit_rule or {}, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
    return os.path.join(_EXIT_CACHE_DIR, dataset, f"{digest}.parquet")


def _wipe_exit_cache_disk(dataset: str) -> None:
    """Delete the on-disk exit cache for one dataset. Called when trades
    parquet mtime changes so the persisted exits can't drift from the
    underlying trade data."""
    if not _EXIT_CACHE_DISK_ENABLED:
        return
    dataset_dir = os.path.join(_EXIT_CACHE_DIR, dataset)
    if not os.path.isdir(dataset_dir):
        return
    try:
        shutil.rmtree(dataset_dir)
        log.info("M7 exit cache (disk) wiped for dataset=%s", dataset)
    except OSError as e:
        log.warning("M7 exit cache (disk) wipe failed for %s: %s", dataset, e)

# Per-rule async warmup state for cells-mode. A cells-mode request with N
# unique rule_dicts on a cold cache would block for N × ~5–15s; if the
# backend restarts mid-request, the user sees a 500. Instead, when any rule
# is cold we kick off a background thread per rule, return a warming response
# immediately, and let the frontend poll. Once cached, the next request
# returns instantly.
_CELLS_WARMUP_TASKS: dict[str, threading.Thread] = {}
_CELLS_WARMUP_LOCK = threading.Lock()


def _warmup_rule_async(rule_dict: dict, dataset: str = "delta_match") -> None:
    """Idempotent: kick off `_compute_all_exits(rule_dict)` in a daemon
    thread so the cells-mode endpoint can return a warming response without
    blocking. Subsequent calls for the same rule_key are no-ops if a thread
    is already running or the result is cached."""
    rule_key = json.dumps(rule_dict or {}, sort_keys=True)
    with _CELLS_WARMUP_LOCK:
        _, mtime = _TRADES_BY_DATASET.get(dataset, (None, 0.0))
        cache_key = (dataset, rule_key, mtime)
        if _EXIT_CACHE.get(cache_key) is not None:
            return
        task_key = f"{dataset}:{rule_key}"
        existing = _CELLS_WARMUP_TASKS.get(task_key)
        if existing is not None and existing.is_alive():
            return

        def _do_warmup() -> None:
            try:
                _derive_exits({}, rule_dict, dataset=dataset)
            except Exception as exc:  # noqa: BLE001
                log.warning("cells warmup: rule %s (%s) failed: %s",
                            rule_dict, dataset, exc)
            finally:
                with _CELLS_WARMUP_LOCK:
                    _CELLS_WARMUP_TASKS.pop(task_key, None)

        t = threading.Thread(target=_do_warmup, daemon=True,
                             name=f"cells-warmup-{task_key[:40]}")
        _CELLS_WARMUP_TASKS[task_key] = t
        t.start()


def _load_trades(dataset: str = "delta_match") -> pd.DataFrame:
    """Load the trades parquet for the requested dataset.

    delta_match: prefers m7_trades_enriched.parquet (calibration_v2 join cols)
                 when present; falls back to plain m7_trades.parquet.
    price_match: m7_trades_price_matched.parquet (no enriched variant for v1).

    Re-reads from disk whenever the file's mtime changes. Maintains per-
    dataset cache in _TRADES_BY_DATASET; the back-compat globals
    (_TRADES_DF, _TRADES_MTIME) track the most-recently-loaded dataset for
    code paths that haven't been threaded yet (`_warmup_rule_async`, cache
    invalidation logic in callers).
    """
    global _TRADES_DF, _TRADES_MTIME
    path = _trades_path_for_dataset(dataset)
    if not os.path.exists(path):
        raise HTTPException(
            status_code=503,
            detail=(f"{os.path.basename(path)} missing under {M7_BASE_DIR}; "
                    f"run the m7 backtester for dataset='{dataset}' first."),
        )
    mtime = os.path.getmtime(path)
    cached_df, cached_mtime = _TRADES_BY_DATASET.get(dataset, (None, 0.0))
    if cached_df is None or mtime != cached_mtime:
        # Trades for this dataset changed → drop ITS exit-cache entries
        for k in list(_EXIT_CACHE.keys()):
            if k[0] == dataset:
                _EXIT_CACHE.pop(k, None)
        # …and wipe its L2 disk cache so persisted exits can't drift from
        # the new trades. Only when mtime actually changed (cached_mtime>0
        # means we had a prior load); on first-ever load there's nothing
        # to wipe and the disk cache may already be populated by a build.
        if cached_mtime > 0 and mtime != cached_mtime:
            _wipe_exit_cache_disk(dataset)
        df = pd.read_parquet(path)
        # Derive expiry_bucket from dte_days (not stored by backtester)
        if "expiry_bucket" not in df.columns and "dte_days" in df.columns:
            df["expiry_bucket"] = pd.cut(
                df["dte_days"],
                bins=[0, 1.5, 2.5, 5, 10, 20, 45, float("inf")],
                labels=["current (Sat)", "next (Sun)", "next_to_next (Mon)",
                        "weekly (7d)", "biweekly (14d)", "monthly (30d)", "quarterly"],
            ).astype(str)
        _add_entry_skew_columns(df)
        _attach_ivrv_and_slope_buckets(df)
        # Query-time pool filters for delta_match only (price_match has its
        # own expiry/delta config — don't clobber it).
        if dataset == "delta_match":
            if "expiry_bucket" in df.columns:
                before = len(df)
                df = df[df["expiry_bucket"].isin(_KEPT_EXPIRY_BUCKETS)]
                log.info("M7 trades expiry filter (%s): %d → %d rows", dataset, before, len(df))
            if "delta_target" in df.columns:
                before = len(df)
                df = df[df["delta_target"].isin(_KEPT_DELTAS)]
                log.info("M7 trades delta filter (%s): %d → %d rows", dataset, before, len(df))
        _TRADES_BY_DATASET[dataset] = (df, mtime)
        log.info("M7 trades reloaded (%s): %d rows from %s",
                 dataset, len(df), path)
        cached_df = df
        cached_mtime = mtime
    # Maintain back-compat globals — track whichever dataset was last loaded.
    _TRADES_DF = cached_df
    _TRADES_MTIME = cached_mtime
    return cached_df


def _attach_ivrv_and_slope_buckets(df: pd.DataFrame) -> None:
    """In-place: derive ivrv_bucket + 4 slope-bucket columns from existing
    per-trade columns. Reads cutoffs from m7_ranking_config:
      - IVRV uses fixed thresholds (IVRV_RICH_THRESHOLD / IVRV_CHEAP_THRESHOLD).
      - Each slope uses empirical p33/p67 from slope_cutoffs_v1.json (loaded
        by load_slope_cutoffs() with a conservative fallback if the JSON is
        absent — the calibration script writes it after enrichment runs).

    Trades missing the source column (e.g. older parquet without IV slopes)
    get NaN/None in the corresponding bucket so they're filtered out as
    `low_n` by downstream consumers.
    """
    from app.api.m7_ranking_config import (
        IVRV_RICH_THRESHOLD, IVRV_CHEAP_THRESHOLD, load_slope_cutoffs,
    )

    # IVRV — uses existing ctx_iv_rv_spread_7d on every enriched trade.
    # Vectorized via np.where to avoid .loc[bool_mask_with_NaN] which
    # tripped a pandas-internals dtype lookup on some load paths.
    import numpy as np
    if "ctx_iv_rv_spread_7d" in df.columns:
        v = pd.to_numeric(df["ctx_iv_rv_spread_7d"], errors="coerce").to_numpy()
        bucket = np.where(
            np.isnan(v), None,
            np.where(v > IVRV_RICH_THRESHOLD, "rich",
            np.where(v < IVRV_CHEAP_THRESHOLD, "cheap", "fair")),
        )
        df["ivrv_bucket"] = bucket
    else:
        df["ivrv_bucket"] = None

    # Slopes — empirical p33/p67 cutoffs per slope.
    cutoffs = load_slope_cutoffs()
    slope_to_bucket_col = {
        "slope_current_next":          "slope_cn_bucket",
        "slope_next_next_to_next":     "slope_nn_bucket",
        "slope_current_next_to_next":  "slope_cnn_bucket",
        "ctx_term_slope_7_30":         "ts_legacy_bucket",
    }
    for slope_col, bucket_col in slope_to_bucket_col.items():
        if slope_col not in df.columns:
            df[bucket_col] = None
            continue
        cuts = cutoffs.get(slope_col, {"p33": -0.01, "p67": 0.01})
        p33 = float(cuts.get("p33", -0.01))
        p67 = float(cuts.get("p67", 0.01))
        v = pd.to_numeric(df[slope_col], errors="coerce").to_numpy()
        bucket = np.where(
            np.isnan(v), None,
            np.where(v < p33, "backwardation",
            np.where(v > p67, "contango", "neutral")),
        )
        df[bucket_col] = bucket


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
    "loss_cause": str,
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
    loss_cause: Optional[str] = None,
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
        "loss_cause": loss_cause,
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
    if rule.get("capital_sl_pct") is not None:
        pct = float(rule['capital_sl_pct'])
        # Capital SL: fires when loss ≥ pct% of total allocated capital.
        # Uses rule['capital_usd'] (not per-trade margin) as the basis.
        capital_usd = float(rule.get('capital_usd', 1000.0))
        threshold = capital_usd * pct / 100.0
        parts.append(f"({pnl_after_slip} <= -{threshold})")
    if not parts:
        return ""
    return " OR ".join(parts)


def _derive_exits(filters: dict, exit_rule: dict,
                   dataset: str = "delta_match") -> pd.DataFrame:
    """For every trade matching `filters`, derive the exit outcome under `exit_rule`.

    Two-level cache:
      L1: in-memory `_EXIT_CACHE` keyed by (dataset, json(rule), mtime).
      L2: on-disk parquet at exit_cache/<dataset>/<sha1(canonical_rule)>.parquet.

    First call for a cold rule:
      - L1 miss → check L2.
      - L2 hit (file exists and mtime >= trades_mtime) → read parquet (~150ms),
        populate L1, return.
      - L2 miss → DuckDB scan (~5-15s), populate L1, atomically write L2,
        return.
    """
    # Ensure trades cache is fresh before computing the cache key.
    _load_trades(dataset)
    _, trades_mtime = _TRADES_BY_DATASET.get(dataset, (None, 0.0))
    rule_key = (dataset, json.dumps(exit_rule or {}, sort_keys=True), trades_mtime)
    full = _EXIT_CACHE.get(rule_key)
    if full is None:
        # Serialise heavy DuckDB scans across threads. Re-check the cache
        # under the lock in case another thread populated it while we waited.
        with _EXIT_COMPUTE_LOCK:
            full = _EXIT_CACHE.get(rule_key)
            if full is None:
                # L2: try disk cache before paying for the DuckDB scan.
                full = _load_exit_cache_disk(dataset, exit_rule, trades_mtime)
                if full is None:
                    full = _compute_all_exits(exit_rule, dataset=dataset)
                    _save_exit_cache_disk(dataset, exit_rule, full)
                    log.info("M7 exit cache populated for (%s, %s) (%d trades)",
                             dataset, rule_key[1][:80], len(full))
                else:
                    log.info("M7 exit cache loaded from disk for (%s, %s) (%d trades)",
                             dataset, rule_key[1][:80], len(full))
                _EXIT_CACHE[rule_key] = full
    if full.empty:
        return full
    return _apply_filters(full, filters)


def _load_exit_cache_disk(dataset: str, exit_rule: dict,
                           trades_mtime: float) -> Optional[pd.DataFrame]:
    """L2 read. Returns None on miss or stale-vs-trades."""
    if not _EXIT_CACHE_DISK_ENABLED:
        return None
    path = _rule_cache_path(dataset, exit_rule)
    if not os.path.exists(path):
        return None
    try:
        file_mtime = os.path.getmtime(path)
    except OSError:
        return None
    # Stale guard: if trades parquet is newer than the cached exits, the
    # exits could be wrong. _load_trades already wipes the directory on
    # mtime change, but a stray file (e.g. from a previous trades version
    # written by the build container before invalidation) would still be
    # rejected here.
    if file_mtime < trades_mtime:
        return None
    try:
        df = pd.read_parquet(path)
        # Apply query-time pool filters to existing disk caches that were
        # written before the filter constants were introduced.  Rows for
        # dropped expiry buckets / deltas are trimmed here so callers see
        # only the kept subset regardless of when the parquet was written.
        if dataset == "delta_match":
            if "expiry_bucket" in df.columns:
                df = df[df["expiry_bucket"].isin(_KEPT_EXPIRY_BUCKETS)]
            if "delta_target" in df.columns:
                df = df[df["delta_target"].isin(_KEPT_DELTAS)]
        # Back-compat: parquets written before Part H don't have is_fallback.
        if "is_fallback" not in df.columns:
            df["is_fallback"] = False
        return df
    except Exception as e:
        log.warning("M7 exit cache (disk) read failed at %s: %s", path, e)
        return None


def _save_exit_cache_disk(dataset: str, exit_rule: dict,
                           df: pd.DataFrame) -> None:
    """L2 write — atomic via tmp + os.replace. Failure logs a warning but
    does not raise; the caller already has the dataframe and can proceed."""
    if not _EXIT_CACHE_DISK_ENABLED:
        return
    if df is None or df.empty:
        # An empty result usually means a misconfigured rule; don't
        # persist or we'd cache a permanent "no trades" state.
        return
    path = _rule_cache_path(dataset, exit_rule)
    dataset_dir = os.path.dirname(path)
    try:
        os.makedirs(dataset_dir, exist_ok=True)
        # Atomic write: tempfile in the same directory, then os.replace.
        # tempfile.NamedTemporaryFile + delete=False so we can write+rename.
        fd, tmp_path = tempfile.mkstemp(suffix=".parquet.tmp", dir=dataset_dir)
        os.close(fd)
        try:
            df.to_parquet(tmp_path, compression="zstd", index=False)
            os.replace(tmp_path, path)
        except Exception:
            # Don't leave stray temp files around.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        log.warning("M7 exit cache (disk) write failed at %s: %s", path, e)


# ── Part H: Deadline-fallback augmentation ────────────────────────────────────

_IV_BANDS_FALLBACK = [
    (0, 20), (20, 30), (30, 40), (40, 50), (50, 60),
    (60, 70), (70, 80), (80, 90), (90, 100), (100, 100_000),
]


def _iv_band_label(iv_pct: float) -> str:
    try:
        v = float(iv_pct)
    except (TypeError, ValueError):
        return "nan"
    if math.isnan(v):
        return "nan"
    for lo, hi in _IV_BANDS_FALLBACK:
        if lo <= v < hi:
            return f"{int(lo)}+" if hi >= 100_000 else f"{int(lo)}-{int(hi)}"
    return "nan"


def _build_fallback_row_for_friday(
    friday_str: str,
    exit_rule: dict,
    trades_df: pd.DataFrame,
    paths_glob: str,
) -> Optional[dict]:
    """Synthesise a single fallback trade row at Sat 12:00 IST for one friday
    that has no strict exits in the rule's exit_cache.

    Uses the IV-regime rule (ATM IV of next_to_next expiry at the deadline):
      < 60 % → chosen expiry = next_to_next (Mon)
      ≥ 60 % → chosen expiry = current (Sat)

    The fallback trade's entry context (strikes, qty, margin) is copied from
    a proxy trade on the same friday with the closest (expiry, delta) combo.
    Entry marks and P&L are re-computed from the path data from the deadline
    timestamp onward. Returns None if any required data is unavailable.
    """
    DEADLINE_HOUR_IST = 12
    IV_THRESHOLD_PCT = 60.0

    try:
        friday_ts_utc = int(pd.Timestamp(friday_str, tz="UTC").timestamp())
    except Exception:
        return None

    # Sat 12:00 IST = Friday midnight UTC + 109800 s
    # (86400 for +1 day, 12*3600 - 19800 for IST→UTC offset: 43200 - 19800 = 23400;
    #  86400 + 23400 = 109800)
    ts_deadline = friday_ts_utc + 109800
    # Sat 17:30 IST = Sat 12:00 UTC = Friday midnight UTC + 86400 + 43200 = + 129600
    _hard_cap_ts = friday_ts_utc + 129600

    # Flat parquet has no partition dirs — use it directly with a WHERE clause.
    # Hive-glob path: try the specific friday partition first for efficiency.
    if "friday_date=*" not in paths_glob:
        use_path = paths_glob  # flat parquet
    else:
        friday_path = paths_glob.replace("friday_date=*", f"friday_date={friday_str}")
        use_path = friday_path if os.path.exists(friday_path) else paths_glob

    friday_pool = trades_df[trades_df["friday_date_ist"].astype(str) == friday_str]
    if friday_pool.empty:
        return None

    # --- ATM IV lookup at deadline from next_to_next expiry proxy ---
    ntn_cands = (friday_pool[friday_pool["expiry_bucket"] == "next_to_next (Mon)"]
                 if "expiry_bucket" in friday_pool.columns else pd.DataFrame())
    iv_proxy_row = (ntn_cands if not ntn_cands.empty else friday_pool).iloc[0]
    iv_proxy_tid = int(iv_proxy_row["trade_id"])

    conn = _duckdb_conn()
    try:
        iv_df = conn.execute(f"""
            SELECT atm_iv_now
            FROM read_parquet('{use_path}', hive_partitioning=true)
            WHERE trade_id = {iv_proxy_tid} AND ts >= {ts_deadline}
            ORDER BY ts LIMIT 1
        """).df()
    except Exception as exc:
        log.debug("fallback iv lookup failed (%s): %s", friday_str, exc)
        return None
    finally:
        conn.close()

    if iv_df.empty:
        return None
    raw_iv = iv_df.iloc[0]["atm_iv_now"]
    try:
        atm_iv_ntn = float(raw_iv)
    except (TypeError, ValueError):
        return None
    if math.isnan(atm_iv_ntn):
        return None

    # --- Regime decision ---
    chosen_expiry = ("next_to_next (Mon)" if atm_iv_ntn < IV_THRESHOLD_PCT
                     else "current (Sat)")

    # --- Proxy trade for (chosen_expiry, best available delta) ---
    exp_pool = (friday_pool[friday_pool["expiry_bucket"] == chosen_expiry]
                if "expiry_bucket" in friday_pool.columns else pd.DataFrame())
    search_pool = exp_pool if not exp_pool.empty else friday_pool

    proxy_row: Optional[pd.Series] = None
    chosen_delta = 0.30
    for delta in sorted(_KEPT_DELTAS):
        cands = (search_pool[search_pool["delta_target"] == delta]
                 if "delta_target" in search_pool.columns else pd.DataFrame())
        if not cands.empty:
            chosen_delta = float(delta)
            proxy_row = cands.iloc[0]
            break
    if proxy_row is None:
        proxy_row = search_pool.iloc[0]
        chosen_delta = float(proxy_row.get("delta_target", 0.30) or 0.30)

    proxy_tid = int(proxy_row["trade_id"])
    qty = float(proxy_row.get("quantity_lots", 1) or 1)
    margin_usd = float(proxy_row.get("margin_used_usd_at_entry", 0) or 0)

    # --- Path data from ts_deadline onward ---
    conn = _duckdb_conn()
    try:
        path_df = conn.execute(f"""
            SELECT ts, atm_iv_now, call_mark, put_mark, spot
            FROM read_parquet('{use_path}', hive_partitioning=true)
            WHERE trade_id = {proxy_tid} AND ts >= {ts_deadline}
            ORDER BY ts
        """).df()
    except Exception as exc:
        log.debug("fallback path read failed (%s tid=%d): %s", friday_str, proxy_tid, exc)
        return None
    finally:
        conn.close()

    if path_df.empty:
        return None

    # Entry values at the deadline bar
    first = path_df.iloc[0]
    entry_call = float(first["call_mark"])
    entry_put = float(first["put_mark"])
    entry_spot = float(first["spot"])
    entry_iv = float(first["atm_iv_now"])
    credit_usd = (entry_call + entry_put) * qty * 0.001

    # Respect fixed_exit_hour_ist cap if the rule has one (clamp path window)
    fixed_hour_ist = exit_rule.get("fixed_exit_hour_ist")
    if fixed_hour_ist is not None:
        fh_ts = friday_ts_utc + 86400 + int(float(fixed_hour_ist)) * 3600 - 19800
        fh_ts = max(fh_ts, ts_deadline)
        path_df = path_df[path_df["ts"] <= fh_ts]
        if path_df.empty:
            return None

    # --- Apply exit-rule conditions row-by-row ---
    sl_pct = exit_rule.get("premium_sl_pct")
    max_profit_pct = exit_rule.get("max_profit_pct")
    margin_target_pct = exit_rule.get("margin_target_pct")
    capital_sl_pct = exit_rule.get("capital_sl_pct")
    sl_mult = (1.0 + float(sl_pct) / 100.0) if sl_pct is not None else None

    exit_ts = int(path_df.iloc[-1]["ts"])
    exit_call = float(path_df.iloc[-1]["call_mark"])
    exit_put = float(path_df.iloc[-1]["put_mark"])
    exit_reason = "fallback_hard_cap"

    for _, pr in path_df.iterrows():
        c = float(pr["call_mark"])
        p = float(pr["put_mark"])
        gross = (entry_call + entry_put - c - p) * qty * 0.001
        fired = False
        if sl_mult is not None and (c >= entry_call * sl_mult or p >= entry_put * sl_mult):
            fired = True
        if (not fired and max_profit_pct is not None and credit_usd > 0
                and gross >= credit_usd * float(max_profit_pct) / 100.0):
            fired = True
        if (not fired and margin_target_pct is not None and margin_usd > 0
                and gross >= margin_usd * float(margin_target_pct) / 100.0):
            fired = True
        if (not fired and capital_sl_pct is not None
                and gross <= -float(exit_rule.get('capital_usd', 1000.0)) * float(capital_sl_pct) / 100.0):
            fired = True
        if fired:
            exit_ts = int(pr["ts"])
            exit_call = c
            exit_put = p
            exit_reason = "fallback_rule_trigger"
            break

    gross_pnl = (entry_call + entry_put - exit_call - exit_put) * qty * 0.001
    is_sl = (exit_reason == "fallback_rule_trigger" and sl_mult is not None
             and (exit_call >= entry_call * sl_mult or exit_put >= entry_put * sl_mult))

    # Synthetic trade_id: hour=12 never appears in real entries (hours 21,22,23,0,1,2,3)
    expiry_iso = str(proxy_row.get("expiry_date", ""))
    sid = f"{friday_str}|12|{expiry_iso}|{chosen_delta:.2f}"
    synthetic_tid = (int.from_bytes(hashlib.sha256(sid.encode()).digest()[:8], "big")
                     & ((1 << 63) - 1))

    # Copy all trade-context columns from proxy_row, then override entry/exit
    row: dict = {col: proxy_row[col] for col in proxy_row.index}
    row.update({
        "trade_id":                synthetic_tid,
        "entry_ts_utc":            ts_deadline,
        "entry_hour_ist":          DEADLINE_HOUR_IST,
        "entry_time_label":        f"{DEADLINE_HOUR_IST:02d}:30",
        "entry_atm_iv_pct":        entry_iv,
        "entry_atm_iv_band":       _iv_band_label(entry_iv),
        "call_entry_mark":         entry_call,
        "put_entry_mark":          entry_put,
        "credit_usd":              credit_usd,
        "spot_at_entry":           entry_spot,
        "total_entry_cost_usd":    0.0,
        "entry_slippage_call_usd": 0.0,
        "entry_slippage_put_usd":  0.0,
        # Exit columns
        "exit_ts":                 exit_ts,
        "exit_reason":             exit_reason,
        "exit_call_mark":          exit_call,
        "exit_put_mark":           exit_put,
        "exit_spot":               entry_spot,
        "gross_pnl_usd":           gross_pnl,
        "pnl_pct_of_credit":       (gross_pnl / credit_usd * 100) if credit_usd > 0 else 0.0,
        "pnl_pct_of_margin":       (gross_pnl / margin_usd * 100) if margin_usd > 0 else 0.0,
        "net_pnl_estimate_usd":    gross_pnl,
        "is_win":                  bool(gross_pnl > 0),
        "is_premium_sl_hit":       is_sl,
        "is_fallback":             True,
        "total_exit_cost_usd":     0.0,
        "exit_slippage_call_usd":  0.0,
        "exit_slippage_put_usd":   0.0,
        "exit_brokerage_call_usd": 0.0,
        "exit_brokerage_put_usd":  0.0,
        # MTM placeholders (no intra-hold-period path scan for fallback rows)
        "max_gross_pnl_usd":       gross_pnl,
        "min_gross_pnl_usd":       gross_pnl,
        "max_mtm_usd":             gross_pnl,
        "min_mtm_usd":             gross_pnl,
        "exit_mtm_usd":            gross_pnl,
        "loss_cause":              None,
    })
    return row


def _resolve_deadline_fallback_trades(
    exit_rule: dict,
    strict_exits_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    paths_glob: str,
) -> pd.DataFrame:
    """Return fallback rows for fridays in trades_df with zero strict exit rows.

    Fires only for *completely dark* fridays (no strict trades at all for the
    rule), which is uncommon with a complete dataset but provides a safety net
    for data gaps and future friday additions.  Fallback rows carry
    is_fallback=True and are merged into the exit_cache parquet alongside the
    strict rows so every downstream consumer (picker, Losses Explorer, Pivot
    Profile) sees them transparently.
    """
    if strict_exits_df.empty or trades_df.empty:
        return pd.DataFrame()
    if "friday_date_ist" not in strict_exits_df.columns:
        return pd.DataFrame()

    covered = frozenset(strict_exits_df["friday_date_ist"].dropna().astype(str).unique())
    all_fridays = sorted(trades_df["friday_date_ist"].dropna().astype(str).unique())
    missing = [f for f in all_fridays if f not in covered]
    if not missing:
        return pd.DataFrame()  # fast path — all fridays have strict coverage

    log.info("fallback resolver: %d/%d fridays uncovered (rule=%s)",
             len(missing), len(all_fridays), list(exit_rule.items())[:2])

    rows: list[dict] = []
    for friday_str in missing:
        r = _build_fallback_row_for_friday(friday_str, exit_rule, trades_df, paths_glob)
        if r is not None:
            rows.append(r)

    if not rows:
        return pd.DataFrame()

    fb_df = pd.DataFrame(rows)
    # Ensure every column present in strict_exits_df is present in fb_df too
    for col in strict_exits_df.columns:
        if col not in fb_df.columns:
            fb_df[col] = np.nan
    return fb_df


def _compute_all_exits(exit_rule: dict,
                        dataset: str = "delta_match") -> pd.DataFrame:
    """Compute exit outcomes for ALL trades under `exit_rule` (no filters).
    This is the expensive DuckDB scan. Result is cached by `_derive_exits`.

    Returns a DataFrame with all trade-level context columns + exit columns:
    [exit_ts, exit_reason, gross_pnl_usd, net_pnl_estimate_usd,
    pnl_pct_of_credit, pnl_pct_of_margin, exit_call_mark, exit_put_mark, exit_spot].
    """
    trades = _load_trades(dataset)
    if trades.empty:
        return pd.DataFrame()
    # Per-dataset on-disk paths (default = delta-match canonical).
    paths_glob_local = _paths_glob_for_dataset(dataset)
    trades_path_local = _trades_path_for_dataset(dataset)

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
        FROM read_parquet('{paths_glob_local}', hive_partitioning=true) p
        JOIN _trade_targets ft ON p.trade_id = ft.trade_id
        WHERE p.ts <= ft._target_ts
        GROUP BY p.trade_id
        """

        # Per-trade rule-trigger ts (first ts where SL/profit/margin fires,
        # bounded to the hour cap). Empty if no rules set.
        pred = _exit_rule_sql_predicate(exit_rule)
        if pred:
            triggers_sql = f"""
            SELECT p.trade_id, MIN(p.ts) AS rule_ts
            FROM read_parquet('{paths_glob_local}', hive_partitioning=true) p
            JOIN read_parquet('{trades_path_local}') t ON p.trade_id = t.trade_id
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
        JOIN read_parquet('{paths_glob_local}', hive_partitioning=true) p
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
        FROM read_parquet('{paths_glob_local}', hive_partitioning=true) p
        WHERE p.ts = {fix_ts}
        """
        conn = _duckdb_conn()
        exits = conn.execute(sql).df()
        conn.close()
    else:
        pred = _exit_rule_sql_predicate(exit_rule)
        # Trades-side projection: marks for premium_sl, slippage + credit + margin
        # for the entry-slippage-adjusted gross-vs-target comparison.
        meta_sql = f"""
        SELECT trade_id, call_entry_mark, put_entry_mark,
               entry_slippage_call_usd, entry_slippage_put_usd,
               credit_usd, margin_used_usd_at_entry
        FROM read_parquet('{trades_path_local}')
        """

        if pred:
            triggers_sql = f"""
            WITH t AS ({meta_sql})
            SELECT p.trade_id,
                   MIN(p.ts) AS first_trigger_ts
            FROM read_parquet('{paths_glob_local}', hive_partitioning=true) p
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
        FROM read_parquet('{paths_glob_local}', hive_partitioning=true)
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
        JOIN read_parquet('{paths_glob_local}', hive_partitioning=true) p
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
        # Per-leg full Greeks at entry (for trade_diagnostic Greeks tab —
        # net Γ/ν/Θ + ratios for IV-driven vs gamma-driven loss diagnosis)
        "call_entry_gamma","put_entry_gamma",
        "call_entry_theta","put_entry_theta",
        "call_entry_vega", "put_entry_vega",
        # Skew cols (derived in _add_entry_skew_columns at load time)
        "delta_skew", "iv_skew_pct", "premium_skew_usd", "premium_skew_pct",
        "iv_skew_bucket", "delta_skew_bucket", "premium_skew_bucket",
        # ── Loss-anatomy chunks need rich entry-context ─────────────────────
        # IV term structure (Chunk 3 winners-vs-losers indicator pool)
        "ctx_atm_iv_14d", "ctx_atm_iv_30d", "ctx_atm_iv_60d",
        # IVP / IV percentile rank
        "ctx_ivp_atm_7d_90d", "ctx_ivp_atm_14d_90d", "ctx_ivp_atm_30d_90d",
        "ctx_ivp_4h",
        # Realized vol + VRP
        "ctx_rv_7d", "ctx_rv_14d", "ctx_rv_30d",
        "ctx_iv_rv_spread_7d", "ctx_iv_rv_spread_30d", "ctx_iv_rv_ratio_7d",
        "ctx_vrp_pct_7d", "ctx_rvp_4h",
        # Skew / smile
        "ctx_risk_reversal_25d", "ctx_butterfly_25d",
        "ctx_wing_atm_ratio", "ctx_term_slope_7_30",
        # Spot regime (used by directional cause + Chunk 3)
        "ctx_adx_14_4h", "ctx_atr_pct_4h",
        # GEX / order-book
        "ctx_pcr_oi", "ctx_total_gex",
        # Premium structure
        "fair_credit_at_ivp", "structural_credit_pct",
        "iv_regime_premium_pct", "excess_over_fair_pct",
        # Greeks ratios
        "theta_per_vega_call", "theta_per_vega_put", "theta_per_vega_combined",
        # Calibration v2 outcomes (overall + pattern-keyed)
        "pattern_winrate", "expectancy_per_credit_pct",
        "bucket_overall_winrate", "n_trades_in_bucket", "bucket_sl_hit_rate",
        # IV velocity / vol-of-vol (Chunk 2 additions)
        "ivp_4h_delta_24h", "ivp_4h_delta_48h",
        "iv_change_stdev_7d", "vov_ratio",
        # Expected move USD at 7d/14d/30d
        "expected_move_1sigma_7d", "expected_move_1sigma_14d", "expected_move_1sigma_30d",
        # Spot technicals at entry — RSI, MACD hist, BB %B, ATR % across all
        # timeframes (5m / 15m / 30m / 1h / 4h / 1d). Used by Chunk 3
        # winners-vs-losers indicator analysis.
        "entry_rsi_14_5m",  "entry_macd_hist_5m",  "entry_bb_pct_b_5m",  "entry_atr_pct_5m",
        "entry_rsi_14_15m", "entry_macd_hist_15m", "entry_bb_pct_b_15m", "entry_atr_pct_15m",
        "entry_rsi_14_30m", "entry_macd_hist_30m", "entry_bb_pct_b_30m", "entry_atr_pct_30m",
        "entry_rsi_14_1h",  "entry_macd_hist_1h",  "entry_bb_pct_b_1h",  "entry_atr_pct_1h",
        "entry_rsi_14_4h",  "entry_macd_hist_4h",  "entry_bb_pct_b_4h",  "entry_atr_pct_4h",
        "entry_rsi_14_1d",  "entry_macd_hist_1d",  "entry_bb_pct_b_1d",  "entry_atr_pct_1d",
        # Joint Δ+price match columns (only present in price-matched dataset;
        # gated below by `if c in trades.columns`).
        "match_mode", "price_diff_usd", "price_diff_pct",
        "delta_diff_call", "delta_diff_put",
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
        # Chunk 1 — loss-cause classifier: project additional path-derived
        # signals at the trough-MTM bar (arg_min over gross_pnl_usd) plus
        # window-wide IV/spot extremes. Used by `_classify_loss_cause()` to
        # distinguish directional / vol-expansion / gamma-squeeze / skew-flip
        # / path-dependent losers.
        mtm_sql = f"""
        WITH troughs AS (
            SELECT p.trade_id,
                   arg_min(p.ts, p.gross_pnl_usd) AS ts_trough
            FROM read_parquet('{paths_glob_local}', hive_partitioning=true) p
            JOIN _trade_exits e ON p.trade_id = e.trade_id
            WHERE p.ts <= e._exit_ts
            GROUP BY p.trade_id
        )
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
                   AS put_leg_min_mtm_usd,
               -- Values at the worst-MTM bar (arg_min over gross_pnl_usd)
               arg_min(p.spot,                    p.gross_pnl_usd) AS spot_at_min_mtm,
               arg_min(p.atm_iv_now,              p.gross_pnl_usd) AS atm_iv_at_min_mtm,
               arg_min(p.net_delta,               p.gross_pnl_usd) AS net_delta_at_min_mtm,
               arg_min(p.theta_per_vega_combined, p.gross_pnl_usd) AS theta_per_vega_at_min_mtm,
               -- Window-wide IV / spot extremes
               MIN(p.atm_iv_now) AS min_atm_iv_in_window,
               MAX(p.atm_iv_now) AS max_atm_iv_in_window,
               MIN(p.spot)       AS min_spot_in_window,
               MAX(p.spot)       AS max_spot_in_window,
               -- Peak before / after trough (v6) — for trade-shape analysis
               MAX(CASE WHEN p.ts < t.ts_trough THEN p.gross_pnl_usd END)
                   AS peak_before_trough_gross,
               MAX(CASE WHEN p.ts > t.ts_trough THEN p.gross_pnl_usd END)
                   AS peak_after_trough_gross,
               arg_max(p.ts, CASE WHEN p.ts < t.ts_trough THEN p.gross_pnl_usd END)
                   AS ts_peak_before,
               arg_max(p.ts, CASE WHEN p.ts > t.ts_trough THEN p.gross_pnl_usd END)
                   AS ts_peak_after
        FROM read_parquet('{paths_glob_local}', hive_partitioning=true) p
        JOIN _trade_exits e ON p.trade_id = e.trade_id
        LEFT JOIN troughs t ON p.trade_id = t.trade_id
        WHERE p.ts <= e._exit_ts
        GROUP BY p.trade_id
        """
    else:
        # Fallback for old schemas without per-leg entry marks
        conn.register("_trade_exits",
                      merged[["trade_id", "exit_ts"]].rename(columns={"exit_ts": "_exit_ts"}))
        mtm_sql = f"""
        WITH troughs AS (
            SELECT p.trade_id,
                   arg_min(p.ts, p.gross_pnl_usd) AS ts_trough
            FROM read_parquet('{paths_glob_local}', hive_partitioning=true) p
            JOIN _trade_exits e ON p.trade_id = e.trade_id
            WHERE p.ts <= e._exit_ts
            GROUP BY p.trade_id
        )
        SELECT p.trade_id,
               MAX(p.gross_pnl_usd) AS max_gross_pnl_usd,
               MIN(p.gross_pnl_usd) AS min_gross_pnl_usd,
               arg_max(p.ts, p.gross_pnl_usd) AS ts_at_max_mtm,
               arg_min(p.ts, p.gross_pnl_usd) AS ts_at_min_mtm,
               MAX(CASE WHEN p.ts < t.ts_trough THEN p.gross_pnl_usd END)
                   AS peak_before_trough_gross,
               MAX(CASE WHEN p.ts > t.ts_trough THEN p.gross_pnl_usd END)
                   AS peak_after_trough_gross,
               arg_max(p.ts, CASE WHEN p.ts < t.ts_trough THEN p.gross_pnl_usd END)
                   AS ts_peak_before,
               arg_max(p.ts, CASE WHEN p.ts > t.ts_trough THEN p.gross_pnl_usd END)
                   AS ts_peak_after
        FROM read_parquet('{paths_glob_local}', hive_partitioning=true) p
        JOIN _trade_exits e ON p.trade_id = e.trade_id
        LEFT JOIN troughs t ON p.trade_id = t.trade_id
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

    # is_premium_sl_hit: True iff the exit fired because EITHER leg's mark
    # crossed the premium_sl threshold (not because max_profit / margin_target
    # / fixed_hour fired). Without this we can't disambiguate "rule trigger
    # was a take-profit" from "rule trigger was a real stop loss".
    if (exit_rule.get("premium_sl_pct") is not None
            and "exit_call_mark" in merged.columns
            and "exit_put_mark" in merged.columns
            and "call_entry_mark" in merged.columns
            and "put_entry_mark" in merged.columns):
        mult = 1.0 + float(exit_rule["premium_sl_pct"]) / 100.0
        crossed = (
            (merged["exit_call_mark"] >= merged["call_entry_mark"] * mult)
            | (merged["exit_put_mark"] >= merged["put_entry_mark"] * mult)
        )
        merged["is_premium_sl_hit"] = (
            (merged["exit_reason"] == "rule_trigger") & crossed
        )
    else:
        merged["is_premium_sl_hit"] = False

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
    # Peak-before-trough and peak-after-trough (v6 path-shape fields).
    # Both convert gross to MTM convention by subtracting entry slip.
    if "peak_before_trough_gross" in merged.columns:
        merged["peak_before_trough_mtm"] = (
            merged["peak_before_trough_gross"] - entry_slip
        )
        merged["peak_after_trough_mtm"] = (
            merged["peak_after_trough_gross"] - entry_slip
        )
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
        if "ts_peak_before" in merged.columns:
            merged["rel_time_peak_before_trough"] = (
                (merged["ts_peak_before"] - merged["entry_ts_utc"]) / duration
            ).where(valid).clip(lower=0.0, upper=1.0)
            merged["rel_time_peak_after_trough"] = (
                (merged["ts_peak_after"] - merged["entry_ts_utc"]) / duration
            ).where(valid).clip(lower=0.0, upper=1.0)
    # % drop from first peak down to trough — what fraction of the highest
    # unrealized P&L did the trade give back to the trough.
    # Normalize by max(peak, |trough|, $0.01) so the ratio stays bounded even
    # when peak ≤ 0 (trade never crossed into positive territory). Always ≥ 0
    # since peak ≥ trough by construction; NaN only when both are NaN.
    if "peak_before_trough_mtm" in merged.columns:
        pb = merged["peak_before_trough_mtm"]
        pa = merged["peak_after_trough_mtm"]
        tr = merged["min_mtm_usd"]
        denom = np.maximum.reduce([pb.fillna(0), tr.abs().fillna(0),
                                    pd.Series(0.01, index=merged.index)])
        merged["pct_drop_peak_to_trough"] = (pb - tr) / denom
        merged["pct_recovery_trough_to_peak"] = np.where(
            tr.abs() > 0,
            (pa - tr) / tr.abs(),
            np.nan,
        )
        # Hypothetical "exit at peak-1" net — used by the alt-net column.
        # Same entry-slip-only convention as exit_mtm_usd, so comparison
        # with "Avg exit MTM" is apples-to-apples.
        merged["alt_net_if_exit_at_peak1"] = merged["peak_before_trough_mtm"]
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
    # Peak unrealized return as % of credit — the "what was achievable
    # at the high-water mark of the trade" number. Useful for evaluating
    # time-based exits: if exit-rule realised 20% but peak was 50%, the
    # rule left 30 pts of credit on the table.
    merged["pct_max_mtm_on_credit"] = (
        merged["max_mtm_usd"] / credit
    ).where(credit > 0)
    merged["pct_min_mtm_on_credit"] = (
        merged["min_mtm_usd"] / credit
    ).where(credit > 0)

    # Chunk 1 — auto-classify each loser into one of 6 causes.
    _classify_loss_cause(merged)

    # Part H: tag all strict rows as non-fallback, then augment with any
    # fallback trades for fridays that had zero strict coverage.
    merged["is_fallback"] = False
    fallback_df = _resolve_deadline_fallback_trades(
        exit_rule, merged, trades, paths_glob_local)
    if not fallback_df.empty:
        for col in merged.columns:
            if col not in fallback_df.columns:
                fallback_df[col] = np.nan
        fallback_df = fallback_df.reindex(columns=merged.columns)
        merged = pd.concat([merged, fallback_df], ignore_index=True)
        log.info("exit cache augmented with %d fallback rows (rule=%s)",
                 len(fallback_df), list(exit_rule.items())[:2])

    return merged


def _classify_loss_cause(df: pd.DataFrame) -> None:
    """Tag each losing trade with `loss_cause` ∈ {directional, vol_expansion,
    path_dependent, gamma_squeeze, skew_flip, unclassified}. Winners get None.

    Priority order (first match wins):
      1. skew_flip       — both legs lost AND directional balance inverted
      2. gamma_squeeze   — early SL hit AND net delta blew past entry deltas
      3. vol_expansion   — ATM IV at the worst-MTM bar jumped >10% over entry
      4. directional     — spot move at min-MTM exceeds 1.5× recent 4h ATR
      5. path_dependent  — went to >30% of credit profit early then collapsed
      6. unclassified    — none of the above (also covers NaN-input rows)

    All work is in-place on `df`. Adds columns:
      loss_cause                — string label or None for winners
      _is_directional / _is_vol_expansion / … / _is_unclassified — float 0/1
                                  (mirrors the leg-winner share trick so
                                  groupby `mean()` works as `share_*`)
    """
    n = len(df)
    if n == 0:
        df["loss_cause"] = None
        for k in ("_is_directional", "_is_vol_expansion", "_is_path_dependent",
                  "_is_gamma_squeeze", "_is_skew_flip", "_is_unclassified"):
            df[k] = 0.0
        return

    # Inputs (some may be missing on old paths — the predicates safely
    # short-circuit to False via fillna).
    is_loser = (~df["is_win"].fillna(False)).to_numpy()

    def _col(name: str, default=np.nan) -> np.ndarray:
        return (df[name] if name in df.columns else
                pd.Series(default, index=df.index)).to_numpy(dtype=float)

    leg_winner = (df["leg_winner"]
                  if "leg_winner" in df.columns
                  else pd.Series([""] * n, index=df.index)).fillna("").to_numpy()
    exit_reason = df["exit_reason"].fillna("").to_numpy()

    call_pnl = _col("call_leg_pnl_usd")
    put_pnl  = _col("put_leg_pnl_usd")
    call_d   = _col("call_entry_delta")
    put_d    = _col("put_entry_delta")

    rel_min_mtm = _col("rel_time_min_mtm")
    rel_max_mtm = _col("rel_time_max_mtm")
    net_delta_min = _col("net_delta_at_min_mtm")
    spot_min  = _col("spot_at_min_mtm")
    spot_in   = _col("spot_at_entry")
    atm_iv_min = _col("atm_iv_at_min_mtm")
    atm_iv_max_w = _col("max_atm_iv_in_window")
    entry_iv = _col("entry_atm_iv")
    atr_pct  = _col("ctx_atr_pct_4h")
    max_mtm  = _col("max_mtm_usd")
    exit_mtm = _col("exit_mtm_usd")
    cred     = _col("credit_usd")

    # ── 1. SKEW_FLIP ──────────────────────────────────────────────────
    # Both legs lost AND the directional balance flipped during the trade.
    # Use leg_winner == 'neither' as the "both-lost" signal. Imbalance: the
    # call vs put loss differs by >50% of the smaller-magnitude side.
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = np.minimum(np.abs(call_pnl), np.abs(put_pnl))
        denom = np.where(denom > 1e-9, denom, np.nan)
        leg_imbalance = np.abs(call_pnl - put_pnl) / denom
    sign_entry = np.sign(call_d + put_d)
    sign_min   = np.sign(net_delta_min)
    direction_flipped = (sign_entry != 0) & (sign_min != 0) & (sign_entry != sign_min)
    cond_skew_flip = (
        (leg_winner == "neither")
        & (np.nan_to_num(leg_imbalance, nan=0.0) > 0.5)
        & direction_flipped
    )

    # ── 2. GAMMA_SQUEEZE ─────────────────────────────────────────────
    # Rule-trigger SL fired in the first 30% of the trade AND |net_delta|
    # at trough > 2× max(|call_entry_delta|, |put_entry_delta|).
    max_abs_entry_delta = np.maximum(np.abs(call_d), np.abs(put_d))
    cond_gamma = (
        (exit_reason == "rule_trigger")
        & (rel_min_mtm < 0.30)
        & (np.abs(net_delta_min) > 2.0 * max_abs_entry_delta)
    )

    # ── 3. VOL_EXPANSION ─────────────────────────────────────────────
    # IV at trough is >10% over entry IV AND peak IV in window is >5% over.
    iv_jump_at_min = np.where(entry_iv > 1e-9,
                              (atm_iv_min - entry_iv) / entry_iv, np.nan)
    iv_jump_max    = np.where(entry_iv > 1e-9,
                              (atm_iv_max_w - entry_iv) / entry_iv, np.nan)
    cond_vol = (np.nan_to_num(iv_jump_at_min, nan=-1.0) > 0.10) & \
               (np.nan_to_num(iv_jump_max,    nan=-1.0) > 0.05)

    # ── 4. DIRECTIONAL ───────────────────────────────────────────────
    # Spot moved more than 1.5× the recent 4h ATR by the time MTM bottomed.
    # NaN ATR → predicate is False → falls through to unclassified (per plan).
    spot_move_pct = np.where(spot_in > 1e-9,
                             np.abs(spot_min - spot_in) / spot_in, np.nan)
    atr_thresh    = 1.5 * (atr_pct / 100.0)
    cond_direct = (np.nan_to_num(spot_move_pct, nan=-1.0) >
                   np.nan_to_num(atr_thresh,    nan=np.inf))

    # ── 5. PATH_DEPENDENT ────────────────────────────────────────────
    # Was in solid profit (>30% of credit) before midpoint, ended below 0.
    cond_path = (
        (max_mtm > 0.30 * np.where(cred > 1e-9, cred, np.nan))
        & (exit_mtm < 0)
        & (rel_max_mtm < 0.60)
    )

    # First match wins — locked priority order.
    cause = np.full(n, None, dtype=object)
    masks = [
        ("skew_flip",      cond_skew_flip),
        ("gamma_squeeze",  cond_gamma),
        ("vol_expansion",  cond_vol),
        ("directional",    cond_direct),
        ("path_dependent", cond_path),
    ]
    assigned = np.zeros(n, dtype=bool)
    for label, mask in masks:
        fire = is_loser & np.nan_to_num(mask, nan=False).astype(bool) & ~assigned
        cause[fire] = label
        assigned |= fire
    # Remaining losers → unclassified.
    cause[is_loser & ~assigned] = "unclassified"

    df["loss_cause"] = cause

    # Boolean indicator cols for share metrics (groupby `.mean()` → `share_*`).
    for label in ("directional", "vol_expansion", "path_dependent",
                  "gamma_squeeze", "skew_flip", "unclassified"):
        df[f"_is_{label}"] = (cause == label).astype(float)


def _compute_trade_hypotheses(row: pd.Series) -> list[dict]:
    """Evaluate each loss-cause predicate independently for ONE trade and
    return them as a flat list of {flag, fired, trigger, value}. Multiple
    flags can fire (unlike `_classify_loss_cause` which assigns one).

    Predicates mirror `_classify_loss_cause` exactly so the two views stay
    consistent — the test suite asserts that whatever `_classify_loss_cause`
    picked for this row appears as `fired=True` in this list.

    Returns 5 entries (one per predicate) regardless of how many fired, so
    the UI can render a stable rubric of all hypotheses with their numeric
    triggers.
    """
    def _g(name: str, default=float("nan")):
        v = row.get(name, default)
        if v is None:
            return default
        try:
            f = float(v)
            if math.isnan(f):
                return default
            return f
        except (TypeError, ValueError):
            return default

    # Inputs
    leg_winner = str(row.get("leg_winner") or "")
    exit_reason = str(row.get("exit_reason") or "")
    call_pnl = _g("call_leg_pnl_usd")
    put_pnl  = _g("put_leg_pnl_usd")
    call_d   = _g("call_entry_delta")
    put_d    = _g("put_entry_delta")
    rel_min  = _g("rel_time_min_mtm")
    rel_max  = _g("rel_time_max_mtm")
    net_dmin = _g("net_delta_at_min_mtm")
    spot_in  = _g("spot_at_entry")
    spot_min = _g("spot_at_min_mtm")
    entry_iv = _g("entry_atm_iv")
    iv_min   = _g("atm_iv_at_min_mtm")
    iv_max_w = _g("max_atm_iv_in_window")
    atr_pct  = _g("ctx_atr_pct_4h")
    max_mtm  = _g("max_mtm_usd")
    exit_mtm = _g("exit_mtm_usd")
    cred     = _g("credit_usd")

    out: list[dict] = []

    # 1. SKEW_FLIP — both legs lost AND directional balance flipped.
    leg_imbalance = float("nan")
    if not (math.isnan(call_pnl) or math.isnan(put_pnl)):
        denom = min(abs(call_pnl), abs(put_pnl))
        if denom > 1e-9:
            leg_imbalance = abs(call_pnl - put_pnl) / denom
    sign_entry = math.copysign(1, call_d + put_d) if not (math.isnan(call_d) or math.isnan(put_d)) and (call_d + put_d) != 0 else 0
    sign_min = math.copysign(1, net_dmin) if not math.isnan(net_dmin) and net_dmin != 0 else 0
    direction_flipped = (sign_entry != 0 and sign_min != 0 and sign_entry != sign_min)
    fire_skew = (
        leg_winner == "neither"
        and not math.isnan(leg_imbalance) and leg_imbalance > 0.5
        and direction_flipped
    )
    out.append({
        "flag": "skew_flipped",
        "fired": bool(fire_skew),
        "trigger": (
            f"both legs lost; leg-imbalance {leg_imbalance:.2f} > 0.50; "
            f"net Δ sign flipped at trough"
            if fire_skew else
            f"leg_winner={leg_winner or '—'}, imbalance="
            f"{'NaN' if math.isnan(leg_imbalance) else f'{leg_imbalance:.2f}'}, "
            f"flipped={direction_flipped}"
        ),
        "value": None if math.isnan(leg_imbalance) else round(leg_imbalance, 4),
    })

    # 2. GAMMA_SQUEEZED — early SL hit AND |net Δ| at trough >> entry.
    max_abs_entry = max(abs(call_d), abs(put_d)) if not (math.isnan(call_d) or math.isnan(put_d)) else float("nan")
    delta_drift_x = float("nan")
    if not math.isnan(max_abs_entry) and max_abs_entry > 1e-9 and not math.isnan(net_dmin):
        delta_drift_x = abs(net_dmin) / max_abs_entry
    fire_gamma = (
        exit_reason == "rule_trigger"
        and not math.isnan(rel_min) and rel_min < 0.30
        and not math.isnan(delta_drift_x) and delta_drift_x > 2.0
    )
    out.append({
        "flag": "gamma_squeezed",
        "fired": bool(fire_gamma),
        "trigger": (
            f"SL fired in first {rel_min*100:.0f}% of window; "
            f"|net Δ| at trough is {delta_drift_x:.1f}× entry max-leg |Δ|"
            if fire_gamma else
            f"exit={exit_reason}, rel_min={'NaN' if math.isnan(rel_min) else f'{rel_min:.2f}'}, "
            f"Δ-drift×={'NaN' if math.isnan(delta_drift_x) else f'{delta_drift_x:.2f}'}"
        ),
        "value": None if math.isnan(delta_drift_x) else round(delta_drift_x, 4),
    })

    # 3. IV_DRIVEN — IV jumped >10% at trough AND >5% peak in window.
    iv_jump_min = float("nan")
    iv_jump_max = float("nan")
    if not math.isnan(entry_iv) and entry_iv > 1e-9:
        if not math.isnan(iv_min):
            iv_jump_min = (iv_min - entry_iv) / entry_iv
        if not math.isnan(iv_max_w):
            iv_jump_max = (iv_max_w - entry_iv) / entry_iv
    fire_iv = (
        not math.isnan(iv_jump_min) and iv_jump_min > 0.10
        and not math.isnan(iv_jump_max) and iv_jump_max > 0.05
    )
    out.append({
        "flag": "iv_driven",
        "fired": bool(fire_iv),
        "trigger": (
            f"max IV in window jumped +{iv_jump_max*100:.1f}% from entry; "
            f"trough-bar IV +{iv_jump_min*100:.1f}%"
            if fire_iv else
            f"max-IV jump={'NaN' if math.isnan(iv_jump_max) else f'{iv_jump_max*100:+.1f}%'}, "
            f"trough-IV jump={'NaN' if math.isnan(iv_jump_min) else f'{iv_jump_min*100:+.1f}%'}"
        ),
        "value": None if math.isnan(iv_jump_max) else round(iv_jump_max, 4),
    })

    # 4. DIRECTIONAL — spot move at trough exceeds 1.5× recent 4h ATR.
    spot_move = float("nan")
    if not math.isnan(spot_in) and spot_in > 1e-9 and not math.isnan(spot_min):
        spot_move = abs(spot_min - spot_in) / spot_in
    atr_thresh = 1.5 * (atr_pct / 100.0) if not math.isnan(atr_pct) else float("nan")
    fire_direct = (
        not math.isnan(spot_move) and not math.isnan(atr_thresh)
        and spot_move > atr_thresh
    )
    out.append({
        "flag": "directional",
        "fired": bool(fire_direct),
        "trigger": (
            f"|spot move| {spot_move*100:.2f}% > 1.5× ATR_4h "
            f"({atr_pct:.2f}% × 1.5 = {atr_thresh*100:.2f}%)"
            if fire_direct else
            f"spot move={'NaN' if math.isnan(spot_move) else f'{spot_move*100:.2f}%'}, "
            f"1.5×ATR={'NaN' if math.isnan(atr_thresh) else f'{atr_thresh*100:.2f}%'}"
        ),
        "value": None if math.isnan(spot_move) else round(spot_move, 6),
    })

    # 5. PATH_DEPENDENT — was up >30% of credit early, exited below 0.
    path_peak_pct = float("nan")
    if not math.isnan(cred) and cred > 1e-9 and not math.isnan(max_mtm):
        path_peak_pct = max_mtm / cred
    fire_path = (
        not math.isnan(path_peak_pct) and path_peak_pct > 0.30
        and not math.isnan(exit_mtm) and exit_mtm < 0
        and not math.isnan(rel_max) and rel_max < 0.60
    )
    out.append({
        "flag": "path_dependent",
        "fired": bool(fire_path),
        "trigger": (
            f"peak MTM was {path_peak_pct*100:.1f}% of credit at "
            f"{rel_max*100:.0f}% of window; ended at $"
            f"{exit_mtm:.2f} (negative)"
            if fire_path else
            f"peak%={'NaN' if math.isnan(path_peak_pct) else f'{path_peak_pct*100:.1f}%'}, "
            f"rel_max={'NaN' if math.isnan(rel_max) else f'{rel_max:.2f}'}, "
            f"exit_mtm={'NaN' if math.isnan(exit_mtm) else f'{exit_mtm:.2f}'}"
        ),
        "value": None if math.isnan(path_peak_pct) else round(path_peak_pct, 4),
    })

    return out


def _project_trade_to_diagnostic(row: pd.Series) -> dict:
    """Project ONE trade row into the sectioned diagnostic shape consumed
    by /trade_diagnostic. NaN → None for JSON safety; floats rounded for
    wire compactness. All derived ratios computed once here so the UI
    doesn't need any math."""
    def _num(name: str):
        v = row.get(name)
        if v is None:
            return None
        try:
            f = float(v)
            if math.isnan(f):
                return None
            return round(f, 4)
        except (TypeError, ValueError):
            return None

    def _str(name: str):
        v = row.get(name)
        if v is None:
            return None
        if isinstance(v, float) and math.isnan(v):
            return None
        return str(v)

    def _int(name: str):
        v = row.get(name)
        if v is None:
            return None
        try:
            f = float(v)
            if math.isnan(f):
                return None
            return int(f)
        except (TypeError, ValueError):
            return None

    def _bool(name: str):
        v = row.get(name)
        if v is None:
            return None
        return bool(v)

    # Derived helpers
    entry_iv = _num("entry_atm_iv")
    iv_max_w = _num("max_atm_iv_in_window")
    iv_min_w = _num("min_atm_iv_in_window")
    iv_jump_pct = None
    if entry_iv and entry_iv > 1e-9 and iv_max_w is not None:
        iv_jump_pct = round((iv_max_w - entry_iv) / entry_iv, 6)

    spot_in = _num("spot_at_entry")
    spot_min = _num("spot_at_min_mtm")
    spot_max_w = _num("max_spot_in_window")
    spot_min_w = _num("min_spot_in_window")
    spot_move_pct = None
    spot_range_pct = None
    if spot_in and spot_in > 1e-9:
        if spot_min is not None:
            spot_move_pct = round((spot_min - spot_in) / spot_in, 6)
        if spot_max_w is not None and spot_min_w is not None:
            spot_range_pct = round((spot_max_w - spot_min_w) / spot_in, 6)

    em_7d  = _num("expected_move_1sigma_7d")
    actual_move_usd = None
    actual_vs_1sigma_7d = None
    exceeded_1sigma_7d = None
    if spot_in is not None and spot_min is not None:
        actual_move_usd = round(abs(spot_min - spot_in), 2)
    if em_7d and em_7d > 1e-6 and actual_move_usd is not None:
        actual_vs_1sigma_7d = round(actual_move_usd / em_7d, 4)
        exceeded_1sigma_7d = bool(actual_vs_1sigma_7d > 1.0)

    call_d = _num("call_entry_delta") or 0.0
    put_d  = _num("put_entry_delta")  or 0.0
    call_g = _num("call_entry_gamma") or 0.0
    put_g  = _num("put_entry_gamma")  or 0.0
    call_t = _num("call_entry_theta") or 0.0
    put_t  = _num("put_entry_theta")  or 0.0
    call_v = _num("call_entry_vega")  or 0.0
    put_v  = _num("put_entry_vega")  or 0.0
    net_d_entry = round(call_d + put_d, 6)
    net_g_entry = round(call_g + put_g, 6)
    net_t_entry = round(call_t + put_t, 6)
    net_v_entry = round(call_v + put_v, 6)
    net_d_min = _num("net_delta_at_min_mtm")
    delta_drift = None
    if net_d_min is not None:
        delta_drift = round(net_d_min - net_d_entry, 6)
    abs_theta_per_gamma = None
    if abs(net_g_entry) > 1e-9:
        abs_theta_per_gamma = round(abs(net_t_entry) / abs(net_g_entry), 4)
    abs_gamma_per_vega = None
    if abs(net_v_entry) > 1e-9:
        abs_gamma_per_vega = round(abs(net_g_entry) / abs(net_v_entry), 4)
    delta_drift_x_max_leg = None
    max_abs_leg = max(abs(call_d), abs(put_d))
    if max_abs_leg > 1e-9 and delta_drift is not None:
        delta_drift_x_max_leg = round(abs(delta_drift) / max_abs_leg, 4)

    duration_minutes = None
    entry_ts = _num("entry_ts_utc")
    exit_ts  = _num("exit_ts")
    if entry_ts is not None and exit_ts is not None:
        duration_minutes = round((exit_ts - entry_ts) / 60.0, 1)

    return {
        "identity": {
            "trade_id": _str("trade_id"),
            "friday_date_ist": _str("friday_date_ist"),
            "entry_ts_utc": _num("entry_ts_utc"),
            "exit_ts": _num("exit_ts"),
            "duration_minutes": duration_minutes,
            "entry_hour_ist": _int("entry_hour_ist"),
            "entry_time_label": _str("entry_time_label"),
            "expiry_bucket": _str("expiry_bucket"),
            "expiry_date": _str("expiry_date"),
            "dte_days": _num("dte_days"),
            "dte_hours_at_entry": _num("dte_hours_at_entry"),
            "delta_target": _num("delta_target"),
            "is_straddle": _bool("is_straddle"),
            "exit_reason": _str("exit_reason"),
            "loss_cause": _str("loss_cause"),
            "leg_winner": _str("leg_winner"),
            "entry_atm_iv_band": _str("entry_atm_iv_band"),
        },
        "pnl": {
            "credit_usd": _num("credit_usd"),
            "margin_used_usd_at_entry": _num("margin_used_usd_at_entry"),
            "gross_pnl_usd": _num("gross_pnl_usd"),
            "net_pnl_estimate_usd": _num("net_pnl_estimate_usd"),
            "is_win": _bool("is_win"),
            "max_mtm_usd": _num("max_mtm_usd"),
            "min_mtm_usd": _num("min_mtm_usd"),
            "exit_mtm_usd": _num("exit_mtm_usd"),
            "max_gross_pnl_usd": _num("max_gross_pnl_usd"),
            "min_gross_pnl_usd": _num("min_gross_pnl_usd"),
            "ts_at_max_mtm": _num("ts_at_max_mtm"),
            "ts_at_min_mtm": _num("ts_at_min_mtm"),
            "rel_time_max_mtm": _num("rel_time_max_mtm"),
            "rel_time_min_mtm": _num("rel_time_min_mtm"),
            "pct_return_on_credit": _num("pct_return_on_credit"),
            "pct_return_on_margin": _num("pct_return_on_margin"),
            "pnl_pct_of_credit": _num("pnl_pct_of_credit"),
            "pnl_pct_of_margin": _num("pnl_pct_of_margin"),
            "pct_max_mtm_on_credit": _num("pct_max_mtm_on_credit"),
            "pct_min_mtm_on_credit": _num("pct_min_mtm_on_credit"),
            "leg_pnl_diff_usd": _num("leg_pnl_diff_usd"),
        },
        "costs": {
            "entry_slippage_call_usd": _num("entry_slippage_call_usd"),
            "entry_slippage_put_usd":  _num("entry_slippage_put_usd"),
            "entry_brokerage_call_usd": _num("entry_brokerage_call_usd"),
            "entry_brokerage_put_usd":  _num("entry_brokerage_put_usd"),
            "exit_slippage_call_usd":  _num("exit_slippage_call_usd"),
            "exit_slippage_put_usd":   _num("exit_slippage_put_usd"),
            "exit_brokerage_call_usd": _num("exit_brokerage_call_usd"),
            "exit_brokerage_put_usd":  _num("exit_brokerage_put_usd"),
            "total_entry_cost_usd":    _num("total_entry_cost_usd"),
            "total_exit_cost_usd":     _num("total_exit_cost_usd"),
        },
        "per_leg": {
            "quantity_lots": _int("quantity_lots"),
            "call": {
                "strike": _num("call_strike"),
                "entry_iv": _num("call_entry_iv"),
                "entry_delta": _num("call_entry_delta"),
                "entry_gamma": _num("call_entry_gamma"),
                "entry_theta": _num("call_entry_theta"),
                "entry_vega":  _num("call_entry_vega"),
                "entry_mark":  _num("call_entry_mark"),
                "exit_mark":   _num("exit_call_mark"),
                "leg_pnl_usd": _num("call_leg_pnl_usd"),
                "leg_max_mtm_usd": _num("call_leg_max_mtm_usd"),
                "leg_min_mtm_usd": _num("call_leg_min_mtm_usd"),
            },
            "put": {
                "strike": _num("put_strike"),
                "entry_iv": _num("put_entry_iv"),
                "entry_delta": _num("put_entry_delta"),
                "entry_gamma": _num("put_entry_gamma"),
                "entry_theta": _num("put_entry_theta"),
                "entry_vega":  _num("put_entry_vega"),
                "entry_mark":  _num("put_entry_mark"),
                "exit_mark":   _num("exit_put_mark"),
                "leg_pnl_usd": _num("put_leg_pnl_usd"),
                "leg_max_mtm_usd": _num("put_leg_max_mtm_usd"),
                "leg_min_mtm_usd": _num("put_leg_min_mtm_usd"),
            },
            "skew": {
                "delta_skew": _num("delta_skew"),
                "iv_skew_pct": _num("iv_skew_pct"),
                "premium_skew_usd": _num("premium_skew_usd"),
                "premium_skew_pct": _num("premium_skew_pct"),
                "iv_skew_bucket": _str("iv_skew_bucket"),
                "delta_skew_bucket": _str("delta_skew_bucket"),
                "premium_skew_bucket": _str("premium_skew_bucket"),
            },
        },
        "vol_regime": {
            "entry_atm_iv": entry_iv,
            "ctx_atm_iv_7d":  _num("ctx_atm_iv_7d"),
            "ctx_atm_iv_14d": _num("ctx_atm_iv_14d"),
            "ctx_atm_iv_30d": _num("ctx_atm_iv_30d"),
            "ctx_atm_iv_60d": _num("ctx_atm_iv_60d"),
            "ctx_ivp_atm_7d_90d":  _num("ctx_ivp_atm_7d_90d"),
            "ctx_ivp_atm_14d_90d": _num("ctx_ivp_atm_14d_90d"),
            "ctx_ivp_atm_30d_90d": _num("ctx_ivp_atm_30d_90d"),
            "ctx_ivp_4h":  _num("ctx_ivp_4h"),
            "ctx_rv_7d":   _num("ctx_rv_7d"),
            "ctx_rv_14d":  _num("ctx_rv_14d"),
            "ctx_rv_30d":  _num("ctx_rv_30d"),
            "ctx_iv_rv_spread_7d":  _num("ctx_iv_rv_spread_7d"),
            "ctx_iv_rv_spread_30d": _num("ctx_iv_rv_spread_30d"),
            "ctx_iv_rv_ratio_7d":   _num("ctx_iv_rv_ratio_7d"),
            "ctx_vrp_pct_7d":       _num("ctx_vrp_pct_7d"),
            "ctx_rvp_4h":           _num("ctx_rvp_4h"),
            "ivp_4h_delta_24h":     _num("ivp_4h_delta_24h"),
            "ivp_4h_delta_48h":     _num("ivp_4h_delta_48h"),
            "iv_change_stdev_7d":   _num("iv_change_stdev_7d"),
            "vov_ratio":            _num("vov_ratio"),
            "atm_iv_at_min_mtm":    _num("atm_iv_at_min_mtm"),
            "min_atm_iv_in_window": iv_min_w,
            "max_atm_iv_in_window": iv_max_w,
            "iv_jump_pct": iv_jump_pct,
        },
        "skew_smile": {
            "ctx_risk_reversal_25d": _num("ctx_risk_reversal_25d"),
            "ctx_butterfly_25d":     _num("ctx_butterfly_25d"),
            "ctx_wing_atm_ratio":    _num("ctx_wing_atm_ratio"),
            "ctx_term_slope_7_30":   _num("ctx_term_slope_7_30"),
        },
        "spot_regime": {
            "spot_at_entry": spot_in,
            "exit_spot": _num("exit_spot"),
            "spot_at_min_mtm": spot_min,
            "min_spot_in_window": spot_min_w,
            "max_spot_in_window": spot_max_w,
            # Full 6-timeframe × 4-indicator spot-technicals grid
            # (per docs/m7_loss_indicators.md categories #5–6).
            "entry_rsi_14_5m":  _num("entry_rsi_14_5m"),
            "entry_rsi_14_15m": _num("entry_rsi_14_15m"),
            "entry_rsi_14_30m": _num("entry_rsi_14_30m"),
            "entry_rsi_14_1h":  _num("entry_rsi_14_1h"),
            "entry_rsi_14_4h":  _num("entry_rsi_14_4h"),
            "entry_rsi_14_1d":  _num("entry_rsi_14_1d"),
            "entry_macd_hist_5m":  _num("entry_macd_hist_5m"),
            "entry_macd_hist_15m": _num("entry_macd_hist_15m"),
            "entry_macd_hist_30m": _num("entry_macd_hist_30m"),
            "entry_macd_hist_1h":  _num("entry_macd_hist_1h"),
            "entry_macd_hist_4h":  _num("entry_macd_hist_4h"),
            "entry_macd_hist_1d":  _num("entry_macd_hist_1d"),
            "entry_bb_pct_b_5m":  _num("entry_bb_pct_b_5m"),
            "entry_bb_pct_b_15m": _num("entry_bb_pct_b_15m"),
            "entry_bb_pct_b_30m": _num("entry_bb_pct_b_30m"),
            "entry_bb_pct_b_1h":  _num("entry_bb_pct_b_1h"),
            "entry_bb_pct_b_4h":  _num("entry_bb_pct_b_4h"),
            "entry_bb_pct_b_1d":  _num("entry_bb_pct_b_1d"),
            "entry_atr_pct_5m":  _num("entry_atr_pct_5m"),
            "entry_atr_pct_15m": _num("entry_atr_pct_15m"),
            "entry_atr_pct_30m": _num("entry_atr_pct_30m"),
            "entry_atr_pct_1h":  _num("entry_atr_pct_1h"),
            "entry_atr_pct_4h":  _num("entry_atr_pct_4h"),
            "entry_atr_pct_1d":  _num("entry_atr_pct_1d"),
            # Regime context
            "ctx_atr_pct_4h":     _num("ctx_atr_pct_4h"),
            "ctx_adx_14_4h":      _num("ctx_adx_14_4h"),
            "ctx_pcr_oi":         _num("ctx_pcr_oi"),
            "ctx_total_gex":      _num("ctx_total_gex"),
            "spot_move_pct": spot_move_pct,
            "spot_range_pct": spot_range_pct,
        },
        "expected_move": {
            "expected_move_1sigma_7d":  em_7d,
            "expected_move_1sigma_14d": _num("expected_move_1sigma_14d"),
            "expected_move_1sigma_30d": _num("expected_move_1sigma_30d"),
            "actual_move_usd": actual_move_usd,
            "actual_vs_1sigma_7d_ratio": actual_vs_1sigma_7d,
            "exceeded_1sigma_7d": exceeded_1sigma_7d,
        },
        "greeks_ratios": {
            "theta_per_vega_call":     _num("theta_per_vega_call"),
            "theta_per_vega_put":      _num("theta_per_vega_put"),
            "theta_per_vega_combined": _num("theta_per_vega_combined"),
            "theta_per_vega_at_min_mtm": _num("theta_per_vega_at_min_mtm"),
            "net_delta_at_entry": net_d_entry,
            "net_delta_at_min_mtm": net_d_min,
            "net_gamma_at_entry": net_g_entry,
            "net_vega_at_entry":  net_v_entry,
            "net_theta_at_entry": net_t_entry,
            "abs_theta_per_gamma": abs_theta_per_gamma,
            "abs_gamma_per_vega":  abs_gamma_per_vega,
            "delta_drift": delta_drift,
            "delta_drift_x_max_leg": delta_drift_x_max_leg,
        },
        "context_premium": {
            "fair_credit_at_ivp":       _num("fair_credit_at_ivp"),
            "structural_credit_pct":    _num("structural_credit_pct"),
            "iv_regime_premium_pct":    _num("iv_regime_premium_pct"),
            "excess_over_fair_pct":     _num("excess_over_fair_pct"),
            "pattern_winrate":          _num("pattern_winrate"),
            "expectancy_per_credit_pct":_num("expectancy_per_credit_pct"),
            "bucket_overall_winrate":   _num("bucket_overall_winrate"),
            "bucket_sl_hit_rate":       _num("bucket_sl_hit_rate"),
        },
        "hypotheses": _compute_trade_hypotheses(row),
    }


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
    "total_win_mtm":          lambda g: g.loc[g["is_win"], "exit_mtm_usd"].sum(),
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
    "total_loss_mtm":         lambda g: g.loc[~g["is_win"], "exit_mtm_usd"].sum(),
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
    # Peak/trough unrealized as % of credit — what was theoretically
    # achievable at the high/low-water mark of each trade.
    "avg_pct_max_mtm_on_credit":   ("pct_max_mtm_on_credit",       "mean"),
    "avg_pct_min_mtm_on_credit":   ("pct_min_mtm_on_credit",       "mean"),
    # Exit-time MTM overall (on-screen P&L at exit, only entry costs subtracted)
    "avg_exit_mtm":                ("exit_mtm_usd",                "mean"),
    # Path peak-trough-peak (v6) — see _compute_all_exits.
    # Same entry-slip-only convention as max/min MTM.
    "avg_peak_before_trough":      ("peak_before_trough_mtm",      "mean"),
    "avg_peak_after_trough":       ("peak_after_trough_mtm",       "mean"),
    "avg_rel_time_peak_before":    ("rel_time_peak_before_trough", "mean"),
    "avg_rel_time_peak_after":     ("rel_time_peak_after_trough",  "mean"),
    "avg_rel_time_trough":         ("rel_time_min_mtm",            "mean"),
    "avg_rel_time_peak":           ("rel_time_max_mtm",            "mean"),
    "avg_pct_drop_peak_to_trough": ("pct_drop_peak_to_trough",     "mean"),
    "avg_pct_recovery_trough_to_peak": ("pct_recovery_trough_to_peak", "mean"),
    # Hypothetical: net P&L if every trade had exited at its first peak
    "avg_alt_net_if_exit_at_peak1": ("alt_net_if_exit_at_peak1",   "mean"),
    # Risk-adjusted (v6 grid columns)
    "stdev_net_pnl":               ("net_pnl_estimate_usd",        "std"),
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
    # Loss-cause shares (Chunk 1) — fraction of trades in the group whose
    # loss_cause matches each label. Winners contribute 0 to all share_*
    # metrics. Compose as a "% of all trades", not "% of losers".
    "share_directional":           ("_is_directional",             "mean"),
    "share_vol_expansion":         ("_is_vol_expansion",           "mean"),
    "share_path_dependent":        ("_is_path_dependent",          "mean"),
    "share_gamma_squeeze":         ("_is_gamma_squeeze",           "mean"),
    "share_skew_flip":             ("_is_skew_flip",               "mean"),
    "share_unclassified":          ("_is_unclassified",            "mean"),
}

# Special-case metrics that don't fit the simple "column + agg" pattern.
def _count_rule_trigger(g): return int((g["exit_reason"] == "rule_trigger").sum())
def _count_hard_cap(g):     return int((g["exit_reason"] == "hard_cap").sum())
def _count_losses(g):       return int((~g["is_win"]).sum())
def _count_wins(g):         return int(g["is_win"].sum())
def _count_premium_sl_hit(g):
    # Actual premium-SL fires only (excludes take-profit rule fires).
    return int(g["is_premium_sl_hit"].sum()) if "is_premium_sl_hit" in g.columns else 0

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

def _max_consecutive_premium_sl_hits(g):
    # Real premium-SL streak — excludes max_profit / margin_target rule fires.
    if "is_premium_sl_hit" not in g.columns:
        return 0
    s = g.sort_values("friday_date_ist", kind="stable")["is_premium_sl_hit"]
    return _max_run_length(s.astype(bool))

def _avg_exit_offset_minutes(g):
    # Mean hold time (entry → exit) across all trades in the group, in minutes.
    if "exit_ts" not in g.columns or "entry_ts_utc" not in g.columns or g.empty:
        return float("nan")
    return float(((g["exit_ts"].astype(float) - g["entry_ts_utc"].astype(float)) / 60.0).mean())

def _avg_winner_exit_offset_minutes(g):
    winners = g[g["is_win"]]
    if winners.empty or "exit_ts" not in winners.columns:
        return float("nan")
    return float(((winners["exit_ts"].astype(float) - winners["entry_ts_utc"].astype(float)) / 60.0).mean())

def _avg_loser_exit_offset_minutes(g):
    losers = g[~g["is_win"]]
    if losers.empty or "exit_ts" not in losers.columns:
        return float("nan")
    return float(((losers["exit_ts"].astype(float) - losers["entry_ts_utc"].astype(float)) / 60.0).mean())

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

def _count_cause(label: str):
    def _f(g): return int((g["loss_cause"] == label).sum()) if "loss_cause" in g.columns else 0
    return _f

def _stdev_losses_only(g):
    """Std of net P&L over losing trades only — downside vol for Sortino."""
    losers = g.loc[~g["is_win"], "net_pnl_estimate_usd"]
    if len(losers) < 2:
        return float("nan")
    return float(losers.std(ddof=1))

def _worst_5_avg_net(g):
    """Mean of the 5 worst-net trades. Tail-risk indicator. If n<5, mean of
    all losers (or NaN if no losers)."""
    s = g["net_pnl_estimate_usd"].dropna().sort_values()
    if s.empty:
        return float("nan")
    take = min(5, len(s))
    return float(s.iloc[:take].mean())

def _var_95_net(g):
    """5th percentile of net P&L distribution. 1-in-20 trade outcome."""
    s = g["net_pnl_estimate_usd"].dropna()
    if len(s) < 2:
        return float("nan")
    return float(s.quantile(0.05))

def _cvar_95_net(g):
    """Mean of trades below the 5th percentile. Expected loss conditional
    on tail event. CVaR is what risk committees actually look at."""
    s = g["net_pnl_estimate_usd"].dropna()
    if len(s) < 2:
        return float("nan")
    threshold = s.quantile(0.05)
    tail = s[s <= threshold]
    if tail.empty:
        return float("nan")
    return float(tail.mean())

def _max_consec_loss_dollars(g):
    """Chronological sum of net P&L during the cell's longest losing streak.
    Most-negative running sum across consecutive losses, reset by any win."""
    s = g.sort_values("friday_date_ist", kind="stable")
    if s.empty:
        return 0.0
    is_loss = (~s["is_win"]).astype(bool).to_numpy()
    pnls = s["net_pnl_estimate_usd"].to_numpy()
    best = 0.0
    cur = 0.0
    for i, loss in enumerate(is_loss):
        if loss:
            cur += pnls[i] if not pd.isna(pnls[i]) else 0.0
            if cur < best:
                best = cur
        else:
            cur = 0.0
    return float(best)  # negative number (or 0 if no streak)

def _recent_26w_filter(g):
    """Subset of g to trades from the last 26 Fridays (chronological)."""
    if "friday_date_ist" not in g.columns or g.empty:
        return g.iloc[0:0]
    unique = sorted(pd.unique(g["friday_date_ist"].astype(str)))
    if len(unique) <= 26:
        return g
    cutoff = unique[-26]
    return g[g["friday_date_ist"].astype(str) >= cutoff]

def _avg_net_pnl_last_26w(g):
    sub = _recent_26w_filter(g)
    if sub.empty:
        return float("nan")
    return float(sub["net_pnl_estimate_usd"].mean())

def _win_rate_last_26w(g):
    sub = _recent_26w_filter(g)
    if sub.empty:
        return float("nan")
    return float(sub["is_win"].mean())

def _count_fixed_hour(g):
    """# of trades that exited at the fixed_hour rule (deterministic time
    exit, neither rule_trigger nor hard_cap)."""
    if g.empty:
        return 0
    er = g["exit_reason"] if "exit_reason" in g.columns else None
    if er is None:
        return 0
    # Anything that's not rule_trigger and not hard_cap is a fixed-hour exit.
    return int(((er != "rule_trigger") & (er != "hard_cap")).sum())

_SPECIAL_METRICS = {
    "n_rule_trigger": _count_rule_trigger,  # # of trades that hit any rule (SL/max-profit/margin)
    "n_premium_sl_hit": _count_premium_sl_hit,  # # that hit premium_sl specifically (excludes take-profits)
    "n_hard_cap":     _count_hard_cap,      # # of trades that exited at Sat 17:30 (no rule fired)
    "n_losses":       _count_losses,        # # of losing trades
    "n_wins":         _count_wins,          # # of winning trades
    "max_consec_losses":  _max_consecutive_losses,   # longest streak of losing trades (chronological)
    "max_consec_wins":    _max_consecutive_wins,     # longest streak of winning trades
    "max_consec_sl_hits": _max_consecutive_sl_hits,  # longest streak of rule-triggered (SL) exits
    "max_consec_premium_sl_hits": _max_consecutive_premium_sl_hits,  # longest streak of actual premium-SL fires
    # Exit-time means (entry → exit hold duration, in minutes)
    "avg_exit_offset_minutes":         _avg_exit_offset_minutes,
    "avg_winner_exit_offset_minutes":  _avg_winner_exit_offset_minutes,
    "avg_loser_exit_offset_minutes":   _avg_loser_exit_offset_minutes,
    "n_winners_below_avg_min_mtm": _n_winners_below_avg_min_mtm,  # winners w/ worse-than-avg drawdown
    "n_losers_above_avg_max_mtm":  _n_losers_above_avg_max_mtm,   # losers w/ better-than-avg peak
    # v6 — fixed-hour exit count (separate from rule_trigger / hard_cap)
    "n_fixed_hour_ist":            _count_fixed_hour,
    # v6 — risk-adjusted / tail / drawdown sequence / edge stability
    "stdev_losses_only":           _stdev_losses_only,
    "worst_5_avg_net":             _worst_5_avg_net,
    "var_95_net":                  _var_95_net,
    "cvar_95_net":                 _cvar_95_net,
    "max_consec_loss_dollars":     _max_consec_loss_dollars,
    "avg_net_pnl_last_26w":        _avg_net_pnl_last_26w,
    "win_rate_last_26w":           _win_rate_last_26w,
    # Loss-cause counts (Chunk 1) — # of losers in the group with each cause
    "n_directional":     _count_cause("directional"),
    "n_vol_expansion":   _count_cause("vol_expansion"),
    "n_path_dependent":  _count_cause("path_dependent"),
    "n_gamma_squeeze":   _count_cause("gamma_squeeze"),
    "n_skew_flip":       _count_cause("skew_flip"),
    "n_unclassified":    _count_cause("unclassified"),
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
    if metric in ("win_rate", "win_rate_last_26w",
                  "avg_pct_return_on_margin", "avg_pct_return_on_credit",
                  "avg_pct_return_on_margin_winners", "avg_pct_return_on_credit_winners",
                  "avg_pct_drop_peak_to_trough", "avg_pct_recovery_trough_to_peak",
                  "avg_rel_time_peak_before", "avg_rel_time_peak_after",
                  "avg_rel_time_trough", "avg_rel_time_peak") \
            or metric.endswith("_share"):  # leg-winner outcome shares
        return round(float(val), 6)
    if metric in ("count", "n_rule_trigger", "n_premium_sl_hit",
                  "n_hard_cap", "n_losses", "n_wins", "n_fixed_hour_ist",
                  "max_consec_losses", "max_consec_wins",
                  "max_consec_sl_hits", "max_consec_premium_sl_hits",
                  "n_winners_below_avg_min_mtm", "n_losers_above_avg_max_mtm"):
        return int(val)
    if metric in ("avg_exit_offset_minutes",
                  "avg_winner_exit_offset_minutes",
                  "avg_loser_exit_offset_minutes"):
        return round(float(val), 1)
    return round(float(val), 4)


# ── Best-cells helper (shared by full_coverage endpoint + losses_distribution scope) ──

def _best_cells_for_metric(derived: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Return one best (band, hour, expiry, delta) cell per IV band, ranked by
    `metric`. Strict pick uses n_trades >= 3; bands not covered by the strict
    pick fall back to best-score regardless of n. Mirrors the selection in
    `m7_full_coverage.get_iv_band_full_coverage` — kept here so scope-aware
    callers (Losses Explorer) get the SAME best cells the table renders.
    """
    if derived.empty:
        return pd.DataFrame(columns=["entry_atm_iv_band", "entry_hour_ist",
                                      "expiry_bucket", "delta_target",
                                      "score", "n_trades"])
    # Drop NaN-gross trades (missing-leg-quote at entry — not valid strangles).
    # Without this, NaN trades inflate n_losses but produce NaN aggregates.
    derived = derived[derived["gross_pnl_usd"].notna()]
    if derived.empty:
        return pd.DataFrame(columns=["entry_atm_iv_band", "entry_hour_ist",
                                      "expiry_bucket", "delta_target",
                                      "score", "n_trades"])
    dims = ["entry_atm_iv_band", "entry_hour_ist", "expiry_bucket", "delta_target"]
    grp = derived.groupby(dims, dropna=False)
    score = _metric_score(grp, metric)
    n = grp.size()
    df = pd.DataFrame({"score": score, "n_trades": n}).reset_index()
    df_valid = df.dropna(subset=["score"])
    if df_valid.empty:
        return df_valid
    strict = df_valid[df_valid["n_trades"] >= 3]
    strict_idx = (strict.groupby("entry_atm_iv_band", dropna=False)["score"].idxmax()
                  if not strict.empty else pd.Index([]))
    strict_best = strict.loc[strict_idx] if len(strict_idx) else strict.iloc[0:0]
    covered = set(strict_best["entry_atm_iv_band"].dropna().tolist())
    fallback = df_valid[~df_valid["entry_atm_iv_band"].isin(covered)]
    if not fallback.empty:
        fb_idx = fallback.groupby("entry_atm_iv_band", dropna=False)["score"].idxmax()
        fallback_best = fallback.loc[fb_idx]
        return pd.concat([strict_best, fallback_best], ignore_index=True)
    return strict_best.reset_index(drop=True)


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
    loss_cause: Optional[str] = None,
    dataset: str = Query("delta_match",
                          description="'delta_match' (default) or 'price_match'."),
):
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist, expiry_bucket,
                              loss_cause=loss_cause)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule, dataset=dataset)
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
    dataset: str = Query("delta_match",
                          description="'delta_match' (default) or 'price_match' "
                          "to read the joint delta+price-matched parquet."),
):
    df = _apply_filters(_load_trades(dataset), {
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
def get_path(
    trade_id: str = Query(...),
    dataset: str = Query("delta_match",
                          description="'delta_match' (default) or 'price_match' "
                          "to read the joint delta+price-matched paths."),
):
    """Return the full 1m path for one trade."""
    try:
        tid = int(trade_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="trade_id must be int")
    paths_glob_local = _paths_glob_for_dataset(dataset)
    sql = f"""
    SELECT * FROM read_parquet('{paths_glob_local}', hive_partitioning=true)
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
    loss_cause: Optional[str] = None,
    dataset: str = Query("delta_match",
                          description="'delta_match' (default) or 'price_match'."),
):
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              None, ivp_bucket, ctx_pattern, None,
                              friday_date_ist, expiry_bucket,
                              loss_cause=loss_cause)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule, dataset=dataset)
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
    loss_cause: Optional[str] = None,
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """Entry-time × Friday heatmap (one cell per friday_date × entry_hour)."""
    filters = _query_filters(delta_target, None, expiry_date,
                              entry_atm_iv_band, None, None, None, None, None,
                              None, None, expiry_bucket,
                              loss_cause=loss_cause)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule, dataset=dataset)
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
    loss_cause: Optional[str] = None,
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """For each Friday NOT represented in any of the 10 IV-band best cells
    (under the same filters + exit rule), return that Friday's own best combo.

    Same logic as /iv_band_summary for picking best cells, then identifies
    orphan Fridays and reports each one's top trade.
    """
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist, expiry_bucket,
                              loss_cause=loss_cause)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule, dataset=dataset)
    if derived.empty:
        return {"rows": [], "n_missed": 0, "n_total_fridays": 0}

    # Drop NaN-gross trades (missing-leg-quote at entry — not valid strangles).
    derived = derived[derived["gross_pnl_usd"].notna()]
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
    loss_cause: Optional[str] = None,
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """For each IV band, find the best (entry_hour, expiry, delta) combo
    by the chosen metric. Headline 'answer the question' table."""
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist, expiry_bucket,
                              loss_cause=loss_cause)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule, dataset=dataset)
    if derived.empty:
        return {"rows": []}

    # Drop NaN-gross trades (missing-leg-quote at entry — not valid strangles).
    derived = derived[derived["gross_pnl_usd"].notna()]
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
        "avg_win_mtm", "largest_win_mtm", "total_win_mtm",
        "avg_loss_mtm", "largest_loss_mtm", "total_loss_mtm",
        # Peak/trough unrealized return as % of credit
        "avg_pct_max_mtm_on_credit", "avg_pct_min_mtm_on_credit",
        # Counts
        "n_rule_trigger", "n_premium_sl_hit", "n_hard_cap", "n_losses", "n_wins",
        # Streaks (chronological by friday_date_ist)
        "max_consec_losses", "max_consec_wins",
        "max_consec_sl_hits", "max_consec_premium_sl_hits",
        # Outlier counts vs group-average MTM
        "n_winners_below_avg_min_mtm", "n_losers_above_avg_max_mtm",
        # Exit-time means
        "avg_exit_offset_minutes", "avg_winner_exit_offset_minutes",
        "avg_loser_exit_offset_minutes",
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
    loss_cause: Optional[str] = None,
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """For each IV band's best (entry_hour × expiry_bucket × delta) combo,
    return per-trade path-marker rows for the path-markers chart:
    relative time of max/min MTM, the MTM values themselves, win/loss, and
    exit reason. Best-combo selection mirrors /iv_band_summary."""
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist, expiry_bucket,
                              loss_cause=loss_cause)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule, dataset=dataset)
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
    loss_cause: Optional[str] = None,
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """Top-N (entry_hour × expiry_bucket × delta) combos by metric, given exit rule."""
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist, expiry_bucket,
                              loss_cause=loss_cause)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule, dataset=dataset)
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
    loss_cause: Optional[str] = None,
    sort_by: str = "friday_date_ist",
    sort_dir: str = "desc",
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
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
        loss_cause,
    )
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule, dataset=dataset)
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
        # Loss-cause classifier (Chunk 1) — None for winners
        "loss_cause",
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
    loss_cause: Optional[str] = None,
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
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
        loss_cause=loss_cause,
    )
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule, dataset=dataset)
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
def get_cost_breakdown(
    trade_id: str = Query(...),
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """Per-leg entry cost decomposition for one trade."""
    try:
        tid = int(trade_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="trade_id must be int")
    df = _load_trades(dataset)
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


# ── Loss-anatomy: per-cell winners-vs-losers indicator gap ──────────────────
# Indicator universe for the cell-level winners-vs-losers analysis. Mirrors
# m4_results._ATTR_INDICATORS plus the Chunk-2 IV-velocity / expected-move /
# spot-technical additions. Each tuple is (column, display_label, category).
_M7_LOSS_INDICATORS: list[tuple[str, str, str]] = [
    # IV term structure
    ("ctx_atm_iv_7d",          "ATM IV 7d",            "IV"),
    ("ctx_atm_iv_14d",         "ATM IV 14d",           "IV"),
    ("ctx_atm_iv_30d",         "ATM IV 30d",           "IV"),
    ("ctx_atm_iv_60d",         "ATM IV 60d",           "IV"),
    ("ctx_ivp_atm_7d_90d",     "IVP 7d/90d (rank)",    "IV"),
    ("ctx_ivp_atm_14d_90d",    "IVP 14d/90d (rank)",   "IV"),
    ("ctx_ivp_atm_30d_90d",    "IVP 30d/90d (rank)",   "IV"),
    ("ctx_ivp_4h",             "IVP 4h",               "IV"),
    # IV velocity / vol-of-vol (Chunk 2 additions)
    ("ivp_4h_delta_24h",       "IVP Δ 24h",            "IV velocity"),
    ("ivp_4h_delta_48h",       "IVP Δ 48h",            "IV velocity"),
    ("iv_change_stdev_7d",     "IV change σ 7d",       "IV velocity"),
    ("vov_ratio",              "Vol-of-vol ratio",     "IV velocity"),
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
    # Spot technicals at entry — all four indicators across 5m / 15m / 30m / 1h / 4h / 1d
    ("entry_rsi_14_5m",        "RSI 14 (5m, entry)",     "Spot technicals 5m"),
    ("entry_macd_hist_5m",     "MACD hist (5m, entry)",  "Spot technicals 5m"),
    ("entry_bb_pct_b_5m",      "BB %B (5m, entry)",      "Spot technicals 5m"),
    ("entry_atr_pct_5m",       "ATR % (5m, entry)",      "Spot technicals 5m"),
    ("entry_rsi_14_15m",       "RSI 14 (15m, entry)",    "Spot technicals 15m"),
    ("entry_macd_hist_15m",    "MACD hist (15m, entry)", "Spot technicals 15m"),
    ("entry_bb_pct_b_15m",     "BB %B (15m, entry)",     "Spot technicals 15m"),
    ("entry_atr_pct_15m",      "ATR % (15m, entry)",     "Spot technicals 15m"),
    ("entry_rsi_14_30m",       "RSI 14 (30m, entry)",    "Spot technicals 30m"),
    ("entry_macd_hist_30m",    "MACD hist (30m, entry)", "Spot technicals 30m"),
    ("entry_bb_pct_b_30m",     "BB %B (30m, entry)",     "Spot technicals 30m"),
    ("entry_atr_pct_30m",      "ATR % (30m, entry)",     "Spot technicals 30m"),
    ("entry_rsi_14_1h",        "RSI 14 (1h, entry)",     "Spot technicals 1h"),
    ("entry_macd_hist_1h",     "MACD hist (1h, entry)",  "Spot technicals 1h"),
    ("entry_bb_pct_b_1h",      "BB %B (1h, entry)",      "Spot technicals 1h"),
    ("entry_atr_pct_1h",       "ATR % (1h, entry)",      "Spot technicals 1h"),
    ("entry_rsi_14_4h",        "RSI 14 (4h, entry)",     "Spot technicals 4h"),
    ("entry_macd_hist_4h",     "MACD hist (4h, entry)",  "Spot technicals 4h"),
    ("entry_bb_pct_b_4h",      "BB %B (4h, entry)",      "Spot technicals 4h"),
    ("entry_atr_pct_4h",       "ATR % (4h, entry)",      "Spot technicals 4h"),
    ("entry_rsi_14_1d",        "RSI 14 (1d, entry)",     "Spot technicals 1d"),
    ("entry_macd_hist_1d",     "MACD hist (1d, entry)",  "Spot technicals 1d"),
    ("entry_bb_pct_b_1d",      "BB %B (1d, entry)",      "Spot technicals 1d"),
    ("entry_atr_pct_1d",       "ATR % (1d, entry)",      "Spot technicals 1d"),
    # Expected move (Chunk 2 additions) — USD
    ("expected_move_1sigma_7d", "Expected move 1σ 7d", "Expected move"),
    ("expected_move_1sigma_14d","Expected move 1σ 14d","Expected move"),
    ("expected_move_1sigma_30d","Expected move 1σ 30d","Expected move"),
    # Order book / GEX
    ("ctx_pcr_oi",             "PCR OI",               "GEX/Flow"),
    ("ctx_total_gex",          "Total GEX",            "GEX/Flow"),
    # Premium structure (entry-side credit / IV-regime context)
    ("fair_credit_at_ivp",     "Fair credit @ IVP",    "Premium"),
    ("structural_credit_pct",  "Structural credit %",  "Premium"),
    ("iv_regime_premium_pct",  "IV regime premium %",  "Premium"),
    ("excess_over_fair_pct",   "Excess over fair %",   "Premium"),
    # Greeks ratios
    ("theta_per_vega_call",    "θ/ν call",             "Greeks"),
    ("theta_per_vega_put",     "θ/ν put",              "Greeks"),
    ("theta_per_vega_combined","θ/ν combined",         "Greeks"),
    # Skew at entry (per-leg)
    ("delta_skew",             "Δ skew (call−put)",    "Skew (entry)"),
    ("iv_skew_pct",            "IV skew % (call−put)", "Skew (entry)"),
    ("premium_skew_pct",       "Premium skew %",       "Skew (entry)"),
]


def _pool_suggestions(cell: dict) -> list[str]:
    """Adjacent-cell suggestions surfaced when the requested cell is too
    small for stable winners-vs-losers stats. UI renders as buttons the
    user can click into. No silent auto-pooling."""
    return [
        "entry_atm_iv_band ±1 (adjacent IV bucket)",
        "entry_hour_ist ±1 (adjacent entry hour)",
        f"delta_target ±0.05 (adjacent Δ around {cell.get('delta_target')})",
        f"expiry_bucket adjacent (currently {cell.get('expiry_bucket')!r})",
    ]


@router.get("/cell_winners_vs_losers")
def get_cell_winners_vs_losers(
    cell: str = Query(..., description="JSON: {entry_atm_iv_band, entry_hour_ist, expiry_bucket, delta_target}"),
    discriminate_sigma: float = Query(0.5, ge=0.0, le=3.0,
        description="Effect-size cutoff: |gap|/σ > this flags discriminating"),
    min_n_per_side: int = Query(3, ge=1, le=20,
        description="Below this, response.low_confidence=true and pool_suggestions are returned"),
    exit_rule: Optional[str] = None,
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """For ONE best-combo cell (band × hour × expiry_bucket × delta), compare
    avg(indicator) for winners vs losers across ~50 indicators.

    `discriminating` flag fires when |gap| > N·σ where σ is the indicator's
    overall std across the full dataset. Also returns Welch's t-test p-value
    as `p_value_t` (informational; not gating).

    When n_win or n_loss < `min_n_per_side`, response sets `low_confidence`
    and surfaces `pool_suggestions` — adjacent cells the user could
    optionally re-query against.
    """
    try:
        cell_obj = json.loads(cell)
        if not isinstance(cell_obj, dict):
            raise ValueError("cell must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"bad cell payload: {e}")

    required = {"entry_atm_iv_band", "entry_hour_ist", "expiry_bucket", "delta_target"}
    missing = required - set(cell_obj.keys())
    if missing:
        raise HTTPException(status_code=400,
            detail=f"cell missing required keys: {sorted(missing)}")

    rule = _parse_exit_rule(exit_rule)
    full_derived = _derive_exits({}, rule, dataset=dataset)
    if full_derived.empty:
        return {"cell": cell_obj, "n_trades": 0, "n_win": 0, "n_loss": 0,
                "win_rate": 0.0, "low_confidence": True,
                "pool_suggestions": _pool_suggestions(cell_obj), "rows": []}

    sub = full_derived
    sub = sub[sub["entry_atm_iv_band"] == str(cell_obj["entry_atm_iv_band"])]
    sub = sub[sub["entry_hour_ist"] == int(cell_obj["entry_hour_ist"])]
    sub = sub[sub["expiry_bucket"] == str(cell_obj["expiry_bucket"])]
    sub = sub[np.isclose(sub["delta_target"].astype(float),
                          float(cell_obj["delta_target"]), atol=1e-6)]

    n_trades = len(sub)
    if n_trades == 0:
        return {"cell": cell_obj, "n_trades": 0, "n_win": 0, "n_loss": 0,
                "win_rate": 0.0, "low_confidence": True,
                "pool_suggestions": _pool_suggestions(cell_obj), "rows": []}

    wins   = sub[sub["is_win"]]
    losses = sub[~sub["is_win"]]
    n_win, n_loss = len(wins), len(losses)
    win_rate = n_win / n_trades if n_trades else 0.0
    low_conf = n_win < min_n_per_side or n_loss < min_n_per_side

    # σ baseline on the FULL derived universe (stable cross-cell scale).
    sigmas: dict[str, float] = {}
    for col, _label, _cat in _M7_LOSS_INDICATORS:
        if col in full_derived.columns and full_derived[col].notna().any():
            sigmas[col] = float(full_derived[col].std())
        else:
            sigmas[col] = 0.0

    try:
        from scipy.stats import ttest_ind  # type: ignore
        _have_scipy = True
    except Exception:
        _have_scipy = False

    rows = []
    for col, label, category in _M7_LOSS_INDICATORS:
        if col not in sub.columns:
            continue
        w_vals = wins[col].dropna().astype(float)
        l_vals = losses[col].dropna().astype(float)
        avg_win  = float(w_vals.mean()) if len(w_vals)  else float("nan")
        avg_loss = float(l_vals.mean()) if len(l_vals) else float("nan")
        gap = (avg_win - avg_loss) if (
            np.isfinite(avg_win) and np.isfinite(avg_loss)) else float("nan")
        sigma = sigmas.get(col, 0.0)
        discriminating = bool(
            np.isfinite(gap) and sigma > 0 and
            abs(gap) > discriminate_sigma * sigma
        )

        p_t = None
        if _have_scipy and len(w_vals) >= 2 and len(l_vals) >= 2:
            try:
                _stat, p_val = ttest_ind(w_vals, l_vals, equal_var=False)
                if np.isfinite(p_val):
                    p_t = round(float(p_val), 6)
            except Exception:
                p_t = None

        rows.append({
            "indicator": col,
            "label": label,
            "category": category,
            "avg_win":  None if not np.isfinite(avg_win)  else round(avg_win, 6),
            "avg_loss": None if not np.isfinite(avg_loss) else round(avg_loss, 6),
            "gap":      None if not np.isfinite(gap)      else round(gap, 6),
            "sigma":    round(sigma, 6),
            "discriminating": discriminating,
            "p_value_t": p_t,
            "n_win":  int(len(w_vals)),
            "n_loss": int(len(l_vals)),
        })

    # Sort discriminating-first then by |gap|/σ desc.
    def _sort_key(r):
        sig = r["sigma"] or 0.0
        eff = abs(r["gap"]) / sig if (sig > 0 and r["gap"] is not None) else 0.0
        return (-int(r["discriminating"]), -eff)
    rows.sort(key=_sort_key)

    return {
        "cell": cell_obj,
        "n_trades": n_trades,
        "n_win": n_win,
        "n_loss": n_loss,
        "win_rate": round(win_rate, 4),
        "low_confidence": low_conf,
        "pool_suggestions": _pool_suggestions(cell_obj) if low_conf else [],
        "rows": rows,
    }


def _losses_empty_response(scope_summary: dict) -> dict:
    """Empty losses_distribution payload preserving the scope_summary block."""
    return {
        "n_losses": 0, "n_total": 0, "loss_rate": 0.0,
        "avg_loss_usd": 0.0, "total_loss_usd": 0.0, "worst_loss_usd": 0.0,
        "by_cause": {}, "by_band": {}, "rows": [],
        "scope_summary": scope_summary,
    }


@router.get("/losses_distribution")
def get_losses_distribution(
    dimensions: Optional[str] = Query(None,
        description="Comma-separated dim cols, e.g. 'loss_cause,entry_atm_iv_band'"),
    exit_rule: Optional[str] = None,
    metric: str = Query("avg_net_pnl",
        description="Best-cell selection metric for scope=full_coverage"),
    scope: Optional[str] = Query(None,
        description="null (universe) | 'full_coverage' | 'best_combo'"),
    ranking: Optional[str] = Query("credit",
        description="credit | margin — used only when scope=best_combo"),
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
    loss_cause: Optional[str] = None,
    include_trades: bool = Query(False,
        description="When true, also return a `losers_sample` array of "
                    "individual losing trades — used by the Losses Explorer "
                    "drill-down."),
    trades_limit: int = Query(50, ge=1, le=200,
        description="Max losing trades to return when include_trades=true"),
    trades_offset: int = Query(0, ge=0,
        description="Offset for paginating losers_sample"),
    trades_sort: str = Query("pnl_asc",
        description="pnl_asc | pnl_desc | friday_asc | friday_desc | band"),
    only_sl_hits: bool = Query(False,
        description="Restrict losers_sample to exit_reason=='rule_trigger'"),
    cells: Optional[str] = Query(None,
        description="JSON list of {entry_atm_iv_band, entry_hour_ist, "
                    "expiry_bucket, delta_target, rule} cells. When given, "
                    "the explorer pulls losses ONLY from these cells "
                    "(scope/ranking/metric are ignored). This is what the "
                    "Best Combo per IV band table on the dashboard passes "
                    "down — its 10 per-band winning rows."),
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """Loss distribution over a chosen trade set.

    Four modes (in priority order — first match wins):

      - **`cells` provided**: explicit list of per-band cells. The endpoint
        derives trades for each `(rule_dict)` once, intersects with each
        cell's `(band, hour, expiry, delta)`, concats. Used by the Losses
        Explorer when the dashboard's Best Combo table is the source of
        truth. Filters in the bar still apply via _query_filters.
      - `scope=full_coverage`: per-band best-cell strict ("rule" kind)
        trade set (legacy — Full Coverage table was removed from the
        dashboard but the endpoint stays callable for back-compat).
      - `scope=best_combo`: per-band best (expiry, delta, rule) using
        ranking=credit|margin (legacy — superseded by `cells`).
      - default (`scope=None`, no `cells`): universe — all filtered trades.

    `loss_cause` filter always applies on top of the scoped set.
    """
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist, expiry_bucket,
                              loss_cause=loss_cause)
    rule = _parse_exit_rule(exit_rule)

    scope_summary: dict = {
        "scope": scope,
        "ranking": ranking if scope == "best_combo" else None,
        "metric": metric if scope == "full_coverage" else None,
        "n_in_scope": 0,
        "exit_rule_overridden": False,
        "per_band_rules": [],
    }

    # ── Mode 1: explicit `cells` list (preferred path used by the dashboard's
    # Best Combo table → Losses Explorer pipeline). Takes priority over scope.
    if cells:
        try:
            cell_list = json.loads(cells)
            if not isinstance(cell_list, list):
                raise ValueError("`cells` must be a JSON array")
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400,
                                detail=f"Invalid `cells` JSON: {exc}")
        if not cell_list:
            return _losses_empty_response(scope_summary)

        scope_summary["scope"] = "cells"
        scope_summary["exit_rule_overridden"] = True  # each cell has own rule

        # Group cells by rule_dict so we call _derive_exits once per unique rule
        # (cache absorbs duplicates within a request too, but this keeps the
        # request fast on cold cache).
        from collections import defaultdict
        by_rule_key: dict[str, list[dict]] = defaultdict(list)
        rule_dicts: dict[str, dict] = {}
        for cell in cell_list:
            if not isinstance(cell, dict):
                continue
            rule_dict = cell.get("rule") or {}
            if not isinstance(rule_dict, dict):
                rule_dict = {}
            # Normalise rule_dict (drop nulls) so the cache key is stable.
            clean_rule = {k: v for k, v in rule_dict.items() if v is not None}
            key = json.dumps(clean_rule, sort_keys=True)
            by_rule_key[key].append(cell)
            rule_dicts[key] = clean_rule

        # Cache-state precheck. On a cold cache, N unique rules would block
        # for N × ~5–15s; the request can outlive a backend restart and 500.
        # Instead we fire async warmups and return a warming response — the
        # frontend polls until everything is cached, then the next request
        # returns instantly (pure pandas filter on the cached frames).
        _load_trades(dataset)  # refresh mtime for this dataset's cache key
        _, ds_mtime = _TRADES_BY_DATASET.get(dataset, (None, 0.0))
        cold_rules: list[str] = []
        for rule_key, rule_dict in rule_dicts.items():
            cache_key = (dataset, rule_key, ds_mtime)
            if _EXIT_CACHE.get(cache_key) is None:
                cold_rules.append(rule_key)
                _warmup_rule_async(rule_dict, dataset=dataset)
        if cold_rules:
            return {
                "n_losses": 0, "n_total": 0, "loss_rate": 0.0,
                "avg_loss_usd": 0.0, "total_loss_usd": 0.0, "worst_loss_usd": 0.0,
                "by_cause": {}, "by_band": {}, "by_band_stats": [], "rows": [],
                "losers_sample": [], "losers_sample_total": 0,
                "losers_sample_offset": 0, "losers_sample_limit": 0,
                "scope_summary": {
                    **scope_summary,
                    "warming": True,
                    "rules_done": len(rule_dicts) - len(cold_rules),
                    "rules_total": len(rule_dicts),
                },
            }

        per_cell_dfs: list[pd.DataFrame] = []
        per_band_rules: list[dict] = []
        for rule_key, rule_cells in by_rule_key.items():
            rule_dict = rule_dicts[rule_key]
            try:
                derived_rule = _derive_exits(filters, rule_dict,
                                              dataset=dataset)
            except Exception as exc:  # noqa: BLE001
                log.warning("cells mode: _derive_exits failed for rule %s: %s",
                            rule_dict, exc)
                continue
            if derived_rule is None or derived_rule.empty:
                continue
            for cell in rule_cells:
                band = cell.get("entry_atm_iv_band")
                hour = cell.get("entry_hour_ist")
                expiry = cell.get("expiry_bucket")
                delta = cell.get("delta_target")
                rule_label = cell.get("rule_label")
                mask = pd.Series(True, index=derived_rule.index)
                if band is not None:
                    mask = mask & (derived_rule["entry_atm_iv_band"] == band)
                if hour is not None:
                    try:
                        mask = mask & (derived_rule["entry_hour_ist"].astype("Int64") == int(hour))
                    except (TypeError, ValueError):
                        pass
                if expiry is not None:
                    mask = mask & (derived_rule["expiry_bucket"] == expiry)
                if delta is not None:
                    try:
                        mask = mask & (derived_rule["delta_target"].astype(float) == float(delta))
                    except (TypeError, ValueError):
                        pass
                sub = derived_rule[mask]
                n_band = int(len(sub))
                if n_band > 0:
                    per_cell_dfs.append(sub)
                per_band_rules.append({
                    "band": str(band) if band is not None else None,
                    "entry_hour_ist": int(hour) if hour is not None else None,
                    "expiry_bucket": str(expiry) if expiry is not None else None,
                    "delta_target": float(delta) if delta is not None else None,
                    "rule_label": str(rule_label) if rule_label is not None else None,
                    "rule_dict": rule_dict,
                    "n_trades": n_band,
                })

        scope_summary["per_band_rules"] = per_band_rules
        if per_cell_dfs:
            derived = pd.concat(per_cell_dfs, ignore_index=True)
            # De-dup defensively (a trade can only land in one cell since each
            # trade has one band, one hour, one expiry, one delta — but a
            # malformed cells payload could double-count).
            if "trade_id" in derived.columns:
                derived = derived.drop_duplicates(subset=["trade_id"], keep="first")
        else:
            derived = pd.DataFrame()
        scope_summary["n_in_scope"] = int(len(derived))

    elif scope == "full_coverage":
        # Filters flow through _query_filters → _derive_exits; the FILTERED
        # candidate pool is what we pick best cells from. Changing filters
        # changes the cells, which changes the trade set.
        candidates = _derive_exits(filters, rule, dataset=dataset)
        if candidates.empty:
            return _losses_empty_response(scope_summary)
        try:
            best = _best_cells_for_metric(candidates, metric)
        except HTTPException:
            raise
        if best.empty:
            return _losses_empty_response(scope_summary)
        # Take ALL trades in each band's best cell — matches the Full Coverage
        # table's per-band `n_trades` column (sum across rows). Each trade has
        # a single entry_atm_iv_band, so concat is duplicate-free.
        per_cell_dfs: list[pd.DataFrame] = []
        per_band_rules: list[dict] = []
        for _, row in best.iterrows():
            mask = (
                (candidates["entry_atm_iv_band"] == row["entry_atm_iv_band"]) &
                (candidates["entry_hour_ist"] == row["entry_hour_ist"]) &
                (candidates["expiry_bucket"] == row["expiry_bucket"]) &
                (candidates["delta_target"] == row["delta_target"])
            )
            sub = candidates[mask]
            if not sub.empty:
                per_cell_dfs.append(sub)
            per_band_rules.append({
                "band": str(row["entry_atm_iv_band"]) if row["entry_atm_iv_band"] is not None else None,
                "entry_hour_ist": int(row["entry_hour_ist"]) if pd.notna(row["entry_hour_ist"]) else None,
                "expiry_bucket": str(row["expiry_bucket"]) if row["expiry_bucket"] is not None else None,
                "delta_target": float(row["delta_target"]) if pd.notna(row["delta_target"]) else None,
                "n_trades": int(len(sub)),
            })
        derived = pd.concat(per_cell_dfs, ignore_index=True) if per_cell_dfs else pd.DataFrame()
        scope_summary["per_band_rules"] = per_band_rules
        scope_summary["n_in_scope"] = int(len(derived))

    elif scope == "best_combo":
        from app.api import m7_best_combo as bc
        # Hydrate / kick off background warmup if grid not yet ready.
        bc_state = bc._get_grid_state(dataset)
        if bc_state["status"] in ("pending", None):
            bc.kick_off_warmup(dataset)
        if bc_state["status"] == "warming":
            return {
                "n_losses": 0, "n_total": 0, "loss_rate": 0.0,
                "avg_loss_usd": 0.0, "total_loss_usd": 0.0, "worst_loss_usd": 0.0,
                "by_cause": {}, "by_band": {}, "rows": [],
                "scope_summary": {
                    **scope_summary,
                    "warming": True,
                    "rules_done": int(bc_state.get("rules_done", 0)),
                    "rules_total": int(bc_state.get(
                        "rules_total", len(bc._rule_variants()))),
                },
            }
        if bc_state["status"] == "error":
            raise HTTPException(status_code=500,
                                detail=f"Best-combo grid warmup failed: "
                                       f"{bc_state.get('error')}")
        grid = bc_state.get("grid")
        if grid is None or grid.empty:
            return _losses_empty_response(scope_summary)
        ranking_eff = ranking if ranking in ("credit", "margin") else "credit"
        scope_summary["ranking"] = ranking_eff
        best = bc._pick_best_per_band(grid, ranking_eff)
        if best.empty:
            return _losses_empty_response(scope_summary)
        scope_summary["exit_rule_overridden"] = True

        per_band_rules: list[dict] = []
        per_band_dfs: list[pd.DataFrame] = []
        for _, row in best.iterrows():
            band = row["iv_band"]
            expiry = row["expiry_bucket"]
            delta = row["delta_target"]
            rule_label = row["rule_label"]
            rule_dict = row["rule"] if row["rule"] is not None else {}
            try:
                band_derived = _derive_exits(filters, rule_dict,
                                              dataset=dataset)
            except Exception as exc:  # noqa: BLE001
                log.warning("scope=best_combo failed to derive band %s rule %s: %s",
                            band, rule_label, exc)
                continue
            n_band = 0
            if band_derived is not None and not band_derived.empty:
                try:
                    delta_f = float(delta) if delta is not None else None
                except (TypeError, ValueError):
                    delta_f = None
                mask = (band_derived["entry_atm_iv_band"] == band) & \
                       (band_derived["expiry_bucket"] == expiry)
                if delta_f is not None:
                    mask = mask & (band_derived["delta_target"].astype(float) == delta_f)
                sub = band_derived[mask]
                if not sub.empty:
                    per_band_dfs.append(sub)
                    n_band = int(len(sub))
            per_band_rules.append({
                "band": str(band) if band is not None else None,
                "rule_label": str(rule_label) if rule_label is not None else None,
                "rule_dict": dict(rule_dict) if isinstance(rule_dict, dict) else {},
                "expiry_bucket": str(expiry) if expiry is not None else None,
                "delta_target": float(delta) if delta is not None else None,
                "n_trades": n_band,
            })
        scope_summary["per_band_rules"] = per_band_rules
        if per_band_dfs:
            derived = pd.concat(per_band_dfs, ignore_index=True)
        else:
            derived = pd.DataFrame()
        scope_summary["n_in_scope"] = int(len(derived))

    else:
        # Universe (legacy / default)
        derived = _derive_exits(filters, rule, dataset=dataset)
        scope_summary["n_in_scope"] = int(len(derived))

    if derived is None or derived.empty:
        return _losses_empty_response(scope_summary)

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

    by_band = {}
    if "entry_atm_iv_band" in losers.columns:
        for band, sub in losers.groupby("entry_atm_iv_band", dropna=False):
            by_band[str(band)] = int(len(sub))

    # Per-IV-band loss stats — mirrors the loss-related columns of the FC /
    # iv_band_summary tables, but on the currently-scoped losers. One row per
    # band that has at least one losing trade; sorted in natural band order.
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
    if not losers.empty and "entry_atm_iv_band" in losers.columns:
        derived_band_n = (derived.groupby("entry_atm_iv_band", dropna=False)
                                  .size().to_dict())
        for band, sub in losers.groupby("entry_atm_iv_band", dropna=False):
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
                "entry_atm_iv_band": band_label,
                "n_band_total":   int(derived_band_n.get(band, 0)),
                "n_loss":         int(len(sub)),
                # Realized loss (after costs)
                "avg_loss_usd":     _safe_stat(net, "mean"),
                "total_loss_usd":   _safe_stat(net, "sum"),
                "largest_loss_usd": _safe_stat(net, "min"),
                # Path MTM stats (entry-cost only)
                "avg_loss_mtm":      _safe_stat(exit_mtm, "mean"),
                "total_loss_mtm":    _safe_stat(exit_mtm, "sum"),
                "largest_loss_mtm":  _safe_stat(exit_mtm, "min"),
                "avg_max_mtm_losers":_safe_stat(max_mtm,  "mean"),
                "avg_min_mtm_losers":_safe_stat(min_mtm,  "mean"),
                "max_mtm_losers":    _safe_stat(max_mtm,  "max"),
                "min_mtm_losers":    _safe_stat(min_mtm,  "min"),
                # Counts
                "n_losers_above_avg_max_mtm": n_above_avg_peak,
                "n_rule_trigger": n_rule_trigger,
                "n_hard_cap":     n_hard_cap,
            })

        def _band_key(s: Optional[str]) -> int:
            if not s:
                return 9999
            if s == "100+":
                return 1000
            try:
                return int(s.split("-")[0])
            except (ValueError, AttributeError):
                return 9999
        by_band_stats.sort(key=lambda r: _band_key(r.get("entry_atm_iv_band")))

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

    # ── Optional individual losing trades (for the Losses Explorer
    # drill-down). Sorted server-side, paginated; returns a thin row
    # schema sized for a clickable table — full diagnostic comes from
    # /trade_diagnostic on click. ───────────────────────────────────────
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
        elif trades_sort == "band":           sort_key, asc = "entry_atm_iv_band",    True
        # default = pnl_asc (worst losers first)
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
            losers_sample.append({
                "trade_id": str(r["trade_id"]) if r.get("trade_id") is not None else None,
                "friday_date_ist": str(r["friday_date_ist"]) if r.get("friday_date_ist") is not None else None,
                "entry_atm_iv_band": str(r["entry_atm_iv_band"]) if r.get("entry_atm_iv_band") is not None else None,
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


@router.get("/cell_worst_fridays")
def get_cell_worst_fridays(
    cell: str = Query(..., description="JSON: {entry_atm_iv_band, entry_hour_ist, expiry_bucket, delta_target}"),
    n: int = Query(5, ge=1, le=50,
        description="How many worst Fridays to surface"),
    n_special: int = Query(5, ge=1, le=20,
        description="How many top |z| context cols to return per Friday"),
    exit_rule: Optional[str] = None,
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """For a best-combo cell, return the N Fridays with the worst
    `net_pnl_estimate_usd`, plus a per-Friday "what made it special" diff:
    the top-K context columns where the Friday's value is most outside
    the cell's median±IQR (highest |z| score).

    Useful for tail-risk tuning ("which 5 Fridays are dragging this cell
    down, and why?"). Pairs with `cell_winners_vs_losers` (Chunk 3) for
    full per-cell loss anatomy.
    """
    try:
        cell_obj = json.loads(cell)
        if not isinstance(cell_obj, dict):
            raise ValueError("cell must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"bad cell payload: {e}")

    required = {"entry_atm_iv_band", "entry_hour_ist", "expiry_bucket", "delta_target"}
    missing = required - set(cell_obj.keys())
    if missing:
        raise HTTPException(status_code=400,
            detail=f"cell missing required keys: {sorted(missing)}")

    rule = _parse_exit_rule(exit_rule)
    full = _derive_exits({}, rule, dataset=dataset)
    if full.empty:
        return {"cell": cell_obj, "n_total_fridays": 0, "rows": []}

    sub = full
    sub = sub[sub["entry_atm_iv_band"] == str(cell_obj["entry_atm_iv_band"])]
    sub = sub[sub["entry_hour_ist"] == int(cell_obj["entry_hour_ist"])]
    sub = sub[sub["expiry_bucket"] == str(cell_obj["expiry_bucket"])]
    sub = sub[np.isclose(sub["delta_target"].astype(float),
                          float(cell_obj["delta_target"]), atol=1e-6)]
    if sub.empty:
        return {"cell": cell_obj, "n_total_fridays": 0, "rows": []}

    # Per-cell median + IQR for the indicator universe — used as the
    # "typical" reference each Friday is compared against.
    cell_median: dict[str, float] = {}
    cell_iqr: dict[str, float] = {}
    for col, _label, _cat in _M7_LOSS_INDICATORS:
        if col not in sub.columns:
            continue
        s = sub[col].dropna().astype(float)
        if s.empty:
            continue
        cell_median[col] = float(s.median())
        q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
        iqr = q3 - q1
        # IQR=0 → fall back to std so z-score is well-defined; if both are
        # zero (constant col), z is undefined and we drop the col.
        if iqr <= 0:
            iqr = float(s.std()) if s.std() > 0 else 0.0
        cell_iqr[col] = iqr

    # Sort losers ascending (worst first), take top n.
    worst = sub.sort_values("net_pnl_estimate_usd", ascending=True, kind="stable").head(n)

    rows = []
    for _, r in worst.iterrows():
        # Per-friday "what made it special": top-K |z| ctx cols.
        special: list[dict] = []
        for col, label, category in _M7_LOSS_INDICATORS:
            if col not in cell_median:
                continue
            v = r.get(col)
            if v is None or pd.isna(v):
                continue
            iqr = cell_iqr.get(col, 0.0)
            if iqr <= 0:
                continue
            z = (float(v) - cell_median[col]) / iqr
            special.append({
                "col": col, "label": label, "category": category,
                "value":       round(float(v), 6),
                "cell_median": round(cell_median[col], 6),
                "z": round(z, 3),
            })
        special.sort(key=lambda x: -abs(x["z"]))
        special = special[:n_special]

        # Path-summary-from-row: spot move %, IV jump %, rel time of trough.
        spot_in = float(r.get("spot_at_entry") or 0)
        spot_min = r.get("spot_at_min_mtm")
        spot_move_pct = (None if spot_min is None or pd.isna(spot_min) or spot_in <= 0
                        else round(100.0 * (float(spot_min) - spot_in) / spot_in, 3))
        entry_iv = float(r.get("entry_atm_iv") or 0)
        max_iv_w = r.get("max_atm_iv_in_window")
        max_iv_jump_pct = (None if max_iv_w is None or pd.isna(max_iv_w) or entry_iv <= 0
                          else round(100.0 * (float(max_iv_w) - entry_iv) / entry_iv, 3))

        rows.append({
            "friday_date_ist": str(r.get("friday_date_ist")),
            "trade_id":  str(int(r["trade_id"])),
            "net_pnl_estimate_usd": round(float(r["net_pnl_estimate_usd"]), 4),
            "gross_pnl_usd":        round(float(r["gross_pnl_usd"]), 4),
            "credit_usd": round(float(r.get("credit_usd") or 0), 4),
            "loss_cause": (None if r.get("loss_cause") is None or
                           (isinstance(r.get("loss_cause"), float) and pd.isna(r.get("loss_cause")))
                           else str(r.get("loss_cause"))),
            "is_win":   bool(r.get("is_win")),
            "exit_reason": str(r.get("exit_reason") or ""),
            "entry_atm_iv_pct":  None if pd.isna(r.get("entry_atm_iv_pct")) else round(float(r["entry_atm_iv_pct"]), 2),
            "spot_move_pct": spot_move_pct,
            "max_iv_jump_pct": max_iv_jump_pct,
            "rel_time_min_mtm": None if pd.isna(r.get("rel_time_min_mtm")) else round(float(r["rel_time_min_mtm"]), 3),
            "max_mtm_usd": None if pd.isna(r.get("max_mtm_usd")) else round(float(r["max_mtm_usd"]), 4),
            "min_mtm_usd": None if pd.isna(r.get("min_mtm_usd")) else round(float(r["min_mtm_usd"]), 4),
            "what_made_it_special": special,
        })

    return {
        "cell": cell_obj,
        "n_total_fridays": int(sub["friday_date_ist"].nunique()),
        "n_total_trades":  int(len(sub)),
        "n_returned": len(rows),
        "rows": rows,
    }


@router.get("/trade_diagnostic")
def get_trade_diagnostic(
    trade_id: str = Query(..., description="Trade ID to diagnose"),
    exit_rule: Optional[str] = None,
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """Sectioned diagnostic for one trade — every indicator at entry,
    per-leg breakdown, derived ratios, and hypothesis flags. Used by the
    Losses Explorer drill-down modal.

    Reuses _derive_exits cache so cost is dominated by a single dataframe
    lookup once the cache is warm.
    """
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits({}, rule, dataset=dataset)
    if derived is None or derived.empty:
        raise HTTPException(status_code=404, detail="No trades available")

    # trade_id may be string or int in source; coerce both sides to string for the lookup
    tid_str = str(trade_id)
    mask = derived["trade_id"].astype(str) == tid_str
    if not mask.any():
        raise HTTPException(status_code=404, detail=f"Trade not found: {trade_id}")

    row = derived[mask].iloc[0]
    return _project_trade_to_diagnostic(row)


@router.get("/trade_context_ohlc")
def get_trade_context_ohlc(
    trade_id: str = Query(..., description="m7 trade_id (string-int)"),
    pad_minutes_before: int = Query(120, ge=0, le=1440,
        description="Pad spot OHLC start backwards by N minutes for indicator warm-up"),
    pad_minutes_after: int = Query(30, ge=0, le=1440,
        description="Pad spot OHLC end forward by N minutes (post-exit visualization)"),
    exit_rule: Optional[str] = None,
    dataset: str = Query("delta_match",
        description="'delta_match' (default) or 'price_match'."),
):
    """Return spot 1m OHLC for the trade's window + per-minute IV and
    Greeks projections from the M7 path parquet. Used by Chunk 4's
    multi-pane chart (`M7TradePathChart`) to overlay client-side
    indicator computations on spot.

    Padding lets the chart render indicator warm-up bars before entry
    and post-exit recovery bars without forcing the trade itself to
    start at index 0.
    """
    try:
        tid = int(trade_id)
    except Exception:
        raise HTTPException(status_code=400, detail="trade_id must be int")

    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits({}, rule, dataset=dataset)
    if derived.empty or tid not in derived["trade_id"].astype(int).values:
        raise HTTPException(status_code=404, detail=f"trade_id {tid} not found")

    row = derived[derived["trade_id"].astype(int) == tid].iloc[0]
    entry_ts = int(row["entry_ts_utc"])
    exit_ts = int(row["exit_ts"])
    spot_at_entry = float(row["spot_at_entry"])
    loss_cause = row.get("loss_cause")
    if isinstance(loss_cause, float) and pd.isna(loss_cause):
        loss_cause = None
    em_7d  = row.get("expected_move_1sigma_7d")
    em_14d = row.get("expected_move_1sigma_14d")
    em_30d = row.get("expected_move_1sigma_30d")

    pad_b = pad_minutes_before * 60
    pad_a = pad_minutes_after * 60

    # Pull 1m spot OHLC over the padded window. Reuse the historical
    # endpoint's helper so the candlestick shape matches what other
    # charts already consume.
    from app.api.historical import _bucketed_spot_ohlc
    ohlc_df = _bucketed_spot_ohlc(entry_ts - pad_b, exit_ts + pad_a, "1m")
    ohlc = [{"time": int(r["time"]),
             "open":  float(r["open"]),
             "high":  float(r["high"]),
             "low":   float(r["low"]),
             "close": float(r["close"]),
             "volume": float(r["volume"]) if pd.notna(r["volume"]) else 0.0}
            for _, r in ohlc_df.iterrows()]

    # Per-minute IV / greeks aligned to spot bars from the path parquet.
    paths_glob_local = _paths_glob_for_dataset(dataset)
    conn = _duckdb_conn()
    try:
        path_sql = f"""
        SELECT ts AS time, atm_iv_now, call_iv, put_iv,
               net_delta, theta_per_vega_combined
        FROM read_parquet('{paths_glob_local}', hive_partitioning=true)
        WHERE trade_id = {tid} AND ts >= {entry_ts - pad_b} AND ts <= {exit_ts + pad_a}
        ORDER BY ts
        """
        path_df = conn.execute(path_sql).df()
    finally:
        conn.close()

    iv_series = [{"time": int(r["time"]),
                  "atm_iv":   None if pd.isna(r["atm_iv_now"]) else float(r["atm_iv_now"]),
                  "call_iv":  None if pd.isna(r["call_iv"])    else float(r["call_iv"]),
                  "put_iv":   None if pd.isna(r["put_iv"])     else float(r["put_iv"])}
                 for _, r in path_df.iterrows()]
    greeks_series = [{"time": int(r["time"]),
                      "net_delta":      None if pd.isna(r["net_delta"]) else float(r["net_delta"]),
                      "theta_per_vega": None if pd.isna(r["theta_per_vega_combined"]) else float(r["theta_per_vega_combined"])}
                     for _, r in path_df.iterrows()]

    return {
        "trade_id": str(tid),
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "spot_at_entry": spot_at_entry,
        "loss_cause": None if loss_cause is None else str(loss_cause),
        "expected_move_1sigma_7d_at_entry":  None if em_7d  is None or pd.isna(em_7d)  else float(em_7d),
        "expected_move_1sigma_14d_at_entry": None if em_14d is None or pd.isna(em_14d) else float(em_14d),
        "expected_move_1sigma_30d_at_entry": None if em_30d is None or pd.isna(em_30d) else float(em_30d),
        "ohlc": ohlc,
        "iv_series": iv_series,
        "greeks_series": greeks_series,
    }


@router.get("/meta")
def get_meta(
    dataset: str = Query("delta_match",
                          description="'delta_match' (default) or 'price_match'."),
):
    """Return the universe of dimension values for filter dropdowns."""
    df = _load_trades(dataset)
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
        # loss_cause classes (Chunk 1) — populated only on losers; static enum
        "loss_causes": ["directional", "vol_expansion", "path_dependent",
                        "gamma_squeeze", "skew_flip", "unclassified"],
    }

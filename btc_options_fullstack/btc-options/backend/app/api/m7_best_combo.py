"""M7 best-combo per IV band — sweeps a fixed exit-rule space, ranks by
% return on credit OR % return on margin.

Per IV band, finds the (expiry_bucket, delta_target, exit_rule) that maxes
the chosen ranking metric. Premium SL is fixed at 100% always-on; the rule
sweep varies max_profit_pct ∈ {10..100} and margin_target_pct ∈ {10..100}.

Adds three exit-time columns the iv_band_summary doesn't expose:
  - avg_exit_offset_minutes        (mean over all trades in the cell)
  - avg_loser_exit_offset_minutes  (restricted to net_pnl < 0; null if no losers)
  - avg_winner_exit_offset_minutes (restricted to net_pnl > 0; null if no winners)

Endpoint: GET /api/v1/m7/iv_band_best_combo?ranking=credit|margin
Optional: &include_grid=true  → also return the full 11,760-cell grid.

Cache strategy: 21 rule variants × ~30 s cold-DuckDB-scan each = ~10 min
total. That's too slow for a synchronous request, so the grid is computed
once in a background thread (kicked off at app startup AND on first
request). While the thread runs, the endpoint returns 202 with progress.
After completion the result is held in `_GRID_STATE["grid"]` for the
process lifetime; backend restart re-warms.
"""
from __future__ import annotations

from typing import Optional
import logging
import math
import os
import threading
import time

from fastapi import APIRouter, HTTPException, Query
import numpy as np
import pandas as pd

from app.api import m7_results as m7r

router = APIRouter()
log = logging.getLogger(__name__)

# Persist the final grid to disk so backend restarts skip the ~10 min rebuild.
# Invalidated by trades-parquet mtime — when the dataset changes, the grid
# file is recomputed.
GRID_PARQUET_PATH = os.path.join(m7r.M7_BASE_DIR, "m7_best_combo_grid_v3.parquet")


# ── Sweep dimensions ──────────────────────────────────────────────────────────

# Three premium SL levels × four take-profit families per SL:
#   1 baseline (SL only) + 10 max_profit + 10 margin_target + 11 fixed-hour
#   = 32 variants per SL × 3 SLs = 96 total rule variants.
_PREMIUM_SL_PCTS = [50, 75, 100]
_PCT_GRID = [10, 15, 20, 25, 30, 40, 50, 60, 75, 100]
# Hourly Saturday exits 8 AM..5 PM IST + 5:29 PM (just before settlement).
# 17.4833 = 17h + 29m / 60 — the engine accepts decimal hours.
_FIXED_HOURS: list[float] = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0,
                              14.0, 15.0, 16.0, 17.0, 17.4833]


def _hour_label(h: float) -> str:
    """Format a fixed-hour value into a stable label suffix.
    8.0 → '8', 17.0 → '17', 17.4833 → '1729' (5:29 PM)."""
    minutes = round((h - int(h)) * 60)
    if minutes == 0:
        return f"{int(h)}"
    return f"{int(h)}{minutes:02d}"


def _rule_variants() -> list[tuple[str, dict]]:
    """96 (label, rule_dict) tuples. Each dict goes to _derive_exits."""
    out: list[tuple[str, dict]] = []
    for sl in _PREMIUM_SL_PCTS:
        out.append((f"sl{sl}_baseline", {"premium_sl_pct": sl}))
        for p in _PCT_GRID:
            out.append((f"sl{sl}_max_profit_{p}",
                        {"premium_sl_pct": sl, "max_profit_pct": p}))
        for p in _PCT_GRID:
            out.append((f"sl{sl}_margin_target_{p}",
                        {"premium_sl_pct": sl, "margin_target_pct": p}))
        for h in _FIXED_HOURS:
            out.append((f"sl{sl}_exit_hr_{_hour_label(h)}",
                        {"premium_sl_pct": sl, "fixed_exit_hour_ist": h}))
    return out


# ── Ranking metrics — name → natural-best direction ──────────────────────────
# Used by _pick_best_per_band. "max" = larger is better (idxmax). "min" =
# smaller is better (idxmin). max_loss_usd is "max" because it's stored as
# a negative number (less negative = smaller loss = better).
_METRIC_DIRECTIONS: dict[str, str] = {
    # P&L (net of all costs)
    "avg_net_pnl":           "max",
    "sum_net_pnl":           "max",
    "avg_win_usd":           "max",
    "avg_loss_usd":          "max",  # losses negative → less negative is better
    "max_win_usd":           "max",
    "max_loss_usd":          "max",  # same — less negative is better
    "total_win_mtm":         "max",
    "total_loss_mtm":        "max",  # losses negative → less negative is better
    # % return
    "avg_pct_return_on_credit":  "max",
    "avg_pct_return_on_margin":  "max",
    "avg_pct_return_on_credit_winners": "max",
    "avg_pct_return_on_margin_winners": "max",
    "avg_pct_max_mtm_on_credit": "max",  # peak unrealized as % credit
    "avg_pct_min_mtm_on_credit": "max",  # trough as % credit (less negative better)
    # Risk (drawdown / streaks)
    "avg_min_mtm_losers":    "max",  # negative → less negative is better
    "avg_min_mtm_winners":   "max",
    "max_consec_losses":     "min",
    "max_consec_sl_hits":    "min",
    # Win counts
    "win_rate":              "max",
    "n_wins":                "max",
    "n_losses":              "min",
    "n_trades":              "max",
}


# Metrics replicated from get_iv_band_summary's EXTRA_METRICS list — keeps
# this endpoint's row schema a superset of what the frontend already renders.
_EXTRA_METRICS = [
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
    "n_rule_trigger", "n_hard_cap", "n_losses", "n_wins",
    "max_consec_losses", "max_consec_wins", "max_consec_sl_hits",
    "n_winners_below_avg_min_mtm", "n_losers_above_avg_max_mtm",
]


def _band_sort_key(b) -> int:
    """Natural order: 0-20, 20-30, …, 100+."""
    if b == "100+":
        return 1000
    try:
        return int(str(b).split("-")[0])
    except (ValueError, AttributeError):
        return 9999


def _safe_mean(s: pd.Series) -> Optional[float]:
    """Mean that returns None on empty / all-NaN."""
    if s.empty:
        return None
    val = s.mean()
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return float(val)


def _compute_cell_metrics(sub: pd.DataFrame) -> dict:
    """Compute every EXTRA_METRIC + the three exit-time means for one cell.

    `sub` is the slice of derived trades for one (iv_band, expiry, delta, rule)
    cell. Empty cells are caller-handled (we never receive an empty sub here).
    """
    grp = sub.groupby(lambda _: 0, sort=False)  # single group → single row
    metrics: dict[str, Optional[float]] = {}
    for m in _EXTRA_METRICS:
        try:
            val = float(m7r._metric_score(grp, m).iloc[0])
            metrics[m] = m7r._round_score(m, val)
        except Exception:
            metrics[m] = None

    # Exit-time means — computed from exit_ts (unix sec) and entry_ts_utc.
    if "exit_ts" in sub.columns and "entry_ts_utc" in sub.columns:
        offset_min = (sub["exit_ts"].astype(float)
                      - sub["entry_ts_utc"].astype(float)) / 60.0
        metrics["avg_exit_offset_minutes"] = _safe_mean(offset_min)
        if "is_win" in sub.columns:
            metrics["avg_winner_exit_offset_minutes"] = _safe_mean(
                offset_min[sub["is_win"].astype(bool)]
            )
            metrics["avg_loser_exit_offset_minutes"] = _safe_mean(
                offset_min[~sub["is_win"].astype(bool)]
            )
        else:
            metrics["avg_winner_exit_offset_minutes"] = None
            metrics["avg_loser_exit_offset_minutes"] = None
    else:
        metrics["avg_exit_offset_minutes"] = None
        metrics["avg_winner_exit_offset_minutes"] = None
        metrics["avg_loser_exit_offset_minutes"] = None

    # Round exit-time fields to 1 decimal minute for the wire.
    for k in ("avg_exit_offset_minutes",
              "avg_winner_exit_offset_minutes",
              "avg_loser_exit_offset_minutes"):
        if metrics[k] is not None:
            metrics[k] = round(metrics[k], 1)

    metrics["n_trades"] = int(len(sub))
    return metrics


def _build_grid(progress_cb=None) -> pd.DataFrame:
    """Compute the full (iv_band × expiry × delta × entry_hour × rule) grid.

    Per rule, derive the full trade set once, group by (iv_band, expiry,
    delta, entry_hour), compute metrics, append rows. After processing each
    rule we DROP that rule's entry from `_EXIT_CACHE` to keep peak memory
    bounded (~16 MB × N rules otherwise; many WSL setups OOM-kill at ~750 MB
    resident).

    Optional `progress_cb(rules_done, rules_total)` is called after each
    rule finishes — used by `_warmup_thread` to update the public progress
    counter so 202 responses can show a useful "X/N" hint.
    """
    rows: list[dict] = []
    variants = _rule_variants()
    for i, (rule_label, rule_dict) in enumerate(variants):
        # Track whether the cache existed before our call so we know whether
        # to evict afterwards. (Cache hit → leave it alone; cache miss → drop.)
        m7r._load_trades()  # ensure mtime is fresh
        rule_key = (
            __import__("json").dumps(rule_dict or {}, sort_keys=True),
            m7r._TRADES_MTIME,
        )
        was_cached_before = rule_key in m7r._EXIT_CACHE

        derived = m7r._derive_exits({}, rule_dict)
        if progress_cb is not None:
            progress_cb(i + 1, len(variants))

        if derived is not None and not derived.empty:
            keep = derived[
                derived["entry_atm_iv_band"].notna()
                & derived["expiry_bucket"].notna()
                & derived["delta_target"].notna()
                & derived["entry_hour_ist"].notna()
            ]
            if not keep.empty:
                grouped = keep.groupby(
                    ["entry_atm_iv_band", "expiry_bucket",
                     "delta_target", "entry_hour_ist"],
                    dropna=False, sort=False,
                )
                for (iv_band, expiry, delta, hour), sub in grouped:
                    if sub.empty:
                        continue
                    cell = _compute_cell_metrics(sub)
                    cell.update({
                        "iv_band": iv_band,
                        "expiry_bucket": expiry,
                        "delta_target": float(delta) if delta is not None else None,
                        "entry_hour_ist": int(hour) if hour is not None else None,
                        "rule_label": rule_label,
                        "rule": rule_dict,
                    })
                    rows.append(cell)

        # Memory hygiene — drop the freshly-populated cache entry now that
        # we've extracted everything we need from it.
        if not was_cached_before:
            m7r._EXIT_CACHE.pop(rule_key, None)
        del derived

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _flatten_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """The grid stores `rule` as a dict — pyarrow can't write nested dicts
    natively. Flatten to three rule_* numeric columns before persisting."""
    if df.empty or "rule" not in df.columns:
        return df
    out = df.copy()
    out["rule_premium_sl_pct"] = out["rule"].apply(
        lambda r: (r or {}).get("premium_sl_pct"))
    out["rule_max_profit_pct"] = out["rule"].apply(
        lambda r: (r or {}).get("max_profit_pct"))
    out["rule_margin_target_pct"] = out["rule"].apply(
        lambda r: (r or {}).get("margin_target_pct"))
    out = out.drop(columns=["rule"])
    return out


def _unflatten_after_load(df: pd.DataFrame) -> pd.DataFrame:
    """Inverse of `_flatten_for_parquet` — reconstruct nested rule dict."""
    if df.empty:
        return df
    rule_cols = ["rule_premium_sl_pct", "rule_max_profit_pct", "rule_margin_target_pct"]
    if not all(c in df.columns for c in rule_cols):
        return df
    out = df.copy()

    def _build_rule(r):
        d = {}
        for src, dst in zip(rule_cols, ["premium_sl_pct", "max_profit_pct", "margin_target_pct"]):
            v = r.get(src)
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                d[dst] = int(v) if dst != "premium_sl_pct" or v == int(v) else v
        return d

    out["rule"] = out[rule_cols].to_dict(orient="records")
    out["rule"] = out["rule"].apply(_build_rule)
    out = out.drop(columns=rule_cols)
    return out


def _grid_cache_is_valid() -> bool:
    """True if the parquet snapshot exists AND is newer than the trades
    parquet (i.e. the dataset hasn't changed since we last computed)."""
    if not os.path.exists(GRID_PARQUET_PATH):
        return False
    grid_mtime = os.path.getmtime(GRID_PARQUET_PATH)
    trades_path = (m7r.TRADES_ENRICHED_PATH
                   if os.path.exists(m7r.TRADES_ENRICHED_PATH)
                   else m7r.TRADES_PATH)
    if not os.path.exists(trades_path):
        return True  # weird, but trust the snapshot
    return grid_mtime >= os.path.getmtime(trades_path)


def _try_load_grid_from_disk() -> Optional[pd.DataFrame]:
    """Load persisted grid if present and fresh; else None."""
    if not _grid_cache_is_valid():
        return None
    try:
        df = pd.read_parquet(GRID_PARQUET_PATH)
        return _unflatten_after_load(df)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to load M7 best-combo grid from %s: %s",
                    GRID_PARQUET_PATH, exc)
        return None


def _persist_grid_to_disk(df: pd.DataFrame) -> None:
    """Write the grid to parquet so future restarts skip the rebuild."""
    if df is None or df.empty:
        return
    try:
        out = _flatten_for_parquet(df)
        # Pyarrow rejects pure-NaN object cols; coerce to float where safe.
        for c in out.columns:
            if out[c].dtype == object:
                # Try numeric coercion; leave strings alone
                if c in ("iv_band", "expiry_bucket", "rule_label"):
                    continue
                with pd.option_context("future.no_silent_downcasting", True):
                    out[c] = pd.to_numeric(out[c], errors="ignore")
        out.to_parquet(GRID_PARQUET_PATH, index=False)
        log.info("M7 best-combo grid persisted to %s (%d cells)",
                 GRID_PARQUET_PATH, len(out))
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to persist M7 best-combo grid: %s", exc)


# ── Background warmup ─────────────────────────────────────────────────────────

# Single-process state — fine for the single-uvicorn-worker deployment.
# Resets on every backend restart (intentional; keeps things simple and the
# grid is fully recomputable from disk parquets).
_GRID_STATE: dict = {
    "status": "pending",          # pending | warming | ready | error
    "rules_done": 0,
    "rules_total": len(_rule_variants()),
    "grid": None,                 # pd.DataFrame once status == "ready"
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_GRID_LOCK = threading.Lock()


def _warmup_thread() -> None:
    """Run the grid build in a background thread, updating `_GRID_STATE`.

    Persists the result to `GRID_PARQUET_PATH` so future backend restarts
    skip the rebuild entirely.
    """
    def _on_progress(done: int, total: int) -> None:
        _GRID_STATE["rules_done"] = done
        _GRID_STATE["rules_total"] = total

    try:
        _GRID_STATE["status"] = "warming"
        _GRID_STATE["started_at"] = time.time()
        grid = _build_grid(progress_cb=_on_progress)
        _GRID_STATE["grid"] = grid
        _GRID_STATE["status"] = "ready"
        _GRID_STATE["finished_at"] = time.time()
        # Persist AFTER marking ready — even if the disk write fails the
        # grid is usable in-memory for this process lifetime.
        _persist_grid_to_disk(grid)
    except Exception as exc:  # noqa: BLE001 — surface to the endpoint
        _GRID_STATE["status"] = "error"
        _GRID_STATE["error"] = repr(exc)
        _GRID_STATE["finished_at"] = time.time()


def kick_off_warmup() -> bool:
    """Hydrate the grid: load from disk if present + fresh, else spawn the
    warmup thread. Idempotent — safe to call from app startup AND lazily
    from the endpoint on a pending state.

    Returns True iff a NEW background build was kicked off (so the caller
    can log the warmup start). Returns False if grid is already ready (from
    cache or previous run) or already warming.
    """
    with _GRID_LOCK:
        if _GRID_STATE["status"] in ("warming", "ready"):
            return False
        # Try fast path first — disk-cached grid from a prior run.
        cached = _try_load_grid_from_disk()
        if cached is not None and not cached.empty:
            _GRID_STATE["grid"] = cached
            _GRID_STATE["status"] = "ready"
            _GRID_STATE["rules_done"] = _GRID_STATE["rules_total"]
            _GRID_STATE["started_at"] = time.time()
            _GRID_STATE["finished_at"] = time.time()
            log.info("M7 best-combo grid loaded from disk cache (%d cells)",
                     len(cached))
            return False
        # Disk cache miss → kick off background rebuild.
        _GRID_STATE["status"] = "warming"
        _GRID_STATE["rules_done"] = 0
        _GRID_STATE["error"] = None
    t = threading.Thread(target=_warmup_thread, daemon=True,
                         name="m7-best-combo-warmup")
    t.start()
    return True


def _resolve_metric(name: str) -> str:
    """Map historical short-form names to the actual grid column.
    Keeps the older `ranking=credit|margin` URLs working."""
    aliases = {
        "credit": "avg_pct_return_on_credit",
        "margin": "avg_pct_return_on_margin",
    }
    return aliases.get(name, name)


def _idx_best(series: pd.Series, direction: str) -> int:
    """Index of best value in a Series under the given direction."""
    return series.idxmin() if direction == "min" else series.idxmax()


def _pick_best_per_band(
    grid: pd.DataFrame,
    ranking: str,
    *,
    secondary: Optional[str] = None,
    tolerance_pct: Optional[float] = None,
) -> pd.DataFrame:
    """For each IV band, pick one cell.

    Pure mode (no `secondary`): per-band idxmax/idxmin on `ranking`.
    Tiebreak mode (secondary given): per band, find primary's best value,
    keep cells whose primary is within `tolerance_pct` of that best, then
    pick by secondary.

    Tolerance is relative — `tolerance_pct=5.0` keeps cells whose primary
    differs from the best by ≤ 5% of |best|. This works across metric units
    (USD, %, counts) without unit-specific logic.

    NO n-gate — every band that has any cells shows up.
    """
    primary_col = _resolve_metric(ranking)
    if grid.empty or primary_col not in grid.columns:
        return grid.iloc[0:0]
    primary_dir = _METRIC_DIRECTIONS.get(primary_col, "max")
    valid = grid.dropna(subset=[primary_col])
    if valid.empty:
        return grid.iloc[0:0]

    use_tiebreak = (
        secondary is not None
        and tolerance_pct is not None
        and tolerance_pct > 0
    )
    secondary_col = _resolve_metric(secondary) if use_tiebreak else None
    if use_tiebreak and (secondary_col not in valid.columns):
        # Secondary not present in grid — silently fall back to pure mode.
        use_tiebreak = False
    secondary_dir = (
        _METRIC_DIRECTIONS.get(secondary_col, "max") if use_tiebreak else None
    )

    rows: list[pd.Series] = []
    for band, sub in valid.groupby("iv_band", dropna=False, sort=False):
        if sub.empty:
            continue
        if not use_tiebreak:
            idx = _idx_best(sub[primary_col], primary_dir)
            rows.append(sub.loc[idx])
            continue
        # Tiebreak: filter to within-tolerance of band's best, then pick by secondary.
        best_val = (sub[primary_col].min() if primary_dir == "min"
                    else sub[primary_col].max())
        if best_val is None or pd.isna(best_val):
            continue
        # Use absolute-value of best as the relative-tolerance reference.
        # If best is 0, fall back to a small absolute epsilon to avoid
        # matching everything.
        denom = abs(float(best_val)) if best_val != 0 else 1e-9
        delta = abs(sub[primary_col].astype(float) - float(best_val))
        within = sub[(delta / denom) * 100.0 <= float(tolerance_pct)]
        sub_for_secondary = within.dropna(subset=[secondary_col])
        if sub_for_secondary.empty:
            # Fall back to plain primary best when the secondary is missing.
            idx = _idx_best(sub[primary_col], primary_dir)
            rows.append(sub.loc[idx])
            continue
        idx = _idx_best(sub_for_secondary[secondary_col], secondary_dir)
        rows.append(sub_for_secondary.loc[idx])

    if not rows:
        return valid.iloc[0:0]
    best = pd.DataFrame(rows).copy()
    best["score"] = best[primary_col]
    if use_tiebreak and secondary_col is not None:
        best["secondary_score"] = best[secondary_col]
    best = best.sort_values(
        "iv_band",
        key=lambda s: s.map(_band_sort_key),
    ).reset_index(drop=True)
    return best


def _records(df: pd.DataFrame) -> list[dict]:
    """JSON-safe records — NaN/NaT → None, numpy scalars → python primitives."""
    if df.empty:
        return []
    out: list[dict] = []
    for rec in df.replace({np.nan: None, pd.NaT: None}).to_dict(orient="records"):
        clean: dict = {}
        for k, v in rec.items():
            if v is None:
                clean[k] = None
            elif isinstance(v, (np.integer,)):
                clean[k] = int(v)
            elif isinstance(v, (np.floating,)):
                clean[k] = None if math.isnan(float(v)) else float(v)
            elif isinstance(v, (np.bool_,)):
                clean[k] = bool(v)
            else:
                clean[k] = v
        out.append(clean)
    return out


# ── Endpoint ──────────────────────────────────────────────────────────────────

_VALID_RANKINGS = set(_METRIC_DIRECTIONS.keys()) | {"credit", "margin"}


@router.get("/iv_band_best_combo")
def get_iv_band_best_combo(
    ranking: str = Query("avg_net_pnl",
                         description="Primary metric. Any key in _METRIC_DIRECTIONS, or legacy 'credit'/'margin'."),
    secondary: Optional[str] = Query(None,
                                     description="Tiebreak metric. When given, cells within tolerance_pct of the per-band primary best are re-ranked by this."),
    tolerance_pct: float = Query(5.0, ge=0.0, le=100.0,
                                  description="Relative tolerance (% of |primary best|). Only used when secondary is provided."),
    include_grid: bool = Query(False,
                               description="If true, also return the full grid"),
):
    """For each of the 10 IV bands, the best (expiry, delta, exit_rule) combo
    by the chosen ranking metric.

    Sweep: 96 rule variants — premium_sl ∈ {50, 75, 100} × {baseline, 10
    max_profit, 10 margin_target, 11 fixed_hour} = 32 per SL × 3 = 96.

    Multi-criteria selection (optional): pass `secondary` and `tolerance_pct`
    to filter per-band cells to within tolerance of the primary's best, then
    re-rank by `secondary`. Lets the user trade off (e.g.) net P&L against
    drawdown.

    Returns 200 once the background grid is ready. Returns 202 with progress
    while warming. Either kicks off the warmup if it hasn't started yet.
    """
    if ranking not in _VALID_RANKINGS:
        raise HTTPException(status_code=400,
                            detail=f"ranking must be one of {sorted(_VALID_RANKINGS)}")
    if secondary is not None and secondary not in _VALID_RANKINGS:
        raise HTTPException(status_code=400,
                            detail=f"secondary must be one of {sorted(_VALID_RANKINGS)}")

    if _GRID_STATE["status"] == "pending":
        kick_off_warmup()

    if _GRID_STATE["status"] == "warming":
        return {
            "ranking": ranking,
            "secondary": secondary,
            "tolerance_pct": tolerance_pct,
            "status": "warming",
            "rules_done": int(_GRID_STATE["rules_done"]),
            "rules_total": int(_GRID_STATE["rules_total"]),
            "started_at": _GRID_STATE["started_at"],
            "rows": [],
        }
    if _GRID_STATE["status"] == "error":
        raise HTTPException(status_code=500,
                            detail=f"warmup failed: {_GRID_STATE['error']}")

    grid: pd.DataFrame = _GRID_STATE["grid"]
    if grid is None or grid.empty:
        return {"ranking": ranking, "secondary": secondary,
                "tolerance_pct": tolerance_pct,
                "status": "ready", "rows": [],
                "n_rules": 0, "n_cells": 0}
    best = _pick_best_per_band(
        grid, ranking,
        secondary=secondary,
        tolerance_pct=tolerance_pct if secondary else None,
    )

    payload = {
        "ranking": ranking,
        "secondary": secondary,
        "tolerance_pct": tolerance_pct,
        "status": "ready",
        "rows": _records(best),
        "n_rules": len(_rule_variants()),
        "n_cells": int(len(grid)),
    }
    if include_grid:
        payload["grid"] = _records(grid)
    return payload


@router.get("/iv_band_best_combo/status")
def get_iv_band_best_combo_status():
    """Lightweight progress probe for clients polling during warmup."""
    return {
        "status": _GRID_STATE["status"],
        "rules_done": int(_GRID_STATE["rules_done"]),
        "rules_total": int(_GRID_STATE["rules_total"]),
        "started_at": _GRID_STATE["started_at"],
        "finished_at": _GRID_STATE["finished_at"],
        "error": _GRID_STATE["error"],
    }

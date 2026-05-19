"""M7 Friday-locked-band best-combo — variant where each Friday is pinned to
ONE IV band (computed from Sat-daily-expiry ATM IV) instead of per-trade
entry_atm_iv. Every Friday lands in exactly one band → no skip / no duplicate
across expiry-delta-hour combos.

Modes (UI toggle):
  A1 — Sat-IV snapshot at 21:00 IST (default, single fixed read per Friday)
  B1 — Modal band (re-read at every entry hour, assign band most-hours-spent)
  D1 — Per-entry-hour band collapsed via prioritized tiebreaker chain

Grid file: /home/abhis/btc-data/derived/m7/m7_friday_band_grid_v1.parquet
  Built offline via `scripts/build_m7_friday_band_grid.py` (~4-5h).
  Mode A1 grid lives at this path; B1/D1 grids derived in-process from
  the same per-trade exits (see _build_mode_grid).

Endpoints mirror /iv_band_best_combo:
  GET /friday_band_best_combo                   — per-band best pick
  GET /friday_band_best_combo/status            — grid warmup probe
  GET /friday_band_best_combo/rule_comparison   — all 96 rules for one cell
  GET /friday_band_best_combo/cross_band_check  — picked combo across bands
  GET /friday_band_best_combo/single_combo_simulation — counterfactual aggregate
  GET /friday_band_best_combo/friday_bands      — per-Friday Sat-IV path + band
"""
from __future__ import annotations

from typing import Optional
import hashlib
import logging
import math
import os
import threading
import time

from fastapi import APIRouter, HTTPException, Query
import numpy as np
import pandas as pd

from app.api import m7_best_combo as m7bc
from app.api import m7_results as m7r

router = APIRouter()
log = logging.getLogger(__name__)

GRID_PARQUET_PATH = "/home/abhis/btc-data/derived/m7/m7_friday_band_grid_v1.parquet"
SAT_IV_PATH = "/home/abhis/btc-data/derived/m7/m7_friday_sat_iv_v1.parquet"
PER_TRADE_DIR = "/home/abhis/btc-data/derived/m7/m7_friday_band_per_trade"
# Auto-persist cache for D1 multi-tiebreaker chains that don't have a named
# pre-built disk grid. First request for any chain pays ~20-30s build time;
# subsequent requests (across restarts) hit this cache.
CHAIN_CACHE_DIR = "/home/abhis/btc-data/derived/m7/m7_friday_band_grid_cache"


# Module-level per-chain build-progress tracking. Read by the
# /friday_band_best_combo/build_progress endpoint so the UI can show a bar
# while a long in-process build runs. Keyed by the same `cache_key` used in
# `_get_grid_for_mode` (e.g. "D1:best_avg_net_pnl,modal_band,earliest_hour_band").
_BUILD_PROGRESS: dict[str, dict] = {}
_BUILD_PROGRESS_LOCK = threading.Lock()


def _track_progress(cache_key: str, phase: str, progress: float,
                   message: Optional[str] = None) -> None:
    with _BUILD_PROGRESS_LOCK:
        _BUILD_PROGRESS[cache_key] = {
            "phase": phase,
            "progress": float(progress),
            "message": message,
            "ts": time.time(),
        }


def _read_progress(cache_key: str) -> dict:
    with _BUILD_PROGRESS_LOCK:
        return dict(_BUILD_PROGRESS.get(cache_key, {
            "phase": "idle", "progress": 0.0, "message": None, "ts": None,
        }))


# ── Mode + tiebreaker spec ────────────────────────────────────────────────────

_VALID_BAND_MODES = {"A1", "B1", "D1"}

# 20 tiebreakers across 5 categories. Each is a function-key understood by
# `_assign_d1_band` below. The last fallback in any priority chain is always
# `earliest_hour_band` (deterministic — never an unresolved tie).
_D1_TIEBREAKERS_NO_LOOKAHEAD = {
    "earliest_hour_band", "latest_hour_band", "modal_band", "median_hour_band",
    "highest_credit", "lowest_margin", "highest_entry_iv", "lowest_entry_iv",
}
_D1_TIEBREAKERS_LOOKAHEAD = {
    "best_avg_net_pnl", "best_total_net_pnl", "best_gross_pnl", "best_win_rate",
    "best_min_mtm", "worst_min_mtm", "best_max_mtm", "smallest_mtm_range",
    "best_max_loss", "best_avg_loss", "worst_max_loss",
}
_VALID_D1_TIEBREAKERS = _D1_TIEBREAKERS_NO_LOOKAHEAD | _D1_TIEBREAKERS_LOOKAHEAD
_D1_FALLBACK_DEFAULT = "earliest_hour_band"


# ── In-process state ──────────────────────────────────────────────────────────

_GRID_STATE: dict = {
    "status": "pending",   # pending | ready | error | not_built
    "grid_a1": None,        # mode A1 grid (loaded from parquet)
    "sat_iv": None,         # per-(friday, hour) Sat-IV map
    "per_trade": None,      # concat of all 96 per-rule per-trade parquets
    "mode_grids": {},       # cache: (mode, tiebreakers_key) → derived grid
    "error": None,
    "grid_a1_path": GRID_PARQUET_PATH,
}
_GRID_LOCK = threading.Lock()
_PER_TRADE_LOCK = threading.Lock()
_MODE_GRID_LOCK = threading.Lock()


def _grid_is_fresh(path: str) -> bool:
    """Grid valid if file exists and is newer than the trades parquet."""
    if not os.path.exists(path):
        return False
    trades = m7r.TRADES_ENRICHED_PATH if os.path.exists(m7r.TRADES_ENRICHED_PATH) else m7r.TRADES_PATH
    if not os.path.exists(trades):
        return True
    return os.path.getmtime(path) >= os.path.getmtime(trades)


def _load_grid_and_sat_iv() -> bool:
    """Lazy-load the A1 grid + Sat-IV map. Idempotent.

    Returns True if grid is ready, False if not yet built.
    """
    with _GRID_LOCK:
        if _GRID_STATE["status"] == "ready":
            return True
        if not os.path.exists(SAT_IV_PATH):
            _GRID_STATE["status"] = "not_built"
            _GRID_STATE["error"] = f"Sat-IV parquet missing: {SAT_IV_PATH}"
            return False
        if not _grid_is_fresh(GRID_PARQUET_PATH):
            _GRID_STATE["status"] = "not_built"
            _GRID_STATE["error"] = f"Friday-band grid missing or stale: {GRID_PARQUET_PATH}"
            return False
        try:
            grid = pd.read_parquet(GRID_PARQUET_PATH)
            grid = m7bc._unflatten_after_load(grid)
            grid = m7bc._enrich_grid_with_overall_mtm(grid)
            grid = m7bc._attach_composite_score(grid)
            grid = m7bc._attach_risk_adjusted(grid)
            sat = pd.read_parquet(SAT_IV_PATH)
            _GRID_STATE["grid_a1"] = grid
            _GRID_STATE["sat_iv"] = sat
            _GRID_STATE["status"] = "ready"
            _GRID_STATE["error"] = None
            log.info("M7 Friday-band grid loaded (%d cells, %d Sat-IV rows)",
                     len(grid), len(sat))
            return True
        except Exception as exc:  # noqa: BLE001
            _GRID_STATE["status"] = "error"
            _GRID_STATE["error"] = f"Load failed: {exc}"
            log.warning("M7 Friday-band grid load failed: %s", exc)
            return False


# ── Per-Friday band assignment ────────────────────────────────────────────────

def _band_lower_edge(band: str) -> int:
    """For tie-break: lower-band wins. '20-30' → 20, '100+' → 100."""
    if band == "100+":
        return 100
    try:
        return int(str(band).split("-")[0])
    except (ValueError, AttributeError):
        return 9999


def _friday_band_map_a1(sat: pd.DataFrame) -> dict[str, str]:
    """Mode A1: band at 21:00 IST snapshot per Friday."""
    snap = sat[sat["entry_hour_ist"] == 21][["friday_date_ist", "sat_iv_band"]]
    return dict(zip(snap["friday_date_ist"], snap["sat_iv_band"]))


def _friday_band_map_b1(sat: pd.DataFrame) -> dict[str, str]:
    """Mode B1: modal band across the 7 entry hours. Tie → lower band."""
    out: dict[str, str] = {}
    for friday, grp in sat.groupby("friday_date_ist"):
        counts = grp["sat_iv_band"].value_counts()
        max_count = counts.max()
        winners = counts[counts == max_count].index.tolist()
        # Lower-band tie-break.
        winners.sort(key=_band_lower_edge)
        out[friday] = winners[0]
    return out


def _friday_band_map_d1(sat: pd.DataFrame, trades_for_lookahead: Optional[pd.DataFrame],
                       tiebreakers: list[str]) -> dict[str, str]:
    """Mode D1: per-entry-hour band, collapsed by prioritized tiebreaker chain.

    For each Friday: identify the bands touched across 7 hours, then apply
    tiebreakers in priority order until one band remains.

    Lookahead tiebreakers require `trades_for_lookahead` (per-trade DataFrame
    with friday_date_ist, entry_hour_ist, and outcome columns). Pass None to
    skip lookahead tiebreakers (use no-lookahead only).
    """
    if not tiebreakers:
        tiebreakers = [_D1_FALLBACK_DEFAULT]
    # Always ensure earliest_hour_band is the last fallback.
    if tiebreakers[-1] != _D1_FALLBACK_DEFAULT:
        tiebreakers = list(tiebreakers) + [_D1_FALLBACK_DEFAULT]

    out: dict[str, str] = {}
    sat_grouped = dict(list(sat.groupby("friday_date_ist")))
    trades_grouped: dict = {}
    if trades_for_lookahead is not None and not trades_for_lookahead.empty:
        for friday, grp in trades_for_lookahead.groupby("friday_date_ist"):
            trades_grouped[friday] = grp

    for friday, sat_grp in sat_grouped.items():
        bands_touched = sat_grp["sat_iv_band"].dropna().unique().tolist()
        if not bands_touched:
            continue
        if len(bands_touched) == 1:
            out[friday] = bands_touched[0]
            continue
        # Apply tiebreakers in order. Each returns a list of candidate bands.
        candidates = list(bands_touched)
        for tb in tiebreakers:
            if len(candidates) <= 1:
                break
            candidates = _apply_tiebreaker(
                tb, sat_grp, trades_grouped.get(friday), candidates,
            )
            if not candidates:
                candidates = list(bands_touched)  # safety
                break
        out[friday] = candidates[0]
    return out


def _apply_tiebreaker(tb: str, sat_grp: pd.DataFrame,
                     trades_grp: Optional[pd.DataFrame],
                     candidates: list[str]) -> list[str]:
    """Filter `candidates` to those that win under `tb`. Returns at least one band."""
    if tb == "earliest_hour_band":
        # Hour ordering: 21, 22, 23, 0, 1, 2, 3 (entry window chronological)
        hour_order = [21, 22, 23, 0, 1, 2, 3]
        for h in hour_order:
            row = sat_grp[sat_grp["entry_hour_ist"] == h]
            if not row.empty:
                band = row["sat_iv_band"].iloc[0]
                if band in candidates:
                    return [band]
        return candidates[:1]
    if tb == "latest_hour_band":
        hour_order = [3, 2, 1, 0, 23, 22, 21]
        for h in hour_order:
            row = sat_grp[sat_grp["entry_hour_ist"] == h]
            if not row.empty:
                band = row["sat_iv_band"].iloc[0]
                if band in candidates:
                    return [band]
        return candidates[:1]
    if tb == "modal_band":
        sub = sat_grp[sat_grp["sat_iv_band"].isin(candidates)]
        if sub.empty:
            return candidates
        counts = sub["sat_iv_band"].value_counts()
        max_c = counts.max()
        winners = [b for b in counts.index if counts[b] == max_c]
        return winners
    if tb == "median_hour_band":
        # IV at the median entry hour reading.
        # Use Sat-IV values sorted; take the median row.
        sub = sat_grp[sat_grp["sat_iv_band"].isin(candidates)].copy()
        if sub.empty:
            return candidates
        sub = sub.sort_values("sat_atm_iv_pct")
        mid = sub.iloc[len(sub) // 2]
        band = mid["sat_iv_band"]
        return [band] if band in candidates else candidates
    if tb == "highest_entry_iv":
        sub = sat_grp[sat_grp["sat_iv_band"].isin(candidates)]
        if sub.empty:
            return candidates
        idx = sub["sat_atm_iv_pct"].idxmax()
        return [sub.loc[idx, "sat_iv_band"]]
    if tb == "lowest_entry_iv":
        sub = sat_grp[sat_grp["sat_iv_band"].isin(candidates)]
        if sub.empty:
            return candidates
        idx = sub["sat_atm_iv_pct"].idxmin()
        return [sub.loc[idx, "sat_iv_band"]]
    # Lookahead tiebreakers — need per-trade outcomes joined by Sat-IV band
    if trades_grp is None or trades_grp.empty:
        return candidates  # no lookahead data → can't apply
    # Attach hour-band to trades and filter to candidates
    hour_to_band = dict(zip(sat_grp["entry_hour_ist"], sat_grp["sat_iv_band"]))
    tg = trades_grp.copy()
    tg["_band"] = tg["entry_hour_ist"].map(hour_to_band)
    tg = tg[tg["_band"].isin(candidates)]
    if tg.empty:
        return candidates
    if tb == "best_avg_net_pnl":
        agg = tg.groupby("_band")["net_pnl_estimate_usd"].mean()
        return [agg.idxmax()]
    if tb == "best_total_net_pnl":
        agg = tg.groupby("_band")["net_pnl_estimate_usd"].sum()
        return [agg.idxmax()]
    if tb == "best_gross_pnl":
        agg = tg.groupby("_band")["gross_pnl_usd"].mean()
        return [agg.idxmax()]
    if tb == "best_win_rate":
        agg = tg.groupby("_band")["is_win"].mean()
        return [agg.idxmax()]
    if tb == "highest_credit":
        agg = tg.groupby("_band")["credit_usd"].mean()
        return [agg.idxmax()]
    if tb == "lowest_margin":
        agg = tg.groupby("_band")["margin_used_usd_at_entry"].mean()
        return [agg.idxmin()]
    if tb == "best_min_mtm":
        if "min_mtm" not in tg.columns:
            return candidates
        agg = tg.groupby("_band")["min_mtm"].mean()
        return [agg.idxmax()]  # less negative = better
    if tb == "worst_min_mtm":
        if "min_mtm" not in tg.columns:
            return candidates
        agg = tg.groupby("_band")["min_mtm"].mean()
        return [agg.idxmin()]
    if tb == "best_max_mtm":
        if "max_mtm" not in tg.columns:
            return candidates
        agg = tg.groupby("_band")["max_mtm"].mean()
        return [agg.idxmax()]
    if tb == "smallest_mtm_range":
        if "max_mtm" not in tg.columns or "min_mtm" not in tg.columns:
            return candidates
        tg2 = tg.assign(_range=tg["max_mtm"] - tg["min_mtm"])
        agg = tg2.groupby("_band")["_range"].mean()
        return [agg.idxmin()]
    if tb in ("best_max_loss", "worst_max_loss"):
        losers = tg[tg["net_pnl_estimate_usd"] < 0]
        if losers.empty:
            return candidates
        agg = losers.groupby("_band")["net_pnl_estimate_usd"].min()
        # Least-bad max loss is highest value (closest to 0).
        return [agg.idxmax() if tb == "best_max_loss" else agg.idxmin()]
    if tb == "best_avg_loss":
        losers = tg[tg["net_pnl_estimate_usd"] < 0]
        if losers.empty:
            return candidates
        agg = losers.groupby("_band")["net_pnl_estimate_usd"].mean()
        return [agg.idxmax()]
    return candidates  # unknown tiebreaker — pass through


def _parse_d1_tiebreakers(raw: Optional[str]) -> list[str]:
    """Parse CSV of tiebreaker keys, validate, dedupe, ensure fallback at end.

    Returns the user's chain with duplicates removed (preserving order) and
    `earliest_hour_band` appended as the deterministic final fallback if not
    already in the chain.
    """
    if not raw:
        return [_D1_FALLBACK_DEFAULT]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    invalid = [p for p in parts if p not in _VALID_D1_TIEBREAKERS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown d1_tiebreakers: {invalid}. "
                   f"Valid: {sorted(_VALID_D1_TIEBREAKERS)}",
        )
    # Dedupe (preserve order)
    seen: set = set()
    deduped: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    parts = deduped
    if not parts or parts[-1] != _D1_FALLBACK_DEFAULT:
        # Drop fallback if it's already earlier in the chain, then re-append.
        if _D1_FALLBACK_DEFAULT in parts:
            parts.remove(_D1_FALLBACK_DEFAULT)
        parts.append(_D1_FALLBACK_DEFAULT)
    return parts


# ── Per-trade lazy loader ─────────────────────────────────────────────────────

def _load_per_trade_archive() -> Optional[pd.DataFrame]:
    """Lazy-load the concat of all 96 per-rule per-trade parquets.

    Cached for process lifetime. ~3.3M rows, ~600MB memory.
    Used to derive B1/D1 grids by re-grouping trades under a different
    per-Friday band assignment.
    """
    with _PER_TRADE_LOCK:
        if _GRID_STATE["per_trade"] is not None:
            return _GRID_STATE["per_trade"]
        if not os.path.isdir(PER_TRADE_DIR):
            log.warning("Per-trade archive missing at %s", PER_TRADE_DIR)
            return None
        import glob
        files = sorted(glob.glob(os.path.join(PER_TRADE_DIR, "*_trades.parquet")))
        if not files:
            return None
        frames = []
        for f in files:
            try:
                frames.append(pd.read_parquet(f))
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to load %s: %s", f, exc)
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True)
        _GRID_STATE["per_trade"] = df
        log.info("M7 Friday-band per-trade archive loaded: %d trades across %d rules",
                 len(df), len(files))
        return df


# ── Build a grid from per-trade data with substituted band column ────────────

def _build_mode_grid(per_trade: pd.DataFrame, friday_band_map: dict[str, str]) -> pd.DataFrame:
    """Apply a per-Friday band map to the per-trade frame, then VECTORIZED-aggregate
    cells in seconds (was ~90 min with per-cell loop).

    Single pandas groupby + multi-column agg() replaces 200K+ individual cell
    aggregations. Result shape matches the disk-loaded A1 grid for the columns
    needed by the picker; MTM-winner/loser columns are NaN (not in per_trade)
    and backfilled by `_load_disk_mode_grid`'s pattern.
    """
    if per_trade is None or per_trade.empty or not friday_band_map:
        return pd.DataFrame()
    df = per_trade.copy()
    df["iv_band"] = df["friday_date_ist"].map(friday_band_map)
    df = df[
        df["iv_band"].notna()
        & df["expiry_bucket"].notna()
        & df["delta_target"].notna()
        & df["entry_hour_ist"].notna()
        & df["gross_pnl_usd"].notna()
    ]
    if df.empty:
        return pd.DataFrame()

    df["is_win_b"] = df["net_pnl_estimate_usd"].astype(float) > 0
    df["is_loss_b"] = df["net_pnl_estimate_usd"].astype(float) < 0
    er = df["exit_reason"].astype(str).str.lower()
    df["is_hard_cap_b"] = er.eq("hard_cap")
    df["is_rule_trigger_b"] = er.eq("rule_trigger")
    df["is_premium_sl_b"] = er.str.contains("premium_sl|sl_hit", regex=True)
    df["net_pnl_winner"] = np.where(df["is_win_b"], df["net_pnl_estimate_usd"], np.nan)
    df["net_pnl_loser"] = np.where(df["is_loss_b"], df["net_pnl_estimate_usd"], np.nan)
    df["pct_credit"] = np.where(df["credit_usd"] > 0,
                                df["net_pnl_estimate_usd"] / df["credit_usd"], np.nan)
    df["pct_margin"] = np.where(df["margin_used_usd_at_entry"] > 0,
                                df["net_pnl_estimate_usd"] / df["margin_used_usd_at_entry"], np.nan)

    grouped = df.groupby(
        ["iv_band", "expiry_bucket", "delta_target", "entry_hour_ist", "rule_label"],
        dropna=False, sort=False, observed=True,
    )
    agg_spec = {
        "net_pnl_estimate_usd": ["mean", "sum", "max", "min", "std"],
        "gross_pnl_usd":        ["mean"],
        "credit_usd":           ["mean"],
        "margin_used_usd_at_entry": ["mean"],
        "is_win_b":             ["sum", "mean"],
        "is_loss_b":            ["sum"],
        "is_hard_cap_b":        ["sum"],
        "is_rule_trigger_b":    ["sum"],
        "is_premium_sl_b":      ["sum"],
        "net_pnl_winner":       ["mean", "max"],
        "net_pnl_loser":        ["mean", "min", "std"],
        "pct_credit":           ["mean"],
        "pct_margin":           ["mean"],
        "trade_id":             ["count"],
    }
    agg = grouped.agg(agg_spec)
    agg.columns = ["_".join([c for c in col if c]).strip("_") for col in agg.columns]
    agg = agg.reset_index()

    rename_map = {
        "net_pnl_estimate_usd_mean": "avg_net_pnl",
        "net_pnl_estimate_usd_sum":  "sum_net_pnl",
        "net_pnl_estimate_usd_max":  "max_win_usd",
        "net_pnl_estimate_usd_min":  "max_loss_usd",
        "net_pnl_estimate_usd_std":  "stdev_net_pnl",
        "gross_pnl_usd_mean":        "avg_gross_pnl",
        "credit_usd_mean":           "avg_credit",
        "margin_used_usd_at_entry_mean": "avg_margin",
        "is_win_b_sum":              "n_wins",
        "is_win_b_mean":             "win_rate",
        "is_loss_b_sum":             "n_losses",
        "is_hard_cap_b_sum":         "n_hard_cap",
        "is_rule_trigger_b_sum":     "n_rule_trigger",
        "is_premium_sl_b_sum":       "n_premium_sl_hit",
        "net_pnl_winner_mean":       "avg_win_usd",
        "net_pnl_loser_mean":        "avg_loss_usd",
        "net_pnl_loser_std":         "stdev_losses_only",
        "pct_credit_mean":           "avg_pct_return_on_credit",
        "pct_margin_mean":           "avg_pct_return_on_margin",
        "trade_id_count":            "n_trades",
    }
    agg = agg.rename(columns=rename_map)
    # Drop duplicate max/min that we already named above
    agg = agg.drop(columns=[c for c in ["net_pnl_winner_max", "net_pnl_loser_min"] if c in agg.columns])
    # Cast int cols
    for col in ["n_wins", "n_losses", "n_hard_cap", "n_rule_trigger", "n_premium_sl_hit", "n_trades"]:
        if col in agg.columns:
            agg[col] = pd.to_numeric(agg[col], errors="coerce").fillna(0).astype(int)

    # Backfill the MTM-related columns the enrichers expect (not in per_trade).
    for col in [
        "avg_min_mtm_winners", "avg_max_mtm_winners",
        "avg_min_mtm_losers",  "avg_max_mtm_losers",
        "min_mtm_winners", "max_mtm_winners",
        "min_mtm_losers",  "max_mtm_losers",
    ]:
        if col not in agg.columns:
            agg[col] = float("nan")

    # Reconstruct rule dict for compat with downstream picker (best fallback hour search).
    agg["rule"] = [{} for _ in range(len(agg))]

    agg = m7bc._enrich_grid_with_overall_mtm(agg)
    agg = m7bc._attach_composite_score(agg)
    agg = m7bc._attach_risk_adjusted(agg)
    return agg


# ── Mode-specific grid retrieval ──────────────────────────────────────────────

# Pre-built disk grids for non-A1 modes. Built by
# `app.scripts.build_m7_friday_band_mode_grids`. When present, loaded
# instantly. When absent (e.g. lookahead D1 tiebreaker chains), the in-process
# build path is used.
_MODE_DISK_GRID_PATH = "/home/abhis/btc-data/derived/m7/m7_friday_band_grid_{}.parquet"


def _load_disk_mode_grid(mode_key: str) -> Optional[pd.DataFrame]:
    """Try to load a pre-built mode grid from disk. Returns None if missing.

    The vectorized builder skips per-trade MTM aggregates (avg_min_mtm_winners
    etc. — those need path data which isn't in the per-trade archive). Fill
    them as NaN columns so the existing enrichers don't fail.
    """
    path = _MODE_DISK_GRID_PATH.format(mode_key)
    if not os.path.exists(path):
        return None
    try:
        grid = pd.read_parquet(path)
        grid = m7bc._unflatten_after_load(grid)
        # Backfill MTM columns the enrichers expect.
        for col in [
            "avg_min_mtm_winners", "avg_max_mtm_winners",
            "avg_min_mtm_losers",  "avg_max_mtm_losers",
            "min_mtm_winners", "max_mtm_winners",
            "min_mtm_losers",  "max_mtm_losers",
        ]:
            if col not in grid.columns:
                grid[col] = float("nan")
        grid = m7bc._enrich_grid_with_overall_mtm(grid)
        grid = m7bc._attach_composite_score(grid)
        grid = m7bc._attach_risk_adjusted(grid)
        return grid
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to load mode grid %s: %s", path, exc)
        return None


def _disk_mode_key(band_mode: str, d1_tiebreakers: list[str]) -> Optional[str]:
    """Map (band_mode, tiebreakers) → pre-built grid filename suffix, or None.

    The only pre-built grids cover the 4 no-lookahead D1 single-tiebreaker chains
    and B1. Any chain whose primary tiebreaker is lookahead must build in process.
    """
    if band_mode == "B1":
        return "B1"
    if band_mode != "D1":
        return None
    if not d1_tiebreakers:
        return None
    # Only use a disk grid when the user effectively has a single tiebreaker
    # (the parser always appends `earliest_hour_band` as fallback, so a "single"
    # chain has len <= 2 with `earliest_hour_band` as the final fallback). With
    # 3+ tiebreakers, the secondary/tertiary may matter for actual ties — fall
    # through to in-process build to evaluate them properly.
    chain_without_fallback = [t for t in d1_tiebreakers if t != _D1_FALLBACK_DEFAULT]
    if len(chain_without_fallback) > 1:
        return None
    primary = d1_tiebreakers[0]
    mapping = {
        # No-lookahead time-based
        "earliest_hour_band":  "D1_earliest",
        "latest_hour_band":    "D1_latest",
        "modal_band":          "B1",  # B1 IS modal — share the grid
        "median_hour_band":    "D1_median",
        # No-lookahead setup-based
        "highest_entry_iv":    "D1_highest_entry_iv",
        "lowest_entry_iv":     "D1_lowest_entry_iv",
        # Lookahead — pre-built in batch
        "best_avg_net_pnl":    "D1_best_avg_net_pnl",
        "best_total_net_pnl":  "D1_best_total_net_pnl",
        "best_gross_pnl":      "D1_best_gross_pnl",
        "best_win_rate":       "D1_best_win_rate",
        "best_max_loss":       "D1_best_max_loss",
        "worst_max_loss":      "D1_worst_max_loss",
        "best_avg_loss":       "D1_best_avg_loss",
        "highest_credit":      "D1_highest_credit",
        "lowest_margin":       "D1_lowest_margin",
        # MTM-based tiebreakers (best_min_mtm, worst_min_mtm, best_max_mtm,
        # smallest_mtm_range) require minute-by-minute MTM data from m7_paths
        # which isn't in the per-trade archive. Not pre-built — falls through.
    }
    return mapping.get(primary)


def _chain_cache_path(band_mode: str, d1_tiebreakers: list[str]) -> Optional[str]:
    """Persistent disk cache path for D1 multi-tiebreaker chains.

    Returns None for B1 and D1-single-tb (those use named pre-built grids).
    Hash-keyed so order matters (tiebreakers are applied in priority order).
    Cached files survive backend restarts and are shared between sessions.
    """
    if band_mode != "D1":
        return None
    chain_without_fallback = [t for t in d1_tiebreakers if t != _D1_FALLBACK_DEFAULT]
    if len(chain_without_fallback) <= 1:
        return None  # named grid handles this
    # Hash the chain (order-preserving) so e.g. [A,B] ≠ [B,A].
    seed = f"D1|{','.join(d1_tiebreakers)}"
    h = hashlib.md5(seed.encode()).hexdigest()[:12]
    return os.path.join(CHAIN_CACHE_DIR, f"d1_chain_{h}.parquet")


def _save_chain_cache(grid: pd.DataFrame, path: str, cache_key: str) -> None:
    """Persist a freshly-built D1 chain grid to disk for next time.

    Tolerates write failure (logs, doesn't raise) since the in-memory cache
    still serves the current process.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Strip enrichment cols (recomputed on load) and the dict-typed `rule`
        # column which doesn't survive parquet — they're re-added by
        # `_load_chain_cache`.
        slim = grid.copy()
        if "rule" in slim.columns:
            slim = slim.drop(columns=["rule"])
        slim.to_parquet(path, index=False)
        log.info("M7 Friday-band chain cache saved: %s (%d cells, key=%s)",
                 os.path.basename(path), len(grid), cache_key)
    except Exception as exc:  # noqa: BLE001
        log.warning("M7 Friday-band chain cache save failed (%s): %s", path, exc)


def _load_chain_cache(path: str) -> Optional[pd.DataFrame]:
    """Load a previously-saved D1 chain grid from disk, run enrichers.

    Validates freshness against the trades parquet — if trades were
    re-derived since the cache was written, returns None so the build path
    re-runs.
    """
    if not os.path.exists(path):
        return None
    if not _grid_is_fresh(path):
        log.info("M7 Friday-band chain cache stale (older than trades), rebuilding: %s",
                 os.path.basename(path))
        return None
    try:
        grid = pd.read_parquet(path)
        grid = m7bc._unflatten_after_load(grid)
        # Backfill MTM cols the enrichers expect.
        for col in [
            "avg_min_mtm_winners", "avg_max_mtm_winners",
            "avg_min_mtm_losers",  "avg_max_mtm_losers",
            "min_mtm_winners", "max_mtm_winners",
            "min_mtm_losers",  "max_mtm_losers",
        ]:
            if col not in grid.columns:
                grid[col] = float("nan")
        if "rule" not in grid.columns:
            grid["rule"] = [{} for _ in range(len(grid))]
        grid = m7bc._enrich_grid_with_overall_mtm(grid)
        grid = m7bc._attach_composite_score(grid)
        grid = m7bc._attach_risk_adjusted(grid)
        return grid
    except Exception as exc:  # noqa: BLE001
        log.warning("M7 Friday-band chain cache load failed (%s): %s", path, exc)
        return None


def _get_grid_for_mode(band_mode: str, d1_tiebreakers: list[str]) -> pd.DataFrame:
    """Return the grid for the chosen mode.

    A1 — loaded from disk parquet (pre-built).
    B1/D1 (no-lookahead primary tiebreaker) — pre-built grid loaded from disk.
    D1 with lookahead primary tiebreaker — derived in-process from per-trade
        archive + the chosen Friday-band assignment. Cached in _GRID_STATE,
        and persisted to disk via `_chain_cache_path` so subsequent
        requests (even after restart) skip the rebuild.
    """
    if band_mode == "A1":
        return _GRID_STATE["grid_a1"]

    disk_key = _disk_mode_key(band_mode, d1_tiebreakers)
    if disk_key is not None:
        with _MODE_GRID_LOCK:
            cached = _GRID_STATE["mode_grids"].get(disk_key)
            if cached is not None:
                return cached
            grid = _load_disk_mode_grid(disk_key)
            if grid is not None and not grid.empty:
                _GRID_STATE["mode_grids"][disk_key] = grid
                log.info("M7 Friday-band mode grid loaded from disk: %s (%d cells)",
                         disk_key, len(grid))
                return grid
        # Disk grid missing — fall through to in-process build.

    # In-process build for lookahead D1 or when disk grids absent
    cache_key = "B1" if band_mode == "B1" else "D1:" + ",".join(d1_tiebreakers)
    with _MODE_GRID_LOCK:
        cached = _GRID_STATE["mode_grids"].get(cache_key)
        if cached is not None:
            _track_progress(cache_key, "done", 1.0, "served from memory cache")
            return cached

        # Try the persistent disk cache (D1 multi-tb chains only).
        chain_path = _chain_cache_path(band_mode, d1_tiebreakers)
        if chain_path is not None:
            _track_progress(cache_key, "loading_disk_cache", 0.05,
                           f"checking {os.path.basename(chain_path)}")
            cached_disk = _load_chain_cache(chain_path)
            if cached_disk is not None and not cached_disk.empty:
                _GRID_STATE["mode_grids"][cache_key] = cached_disk
                _track_progress(cache_key, "done", 1.0,
                               f"loaded from disk cache ({len(cached_disk)} cells)")
                log.info("M7 Friday-band chain cache hit: %s (%d cells, key=%s)",
                         os.path.basename(chain_path), len(cached_disk), cache_key)
                return cached_disk

        sat = _GRID_STATE["sat_iv"]
        if sat is None:
            _track_progress(cache_key, "error", 0.0, "sat_iv map not loaded")
            return pd.DataFrame()

        _track_progress(cache_key, "loading_per_trade_archive", 0.10,
                       "loading 3.25M-row trade archive")
        per_trade = _load_per_trade_archive()
        if per_trade is None or per_trade.empty:
            _track_progress(cache_key, "error", 0.0, "per-trade archive empty")
            return pd.DataFrame()

        _track_progress(cache_key, "building_band_map", 0.30,
                       "assigning each Friday to a band")
        if band_mode == "B1":
            fb_map = _friday_band_map_b1(sat)
        else:  # D1
            fb_map = _friday_band_map_d1(sat, per_trade, d1_tiebreakers)

        if not fb_map:
            _track_progress(cache_key, "error", 0.0, "band assignment empty")
            return pd.DataFrame()

        _track_progress(cache_key, "aggregating_grid", 0.55,
                       "vectorized aggregation across cells")
        grid = _build_mode_grid(per_trade, fb_map)
        _GRID_STATE["mode_grids"][cache_key] = grid
        log.info("M7 Friday-band mode grid built in-process: %s → %d cells",
                 cache_key, len(grid))

        # Persist to disk so the next session/restart skips this 20-30s build.
        if chain_path is not None and not grid.empty:
            _track_progress(cache_key, "saving_to_disk", 0.92,
                           f"writing {os.path.basename(chain_path)}")
            _save_chain_cache(grid, chain_path, cache_key)

        _track_progress(cache_key, "done", 1.0,
                       f"built {len(grid)} cells")
        return grid


# ── Per-band Friday count (coverage column source) ────────────────────────────

def _compute_n_fridays_per_band(band_mode: str, d1_tiebreakers: list[str]) -> dict[str, int]:
    """For the chosen mode, count how many Fridays end up in each band.

    This is the DENOMINATOR for the UI's coverage column. Sums to 121 across
    all populated bands (because each Friday is assigned to exactly one band).

    Returns: {band_label: friday_count}, e.g. {"0-20": 3, "20-30": 16, ...}
    """
    sat = _GRID_STATE.get("sat_iv")
    if sat is None or sat.empty:
        return {}
    try:
        if band_mode == "A1":
            fb_map = _friday_band_map_a1(sat)
        elif band_mode == "B1":
            fb_map = _friday_band_map_b1(sat)
        else:  # D1
            # Lookahead tiebreakers need trades — pass the cached per-trade frame.
            pt = _load_per_trade_archive() if any(
                t in _D1_TIEBREAKERS_LOOKAHEAD for t in d1_tiebreakers
            ) else None
            fb_map = _friday_band_map_d1(sat, pt, d1_tiebreakers)
    except Exception:
        return {}
    out: dict[str, int] = {}
    for band in fb_map.values():
        out[band] = out.get(band, 0) + 1
    return out


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/friday_band_best_combo")
def get_friday_band_best_combo(
    band_mode: str = Query("A1", description="Friday-band assignment mode: A1 | B1 | D1"),
    d1_tiebreakers: Optional[str] = Query(None,
        description="CSV of prioritized D1 tiebreaker keys (e.g. 'best_avg_net_pnl,modal_band'). Ignored unless band_mode=D1."),
    ranking: str = Query("avg_net_pnl"),
    secondary: Optional[str] = Query(None),
    tolerance_pct: float = Query(5.0, ge=0.0, le=100.0),
    rule_family: str = Query("all"),
    total_capital_usd: Optional[float] = Query(None, ge=0),
    pct_deploy: float = Query(100.0, ge=0, le=100),
    dd_metric: Optional[str] = Query(None),
    dd_threshold: Optional[float] = Query(None),
    min_hit_pct: float = Query(50.0, ge=0.0, le=100.0),
    max_loss_cap_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    max_drop_peak_to_trough_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    min_n_trades: int = Query(5, ge=0),
    min_win_rate: Optional[float] = Query(None, ge=0.0, le=100.0),
    max_losing_streak: Optional[int] = Query(None, ge=1),
    pick_mode: str = Query("by_hour"),
    expiry_buckets: Optional[str] = Query(
        "current (Sat),next (Sun),next_to_next (Mon),weekly (7d)",
        description="CSV of allowed expiry buckets. Default restricts to the 4 popular expiries tradeable on every Friday. Pass empty string to allow all."),
    include_grid: bool = Query(False),
):
    """Friday-locked-band best-combo picker.

    Each Friday is assigned exactly one IV band (computed from Saturday-daily-
    expiry ATM IV). All combos within a band see the same Friday cohort —
    cross-combo comparisons are apples-to-apples.
    """
    # Validate query params (reuse existing rankings)
    if ranking not in m7bc._VALID_RANKINGS:
        raise HTTPException(400, f"ranking must be one of {sorted(m7bc._VALID_RANKINGS)}")
    if secondary is not None and secondary not in m7bc._VALID_RANKINGS:
        raise HTTPException(400, f"secondary must be one of {sorted(m7bc._VALID_RANKINGS)}")
    if rule_family not in m7bc._VALID_RULE_FAMILIES:
        raise HTTPException(400, f"rule_family must be one of {sorted(m7bc._VALID_RULE_FAMILIES)}")
    if pick_mode not in ("by_hour", "aggregate_hours"):
        raise HTTPException(400, "pick_mode must be 'by_hour' or 'aggregate_hours'")
    if band_mode not in _VALID_BAND_MODES:
        raise HTTPException(400, f"band_mode must be one of {sorted(_VALID_BAND_MODES)}")

    tiebreakers = _parse_d1_tiebreakers(d1_tiebreakers) if band_mode == "D1" else []

    _load_grid_and_sat_iv()
    status = _GRID_STATE["status"]
    if status == "not_built":
        return {
            "band_mode": band_mode,
            "d1_tiebreakers": tiebreakers,
            "status": "not_built",
            "message": _GRID_STATE.get("error")
                       or "Grid not built. Run "
                          "`docker compose run -d --rm --name m7-friday-band-grid-builder backend "
                          "python -m app.scripts.build_m7_friday_band_grid`",
            "rows": [],
        }
    if status == "error":
        raise HTTPException(500, _GRID_STATE.get("error") or "Grid load error")

    grid = _get_grid_for_mode(band_mode, tiebreakers)
    if grid is None or grid.empty:
        return {
            "band_mode": band_mode,
            "d1_tiebreakers": tiebreakers,
            "status": "ready",
            "rows": [],
            "n_cells": 0,
        }

    family_grid = m7bc._filter_grid_by_family(grid, rule_family)

    # Optional expiry-bucket filter — default restricts to current/next/next_to_next/weekly
    # so the picker never selects niche expiries (biweekly/monthly/quarterly) that aren't
    # tradeable on every Friday in a band.
    if expiry_buckets is not None and expiry_buckets.strip():
        allowed = [b.strip() for b in expiry_buckets.split(",") if b.strip()]
        if allowed and "expiry_bucket" in family_grid.columns:
            family_grid = family_grid[family_grid["expiry_bucket"].isin(allowed)]

    if pick_mode == "aggregate_hours":
        family_grid = m7bc._aggregate_across_hours(family_grid)
    best = m7bc._pick_best_per_band(
        family_grid, ranking,
        secondary=secondary,
        tolerance_pct=tolerance_pct if secondary else None,
        total_capital_usd=total_capital_usd,
        pct_deploy=pct_deploy,
        dd_metric=dd_metric,
        dd_threshold=dd_threshold,
        min_hit_pct=min_hit_pct,
        max_loss_cap_pct=max_loss_cap_pct,
        max_drop_peak_to_trough_pct=max_drop_peak_to_trough_pct,
        min_n_trades=min_n_trades,
        min_win_rate=min_win_rate,
        max_losing_streak=max_losing_streak,
    )

    # Attach per-band Friday count (n_fridays_in_band) so the UI can show
    # coverage = picked-cell-n_trades / band-Friday-count. This makes the
    # "44 Fridays in band 30-40 vs picked cell n=5" disconnect visible.
    band_to_n_fridays = _compute_n_fridays_per_band(band_mode, tiebreakers)
    if not best.empty and band_to_n_fridays:
        best = best.copy()
        best["n_fridays_in_band"] = best["iv_band"].map(band_to_n_fridays).fillna(0).astype(int)

    payload = {
        "band_mode": band_mode,
        "d1_tiebreakers": tiebreakers,
        "ranking": ranking,
        "secondary": secondary,
        "tolerance_pct": tolerance_pct,
        "rule_family": rule_family,
        "total_capital_usd": total_capital_usd,
        "pct_deploy": pct_deploy,
        "dd_metric": dd_metric,
        "dd_threshold": dd_threshold,
        "min_hit_pct": min_hit_pct,
        "max_loss_cap_pct": max_loss_cap_pct,
        "max_drop_peak_to_trough_pct": max_drop_peak_to_trough_pct,
        "min_n_trades": min_n_trades,
        "min_win_rate": min_win_rate,
        "max_losing_streak": max_losing_streak,
        "pick_mode": pick_mode,
        "expiry_buckets": expiry_buckets,
        "status": "ready",
        "rows": m7bc._records(m7bc._attach_zigzag_v7_to_picks(best)),
        "n_rules": len(m7bc._rule_variants()),
        "n_cells": int(len(family_grid)),
    }
    if include_grid:
        payload["grid"] = m7bc._records(family_grid)
    return payload


@router.get("/friday_band_best_combo/status")
def get_friday_band_best_combo_status():
    """Lightweight probe — does the grid exist?"""
    _load_grid_and_sat_iv()
    return {
        "status": _GRID_STATE["status"],
        "error": _GRID_STATE["error"],
        "grid_a1_path": _GRID_STATE["grid_a1_path"],
        "grid_a1_exists": os.path.exists(GRID_PARQUET_PATH),
        "sat_iv_exists": os.path.exists(SAT_IV_PATH),
    }


@router.get("/friday_band_best_combo/build_progress")
def get_friday_band_build_progress(
    band_mode: str = Query("A1"),
    d1_tiebreakers: Optional[str] = Query(None),
):
    """Progress probe for an in-flight D1 multi-tiebreaker grid build.

    Returns the current build phase + progress for the (band_mode, tiebreakers)
    chain. The frontend can poll this every 1-2s while the main fetch is
    pending to render a "Building Friday-band grid…" progress bar.

    Phases: idle | loading_disk_cache | loading_per_trade_archive |
    building_band_map | aggregating_grid | saving_to_disk | done | error.

    Also reports cache hits — A1 / B1 / D1-single-tb / D1-multi-tb-cached
    return immediately with phase="done", progress=1.0.
    """
    if band_mode not in _VALID_BAND_MODES:
        raise HTTPException(400, f"band_mode must be one of {sorted(_VALID_BAND_MODES)}")
    tb = _parse_d1_tiebreakers(d1_tiebreakers) if band_mode == "D1" else []

    # Fast-path responses for already-cached modes
    if band_mode == "A1" and _GRID_STATE.get("grid_a1") is not None:
        return {"phase": "done", "progress": 1.0, "ready": True,
                "source": "a1_disk_grid", "message": "A1 grid in memory"}
    disk_key = _disk_mode_key(band_mode, tb)
    if disk_key is not None and disk_key in _GRID_STATE["mode_grids"]:
        return {"phase": "done", "progress": 1.0, "ready": True,
                "source": "named_disk_grid", "message": f"{disk_key} grid in memory"}

    cache_key = "B1" if band_mode == "B1" else "D1:" + ",".join(tb)
    if cache_key in _GRID_STATE["mode_grids"]:
        return {"phase": "done", "progress": 1.0, "ready": True,
                "source": "memory_cache", "message": "served from in-memory cache"}

    # Otherwise return the build-tracker state. If a disk-cache file exists
    # for the chain, the next request will load it in ~500ms — flag that so
    # the UI shows "loading from disk" instead of "building".
    chain_path = _chain_cache_path(band_mode, tb)
    disk_cache_exists = bool(chain_path and os.path.exists(chain_path))

    prog = _read_progress(cache_key)
    return {
        **prog,
        "ready": prog.get("phase") == "done",
        "cache_key": cache_key,
        "disk_cache_path": (os.path.basename(chain_path) if chain_path else None),
        "disk_cache_exists": disk_cache_exists,
    }


@router.get("/friday_band_best_combo/rule_comparison")
def get_friday_band_rule_comparison(
    band: str = Query(...),
    expiry_bucket: str = Query(...),
    delta_target: float = Query(..., ge=0.0, le=1.0),
    entry_hour_ist: int = Query(..., ge=0, le=23),
    band_mode: str = Query("A1"),
    d1_tiebreakers: Optional[str] = Query(None),
    total_capital_usd: Optional[float] = Query(None, ge=0),
    pct_deploy: float = Query(100.0, ge=0, le=100),
    dd_metric: Optional[str] = Query(None),
    dd_threshold: Optional[float] = Query(None),
    min_hit_pct: float = Query(0.0, ge=0.0, le=100.0),
    max_loss_cap_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    max_drop_peak_to_trough_pct: Optional[float] = Query(None, ge=0.0, le=100.0),
    min_n_trades: int = Query(0, ge=0),
    min_win_rate: Optional[float] = Query(None, ge=0.0, le=100.0),
    rule_family: str = Query("all"),
):
    """All 96 rules for a fixed (band, expiry, delta, hour) cell. Mirrors the
    existing /iv_band_best_combo/rule_comparison logic but reads from the
    Friday-band grid.
    """
    if band_mode not in _VALID_BAND_MODES:
        raise HTTPException(400, f"band_mode must be one of {sorted(_VALID_BAND_MODES)}")
    tiebreakers = _parse_d1_tiebreakers(d1_tiebreakers) if band_mode == "D1" else []

    _load_grid_and_sat_iv()
    if _GRID_STATE["status"] != "ready":
        return {"rows": [], "status": _GRID_STATE["status"]}
    grid = _get_grid_for_mode(band_mode, tiebreakers)
    if grid is None or grid.empty:
        return {"rows": [], "status": "empty"}

    sub = grid[
        (grid["iv_band"] == band)
        & (grid["expiry_bucket"] == expiry_bucket)
        & (np.isclose(grid["delta_target"].astype(float), float(delta_target), atol=0.001))
        & (grid["entry_hour_ist"] == int(entry_hour_ist))
    ].copy()
    if sub.empty:
        return {"rows": [], "status": "ready", "band": band,
                "expiry_bucket": expiry_bucket,
                "delta_target": delta_target,
                "entry_hour_ist": entry_hour_ist}

    n_tr = sub["n_trades"].astype(float)
    nhc = sub["n_hard_cap"].astype(float)
    sub["hit_pct"] = np.where(n_tr > 0, (n_tr - nhc) / n_tr, np.nan)

    sizing_active = total_capital_usd is not None and total_capital_usd > 0
    if sizing_active:
        sub = m7bc._attach_lots_column(sub, total_capital_usd, pct_deploy, dd_metric, dd_threshold)
        sub["lots"] = sub["_lots"].astype(int)
        sub = sub.drop(columns=["_lots"], errors="ignore")
    else:
        sub["lots"] = 100

    sub["scaled_avg_net_pnl"] = sub["avg_net_pnl"].astype(float) * sub["lots"] / 100.0
    if "max_loss_usd" in sub.columns:
        sub["scaled_max_loss_usd"] = sub["max_loss_usd"].astype(float) * sub["lots"] / 100.0

    sub = sub.sort_values(["hit_pct", "avg_net_pnl"], ascending=[False, False])
    return {
        "rows": m7bc._records(sub),
        "status": "ready",
        "band": band,
        "expiry_bucket": expiry_bucket,
        "delta_target": delta_target,
        "entry_hour_ist": entry_hour_ist,
        "band_mode": band_mode,
        "d1_tiebreakers": tiebreakers,
        "n_rules": int(len(sub)),
        "sizing_active": sizing_active,
        "total_capital_usd": total_capital_usd,
        "pct_deploy": pct_deploy,
        "dd_metric": dd_metric,
        "dd_threshold": dd_threshold,
    }


@router.get("/friday_band_best_combo/cross_band_check")
def get_friday_band_cross_band_check(
    band: str = Query(...),
    expiry_bucket: str = Query(...),
    delta_target: float = Query(..., ge=0.0, le=1.0),
    entry_hour_ist: int = Query(..., ge=0, le=23),
    rule_label: str = Query(...),
    band_mode: str = Query("A1"),
    d1_tiebreakers: Optional[str] = Query(None),
):
    """For the picked cell's (rule + entry_hour + expiry + delta), show how it
    performed in each of the 10 Friday-locked bands.
    """
    if band_mode not in _VALID_BAND_MODES:
        raise HTTPException(400, f"band_mode must be one of {sorted(_VALID_BAND_MODES)}")
    tiebreakers = _parse_d1_tiebreakers(d1_tiebreakers) if band_mode == "D1" else []

    _load_grid_and_sat_iv()
    if _GRID_STATE["status"] != "ready":
        return {"rows": [], "status": _GRID_STATE["status"]}
    grid = _get_grid_for_mode(band_mode, tiebreakers)
    if grid is None or grid.empty:
        return {"rows": [], "status": "empty"}

    sub = grid[
        (grid["expiry_bucket"] == expiry_bucket)
        & (np.isclose(grid["delta_target"].astype(float), float(delta_target), atol=0.001))
        & (grid["entry_hour_ist"] == int(entry_hour_ist))
        & (grid["rule_label"] == rule_label)
    ].copy()

    sub = sub.sort_values("iv_band", key=lambda s: s.map(m7bc._band_sort_key))
    return {
        "rows": m7bc._records(sub),
        "status": "ready",
        "band": band,
        "expiry_bucket": expiry_bucket,
        "delta_target": delta_target,
        "entry_hour_ist": entry_hour_ist,
        "rule_label": rule_label,
        "band_mode": band_mode,
        "d1_tiebreakers": tiebreakers,
        "n_bands": int(len(sub)),
    }


@router.get("/friday_band_best_combo/cell_friday_detail")
def get_friday_band_cell_friday_detail(
    band: str = Query(..., description="The picked cell's Friday-locked band (e.g. '0-20')"),
    expiry_bucket: str = Query(...),
    delta_target: float = Query(..., ge=0.0, le=1.0),
    entry_hour_ist: int = Query(..., ge=0, le=23),
    rule_label: str = Query(...),
    band_mode: str = Query("A1"),
    d1_tiebreakers: Optional[str] = Query(None),
):
    """Friday-Band variant of /iv_band_best_combo/cell_friday_detail.

    For the picked cell, surfaces the Friday dates behind the four aggregates
    (losing fridays, worst-MTM winner, largest-win trade, winners < avg
    MinMTM). Trades are restricted to Fridays assigned to `band` under the
    chosen `band_mode` (+ optional D1 tiebreakers).
    """
    if band_mode not in _VALID_BAND_MODES:
        raise HTTPException(400, f"band_mode must be one of {sorted(_VALID_BAND_MODES)}")
    tiebreakers = _parse_d1_tiebreakers(d1_tiebreakers) if band_mode == "D1" else []

    _load_grid_and_sat_iv()
    sat = _GRID_STATE.get("sat_iv")
    if sat is None or sat.empty:
        return {
            "status": "not_built",
            "cell": None,
            "losers": [],
            "worst_winner": None,
            "largest_win": None,
            "winners_below_avg_min_mtm": [],
        }
    if band_mode == "A1":
        fb_map = _friday_band_map_a1(sat)
    elif band_mode == "B1":
        fb_map = _friday_band_map_b1(sat)
    else:
        pt = _load_per_trade_archive() if any(
            t in _D1_TIEBREAKERS_LOOKAHEAD for t in tiebreakers
        ) else None
        fb_map = _friday_band_map_d1(sat, pt, tiebreakers)
    friday_set = {str(f) for f, b in fb_map.items() if b == band}

    detail = m7bc._compute_cell_friday_detail(
        band=band,
        expiry_bucket=expiry_bucket,
        delta_target=delta_target,
        entry_hour_ist=entry_hour_ist,
        rule_label=rule_label,
        dataset="delta_match",
        friday_set=friday_set,
    )
    detail["band_mode"] = band_mode
    detail["d1_tiebreakers"] = tiebreakers
    return detail


@router.get("/friday_band_best_combo/single_combo_simulation")
def get_friday_band_single_combo_simulation(
    expiry_bucket: str = Query(...),
    delta_target: float = Query(..., ge=0.0, le=1.0),
    entry_hour_ist: int = Query(..., ge=0, le=23),
    rule_label: str = Query(...),
    band_mode: str = Query("A1"),
    d1_tiebreakers: Optional[str] = Query(None),
    total_capital_usd: Optional[float] = Query(None, ge=0),
    pct_deploy: float = Query(100.0, ge=0, le=100),
):
    """Counterfactual: if every Friday traded this single combo regardless of
    IV regime, what would the headline + per-band breakdown look like?
    """
    if band_mode not in _VALID_BAND_MODES:
        raise HTTPException(400, f"band_mode must be one of {sorted(_VALID_BAND_MODES)}")
    tiebreakers = _parse_d1_tiebreakers(d1_tiebreakers) if band_mode == "D1" else []

    _load_grid_and_sat_iv()
    if _GRID_STATE["status"] != "ready":
        return {"per_band": [], "headline": {}, "status": _GRID_STATE["status"]}
    grid = _get_grid_for_mode(band_mode, tiebreakers)
    if grid is None or grid.empty:
        return {"per_band": [], "headline": {}, "status": "empty"}

    sub = grid[
        (grid["expiry_bucket"] == expiry_bucket)
        & (np.isclose(grid["delta_target"].astype(float), float(delta_target), atol=0.001))
        & (grid["entry_hour_ist"] == int(entry_hour_ist))
        & (grid["rule_label"] == rule_label)
    ].copy()

    if sub.empty:
        return {"per_band": [], "headline": {}, "status": "ready"}

    # Aggregate across all 10 bands.
    n_tr = pd.to_numeric(sub.get("n_trades"), errors="coerce").fillna(0)
    n_w = pd.to_numeric(sub.get("n_wins"), errors="coerce").fillna(0)
    avg_net = pd.to_numeric(sub.get("avg_net_pnl"), errors="coerce")
    headline = {
        "n_trades": int(n_tr.sum()),
        "n_wins": int(n_w.sum()),
        "win_rate": float(n_w.sum() / n_tr.sum()) if n_tr.sum() > 0 else None,
        "avg_net_pnl": float((avg_net * n_tr).sum() / n_tr.sum()) if n_tr.sum() > 0 else None,
        "total_net_pnl": float((avg_net * n_tr).sum()),
        "max_loss_usd": float(pd.to_numeric(sub.get("max_loss_usd"), errors="coerce").min()),
    }

    sub = sub.sort_values("iv_band", key=lambda s: s.map(m7bc._band_sort_key))
    return {
        "per_band": m7bc._records(sub),
        "headline": headline,
        "status": "ready",
        "expiry_bucket": expiry_bucket,
        "delta_target": delta_target,
        "entry_hour_ist": entry_hour_ist,
        "rule_label": rule_label,
        "band_mode": band_mode,
        "d1_tiebreakers": tiebreakers,
    }


@router.get("/friday_band_best_combo/friday_bands")
def get_friday_bands(
    band_mode: str = Query("A1"),
    d1_tiebreakers: Optional[str] = Query(None),
):
    """Per-Friday band assignment + Sat-IV path. For the Inspector UI."""
    if band_mode not in _VALID_BAND_MODES:
        raise HTTPException(400, f"band_mode must be one of {sorted(_VALID_BAND_MODES)}")
    tiebreakers = _parse_d1_tiebreakers(d1_tiebreakers) if band_mode == "D1" else []

    _load_grid_and_sat_iv()
    if _GRID_STATE["status"] != "ready":
        return {"rows": [], "status": _GRID_STATE["status"]}

    sat: pd.DataFrame = _GRID_STATE["sat_iv"]
    if band_mode == "A1":
        bm = _friday_band_map_a1(sat)
    elif band_mode == "B1":
        bm = _friday_band_map_b1(sat)
    else:
        # D1 — lookahead tiebreakers need trades parquet
        trades = None
        if any(t in _D1_TIEBREAKERS_LOOKAHEAD for t in tiebreakers):
            trades_path = m7r.TRADES_ENRICHED_PATH if os.path.exists(m7r.TRADES_ENRICHED_PATH) else m7r.TRADES_PATH
            trades = pd.read_parquet(trades_path) if os.path.exists(trades_path) else None
        bm = _friday_band_map_d1(sat, trades, tiebreakers)

    # Build per-Friday rows: hour-by-hour Sat-IV path + chosen band
    out_rows: list[dict] = []
    for friday, grp in sat.groupby("friday_date_ist"):
        grp_sorted = grp.sort_values("entry_hour_ist")
        path = [
            {
                "entry_hour_ist": int(r["entry_hour_ist"]),
                "sat_atm_iv_pct": float(r["sat_atm_iv_pct"]),
                "sat_iv_band": r["sat_iv_band"],
            }
            for _, r in grp_sorted.iterrows()
        ]
        out_rows.append({
            "friday_date_ist": friday,
            "chosen_band": bm.get(friday),
            "bands_touched": sorted(grp["sat_iv_band"].dropna().unique().tolist(),
                                    key=m7bc._band_sort_key),
            "n_bands_touched": int(grp["sat_iv_band"].dropna().nunique()),
            "path": path,
        })
    return {
        "rows": out_rows,
        "status": "ready",
        "band_mode": band_mode,
        "d1_tiebreakers": tiebreakers,
        "n_fridays": len(out_rows),
    }

"""M7 Pivot Profile API — segment-based per-trade peak/trough/DD per IV band.

GET /api/v1/m7/pivot_profile
  - cells=<json>     scope per (band, entry_hour) cell  (preferred — driven
                     by Best Combo per IV band selection)
  - entry_hours=...  global entry-hour filter (used when cells is empty)

Uses the warming-response pattern (status: warming|ready). Cache key =
(dataset, scope_signature). Repeat queries with the same scope return
instantly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Query

from app.analytics.m7_pivot_profile import (
    MIN_TRADES_PER_BAND_CELL, aggregate_pivot_profile,
)
from app.api.m7_results import (
    M7_BASE_DIR, _TRADES_BY_DATASET, _derive_exits, _load_trades,
    _paths_glob_for_dataset,
)

router = APIRouter()
log = logging.getLogger(__name__)

_PIVOT_DISK_DIR = os.path.join(M7_BASE_DIR, "pivot_cache")


def _pivot_cache_path(cache_key: tuple) -> str:
    h = hashlib.sha1(repr(cache_key).encode()).hexdigest()
    return os.path.join(_PIVOT_DISK_DIR, f"{h}.json")


def _load_pivot_disk(cache_key: tuple, ds_mtime: float) -> Optional[dict]:
    path = _pivot_cache_path(cache_key)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("_ds_mtime") != ds_mtime:
            return None
        data.pop("_ds_mtime", None)
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("Pivot disk cache read failed (%s): %s", path, exc)
        return None


def _save_pivot_disk(cache_key: tuple, result: dict, ds_mtime: float) -> None:
    try:
        os.makedirs(_PIVOT_DISK_DIR, exist_ok=True)
        path = _pivot_cache_path(cache_key)
        payload = {**result, "_ds_mtime": ds_mtime}
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        log.warning("Pivot disk cache write failed: %s", exc)


# Cache key = (dataset, scope_signature). Each entry:
#   {"status": "warming"|"ready"|"error", "result": dict|None,
#    "error": str|None, "progress": float in [0,1], "started_at": float,
#    "finished_at": float|None}
_CACHE: dict[tuple[str, str], dict] = {}
_CACHE_LOCK = threading.Lock()


def _new_state() -> dict:
    return {
        "status": "warming", "result": None, "error": None,
        "progress": 0.0,
        "started_at": time.time(), "finished_at": None,
    }


def _parse_hours(raw: str) -> tuple[int, ...]:
    """Parse '21,22,23,0' → (0, 21, 22, 23). Invalid items dropped."""
    if not raw:
        return tuple(sorted({21, 22, 23, 0, 1, 2, 3}))
    seen: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            h = int(part)
        except ValueError:
            continue
        if 0 <= h <= 23:
            seen.add(h)
    if not seen:
        seen = {21, 22, 23, 0, 1, 2, 3}
    return tuple(sorted(seen))


def _parse_cells(raw: Optional[str]) -> list[dict]:
    """Parse JSON-encoded list of {entry_atm_iv_band, entry_hour_ist}.
    Returns []  if the param is absent/invalid/empty."""
    if not raw:
        return []
    try:
        cells = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(cells, list):
        return []
    out: list[dict] = []
    for c in cells:
        if not isinstance(c, dict):
            continue
        band = c.get("entry_atm_iv_band")
        hour = c.get("entry_hour_ist")
        expiry = c.get("expiry_bucket")
        delta = c.get("delta_target")
        rule = c.get("rule")
        lots = c.get("lots")
        if band is None or hour is None:
            continue
        if not isinstance(rule, dict):
            rule = {}
        try:
            out.append({
                "entry_atm_iv_band": str(band),
                "entry_hour_ist": int(hour),
                "expiry_bucket": str(expiry) if expiry is not None else None,
                "delta_target": (float(delta) if delta is not None else None),
                "rule": {k: v for k, v in rule.items() if v is not None},
                "lots": (int(lots) if lots is not None
                         and not isinstance(lots, bool) else None),
            })
        except (TypeError, ValueError):
            continue
    return out


def _scope_signature(cells: list[dict], hours: tuple[int, ...]) -> str:
    """Stable JSON key for caching. Cells take precedence."""
    if cells:
        norm = sorted(
            (c["entry_atm_iv_band"], c["entry_hour_ist"],
             c.get("expiry_bucket"), c.get("delta_target"),
             json.dumps(c.get("rule") or {}, sort_keys=True),
             c.get("lots"))
            for c in cells)
        return "cells:" + json.dumps(norm)
    return "hours:" + ",".join(str(h) for h in hours)


def _classify_winners_losers(
    cells: list[dict],
    dataset: str,
) -> tuple[set[str], set[str], dict[str, float]]:
    """For each cell, apply its rule via _derive_exits, find trades matching
    the cell's (band, hour, expiry, delta), and split by `net_pnl_usd > 0`.

    Returns (winner_trade_ids, loser_trade_ids, pnl_by_trade_id). The pnl map
    carries the per-trade `net_pnl_estimate_usd` so the analytics layer can
    rank/sort trades without re-running `_derive_exits`.
    """
    import pandas as pd
    winners: set[str] = set()
    losers: set[str] = set()
    pnl_by_tid: dict[str, float] = {}
    for c in cells:
        rule = c.get("rule") or {}
        try:
            derived = _derive_exits({}, rule, dataset=dataset)
        except Exception as exc:  # noqa: BLE001
            log.warning("pivot_profile: _derive_exits failed for cell %s: %s",
                        c, exc)
            continue
        if derived is None or derived.empty:
            continue
        band = c.get("entry_atm_iv_band")
        hour = c.get("entry_hour_ist")
        expiry = c.get("expiry_bucket")
        delta = c.get("delta_target")
        mask = pd.Series(True, index=derived.index)
        if band is not None:
            mask &= (derived["entry_atm_iv_band"].astype(str) == str(band))
        if hour is not None:
            mask &= (derived["entry_hour_ist"].astype("Int64")
                     == int(hour))
        if expiry is not None and "expiry_bucket" in derived.columns:
            mask &= (derived["expiry_bucket"].astype(str) == str(expiry))
        if delta is not None and "delta_target" in derived.columns:
            mask &= (derived["delta_target"].astype(float).round(4)
                     == round(float(delta), 4))
        # _derive_exits exposes both `is_win` and `net_pnl_estimate_usd`.
        # Prefer the explicit boolean when present; fall back to pnl > 0.
        cols = ["trade_id"]
        if "is_win" in derived.columns:
            cols.append("is_win")
        elif "net_pnl_estimate_usd" in derived.columns:
            cols.append("net_pnl_estimate_usd")
        else:
            log.warning("pivot_profile: derived frame missing both is_win "
                        "and net_pnl_estimate_usd; skipping cell %s", c)
            continue
        # Always also pull net_pnl_estimate_usd so we can populate pnl_by_tid
        # even when classification uses the cheaper `is_win` boolean.
        if "net_pnl_estimate_usd" in derived.columns and \
                "net_pnl_estimate_usd" not in cols:
            cols.append("net_pnl_estimate_usd")
        sub = derived.loc[mask, cols]
        if sub.empty:
            continue
        if "is_win" in sub.columns:
            win_mask = sub["is_win"].fillna(False).astype(bool)
            usable = win_mask | (~win_mask)  # all rows
        else:
            pnl_num = pd.to_numeric(sub["net_pnl_estimate_usd"],
                                     errors="coerce")
            usable = pnl_num.notna()
            win_mask = pnl_num > 0
        for tid in sub.loc[win_mask, "trade_id"].astype(str):
            winners.add(tid)
        for tid in sub.loc[~win_mask & usable, "trade_id"].astype(str):
            losers.add(tid)
        if "net_pnl_estimate_usd" in sub.columns:
            pnl_series = pd.to_numeric(sub["net_pnl_estimate_usd"],
                                        errors="coerce")
            for tid, v in zip(sub["trade_id"].astype(str), pnl_series):
                if pd.notna(v):
                    pnl_by_tid[str(tid)] = float(v)
    return winners, losers, pnl_by_tid


def _warm_cache_entry(
    dataset: str,
    entry_hours: tuple[int, ...],
    cells: list[dict],
    state: dict,
    cache_key: tuple | None = None,
    ds_mtime: float = 0.0,
) -> None:
    """Background worker — builds the pivot-profile and writes into `state`."""
    try:
        trades = _load_trades(dataset)
        paths_glob = _paths_glob_for_dataset(dataset)

        def _on_progress(done: int, total: int) -> None:
            if total > 0:
                state["progress"] = min(1.0, done / float(total))

        # Classify winners/losers per cell rule before computing pivots so the
        # aggregator can fork the per-band stats into 3 buckets and rank
        # individual trades for the drill-down panels.
        winner_tids: set[str] = set()
        loser_tids: set[str] = set()
        pnl_by_tid: dict[str, float] = {}
        if cells:
            winner_tids, loser_tids, pnl_by_tid = _classify_winners_losers(
                cells, dataset)
            log.info("M7 pivot_profile: %d winners, %d losers across %d cells",
                     len(winner_tids), len(loser_tids), len(cells))

        result = aggregate_pivot_profile(
            trades, paths_glob, entry_hours,
            cells=(cells or None),
            winner_tids=winner_tids if cells else None,
            loser_tids=loser_tids if cells else None,
            pnl_by_tid=pnl_by_tid if cells else None,
            progress_cb=_on_progress)
        state["result"] = result
        state["status"] = "ready"
        state["progress"] = 1.0
        state["finished_at"] = time.time()
        log.info("M7 pivot_profile built (%s, cells=%d, hours=%s) in %.1fs; %d bands",
                 dataset, len(cells), entry_hours,
                 (state["finished_at"] - state["started_at"]),
                 len(result.get("by_band", {})))
        if cache_key is not None and ds_mtime > 0:
            _save_pivot_disk(cache_key, result, ds_mtime)
    except Exception as exc:  # noqa: BLE001
        state["status"] = "error"
        state["error"] = repr(exc)
        state["finished_at"] = time.time()
        log.exception("M7 pivot_profile build failed (%s, cells=%d, hours=%s)",
                      dataset, len(cells), entry_hours)


@router.get("/pivot_profile")
def pivot_profile(
    entry_hours: str = Query(
        "21,22,23,0,1,2,3",
        description="Comma-separated IST entry hours, e.g. '23,0'. "
                    "Ignored if `cells` is provided."),
    cells: Optional[str] = Query(
        None,
        description="JSON-encoded list of {entry_atm_iv_band, "
                    "entry_hour_ist} from the Best Combo per IV Band "
                    "selection. When set, scopes each band's pivot data "
                    "to its own cell."),
    dataset: str = Query("delta_match",
                          description="delta_match | price_match"),
) -> dict:
    """Return segment-based peak/trough/DD averages per IV band.

    Response shape:
      {
        "status": "warming" | "ready" | "error",
        "progress": 0.0..1.0,
        "result": { "by_band": {...}, "params": {...} } or null,
        "error": str | null,
        "min_trades_per_band_cell": 5,
      }
    """
    parsed_cells = _parse_cells(cells)
    hours = _parse_hours(entry_hours)
    sig = _scope_signature(parsed_cells, hours)
    cache_key = (dataset, sig)

    # Resolve ds_mtime for disk cache staleness check.
    _load_trades(dataset)
    _, ds_mtime = _TRADES_BY_DATASET.get(dataset, (None, 0.0))

    with _CACHE_LOCK:
        state = _CACHE.get(cache_key)
        if state is None or state["status"] == "error":
            # Check L2 disk cache before spawning a background thread.
            disk_result = _load_pivot_disk(cache_key, ds_mtime)
            if disk_result is not None:
                state = {
                    "status": "ready", "result": disk_result, "error": None,
                    "progress": 1.0, "started_at": time.time(),
                    "finished_at": time.time(),
                }
                _CACHE[cache_key] = state
            else:
                # Fresh slot — start a background warm.
                state = _new_state()
                _CACHE[cache_key] = state
                t = threading.Thread(
                    target=_warm_cache_entry,
                    args=(dataset, hours, parsed_cells, state, cache_key, ds_mtime),
                    daemon=True,
                    name=f"pivot_profile-{dataset}-{sig[:32]}",
                )
                t.start()

    return {
        "status": state["status"],
        "progress": float(state.get("progress", 0.0)),
        "result": state.get("result"),
        "error": state.get("error"),
        "min_trades_per_band_cell": MIN_TRADES_PER_BAND_CELL,
        "params_echo": {
            "dataset": dataset,
            "entry_hours": list(hours),
            "n_cells": len(parsed_cells),
        },
    }

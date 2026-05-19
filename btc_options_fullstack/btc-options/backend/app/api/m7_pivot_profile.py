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

import json
import logging
import threading
import time
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Query

from app.analytics.m7_pivot_profile import (
    MIN_TRADES_PER_BAND_CELL, aggregate_pivot_profile,
)
from app.api.m7_results import (
    _derive_exits, _load_trades, _paths_glob_for_dataset,
)

router = APIRouter()
log = logging.getLogger(__name__)


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
) -> tuple[set[str], set[str]]:
    """For each cell, apply its rule via _derive_exits, find trades matching
    the cell's (band, hour, expiry, delta), and split by `net_pnl_usd > 0`.

    Returns (winner_trade_ids, loser_trade_ids) — sets of trade_id strings.
    """
    import pandas as pd
    winners: set[str] = set()
    losers: set[str] = set()
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
    return winners, losers


def _warm_cache_entry(
    dataset: str,
    entry_hours: tuple[int, ...],
    cells: list[dict],
    state: dict,
) -> None:
    """Background worker — builds the pivot-profile and writes into `state`."""
    try:
        trades = _load_trades(dataset)
        paths_glob = _paths_glob_for_dataset(dataset)

        def _on_progress(done: int, total: int) -> None:
            if total > 0:
                state["progress"] = min(1.0, done / float(total))

        # Classify winners/losers per cell rule before computing pivots so the
        # aggregator can fork the per-band stats into 3 buckets.
        winner_tids: set[str] = set()
        loser_tids: set[str] = set()
        if cells:
            winner_tids, loser_tids = _classify_winners_losers(cells, dataset)
            log.info("M7 pivot_profile: %d winners, %d losers across %d cells",
                     len(winner_tids), len(loser_tids), len(cells))

        result = aggregate_pivot_profile(
            trades, paths_glob, entry_hours,
            cells=(cells or None),
            winner_tids=winner_tids if cells else None,
            loser_tids=loser_tids if cells else None,
            progress_cb=_on_progress)
        state["result"] = result
        state["status"] = "ready"
        state["progress"] = 1.0
        state["finished_at"] = time.time()
        log.info("M7 pivot_profile built (%s, cells=%d, hours=%s) in %.1fs; %d bands",
                 dataset, len(cells), entry_hours,
                 (state["finished_at"] - state["started_at"]),
                 len(result.get("by_band", {})))
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

    with _CACHE_LOCK:
        state = _CACHE.get(cache_key)
        if state is None or state["status"] == "error":
            # Fresh slot — start a background warm.
            state = _new_state()
            _CACHE[cache_key] = state
            t = threading.Thread(
                target=_warm_cache_entry,
                args=(dataset, hours, parsed_cells, state),
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

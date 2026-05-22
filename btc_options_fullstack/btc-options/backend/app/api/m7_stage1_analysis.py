"""Stage-1 Partial Exit — HTTP API.

POST /api/v1/m7/stage1_analysis
  Body: {"per_band_rules": {...}, "dataset": "delta_match"}
  Returns: {status, progress, result, error, params_echo}

POST /api/v1/m7/stage1_analysis/precheck
  Same body. Returns per-band exit-cache warm/cold status instantly.

Uses the warming-response pattern (status: warming|ready). Cache key =
SHA1 of {per_band_rules, dataset, response_version}. Repeat POSTs with
the same rules return instantly from disk on the second call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
import threading
import time
from typing import Any, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.m7_results import (
    M7_BASE_DIR, _TRADES_BY_DATASET, _load_trades, _rule_cache_path,
    _derive_exits,
)
from app.scripts.stage1_partial_exit_per_band_2d import run_stage1_sweep

router = APIRouter()
log = logging.getLogger(__name__)

_STAGE1_DISK_DIR = os.path.join(M7_BASE_DIR, "stage1_cache")
_STAGE1_RESPONSE_VERSION = 2  # bumped: removed n<30 and n<10 reference-level gates


# ─── Cache helpers ────────────────────────────────────────────────────────────

def _scope_signature(per_band_rules: dict, dataset: str) -> str:
    canonical = json.dumps(
        {"rules": per_band_rules, "dataset": dataset, "v": _STAGE1_RESPONSE_VERSION},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _disk_path(sig: str) -> str:
    os.makedirs(_STAGE1_DISK_DIR, exist_ok=True)
    return os.path.join(_STAGE1_DISK_DIR, f"{sig}.json")


def _load_disk(sig: str, ds_mtime: float) -> Optional[dict]:
    path = _disk_path(sig)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("_ds_mtime") != ds_mtime:
            return None
        if data.get("_response_version") != _STAGE1_RESPONSE_VERSION:
            return None
        data.pop("_ds_mtime", None)
        data.pop("_response_version", None)
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("stage1 disk cache read failed (%s): %s", path, exc)
        return None


def _save_disk(sig: str, result: dict, ds_mtime: float) -> None:
    try:
        path = _disk_path(sig)
        payload = {**result, "_ds_mtime": ds_mtime, "_response_version": _STAGE1_RESPONSE_VERSION}
        tmp_fd, tmp_path = tempfile.mkstemp(dir=_STAGE1_DISK_DIR, suffix=".tmp")
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)
    except Exception as exc:  # noqa: BLE001
        log.warning("stage1 disk cache write failed: %s", exc)


# ─── In-memory state ──────────────────────────────────────────────────────────

_CACHE: dict[str, dict] = {}   # sig → state dict
_CACHE_LOCK = threading.Lock()


def _new_state() -> dict:
    return {
        "status": "warming", "result": None, "error": None,
        "progress": 0.0,
        "started_at": time.time(), "finished_at": None,
    }


# ─── Serialisation ────────────────────────────────────────────────────────────

def _sanitise(obj: Any) -> Any:
    """Recursively replace NaN/inf with None for JSON serialisation."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, (np.floating, np.integer)):
        v = float(obj) if isinstance(obj, np.floating) else int(obj)
        return None if (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else v
    if isinstance(obj, dict):
        return {k: _sanitise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitise(v) for v in obj]
    return obj


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Convert dataframe to JSON-safe list-of-dicts."""
    if df is None or df.empty:
        return []
    records = df.where(pd.notna(df), other=None).to_dict("records")
    return _sanitise(records)


def _sweep_to_json(sweep: dict) -> dict:
    """Serialise run_stage1_sweep() output to JSON-safe dict."""
    return {
        "all_cells":      _df_to_records(sweep.get("all_cells")),
        "best_cells":     _df_to_records(sweep.get("best_cells")),
        "distribution":   _df_to_records(sweep.get("distribution")),
        "band_summaries": _sanitise(sweep.get("band_summaries", [])),
        "validation":     _sanitise(sweep.get("validation", {})),
        "caveats":        sweep.get("caveats", []),
        "params_echo":    _sanitise(sweep.get("params_echo", {})),
    }


# ─── Background worker ────────────────────────────────────────────────────────

def _warm_cache_entry(
    per_band_rules: dict,
    dataset: str,
    state: dict,
    sig: str,
    ds_mtime: float,
) -> None:
    try:
        def _cb(p: float) -> None:
            state["progress"] = float(p)

        sweep = run_stage1_sweep(per_band_rules, dataset, progress_cb=_cb)
        result = _sweep_to_json(sweep)
        state["result"] = result
        state["status"] = "ready"
        state["finished_at"] = time.time()
        _save_disk(sig, result, ds_mtime)
    except Exception as exc:  # noqa: BLE001
        log.exception("stage1 sweep failed (sig=%s): %s", sig, exc)
        state["status"] = "error"
        state["error"] = str(exc)
        state["finished_at"] = time.time()


# ─── Request models ───────────────────────────────────────────────────────────

class Stage1Request(BaseModel):
    per_band_rules: dict = Field(..., description="IV-band → {rule_label, rule_dict, ...}")
    dataset: str = Field("delta_match", description="delta_match | price_match")


class Stage1BandTradesRequest(BaseModel):
    band: str = Field(..., description="IV band label e.g. '30-40'")
    rule_dict: dict = Field(..., description="Exit rule dict (nulls stripped)")
    expiry_bucket: Optional[str] = Field(None)
    delta_target: Optional[float] = Field(None)
    entry_hour_ist: Optional[int] = Field(None)
    dataset: str = Field("delta_match")


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/stage1_analysis")
def stage1_analysis(body: Stage1Request) -> dict:
    """Run Stage-1 2D sweep for the given per-band rules.

    First call launches a background thread and returns status='warming'.
    Poll at ~2s intervals; returns status='ready' with result when done.
    Identical calls hit disk/memory cache instantly.
    """
    per_band_rules = body.per_band_rules
    dataset = body.dataset

    _load_trades(dataset)
    _, ds_mtime = _TRADES_BY_DATASET.get(dataset, (None, 0.0))

    sig = _scope_signature(per_band_rules, dataset)

    with _CACHE_LOCK:
        state = _CACHE.get(sig)
        if state is None or state["status"] == "error":
            disk_result = _load_disk(sig, ds_mtime)
            if disk_result is not None:
                state = {
                    "status": "ready", "result": disk_result, "error": None,
                    "progress": 1.0,
                    "started_at": time.time(), "finished_at": time.time(),
                }
                _CACHE[sig] = state
            else:
                state = _new_state()
                _CACHE[sig] = state
                t = threading.Thread(
                    target=_warm_cache_entry,
                    args=(per_band_rules, dataset, state, sig, ds_mtime),
                    daemon=True,
                    name=f"stage1-{sig[:16]}",
                )
                t.start()

    return {
        "status":    state["status"],
        "progress":  float(state.get("progress", 0.0)),
        "result":    state.get("result"),
        "error":     state.get("error"),
        "params_echo": {
            "dataset":         dataset,
            "sig":             sig,
            "n_bands":         len(per_band_rules),
            "bands":           list(per_band_rules.keys()),
        },
    }


@router.post("/stage1_analysis/precheck")
def stage1_analysis_precheck(body: Stage1Request) -> dict:
    """Return warm/cold exit-cache status for each band's rule_dict.

    This is a sub-100ms pre-flight call. The frontend uses it to show the
    FilterSummary panel with estimated compute time before clicking Compute.
    'warm' = exit-cache parquet exists on disk (that band will be fast).
    'cold' = parquet missing (will need ~5–10s to build that band).
    Does NOT start a sweep — purely a file-system check.
    """
    dataset = body.dataset
    per_band_status: dict[str, str] = {}
    for band, cfg in body.per_band_rules.items():
        rule_dict = {k: v for k, v in cfg.get("rule_dict", {}).items() if v is not None}
        path = _rule_cache_path(dataset, rule_dict)
        per_band_status[band] = "warm" if os.path.exists(path) else "cold"

    n_cold = sum(1 for s in per_band_status.values() if s == "cold")
    estimated_seconds = n_cold * 8  # ~8s per cold band from L1-miss derive_exits

    return {
        "per_band_cache_status": per_band_status,
        "n_warm":             len(per_band_status) - n_cold,
        "n_cold":             n_cold,
        "estimated_compute_seconds": estimated_seconds,
    }


@router.post("/stage1_analysis/band_trades")
def stage1_band_trades(body: Stage1BandTradesRequest) -> dict:
    """Return all per-trade rows for one band's baseline rule.

    Trigger reference levels (SL_avg, L_avg, W_avg) are computed from
    whatever trades exist — no minimum n threshold. Even 1 trade is enough.

    The client applies case classification and hypothetical P&L arithmetic
    so it can react instantly to trigger/exit_frac changes without re-fetching.

    Returns:
      trades: list of per-trade dicts
      trigger_levels: {sl_avg, l_avg, w_avg}  (null when subgroup is empty)
      n_total, n_sl_hit, n_losers, n_winners
    """
    filters: dict = {"entry_atm_iv_band": body.band}
    if body.expiry_bucket is not None:
        filters["expiry_bucket"] = body.expiry_bucket
    if body.delta_target is not None:
        filters["delta_target"] = body.delta_target
    if body.entry_hour_ist is not None:
        filters["entry_hour_ist"] = body.entry_hour_ist

    rule_dict = {k: v for k, v in body.rule_dict.items() if v is not None}

    try:
        df = _derive_exits(filters, rule_dict, dataset=body.dataset)
    except Exception as exc:
        log.exception("stage1_band_trades _derive_exits failed: %s", exc)
        return {"error": str(exc), "trades": [], "trigger_levels": {}, "n_total": 0}

    if df.empty:
        return {
            "trades": [],
            "trigger_levels": {"sl_avg": None, "l_avg": None, "w_avg": None},
            "n_total": 0, "n_sl_hit": 0, "n_losers": 0, "n_winners": 0,
        }

    # Compute trigger reference levels from whatever data exists (no threshold).
    sl_mask  = df["is_premium_sl_hit"].astype(bool)
    loss_mask = df["net_pnl_estimate_usd"] < 0
    win_mask  = df["net_pnl_estimate_usd"] >= 0

    def _safe_mean(mask: "pd.Series") -> Optional[float]:
        sub = df.loc[mask, "min_mtm_usd"]
        if sub.empty:
            return None
        v = float(sub.mean())
        return None if (math.isnan(v) or math.isinf(v)) else v

    sl_avg = _safe_mean(sl_mask)
    l_avg  = _safe_mean(loss_mask)
    w_avg  = _safe_mean(win_mask)

    # Select columns to return per trade
    want_cols = [
        "trade_id", "friday_date_ist", "entry_ts_utc",
        "expiry_bucket", "delta_target", "entry_hour_ist",
        "min_mtm_usd", "max_mtm_usd", "net_pnl_estimate_usd",
        "is_premium_sl_hit", "rel_time_min_mtm", "rel_time_max_mtm",
        "exit_reason",
        # Per-trade context fields — used by frontend F14 to compute hyp
        # exit MTM, Peak %, Trough %, and per-trade Ret/credit, Ret/margin.
        "exit_mtm_usd", "credit_usd", "margin_used_usd_at_entry",
        "pct_max_mtm_on_credit", "pct_min_mtm_on_credit",
        "pct_return_on_credit", "pct_return_on_margin",
    ]
    cols = [c for c in want_cols if c in df.columns]
    trades_df = df[cols].copy()

    # Cast trade_id to str for JS precision safety
    if "trade_id" in trades_df.columns:
        trades_df["trade_id"] = trades_df["trade_id"].astype(str)

    return {
        "trades": _df_to_records(trades_df),
        "trigger_levels": {
            "sl_avg": sl_avg,
            "l_avg":  l_avg,
            "w_avg":  w_avg,
        },
        "n_total":   len(df),
        "n_sl_hit":  int(sl_mask.sum()),
        "n_losers":  int(loss_mask.sum()),
        "n_winners": int(win_mask.sum()),
    }


@router.post("/stage1_analysis/all_trades")
def stage1_all_trades(body: Stage1Request) -> dict:
    """All trades across every band in per_band_rules, each tagged with its
    band's trigger reference levels (SL_avg / L_avg / W_avg, no min-n threshold).

    The client applies per-trade classification so it can react to trigger /
    exit_frac changes without re-fetching. Each trade's band_*_avg fields carry
    that band's dollar value so that 'l_avg' applies each band's own L_avg to
    its own trades — not a global cross-band level.

    Future work: per-cell pivot graph showing trough distribution of winners vs
    losers with trigger line marked — needs per-bar path data (similar to pivot
    profile 5-segment charts), deferred to a future session.
    """
    all_rows: list[dict] = []
    n_by_band: dict[str, int] = {}

    want_cols = [
        "trade_id", "friday_date_ist", "entry_ts_utc",
        "expiry_bucket", "delta_target", "entry_hour_ist",
        "min_mtm_usd", "max_mtm_usd", "net_pnl_estimate_usd",
        "is_premium_sl_hit", "rel_time_min_mtm", "rel_time_max_mtm",
        "exit_reason",
        "exit_mtm_usd", "credit_usd", "margin_used_usd_at_entry",
        "pct_max_mtm_on_credit", "pct_min_mtm_on_credit",
        "pct_return_on_credit", "pct_return_on_margin",
    ]

    for band, cfg in body.per_band_rules.items():
        rule_dict = {k: v for k, v in cfg.get("rule_dict", {}).items() if v is not None}
        rule_label = cfg.get("rule_label", "")

        filters: dict = {"entry_atm_iv_band": band}
        if cfg.get("expiry_bucket"):
            filters["expiry_bucket"] = cfg["expiry_bucket"]
        if cfg.get("delta_target") is not None:
            filters["delta_target"] = cfg["delta_target"]
        if cfg.get("entry_hour_ist") is not None:
            filters["entry_hour_ist"] = cfg["entry_hour_ist"]

        try:
            df = _derive_exits(filters, rule_dict, dataset=body.dataset)
        except Exception as exc:  # noqa: BLE001
            log.warning("stage1_all_trades band=%s: %s", band, exc)
            n_by_band[band] = 0
            continue

        if df.empty:
            n_by_band[band] = 0
            continue

        sl_m   = df["is_premium_sl_hit"].astype(bool)
        loss_m = df["net_pnl_estimate_usd"] < 0
        win_m  = df["net_pnl_estimate_usd"] >= 0

        def _bm(mask: "pd.Series") -> Optional[float]:
            sub = df.loc[mask, "min_mtm_usd"]
            if sub.empty:
                return None
            v = float(sub.mean())
            return None if (math.isnan(v) or math.isinf(v)) else v

        cols = [c for c in want_cols if c in df.columns]
        sub = df[cols].copy()
        if "trade_id" in sub.columns:
            sub["trade_id"] = sub["trade_id"].astype(str)
        sub["band"]        = band
        sub["rule_label"]  = rule_label
        sub["band_sl_avg"] = _bm(sl_m)
        sub["band_l_avg"]  = _bm(loss_m)
        sub["band_w_avg"]  = _bm(win_m)

        all_rows.extend(_df_to_records(sub))
        n_by_band[band] = len(df)

    return {"trades": all_rows, "n_total": len(all_rows), "n_by_band": n_by_band}

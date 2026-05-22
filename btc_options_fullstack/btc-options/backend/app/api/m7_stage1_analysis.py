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
)
from app.scripts.stage1_partial_exit_per_band_2d import run_stage1_sweep

router = APIRouter()
log = logging.getLogger(__name__)

_STAGE1_DISK_DIR = os.path.join(M7_BASE_DIR, "stage1_cache")
_STAGE1_RESPONSE_VERSION = 1


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

"""
Two-tier cache for completed backtest results.

Memory tier: bounded LRU dict (most recent N results).
Disk tier:   JSON files at /home/abhis/btc-data/derived/backtests/<key>.json
             — shared across slots via bind mount.

Cache key is sha256 of canonical-serialised params (sorted keys, no whitespace).
Same strategy + same date range = same key = cache hit.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_DIR = "/home/abhis/btc-data/derived/backtests"
_MEM_MAX = 32  # most-recent N results kept in process memory

_mem: "OrderedDict[str, dict]" = OrderedDict()
_lock = Lock()


def cache_key(params: dict) -> str:
    """Stable hash of params — same params dict produces same key across runs."""
    canon = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _disk_path(key: str) -> str:
    return os.path.join(_CACHE_DIR, f"{key}.json")


def load(key: str) -> Optional[dict]:
    """Return cached result or None. Promotes disk hit into memory tier."""
    with _lock:
        if key in _mem:
            _mem.move_to_end(key)
            return _mem[key]
    path = _disk_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            result = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("backtest_cache: failed to read %s (%s) — treating as miss", path, e)
        return None
    with _lock:
        _mem[key] = result
        _mem.move_to_end(key)
        while len(_mem) > _MEM_MAX:
            _mem.popitem(last=False)
    return result


def save(key: str, result: dict) -> None:
    """Persist result to both tiers. Atomic-replace on disk to avoid partial files."""
    with _lock:
        _mem[key] = result
        _mem.move_to_end(key)
        while len(_mem) > _MEM_MAX:
            _mem.popitem(last=False)
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _disk_path(key)
        fd, tmp = tempfile.mkstemp(dir=_CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(result, f)
        os.replace(tmp, path)
        logger.info("backtest_cache: saved key=%s (%d trades)", key, len(result.get("trades", [])))
    except OSError as e:
        logger.warning("backtest_cache: disk save failed for key=%s (%s)", key, e)

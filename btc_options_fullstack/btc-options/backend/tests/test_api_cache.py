"""Unit tests for the disk+memory response cache helper in app.api.historical.

Verifies: a second call returns the cached value (no recompute), the value persists
to disk (survives clearing the in-memory layer), and a change to a source file's mtime
invalidates the entry (recompute).
"""

from __future__ import annotations

import os

import app.api.historical as h


def test_api_cached_memory_disk_and_invalidation(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "_API_CACHE_DIR", str(tmp_path / "api_cache"))
    h._api_mem.clear()

    src = tmp_path / "src.parquet"
    src.write_text("a")

    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"v": calls["n"]}

    # 1) first call computes
    r1 = h._api_cached("t", {"k": 1}, [str(src)], compute)
    assert r1 == {"v": 1}
    assert calls["n"] == 1

    # 2) second call → in-memory hit, no recompute
    r2 = h._api_cached("t", {"k": 1}, [str(src)], compute)
    assert r2 == {"v": 1}
    assert calls["n"] == 1

    # 3) clear the memory layer → disk hit, still no recompute
    h._api_mem.clear()
    r3 = h._api_cached("t", {"k": 1}, [str(src)], compute)
    assert r3 == {"v": 1}
    assert calls["n"] == 1
    # the disk artifact exists
    assert os.path.isdir(str(tmp_path / "api_cache" / "t"))

    # 4) change the source mtime → token mismatch → recompute
    future = os.path.getmtime(str(src)) + 100
    os.utime(str(src), (future, future))
    h._api_mem.clear()
    r4 = h._api_cached("t", {"k": 1}, [str(src)], compute)
    assert calls["n"] == 2
    assert r4 == {"v": 2}

    # 5) a different key is independent
    r5 = h._api_cached("t", {"k": 2}, [str(src)], compute)
    assert calls["n"] == 3
    assert r5 == {"v": 3}

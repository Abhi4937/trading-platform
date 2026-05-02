"""Tests for merge_live_to_main: dedupe, archive, idempotency, schema-mismatch guard."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services import merge_live_to_main as mm

OPTIONS_SCHEMA = pa.schema([
    pa.field("timestamp_ist",  pa.timestamp("s"),  nullable=False),
    pa.field("timestamp_unix", pa.int64(),          nullable=False),
    pa.field("mark_open",      pa.float64()),
    pa.field("mark_high",      pa.float64()),
    pa.field("mark_low",       pa.float64()),
    pa.field("mark_close",     pa.float64()),
    pa.field("oi_open",        pa.float64()),
    pa.field("oi_high",        pa.float64()),
    pa.field("oi_low",         pa.float64()),
    pa.field("oi_close",       pa.float64()),
])


def _make_table(timestamps: list[int], mark_close: float = 0.5) -> pa.Table:
    n = len(timestamps)
    rows = {
        "timestamp_ist":  [datetime.fromtimestamp(t).replace(microsecond=0) for t in timestamps],
        "timestamp_unix": timestamps,
        "mark_open":      [mark_close] * n,
        "mark_high":      [mark_close] * n,
        "mark_low":       [mark_close] * n,
        "mark_close":     [mark_close] * n,
        "oi_open":        [None] * n,
        "oi_high":        [None] * n,
        "oi_low":         [None] * n,
        "oi_close":       [None] * n,
    }
    return pa.table(rows, schema=OPTIONS_SCHEMA)


def _setup_paths(monkeypatch, tmp_path):
    live = tmp_path / "data_live"
    main = tmp_path / "data"
    archive = live / "archive"
    state = tmp_path / "logs" / "merge_state.json"
    monkeypatch.setattr(mm, "DATA_LIVE", live)
    monkeypatch.setattr(mm, "DATA_MAIN", main)
    monkeypatch.setattr(mm, "ARCHIVE",   archive)
    monkeypatch.setattr(mm, "STATE_FILE", state)
    live.mkdir(parents=True)
    main.mkdir(parents=True)
    return live, main, archive, state


def test_merge_creates_when_main_missing(monkeypatch, tmp_path):
    live, main, archive, _ = _setup_paths(monkeypatch, tmp_path)
    rel = "options/expiry=2025-05-08/strike=100000/CE.parquet"
    live_p = live / rel
    live_p.parent.mkdir(parents=True)
    pq.write_table(_make_table([1, 2, 3]), live_p)

    stats = mm.run_merge()

    assert stats["files_created"] == 1
    main_p = main / rel
    assert main_p.exists()
    t = pq.ParquetFile(str(main_p)).read()
    assert sorted(t.column("timestamp_unix").to_pylist()) == [1, 2, 3]

    # Live file moved to archive
    assert not live_p.exists()
    today = datetime.now().strftime("%Y-%m-%d")
    archived = archive / today / rel
    assert archived.exists()


def test_merge_dedupes_overlap(monkeypatch, tmp_path):
    live, main, archive, _ = _setup_paths(monkeypatch, tmp_path)
    rel = "options/expiry=2025-05-08/strike=100000/CE.parquet"

    # Existing main file [1,2,3] with mark=0.5
    main_p = main / rel
    main_p.parent.mkdir(parents=True)
    pq.write_table(_make_table([1, 2, 3], 0.5), main_p)

    # Live file [3,4,5] with mark=0.9 (overlap on ts=3, live wins)
    live_p = live / rel
    live_p.parent.mkdir(parents=True)
    pq.write_table(_make_table([3, 4, 5], 0.9), live_p)

    stats = mm.run_merge()

    assert stats["files_merged"] == 1
    t = pq.ParquetFile(str(main_p)).read()
    # 1,2,3,4,5 — dedupe collapses overlap on 3
    assert sorted(t.column("timestamp_unix").to_pylist()) == [1, 2, 3, 4, 5]
    # Sort order ascending
    assert t.column("timestamp_unix").to_pylist() == [1, 2, 3, 4, 5]
    # ts=3 should keep live's 0.9 value (last wins on dedupe)
    by_ts = {r["timestamp_unix"]: r for r in t.to_pylist()}
    assert by_ts[3]["mark_close"] == 0.9


def test_merge_dry_run_writes_nothing(monkeypatch, tmp_path):
    live, main, archive, _ = _setup_paths(monkeypatch, tmp_path)
    rel = "options/expiry=2025-05-08/strike=100000/CE.parquet"
    live_p = live / rel
    live_p.parent.mkdir(parents=True)
    pq.write_table(_make_table([1, 2]), live_p)

    stats = mm.run_merge(dry_run=True)
    assert stats["dry_run"]
    assert stats["files_created"] == 1
    # main file NOT created in dry-run
    assert not (main / rel).exists()
    # live file NOT moved to archive
    assert live_p.exists()


def test_merge_skips_archive_subtree(monkeypatch, tmp_path):
    live, main, archive, _ = _setup_paths(monkeypatch, tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")
    arch_p = archive / today / "options/expiry=2025-05-08/strike=100000/CE.parquet"
    arch_p.parent.mkdir(parents=True)
    pq.write_table(_make_table([1, 2]), arch_p)
    # No live file — archive parquet should NOT be re-merged.
    stats = mm.run_merge()
    assert stats["files_seen"] == 0
    assert not (main / "options/expiry=2025-05-08/strike=100000/CE.parquet").exists()


def test_merge_state_records_last_run(monkeypatch, tmp_path):
    live, main, archive, state = _setup_paths(monkeypatch, tmp_path)
    rel = "options/expiry=2025-05-08/strike=100000/CE.parquet"
    live_p = live / rel
    live_p.parent.mkdir(parents=True)
    pq.write_table(_make_table([1, 2]), live_p)

    mm.run_merge()

    assert state.exists()
    h = mm.hours_since_last_merge()
    assert h is not None
    assert h < 1.0  # just ran


def test_archive_pruning(monkeypatch, tmp_path):
    live, main, archive, _ = _setup_paths(monkeypatch, tmp_path)
    # old archive day
    old_day = (datetime.now() - timedelta(days=mm.ARCHIVE_RETENTION_DAYS + 1)).strftime("%Y-%m-%d")
    (archive / old_day / "options").mkdir(parents=True)
    (archive / old_day / "options" / "stale.parquet").touch()
    # recent archive day (should stay)
    recent_day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    (archive / recent_day / "options").mkdir(parents=True)
    (archive / recent_day / "options" / "fresh.parquet").touch()

    removed = mm._prune_archive()

    assert removed == 1
    assert not (archive / old_day).exists()
    assert (archive / recent_day).exists()

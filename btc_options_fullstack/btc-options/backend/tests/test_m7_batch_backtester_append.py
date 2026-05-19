"""Tests for the trades-parquet append/merge logic added in B1.

Covers `_write_trades_atomic` (the helper) and the `run()`-level
clobber-guard / CLI flag interaction. Pure-Python — no DuckDB or M3
dependencies — so the suite runs fast.
"""
from __future__ import annotations

import argparse
import os
from unittest import mock

import pandas as pd
import pytest

from app.analytics.m7_batch_backtester import _write_trades_atomic


def _row(friday: str, hour: int = 23, expiry: str = "2024-01-12",
         delta: float = 0.30, **extra) -> dict:
    """Build a minimal trade-row dict matching the schema fields the
    helper relies on for dedup + sort. Other columns are optional in
    these tests because the helper doesn't read them."""
    base = {
        "trade_id": f"{friday}_{hour}_{expiry}_{delta}",
        "friday_date_ist": friday,
        "entry_hour_ist": hour,
        "expiry_date": expiry,
        "delta_target": delta,
        "gross_pnl_usd": 0.0,
    }
    base.update(extra)
    return base


def test_write_to_empty_target_with_append_writes_new_rows(tmp_path):
    """append=True against a non-existent target == regular write."""
    out = tmp_path / "m7_trades.parquet"
    rows = [_row("2026-04-24"), _row("2026-05-01")]
    n = _write_trades_atomic(rows, str(out), append=True)
    assert n == 2
    df = pd.read_parquet(out)
    assert sorted(df["friday_date_ist"].tolist()) == ["2026-04-24", "2026-05-01"]


def test_write_to_empty_target_with_append_false_writes_new_rows(tmp_path):
    """append=False against non-existent target == regular write."""
    out = tmp_path / "m7_trades.parquet"
    rows = [_row("2026-04-24")]
    n = _write_trades_atomic(rows, str(out), append=False)
    assert n == 1
    df = pd.read_parquet(out)
    assert df["friday_date_ist"].tolist() == ["2026-04-24"]


def test_append_to_existing_target_merges_disjoint_fridays(tmp_path):
    """Existing 121-friday history + 4 new fridays via append → 125 rows."""
    out = tmp_path / "m7_trades.parquet"
    existing_rows = [_row(f"2024-01-{d:02d}") for d in (5, 12, 19, 26)]
    _write_trades_atomic(existing_rows, str(out), append=False)
    new_rows = [_row("2026-04-24"), _row("2026-05-01"), _row("2026-05-08"),
                _row("2026-05-15")]
    n = _write_trades_atomic(new_rows, str(out), append=True)
    assert n == 8
    df = pd.read_parquet(out)
    assert sorted(df["friday_date_ist"].unique().tolist()) == [
        "2024-01-05", "2024-01-12", "2024-01-19", "2024-01-26",
        "2026-04-24", "2026-05-01", "2026-05-08", "2026-05-15",
    ]


def test_append_with_overlapping_fridays_is_idempotent(tmp_path):
    """Re-running --append with same fridays produces the same parquet —
    no duplicate rows. This is the key idempotency property for the
    Phase 0 trade refresh: a user re-running the same --since command
    should not double-count any friday's trades."""
    out = tmp_path / "m7_trades.parquet"
    rows_v1 = [_row("2026-04-24", hour=23, gross_pnl_usd=10.0),
               _row("2026-05-01", hour=23, gross_pnl_usd=20.0)]
    _write_trades_atomic(rows_v1, str(out), append=False)
    # Second run on same fridays — possibly with refreshed bar data so
    # gross_pnl changes. The OLD rows for these fridays must be replaced,
    # not concatenated.
    rows_v2 = [_row("2026-04-24", hour=23, gross_pnl_usd=11.5),
               _row("2026-05-01", hour=23, gross_pnl_usd=22.0)]
    n = _write_trades_atomic(rows_v2, str(out), append=True)
    assert n == 2
    df = pd.read_parquet(out)
    assert df["gross_pnl_usd"].sort_values().tolist() == [11.5, 22.0]


def test_append_with_partial_overlap_drops_only_overlapping_fridays(tmp_path):
    """If 1 existing friday is in the new range AND 1 isn't, only the
    overlapping one is replaced; the non-overlapping is preserved."""
    out = tmp_path / "m7_trades.parquet"
    existing = [_row("2024-01-05", gross_pnl_usd=5.0),
                _row("2024-01-12", gross_pnl_usd=12.0)]
    _write_trades_atomic(existing, str(out), append=False)
    new_rows = [_row("2024-01-12", gross_pnl_usd=99.0),  # overlap → replace
                _row("2024-01-19", gross_pnl_usd=19.0)]   # new → add
    n = _write_trades_atomic(new_rows, str(out), append=True)
    assert n == 3
    df = pd.read_parquet(out).sort_values("friday_date_ist").reset_index(drop=True)
    assert df["friday_date_ist"].tolist() == ["2024-01-05", "2024-01-12", "2024-01-19"]
    assert df.loc[df["friday_date_ist"] == "2024-01-12", "gross_pnl_usd"].iloc[0] == 99.0
    assert df.loc[df["friday_date_ist"] == "2024-01-05", "gross_pnl_usd"].iloc[0] == 5.0


def test_rebuild_overwrites_unconditionally(tmp_path):
    """append=False with an existing target overwrites — destructive."""
    out = tmp_path / "m7_trades.parquet"
    _write_trades_atomic([_row("2024-01-05"), _row("2024-01-12")],
                         str(out), append=False)
    n = _write_trades_atomic([_row("2026-05-15")], str(out), append=False)
    assert n == 1
    df = pd.read_parquet(out)
    assert df["friday_date_ist"].tolist() == ["2026-05-15"]


def test_write_empty_rows_raises(tmp_path):
    """Helper refuses to write an empty parquet — caller bug, not user error."""
    out = tmp_path / "m7_trades.parquet"
    with pytest.raises(ValueError):
        _write_trades_atomic([], str(out), append=True)


def test_atomic_write_leaves_target_intact_on_failure(tmp_path, monkeypatch):
    """If to_parquet raises mid-write, the existing target file must
    remain untouched (the .tmp dance + os.replace guarantees this)."""
    out = tmp_path / "m7_trades.parquet"
    _write_trades_atomic([_row("2024-01-05", gross_pnl_usd=42.0)],
                         str(out), append=False)
    original_mtime = os.path.getmtime(out)
    original_bytes = out.read_bytes()

    # Force to_parquet to raise — simulate a disk-full / permission error.
    def boom(self, *a, **kw):
        raise OSError("simulated disk full")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", boom)

    with pytest.raises(OSError):
        _write_trades_atomic([_row("2026-05-15")], str(out), append=True)

    # Target file is untouched — same mtime, same bytes.
    assert os.path.getmtime(out) == original_mtime
    assert out.read_bytes() == original_bytes


def test_sort_order_friday_hour_expiry_delta(tmp_path):
    """Output is sorted by (friday_date_ist, entry_hour_ist, expiry_date,
    delta_target) — deterministic regardless of input order."""
    out = tmp_path / "m7_trades.parquet"
    rows = [
        _row("2024-01-12", hour=23, expiry="2024-01-19", delta=0.30),
        _row("2024-01-05", hour=22, expiry="2024-01-12", delta=0.50),
        _row("2024-01-05", hour=23, expiry="2024-01-12", delta=0.10),
        _row("2024-01-05", hour=22, expiry="2024-01-12", delta=0.20),
    ]
    _write_trades_atomic(rows, str(out), append=False)
    df = pd.read_parquet(out)
    assert df["friday_date_ist"].tolist() == [
        "2024-01-05", "2024-01-05", "2024-01-05", "2024-01-12"]
    assert df.loc[df["friday_date_ist"] == "2024-01-05", "entry_hour_ist"
                  ].tolist() == [22, 22, 23]
    assert df.loc[(df["friday_date_ist"] == "2024-01-05") &
                  (df["entry_hour_ist"] == 22), "delta_target"
                  ].tolist() == [0.20, 0.50]


# ── run()-level clobber-guard tests ──────────────────────────────────────────
#
# `run()` calls into the full backtester pipeline (loads M3, walks fridays,
# etc.) which we don't want to exercise in unit tests. The guard logic lives
# at the top of run() before any heavy work, so we can test it by mocking
# argparse and asserting the RuntimeError raises BEFORE any data loading.

def _args(out_dir: str, *, append: bool = False, rebuild: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        since=None, through=None, max_fridays=None, max_expiries=None,
        out_dir=out_dir, append=append, rebuild=rebuild,
    )


def test_clobber_guard_raises_when_target_exists_and_no_flags(tmp_path):
    """run() must refuse to overwrite m7_trades.parquet without explicit flag."""
    from app.analytics import m7_batch_backtester as bt
    (tmp_path / "m7_trades.parquet").write_bytes(b"existing parquet bytes")
    args = _args(str(tmp_path), append=False, rebuild=False)
    # _load_m3 should never be called — guard fires first.
    with mock.patch.object(bt, "_load_m3") as m3_mock, \
         pytest.raises(RuntimeError, match="already exists"):
        bt.run(args)
    m3_mock.assert_not_called()


def test_clobber_guard_passes_with_append_flag(tmp_path):
    """--append bypasses the guard."""
    from app.analytics import m7_batch_backtester as bt
    (tmp_path / "m7_trades.parquet").write_bytes(b"existing parquet bytes")
    args = _args(str(tmp_path), append=True, rebuild=False)
    # Mock M3 to raise so we know we got PAST the guard but stop before
    # the rest of the pipeline.
    sentinel = RuntimeError("sentinel — guard passed, pipeline proceeded")
    with mock.patch.object(bt, "_load_m3", side_effect=sentinel), \
         pytest.raises(RuntimeError, match="sentinel"):
        bt.run(args)


def test_clobber_guard_passes_with_rebuild_flag(tmp_path):
    """--rebuild bypasses the guard."""
    from app.analytics import m7_batch_backtester as bt
    (tmp_path / "m7_trades.parquet").write_bytes(b"existing parquet bytes")
    args = _args(str(tmp_path), append=False, rebuild=True)
    sentinel = RuntimeError("sentinel — guard passed")
    with mock.patch.object(bt, "_load_m3", side_effect=sentinel), \
         pytest.raises(RuntimeError, match="sentinel"):
        bt.run(args)


def test_clobber_guard_inactive_when_target_missing(tmp_path):
    """First-ever run with no existing parquet — no flag required."""
    from app.analytics import m7_batch_backtester as bt
    args = _args(str(tmp_path), append=False, rebuild=False)
    sentinel = RuntimeError("sentinel — guard passed")
    with mock.patch.object(bt, "_load_m3", side_effect=sentinel), \
         pytest.raises(RuntimeError, match="sentinel"):
        bt.run(args)


def test_rebuild_takes_precedence_over_append(tmp_path):
    """--append --rebuild together → rebuild wins (destructive intent explicit)."""
    out = tmp_path / "m7_trades.parquet"
    _write_trades_atomic([_row("2024-01-05"), _row("2024-01-12")],
                         str(out), append=False)
    # Now simulate run() logic: effective_append = append AND NOT rebuild.
    args_both = _args(str(tmp_path), append=True, rebuild=True)
    effective_append = bool(args_both.append and not args_both.rebuild)
    assert effective_append is False
    # Confirm: a subsequent helper call with the precedence-resolved flag
    # overwrites.
    n = _write_trades_atomic([_row("2026-05-15")], str(out),
                              append=effective_append)
    assert n == 1
    df = pd.read_parquet(out)
    assert df["friday_date_ist"].tolist() == ["2026-05-15"]

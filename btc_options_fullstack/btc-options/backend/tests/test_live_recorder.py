"""Tests for live_recorder: symbol parsing, bar aggregation, parquet append/dedupe."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services import live_recorder as lr


# ── Symbol parsing ────────────────────────────────────────────────────────────

def test_parse_option_mark():
    r = lr.parse_option_symbol("MARK:C-BTC-100000-080525")
    assert r == {"kind": "mark", "side": "CE", "strike": 100000, "expiry": "2025-05-08"}

def test_parse_option_oi():
    r = lr.parse_option_symbol("OI:P-BTC-94500-260925")
    assert r == {"kind": "oi", "side": "PE", "strike": 94500, "expiry": "2025-09-26"}

def test_parse_option_garbage():
    assert lr.parse_option_symbol("MARK:BTCUSD") is None
    assert lr.parse_option_symbol("garbage") is None
    assert lr.parse_option_symbol("LTP:C-BTC-100000-080525") is None  # only MARK/OI

def test_parse_spot():
    assert lr.parse_spot_symbol("MARK:BTCUSD") == "mark"
    assert lr.parse_spot_symbol("OI:BTCUSD") == "oi"
    assert lr.parse_spot_symbol("MARK:C-BTC-100000-080525") is None

def test_round_atm():
    # Python's banker's rounding: .5 rounds to even. Skip the .5 boundary in
    # tests; ATM is unambiguous either side of it.
    assert lr.round_atm(100049) == 100000
    assert lr.round_atm(100051) == 100100
    assert lr.round_atm(99949)  == 99900
    assert lr.round_atm(99951)  == 100000


# ── Writer: append + dedupe ──────────────────────────────────────────────────

def test_writer_option_append_and_dedupe(monkeypatch, tmp_path):
    monkeypatch.setattr(lr, "DATA_LIVE_BASE", tmp_path)
    monkeypatch.setattr(lr, "OPTIONS_BASE", tmp_path / "options")
    monkeypatch.setattr(lr, "SPOT_PARQUET", tmp_path / "spot" / "BTCUSD_1min.parquet")

    w = lr.LiveWriter()
    # Bar 1: mark + oi for the same minute → merged into one row
    w.queue_option_bar("2025-05-08", 100000, "CE", "mark", 1714657200, 0.5, 0.6, 0.4, 0.5)
    w.queue_option_bar("2025-05-08", 100000, "CE", "oi",   1714657200, 100, 110, 95, 105)
    # Bar 2: another minute, mark only
    w.queue_option_bar("2025-05-08", 100000, "CE", "mark", 1714657260, 0.55, 0.6, 0.5, 0.55)

    spot_snap = {}
    opt_snap = {k: dict(v) for k, v in w._opt_buf.items()}
    w._opt_buf.clear()
    w._do_flush(spot_snap, opt_snap)

    path = tmp_path / "options/expiry=2025-05-08/strike=100000/CE.parquet"
    assert path.exists()
    t = pq.ParquetFile(str(path)).read()
    assert sorted(t.column("timestamp_unix").to_pylist()) == [1714657200, 1714657260]
    rows = t.to_pylist()
    by_ts = {r["timestamp_unix"]: r for r in rows}
    # First row should have both mark and oi populated.
    assert by_ts[1714657200]["mark_open"] == 0.5
    assert by_ts[1714657200]["oi_close"]  == 105
    # Second row mark only — oi columns null.
    assert by_ts[1714657260]["mark_open"] == 0.55
    assert by_ts[1714657260]["oi_open"]   is None


def test_writer_dedupe_keeps_last(monkeypatch, tmp_path):
    monkeypatch.setattr(lr, "DATA_LIVE_BASE", tmp_path)
    monkeypatch.setattr(lr, "OPTIONS_BASE", tmp_path / "options")
    monkeypatch.setattr(lr, "SPOT_PARQUET", tmp_path / "spot" / "BTCUSD_1min.parquet")

    w = lr.LiveWriter()
    # Two writes at the same timestamp_unix in two separate flush cycles.
    w.queue_option_bar("2025-05-08", 100000, "CE", "mark", 1714657200, 0.5, 0.5, 0.5, 0.5)
    w._do_flush({}, {k: dict(v) for k, v in w._opt_buf.items()})
    w._opt_buf.clear()

    w.queue_option_bar("2025-05-08", 100000, "CE", "mark", 1714657200, 0.99, 0.99, 0.99, 0.99)
    w._do_flush({}, {k: dict(v) for k, v in w._opt_buf.items()})
    w._opt_buf.clear()

    path = tmp_path / "options/expiry=2025-05-08/strike=100000/CE.parquet"
    t = pq.ParquetFile(str(path)).read()
    assert len(t) == 1
    assert t.column("mark_close")[0].as_py() == 0.99


# ── Recorder bar-close logic ─────────────────────────────────────────────────

def test_recorder_minute_roll_closes_previous(monkeypatch, tmp_path):
    monkeypatch.setattr(lr, "DATA_LIVE_BASE", tmp_path)
    monkeypatch.setattr(lr, "OPTIONS_BASE", tmp_path / "options")
    monkeypatch.setattr(lr, "SPOT_PARQUET", tmp_path / "spot" / "BTCUSD_1min.parquet")

    r = lr.LiveRecorder()
    sym = "MARK:C-BTC-100000-080525"
    # First message at minute T
    r._handle_message({
        "type": "candlestick_1m", "symbol": sym,
        "candle_start_time": 1714657200_000_000,
        "open": "0.5", "high": "0.6", "low": "0.4", "close": "0.55", "volume": "0",
    })
    assert r._bars_closed == 0  # only one bar seen — not closed yet

    # Newer minute → previous bar closes
    r._handle_message({
        "type": "candlestick_1m", "symbol": sym,
        "candle_start_time": 1714657260_000_000,
        "open": "0.55", "high": "0.6", "low": "0.5", "close": "0.58", "volume": "0",
    })
    assert r._bars_closed == 1
    # Pending option write present
    paths = list(r.writer._opt_buf.keys())
    assert len(paths) == 1
    assert "expiry=2025-05-08" in paths[0]
    assert "strike=100000" in paths[0]
    assert "CE.parquet" in paths[0]


def test_recorder_ignores_non_candle_msgs():
    r = lr.LiveRecorder()
    r._handle_message({"type": "v2/ticker", "symbol": "MARK:C-BTC-100000-080525"})
    assert r._bars_closed == 0
    assert not r._inflight


def test_recorder_handles_spot():
    r = lr.LiveRecorder()
    sym = "MARK:BTCUSD"
    r._handle_message({
        "type": "candlestick_1m", "symbol": sym,
        "candle_start_time": 1714657200_000_000,
        "open": "100000", "high": "100200", "low": "99900", "close": "100100", "volume": "0",
    })
    r._handle_message({
        "type": "candlestick_1m", "symbol": sym,
        "candle_start_time": 1714657260_000_000,
        "open": "100100", "high": "100100", "low": "100000", "close": "100050", "volume": "0",
    })
    assert r._bars_closed == 1
    spot_pending = sum(len(v) for v in r.writer._spot_buf.values())
    assert spot_pending == 1

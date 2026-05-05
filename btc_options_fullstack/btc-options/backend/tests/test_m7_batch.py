"""Tests for M7 batch backtester helpers — no parquet dependencies."""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.analytics.m7_batch_backtester import (
    ENTRY_HOURS_IST,
    IV_BANDS,
    TARGET_DELTAS,
    entry_ts_for_friday,
    fridays_in_range,
    iv_band_label,
    make_trade_id,
    pick_strikes,
    sat_exit_ts_for_friday,
)


# ── Time helpers ──────────────────────────────────────────────────────────────

def test_entry_ts_evening_hours_same_utc_date():
    """IST 21,22,23 → same UTC date as friday at hour-5:30."""
    f = date(2024, 1, 5)
    assert datetime.fromtimestamp(entry_ts_for_friday(f, 21), tz=timezone.utc) \
        == datetime(2024, 1, 5, 15, 30, tzinfo=timezone.utc)
    assert datetime.fromtimestamp(entry_ts_for_friday(f, 22), tz=timezone.utc) \
        == datetime(2024, 1, 5, 16, 30, tzinfo=timezone.utc)
    assert datetime.fromtimestamp(entry_ts_for_friday(f, 23), tz=timezone.utc) \
        == datetime(2024, 1, 5, 17, 30, tzinfo=timezone.utc)


def test_entry_ts_early_morning_hours_same_utc_date():
    """IST 0,1,2,3 (Sat morning IST) → same UTC date as friday at hour+18:30."""
    f = date(2024, 1, 5)
    assert datetime.fromtimestamp(entry_ts_for_friday(f, 0), tz=timezone.utc) \
        == datetime(2024, 1, 5, 18, 30, tzinfo=timezone.utc)
    assert datetime.fromtimestamp(entry_ts_for_friday(f, 3), tz=timezone.utc) \
        == datetime(2024, 1, 5, 21, 30, tzinfo=timezone.utc)


def test_sat_exit_is_noon_utc_next_day():
    """Sat 17:30 IST = Sat 12:00 UTC."""
    f = date(2024, 1, 5)
    assert datetime.fromtimestamp(sat_exit_ts_for_friday(f), tz=timezone.utc) \
        == datetime(2024, 1, 6, 12, 0, tzinfo=timezone.utc)


def test_fridays_in_range_returns_only_fridays():
    """Range covering multiple weeks returns only Fridays."""
    start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2024, 1, 31, tzinfo=timezone.utc).timestamp())
    fris = fridays_in_range(start, end)
    assert fris == [date(2024, 1, 5), date(2024, 1, 12),
                    date(2024, 1, 19), date(2024, 1, 26)]
    for f in fris:
        assert f.weekday() == 4


# ── IV band labels ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("iv,expected", [
    (0.0, "0-20"), (15.0, "0-20"), (19.99, "0-20"),
    (20.0, "20-30"), (29.5, "20-30"),
    (50.0, "50-60"), (95.0, "90-100"),
    (100.0, "100+"), (150.0, "100+"),
])
def test_iv_band_label_boundaries(iv: float, expected: str):
    assert iv_band_label(iv) == expected


def test_iv_band_label_handles_nan():
    assert iv_band_label(float("nan")) == "nan"
    assert iv_band_label(None) == "nan"


# ── Trade ID hash ─────────────────────────────────────────────────────────────

def test_trade_id_stable_and_unique():
    a = make_trade_id(date(2024, 1, 5), 21, "2024-01-12", 0.30)
    b = make_trade_id(date(2024, 1, 5), 21, "2024-01-12", 0.30)
    c = make_trade_id(date(2024, 1, 5), 22, "2024-01-12", 0.30)
    d = make_trade_id(date(2024, 1, 5), 21, "2024-01-12", 0.50)
    assert a == b
    assert a != c
    assert a != d
    assert all(0 <= x < (1 << 63) for x in (a, b, c, d))


# ── Strike picker ─────────────────────────────────────────────────────────────

def _fake_chain(strikes: list[int], spot: float, iv: float = 0.5) -> pd.DataFrame:
    """Construct a synthetic chain DataFrame with BS-priced marks for testing."""
    from app.core.greeks import compute_greeks
    rows = []
    for k in strikes:
        T = 7 / 365.0
        cg = compute_greeks(spot, k, T, 0.0, iv, "call")
        pg = compute_greeks(spot, k, T, 0.0, iv, "put")
        rows.append({"timestamp_unix": 0, "strike": k, "opt_type": "CE",
                     "mark_close": cg.price_bs, "oi_close": 100.0})
        rows.append({"timestamp_unix": 0, "strike": k, "opt_type": "PE",
                     "mark_close": pg.price_bs, "oi_close": 100.0})
    return pd.DataFrame(rows)


def test_pick_strikes_straddle_uses_same_strike_for_both_legs():
    """delta_target=0.50 should pick the strike closest to spot for both legs."""
    spot = 50000.0
    chain = _fake_chain([48000, 49000, 50000, 51000, 52000], spot)
    res = pick_strikes(chain, spot, 7 / 365.0, 0.50)
    assert res is not None
    assert res["call_strike"] == res["put_strike"] == 50000


def test_pick_strikes_closest_from_below_for_low_delta():
    """For target 0.20, picker should pick the highest |delta| ≤ 0.20."""
    spot = 50000.0
    chain = _fake_chain([45000, 47000, 49000, 50000, 51000, 53000, 55000], spot)
    res = pick_strikes(chain, spot, 7 / 365.0, 0.20)
    assert res is not None
    assert abs(res["call_delta"]) <= 0.20
    assert abs(res["put_delta"]) <= 0.20


def test_pick_strikes_returns_none_when_no_qualifying():
    """If no strike has |delta| ≤ target, return None (skip the trade)."""
    spot = 50000.0
    # Only strikes near ATM → all deltas will be > 0.10
    chain = _fake_chain([49500, 50000, 50500], spot, iv=0.5)
    res = pick_strikes(chain, spot, 7 / 365.0, 0.05)
    # All strikes here will have |delta| > 0.05
    assert res is None or (abs(res["call_delta"]) <= 0.05 and abs(res["put_delta"]) <= 0.05)


def test_pick_strikes_returns_none_on_empty_chain():
    res = pick_strikes(pd.DataFrame(), 50000.0, 7 / 365.0, 0.30)
    assert res is None


# ── Constants sanity ──────────────────────────────────────────────────────────

def test_target_deltas_include_0_50_for_straddle():
    assert 0.50 in TARGET_DELTAS


def test_entry_hours_cover_fri_evening_to_sat_morning():
    assert ENTRY_HOURS_IST == (21, 22, 23, 0, 1, 2, 3)


def test_iv_bands_cover_zero_to_one_hundred_plus():
    band_lows = [b[0] for b in IV_BANDS]
    assert 0 in band_lows
    assert 100 in band_lows

"""Tests for app.analytics.m7_pivot_profile.

Cover three synthetic curves and the aggregator. No filesystem touches —
we build numpy arrays and feed `segment_pivots` directly.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import pytest

from app.analytics.m7_pivot_profile import (
    SegmentPivot, _agg_segment, _circular_mean_mod, _fmt_minute_of_day,
    aggregate_pivot_profile, segment_pivots,
)


IST = timezone(timedelta(hours=5, minutes=30))


def _ist_datetime_to_unix(year: int, month: int, day: int,
                           hour: int, minute: int) -> int:
    """Build an IST wall-clock datetime and return its unix timestamp."""
    dt = datetime(year, month, day, hour, minute, tzinfo=IST)
    return int(dt.timestamp())


def _build_path_for_entry(entry_ist: datetime, n_minutes: int,
                          mtm_fn) -> tuple[np.ndarray, np.ndarray, int]:
    """Construct a synthetic per-minute path starting at `entry_ist` IST.

    mtm_fn(minute_offset) -> float. Returns (ts_unix, mtm_array, entry_unix).
    """
    entry_unix = int(entry_ist.timestamp())
    ts = np.arange(n_minutes, dtype=np.int64) * 60 + entry_unix
    mtm = np.array([float(mtm_fn(i)) for i in range(n_minutes)],
                   dtype=np.float64)
    return ts, mtm, entry_unix


def test_full_24h_path_known_peaks_troughs():
    """A 19h synthetic trade (Fri 23:00 IST → Sat 17:30 IST = 1050 min) with
    one obvious peak and trough per segment. Each segment puts its peak at a
    known IST time and trough at another known IST time; we assert the
    detection matches.
    """
    entry_ist = datetime(2024, 1, 5, 23, 0, tzinfo=IST)
    # Trade-local segment ranges (entry_mod = 23*60 = 1380):
    #   Seg1 entry→05:00 IST → trade-local 0..360
    #   Seg2 05:00→08:00 IST → 360..540
    #   Seg3 08:00→12:00 IST → 540..780
    #   Seg4 12:00→15:00 IST → 780..960
    #   Seg5 15:00→17:30 IST → 960..1110
    # Place peaks at minute-offsets within each segment, troughs at others.
    PEAK_OFFSETS = {0: 100, 1: 400, 2: 600, 3: 850, 4: 1000}
    TROUGH_OFFSETS = {0: 200, 1: 500, 2: 700, 3: 900, 4: 1080}

    def mtm_at(min_off: int) -> float:
        # Default baseline.
        v = 0.0
        for seg, peak in PEAK_OFFSETS.items():
            if min_off == peak:
                return 10.0 + seg * 5  # Seg1=10, Seg2=15, ..., Seg5=30
        for seg, trough in TROUGH_OFFSETS.items():
            if min_off == trough:
                return -2.0 - seg  # Seg1=-2, Seg2=-3, ..., Seg5=-6
        return v

    ts, mtm, entry_unix = _build_path_for_entry(
        entry_ist, 1110, mtm_at)
    pivots = segment_pivots(ts, mtm, entry_unix)

    assert all(p is not None for p in pivots)
    for seg_idx, p in enumerate(pivots):
        assert p.peak_minute_offset == PEAK_OFFSETS[seg_idx], \
            f"Seg {seg_idx+1} peak offset"
        assert p.peak_mtm == pytest.approx(10.0 + seg_idx * 5)
        assert p.trough_minute_offset == TROUGH_OFFSETS[seg_idx], \
            f"Seg {seg_idx+1} trough offset"
        assert p.trough_mtm == pytest.approx(-2.0 - seg_idx)
        # dd_usd positive
        assert p.dd_usd == pytest.approx(p.peak_mtm - p.trough_mtm)
        # dd_pct = dd_usd / peak * 100; peak > 0 so always defined
        assert p.dd_pct_from_peak == pytest.approx(
            (p.dd_usd / p.peak_mtm) * 100)


def test_short_trade_only_first_two_segments():
    """Trade exits at 09:00 IST → Seg1 + Seg2 + partial Seg3 populated;
    Seg4 and Seg5 = None.
    """
    entry_ist = datetime(2024, 1, 5, 23, 0, tzinfo=IST)
    # 10 hours = 600 minutes (until 09:00 IST). At minute 540 we're in Seg3
    # (08:00 IST). So Seg1, Seg2, Seg3 should be populated; Seg4 and Seg5 not.
    def mtm_at(min_off: int) -> float:
        if min_off == 100:
            return 5.0
        if min_off == 200:
            return -1.0
        if min_off == 400:
            return 8.0
        if min_off == 500:
            return -2.0
        if min_off == 580:
            return 12.0
        return 0.0
    ts, mtm, entry_unix = _build_path_for_entry(
        entry_ist, 600, mtm_at)
    pivots = segment_pivots(ts, mtm, entry_unix)

    assert pivots[0] is not None and pivots[0].peak_mtm == 5.0
    assert pivots[1] is not None and pivots[1].peak_mtm == 8.0
    assert pivots[2] is not None and pivots[2].peak_mtm == 12.0
    assert pivots[3] is None
    assert pivots[4] is None


def test_early_entry_at_03_ist():
    """Entry at 03 IST → Seg1 covers only 03:00→05:00 (2h, trade-local 0..120).
    Seg2..Seg5 are at trade-local 120..900.
    """
    entry_ist = datetime(2024, 1, 6, 3, 0, tzinfo=IST)
    # 14.5h = 870 minutes (until 17:30 IST).
    def mtm_at(min_off: int) -> float:
        if min_off == 60:
            return 3.0  # Seg1 peak
        if min_off == 100:
            return -1.0  # Seg1 trough
        if min_off == 200:
            return 7.0  # Seg2 peak
        if min_off == 250:
            return -3.0  # Seg2 trough
        return 0.0
    ts, mtm, entry_unix = _build_path_for_entry(
        entry_ist, 870, mtm_at)
    pivots = segment_pivots(ts, mtm, entry_unix)
    # Seg1: trade-local 0..120 (entry 03:00 → 05:00 IST)
    assert pivots[0] is not None
    assert pivots[0].n_minutes == 120
    assert pivots[0].peak_minute_offset == 60
    assert pivots[0].peak_mtm == pytest.approx(3.0)
    # Seg2 should pick up the peak at 200
    assert pivots[1] is not None
    assert pivots[1].peak_minute_offset == 200
    assert pivots[1].peak_mtm == pytest.approx(7.0)


def test_dd_pct_handles_nonpositive_peak():
    """A segment whose peak is ≤ $0 must report dd_pct_from_peak as None."""
    entry_ist = datetime(2024, 1, 5, 23, 0, tzinfo=IST)
    # Trade that's always underwater in Seg1.
    def mtm_at(min_off: int) -> float:
        if min_off < 360:
            return -5.0 - (min_off % 7)  # always negative
        return 0.0
    ts, mtm, entry_unix = _build_path_for_entry(
        entry_ist, 400, mtm_at)
    pivots = segment_pivots(ts, mtm, entry_unix)
    seg1 = pivots[0]
    assert seg1 is not None
    assert seg1.peak_mtm < 0
    assert seg1.dd_pct_from_peak is None
    assert seg1.dd_usd > 0  # peak − trough still computable


def test_aggregator_handles_none_segments():
    """Aggregator should exclude None segments from per-segment averages and
    track n_trades_for_dd_pct vs n_trades correctly.
    """
    # Build a synthetic trades dataframe + on-the-fly path lookup. We bypass
    # the parquet reader by monkey-patching the loop: construct fake
    # `band_pivots` dict and call _agg_segment directly to verify the
    # aggregator-level math.
    p1 = SegmentPivot(seg="Seg1", n_minutes=300,
                       peak_mtm=10.0, peak_minute_offset=100,
                       peak_ts_ist_minute_of_day=120,
                       trough_mtm=-2.0, trough_minute_offset=200,
                       trough_ts_ist_minute_of_day=240,
                       dd_usd=12.0, dd_pct_from_peak=120.0)
    p2 = SegmentPivot(seg="Seg1", n_minutes=300,
                       peak_mtm=20.0, peak_minute_offset=80,
                       peak_ts_ist_minute_of_day=100,
                       trough_mtm=0.0, trough_minute_offset=250,
                       trough_ts_ist_minute_of_day=270,
                       dd_usd=20.0, dd_pct_from_peak=100.0)
    p3 = SegmentPivot(seg="Seg1", n_minutes=300,
                       peak_mtm=-1.0, peak_minute_offset=150,
                       peak_ts_ist_minute_of_day=170,
                       trough_mtm=-5.0, trough_minute_offset=210,
                       trough_ts_ist_minute_of_day=230,
                       dd_usd=4.0, dd_pct_from_peak=None)
    agg = _agg_segment([p1, p2, p3])
    assert agg["n_trades"] == 3
    assert agg["n_trades_for_dd_pct"] == 2
    assert agg["avg_peak_mtm_usd"] == pytest.approx((10 + 20 - 1) / 3)
    assert agg["avg_dd_usd"] == pytest.approx((12 + 20 + 4) / 3)
    # Only p1, p2 contribute to the % avg (p3 had peak ≤ 0)
    assert agg["avg_dd_pct_from_peak"] == pytest.approx((120 + 100) / 2)


def test_fmt_minute_of_day():
    assert _fmt_minute_of_day(0) == "00:00"
    assert _fmt_minute_of_day(330) == "05:30"
    assert _fmt_minute_of_day(1050) == "17:30"
    assert _fmt_minute_of_day(1439) == "23:59"
    assert _fmt_minute_of_day(None) == "—"
    assert _fmt_minute_of_day(float("nan")) == "—"


def test_circular_mean_handles_midnight_wrap():
    """Mods straddling the IST-midnight wrap must average circularly so
    e.g. (23:30 IST=1410, 00:30 IST=30) averages to 00:00 IST = 0, not
    720 (12:00 IST) which a naive arithmetic mean would yield.
    """
    # 1410 (23:30 IST) + 30 (00:30 IST) should average to 00:00 IST = 0
    avg = _circular_mean_mod(np.array([1410.0, 30.0], dtype=np.float64))
    assert avg == pytest.approx(0.0, abs=0.5)

    # 1380 (23:00) + 100 (01:40) → mid Fri 23:00 + 100min after midnight is
    # an axis-anchored average of -60 + 100 = 20 → 00:20 IST = 20
    avg = _circular_mean_mod(np.array([1380.0, 100.0], dtype=np.float64))
    assert avg == pytest.approx(20.0, abs=0.5)

    # All within Sat morning, no wrap.
    avg = _circular_mean_mod(np.array([200.0, 400.0], dtype=np.float64))
    assert avg == pytest.approx(300.0, abs=0.5)


def test_aggregate_with_empty_trades_returns_empty_response():
    empty = pd.DataFrame(columns=[
        "trade_id", "entry_ts_utc", "entry_hour_ist",
        "entry_atm_iv_band", "friday_date_ist"])
    out = aggregate_pivot_profile(empty, "/dev/null", [23, 0])
    assert out["by_band"] == {}
    assert out["params"]["n_after_filter"] == 0

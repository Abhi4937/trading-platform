"""Unit tests for `pick_strikes_with_match` in m_month_batch_backtester.

Covers the strike-matching retry policy (per-leg tolerance + leg-gap +
walk_end bound), straddle short-circuit, and legacy single-attempt path.
The inner `pick_strikes` call is mocked so each test controls exactly what
shape comes back per attempt timestamp.
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import patch

import pandas as pd
import pytest

from app.analytics.m_month_batch_backtester import pick_strikes_with_match


# ── Fixtures ─────────────────────────────────────────────────────────────────

REQUESTED_TS = 1_000_000
EXPIRY_TS = REQUESTED_TS + 7 * 86400  # ~7 days out
WALK_END_DEFAULT = REQUESTED_TS + 86400  # 1-day window unless overridden


def _spot_at(ts_list: list[int], spot: float = 50_000.0) -> pd.DataFrame:
    """Build a spot_series DataFrame indexed by unix-ts with `mark_close`.

    `pick_strikes_with_match` uses both `ts in spot_series.index` membership
    checks and `.searchsorted` fallback; both are satisfied by a sorted int
    index of timestamps.
    """
    df = pd.DataFrame({"mark_close": [spot] * len(ts_list)}, index=ts_list)
    df.index.name = "timestamp_unix"
    df = df.sort_index()
    return df


def _chain_at(ts_list: list[int]) -> pd.DataFrame:
    """Minimal chain frame with `timestamp_unix` rows for every snap timestamp.

    `pick_strikes_with_match` checks `snap_ts = ts - ts % 300` and filters
    `chain[chain['timestamp_unix'] == snap_ts]`. We only need the row to
    be non-empty for the mocked `pick_strikes` to be invoked.
    """
    return pd.DataFrame({
        "timestamp_unix": ts_list,
        "strike": [50_000] * len(ts_list),
        "opt_type": ["CE"] * len(ts_list),
        "mark_close": [100.0] * len(ts_list),
    })


def _good_picks(
    call_delta: float = 0.10,
    put_delta: float = -0.10,
    call_strike: int = 75_000,
    put_strike: int = 73_000,
    call_mark: float = 100.0,
    put_mark: float = 100.0,
    call_iv: float = 0.5,
    put_iv: float = 0.5,
) -> dict:
    return {
        "call_strike": call_strike,
        "put_strike": put_strike,
        "call_mark": call_mark,
        "put_mark": put_mark,
        "call_iv": call_iv,
        "put_iv": put_iv,
        "call_delta": call_delta,
        "put_delta": put_delta,
    }


# Build snap timestamps covering [REQUESTED_TS, REQUESTED_TS + 60min] at 5-min steps,
# rounded down to the 5m chain boundary (which REQUESTED_TS=1_000_000 already is:
# 1_000_000 % 300 == 100, so the snap boundary is 999_900). Add both to be safe.
_SNAP_TS_LIST = sorted({
    (REQUESTED_TS + off * 60) - ((REQUESTED_TS + off * 60) % 300)
    for off in range(0, 65, 5)
} | {
    REQUESTED_TS + off * 60
    for off in range(0, 65, 5)
})


@pytest.fixture
def chain_5m() -> pd.DataFrame:
    return _chain_at(_SNAP_TS_LIST)


@pytest.fixture
def spot_series() -> pd.DataFrame:
    return _spot_at(_SNAP_TS_LIST)


# ── T1 — Trivial match at requested ts ────────────────────────────────────────

def test_t1_trivial_match_at_requested_ts(chain_5m, spot_series):
    with patch(
        "app.analytics.m_month_batch_backtester.pick_strikes",
        return_value=_good_picks(call_delta=0.10, put_delta=-0.10),
    ) as mock_pick:
        result = pick_strikes_with_match(
            chain_5m, spot_series, EXPIRY_TS, target_delta=0.10,
            requested_entry_ts=REQUESTED_TS, walk_end_ts=WALK_END_DEFAULT,
        )

    assert result is not None
    assert result["wait_minutes"] == 0
    assert result["match_quality"] < 0.01
    assert result["skipped_reason"] is None
    assert result["actual_entry_ts"] == REQUESTED_TS
    # Only one attempt needed since first match satisfies tolerance.
    assert mock_pick.call_count == 1


# ── T2 — Retry succeeds 5 min later ──────────────────────────────────────────

def test_t2_match_on_first_retry(chain_5m, spot_series):
    bad = _good_picks(call_delta=0.18, put_delta=-0.07)   # gap 0.11 + per-leg miss
    good = _good_picks(call_delta=0.10, put_delta=-0.10)

    def side_effect(_chain, _spot, _T, _td):
        # First call returns bad picks, all subsequent calls return good picks.
        if mock_pick.call_count == 1:
            return bad
        return good

    with patch(
        "app.analytics.m_month_batch_backtester.pick_strikes",
        side_effect=side_effect,
    ) as mock_pick:
        result = pick_strikes_with_match(
            chain_5m, spot_series, EXPIRY_TS, target_delta=0.10,
            requested_entry_ts=REQUESTED_TS, walk_end_ts=WALK_END_DEFAULT,
        )

    assert result is not None
    assert result["wait_minutes"] == 5
    assert result["actual_entry_ts"] == REQUESTED_TS + 300
    assert result["skipped_reason"] is None
    assert mock_pick.call_count == 2


# ── T3 — No match in 60-min window → best-effort + skipped_reason ────────────

def test_t3_unmatched_returns_best_with_skipped_reason(chain_5m, spot_series):
    bad = _good_picks(call_delta=0.05, put_delta=-0.20)  # gap 0.15, never tightens

    with patch(
        "app.analytics.m_month_batch_backtester.pick_strikes",
        return_value=bad,
    ) as mock_pick:
        result = pick_strikes_with_match(
            chain_5m, spot_series, EXPIRY_TS, target_delta=0.10,
            requested_entry_ts=REQUESTED_TS, walk_end_ts=WALK_END_DEFAULT,
        )

    assert result is not None
    assert result["skipped_reason"] == "strike_unmatched"
    assert result["wait_minutes"] <= 60
    # tolerance is 0.025 per-leg / 0.020 leg-gap; mismatch is far above both
    assert result["match_quality"] > 0.025
    # 60min / 5min interval + 1 = 13 attempts
    assert mock_pick.call_count == 13


# ── T4 — Straddle short-circuit ──────────────────────────────────────────────

def test_t4_straddle_short_circuit(chain_5m, spot_series):
    straddle = _good_picks(
        call_strike=80_000, put_strike=80_000,
        call_delta=0.5, put_delta=-0.5,
    )

    with patch(
        "app.analytics.m_month_batch_backtester.pick_strikes",
        return_value=straddle,
    ) as mock_pick:
        result = pick_strikes_with_match(
            chain_5m, spot_series, EXPIRY_TS, target_delta=0.50,
            requested_entry_ts=REQUESTED_TS, walk_end_ts=WALK_END_DEFAULT,
        )

    assert result is not None
    assert result["wait_minutes"] == 0
    assert result["skipped_reason"] is None
    # Straddle path returns hard-coded match_quality=0.0 with no retry loop.
    assert result["match_quality"] == 0.0
    assert mock_pick.call_count == 1


# ── T5 — match_mode=False (legacy single attempt) ────────────────────────────

def test_t5_match_mode_false_uses_requested_ts_only(chain_5m, spot_series):
    bad = _good_picks(call_delta=0.18, put_delta=-0.07)
    good = _good_picks(call_delta=0.10, put_delta=-0.10)

    def side_effect(_chain, _spot, _T, _td):
        if mock_pick.call_count == 1:
            return bad
        return good  # would match if retry were attempted — but it shouldn't be

    with patch(
        "app.analytics.m_month_batch_backtester.pick_strikes",
        side_effect=side_effect,
    ) as mock_pick:
        result = pick_strikes_with_match(
            chain_5m, spot_series, EXPIRY_TS, target_delta=0.10,
            requested_entry_ts=REQUESTED_TS, walk_end_ts=WALK_END_DEFAULT,
            match_mode=False,
        )

    assert result is not None
    assert result["wait_minutes"] == 0
    assert result["skipped_reason"] is None
    assert result["actual_entry_ts"] == REQUESTED_TS
    # mismatch was bad: call 0.18 vs target 0.10 (diff 0.08), put 0.07 vs 0.10
    # (diff 0.03), gap 0.11 → quality = max(0.08, 0.03, 0.11) = 0.11
    assert result["match_quality"] == pytest.approx(0.11, abs=1e-9)
    # No retry — exactly one call.
    assert mock_pick.call_count == 1


# ── T6 — walk_end_ts bound prevents reaching a later good attempt ────────────

def test_t6_walk_end_bound_stops_retry_loop(chain_5m, spot_series):
    bad = _good_picks(call_delta=0.18, put_delta=-0.07)
    good = _good_picks(call_delta=0.10, put_delta=-0.10)

    def side_effect(_chain, _spot, _T, _td):
        # Mismatches on attempts 1,2,3 (offsets 0/5/10 min); success at attempt 4
        # (offset 15 min, ts=REQUESTED_TS + 900) — but walk_end caps at +600s.
        if mock_pick.call_count <= 3:
            return bad
        return good

    walk_end = REQUESTED_TS + 600  # 10 min — attempt-4 ts would be at +900s

    with patch(
        "app.analytics.m_month_batch_backtester.pick_strikes",
        side_effect=side_effect,
    ) as mock_pick:
        result = pick_strikes_with_match(
            chain_5m, spot_series, EXPIRY_TS, target_delta=0.10,
            requested_entry_ts=REQUESTED_TS, walk_end_ts=walk_end,
        )

    assert result is not None
    assert result["skipped_reason"] == "strike_unmatched"
    # Loop breaks once ts >= walk_end_ts (offset 10 → ts == walk_end exactly,
    # break condition is `ts >= walk_end_ts`). So at most attempts at offsets
    # 0 and 5 fire — 2 attempts.
    assert mock_pick.call_count == 2
    # Did NOT pop into the good branch.
    assert result["match_quality"] > 0.025


# ── T7 — Empty chain → return None ───────────────────────────────────────────

def test_t7_empty_chain_returns_none(spot_series):
    empty_chain = pd.DataFrame(columns=["timestamp_unix", "strike", "opt_type", "mark_close"])

    with patch(
        "app.analytics.m_month_batch_backtester.pick_strikes",
        return_value=_good_picks(),
    ) as mock_pick:
        result = pick_strikes_with_match(
            empty_chain, spot_series, EXPIRY_TS, target_delta=0.10,
            requested_entry_ts=REQUESTED_TS, walk_end_ts=WALK_END_DEFAULT,
        )

    assert result is None
    # `_attempt` short-circuits before pick_strikes when the snap slice is empty,
    # so the mock is never called.
    assert mock_pick.call_count == 0

"""Unit tests for live_signal_compute.scan_live_candidates.

Synthetic ticker_store + monkey-patched chain/context/calibration → verify
candidate enumeration, ranking, hard-filter flags, and v2 field passthrough.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.models import (
    ChainRow, ExpiryInfo, ExpiryListResponse, OptionChainResponse, OptionLeg,
)
from app.services import live_signal_compute as lsc


# ── Helpers ──────────────────────────────────────────────────────────────────

def _leg(strike: float, opt_type: str, mark: float, delta: float,
         iv: float = 0.6, expiry: date = date(2026, 5, 10)) -> OptionLeg:
    """Build a synthetic OptionLeg with just enough fields to flow through
    the scan path. compute_greeks is bypassed because we mock the analytics
    function — the leg's stored greeks are read directly."""
    return OptionLeg(
        strike=strike, expiry=expiry, option_type=opt_type,
        symbol=f"{'C' if opt_type == 'call' else 'P'}-BTC-{int(strike)}-100526",
        last_price=mark, bid=mark - 0.05, ask=mark + 0.05, mid=mark,
        iv=iv, iv_pct=iv * 100, delta=delta,
        gamma=0.00002, theta=-1.5, vega=20.0, rho=1.0,
        price_bs=mark, underlying_price=100_000.0, days_to_expiry=7.0,
    )


def _make_chain(expiry: date, spot: float = 100_000.0) -> OptionChainResponse:
    """Build a synthetic 5-strike chain spanning ~ATM ± wings.
    Deltas are seeded to make closest-delta picking deterministic:
      Strike  90k  95k  100k  105k  110k
      C abs δ 0.85 0.55 0.50 0.30 0.10
      P abs δ 0.10 0.30 0.50 0.55 0.85
    """
    rows = []
    seed = [
        (90_000.0,  0.85, 1500.0,   -0.10,  100.0),
        (95_000.0,  0.55,  800.0,   -0.30,  300.0),
        (100_000.0, 0.50,  600.0,   -0.50,  600.0),
        (105_000.0, 0.30,  300.0,   -0.55,  800.0),
        (110_000.0, 0.10,  100.0,   -0.85, 1500.0),
    ]
    for strike, c_delta, c_mark, p_delta, p_mark in seed:
        rows.append(ChainRow(
            strike=strike,
            call=_leg(strike, "call", c_mark, c_delta, expiry=expiry),
            put=_leg(strike, "put",  p_mark, p_delta, expiry=expiry),
            is_atm=(strike == 100_000.0),
        ))
    return OptionChainResponse(
        expiry=expiry, underlying="BTC", spot_price=spot,
        atm_strike=100_000.0, days_to_expiry=7.0,
        atm_iv_call=0.60, atm_iv_put=0.60, chain=rows,
        fetched_at=datetime.now(timezone.utc),
    )


def _make_expiry_resp(expiries: list[date]) -> ExpiryListResponse:
    return ExpiryListResponse(
        underlying="BTC", spot_price=100_000.0,
        expiries=[ExpiryInfo(date=e, label=e.isoformat(),
                              days=(e - date.today()).days) for e in expiries],
    )


def _patch_common(monkeypatch, *, expiries: list[date], spot: float = 100_000.0,
                  ctx: dict | None = None,
                  calib: dict | None = None,
                  analytics_overrides: dict | None = None,
                  has_data: bool = True):
    """Patch all external touchpoints with simple synthetic returns."""
    monkeypatch.setattr(lsc.ticker_store, "has_data", lambda: has_data)
    monkeypatch.setattr(lsc.ticker_store, "get_spot",  lambda: spot)

    async def _exp():
        return _make_expiry_resp(expiries)
    monkeypatch.setattr(lsc, "get_expiries", _exp)

    async def _chain(exp):
        return _make_chain(exp, spot=spot)
    monkeypatch.setattr(lsc, "get_option_chain_from_store", _chain)

    monkeypatch.setattr(lsc, "get_market_context", lambda ts: (ctx or {}))

    # compute_trade_analytics: deterministic score keyed by target_delta to
    # make ranking assertions unambiguous. Higher delta → higher score.
    def _analytics(legs, *, spot, dte, entry_ts, expiry_ts):
        target = (abs(legs[0].delta) + abs(legs[1].delta)) / 2.0
        out = {
            "quality_score": 30.0 + 100.0 * target,
            "quality_source": "calibrated_v2",
            "size_band": "standard",
            "pattern": "A",
            "fair_credit_at_ivp": 0.004,
            "structural_credit_pct": 0.003,
            "iv_regime_premium_pct": 0.001,
            "excess_over_fair_pct": 0.001,
            "z_credit_pct": 0.5,
            "theta_vega_ratio": -0.075,
            "gamma_theta_dollar": 0.001,
            "ivp_atm_7d_90d_at_entry": 65.0,
        }
        if analytics_overrides:
            out.update(analytics_overrides)
        return out
    monkeypatch.setattr(lsc, "compute_trade_analytics", _analytics)

    # lookup_calibration: returns a v2-shaped bucket for every call
    default_calib = {
        "source": "specific_bucket",
        "credit_pct_median": 0.005,
        "credit_pct_mean": 0.005,
        "credit_pct_std": 0.001,
        "z_winners_mean": 0.02,
        "z_winners_std": 0.005,
        "overall_winrate": 0.62,
        "pattern_winrate": json.dumps({"A": 0.70, "B": 0.50, "Other": 0.45}),
        "n_trades": 42,
    }
    monkeypatch.setattr(
        lsc, "lookup_calibration",
        lambda dte, spot, td, ivp: (calib if calib is not None else default_calib),
    )


# ── Tests ────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_empty_ticker_store_returns_empty_list(monkeypatch):
    _patch_common(monkeypatch, expiries=[date.today() + timedelta(days=7)],
                  has_data=False)
    out = _run(lsc.scan_live_candidates())
    assert out == []


def test_zero_spot_returns_empty(monkeypatch):
    _patch_common(monkeypatch, expiries=[date.today() + timedelta(days=7)],
                  spot=0.0)
    out = _run(lsc.scan_live_candidates())
    assert out == []


def test_empty_expiries_returns_empty(monkeypatch):
    _patch_common(monkeypatch, expiries=[])
    out = _run(lsc.scan_live_candidates())
    assert out == []


def test_basic_scan_emits_six_candidates_per_expiry(monkeypatch):
    """1 expiry × 6 deltas → 6 candidates."""
    exp = date.today() + timedelta(days=7)
    _patch_common(monkeypatch, expiries=[exp])
    out = _run(lsc.scan_live_candidates())
    assert len(out) == 6
    # All 6 target deltas surfaced
    assert sorted({c.target_delta for c in out}) == list(lsc.TARGET_DELTAS)


def test_two_expiries_produces_twelve_candidates(monkeypatch):
    e1 = date.today() + timedelta(days=7)
    e2 = date.today() + timedelta(days=14)
    _patch_common(monkeypatch, expiries=[e1, e2])
    out = _run(lsc.scan_live_candidates())
    assert len(out) == 12
    # Both expiries represented
    assert {c.expiry for c in out} == {e1.isoformat(), e2.isoformat()}


def test_candidates_sorted_by_quality_desc(monkeypatch):
    """Mock analytics returns score = 30 + 100·avg(|leg deltas|). Top should
    be the candidate that picked the highest-delta legs (target_delta=0.50,
    which picks the ATM 0.50/0.50 strikes)."""
    exp = date.today() + timedelta(days=7)
    _patch_common(monkeypatch, expiries=[exp])
    out = _run(lsc.scan_live_candidates())
    scores = [c.quality_score for c in out]
    assert scores == sorted(scores, reverse=True)
    assert out[0].target_delta == 0.50
    # Lowest score belongs to the smallest leg-delta picks (5-strike chain
    # only has |δ| ∈ {0.10, 0.30, 0.50, 0.55, 0.85}, so target 0.05 / 0.10 /
    # 0.15 all snap to the 0.10 wings → tied at the bottom).
    assert out[-1].quality_score == pytest.approx(40.0)


def test_v2_fields_threaded_through(monkeypatch):
    """overall_winrate / pattern_winrate / n_trades from calibration
    bucket should land on the candidate."""
    exp = date.today() + timedelta(days=7)
    _patch_common(monkeypatch, expiries=[exp])
    out = _run(lsc.scan_live_candidates())
    # Pattern "A" wins lookup → 0.70
    assert all(c.overall_winrate == pytest.approx(0.62) for c in out)
    assert all(c.pattern_winrate == pytest.approx(0.70) for c in out)
    assert all(c.n_trades_in_bucket == 42 for c in out)


def test_pattern_winrate_falls_back_to_other(monkeypatch):
    """When the trade's pattern key is missing from the JSON map, use 'Other'."""
    exp = date.today() + timedelta(days=7)
    _patch_common(monkeypatch, expiries=[exp],
                  analytics_overrides={"pattern": "ZZ"})  # not in map
    out = _run(lsc.scan_live_candidates())
    assert all(c.pattern_winrate == pytest.approx(0.45) for c in out)


def test_no_calibration_bucket_v2_fields_none(monkeypatch):
    """When lookup_calibration returns None, v2 fields stay None but
    candidate is still emitted (analytics_score may still be present)."""
    exp = date.today() + timedelta(days=7)
    _patch_common(monkeypatch, expiries=[exp], calib=None)

    # Re-patch lookup_calibration to return None explicitly
    monkeypatch.setattr(lsc, "lookup_calibration",
                        lambda dte, spot, td, ivp: None)
    out = _run(lsc.scan_live_candidates())
    assert len(out) == 6
    assert all(c.overall_winrate is None for c in out)
    assert all(c.pattern_winrate is None for c in out)
    assert all(c.n_trades_in_bucket is None for c in out)


def test_credit_calculations(monkeypatch):
    """Credit should reflect the picked legs' marks. Closest-delta picking:
    Δ=0.50 → CE strike 100k mark=600, PE strike 100k mark=600 → total=1200,
    credit_usd = 1200 × 100 × 0.001 = 120.
    Δ=0.10 → CE strike 110k mark=100, PE strike 90k mark=100 → total=200,
    credit_usd = 200 × 100 × 0.001 = 20."""
    exp = date.today() + timedelta(days=7)
    _patch_common(monkeypatch, expiries=[exp])
    out = _run(lsc.scan_live_candidates())
    by_td = {c.target_delta: c for c in out}
    assert by_td[0.50].total_credit == pytest.approx(1200.0)
    assert by_td[0.50].credit_usd == pytest.approx(120.0)
    assert by_td[0.10].total_credit == pytest.approx(200.0)
    assert by_td[0.10].credit_usd == pytest.approx(20.0)
    # credit_pct = total_credit / spot
    assert by_td[0.10].credit_pct == pytest.approx(200.0 / 100_000.0)


def test_strike_picks_match_closest_delta(monkeypatch):
    """Verify the closest-delta picker selects the expected strikes."""
    exp = date.today() + timedelta(days=7)
    _patch_common(monkeypatch, expiries=[exp])
    out = _run(lsc.scan_live_candidates())
    by_td = {c.target_delta: c for c in out}
    # Δ=0.30 → CE 105k (δ=0.30), PE 95k (|δ|=0.30)
    assert by_td[0.30].call_strike == 105_000
    assert by_td[0.30].put_strike == 95_000
    # Δ=0.10 → CE 110k (δ=0.10), PE 90k (|δ|=0.10)
    assert by_td[0.10].call_strike == 110_000
    assert by_td[0.10].put_strike == 90_000


def test_hard_filters_pass_with_good_context(monkeypatch):
    """Context with IVP=70, IV-RV+ve, ADX=20, GEX OK, DTE=7 → all flags True."""
    exp = date.today() + timedelta(days=7)
    ctx = {
        "ivp_atm_7d_90d": 70.0,
        "atm_iv_7d": 0.60,
        "rv_7d": 50.0,         # 50% — atm 60% > rv/100 = 0.50
        "adx_14_4h": 20.0,
        "gex_regime": "STABLE",
    }
    _patch_common(monkeypatch, expiries=[exp], ctx=ctx)
    out = _run(lsc.scan_live_candidates())
    c = out[0]
    assert c.flt_ivp_gt50 is True
    assert c.flt_iv_rv_spread_pos is True
    assert c.flt_adx_lt30 is True
    assert c.flt_dte_5_14 is True
    assert c.flt_gex_ok is True
    assert c.flt_all_pass is True


def test_hard_filters_fail_with_bad_context(monkeypatch):
    """Context with IVP=30, ADX=40, GEX NEAR_FLIP, dte=2 → all flags False."""
    exp = date.today() + timedelta(days=2)
    ctx = {
        "ivp_atm_7d_90d": 30.0,
        "atm_iv_7d": 0.30,
        "rv_7d": 80.0,
        "adx_14_4h": 40.0,
        "gex_regime": "NEAR_FLIP",
    }
    _patch_common(monkeypatch, expiries=[exp], ctx=ctx)
    out = _run(lsc.scan_live_candidates())
    c = out[0]
    assert c.flt_ivp_gt50 is False
    assert c.flt_iv_rv_spread_pos is False
    assert c.flt_adx_lt30 is False
    assert c.flt_dte_5_14 is False
    assert c.flt_gex_ok is False
    assert c.flt_all_pass is False


def test_max_expiries_caps_scan(monkeypatch):
    e1 = date.today() + timedelta(days=7)
    e2 = date.today() + timedelta(days=14)
    e3 = date.today() + timedelta(days=21)
    _patch_common(monkeypatch, expiries=[e1, e2, e3])
    out = _run(lsc.scan_live_candidates(max_expiries=2))
    # 2 expiries × 6 deltas = 12 candidates, never the third
    assert len({c.expiry for c in out}) == 2
    assert e3.isoformat() not in {c.expiry for c in out}


def test_candidate_to_dict_round_trips(monkeypatch):
    exp = date.today() + timedelta(days=7)
    _patch_common(monkeypatch, expiries=[exp])
    out = _run(lsc.scan_live_candidates())
    d = lsc.candidate_to_dict(out[0])
    # All declared fields present
    for field_name in lsc.LiveSignalCandidate.__dataclass_fields__:
        assert field_name in d
    # JSON-serializable (no numpy / dataclass leftovers)
    json.dumps(d, default=str)

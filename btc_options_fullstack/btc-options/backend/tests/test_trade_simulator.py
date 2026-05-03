"""Unit tests for trade_simulator.

Synthetic-data tests that don't touch real parquets: they monkey-patch
`get_spot_at_or_before`, `get_mark_at_or_before`, and `load_leg_series` to
return controlled values so the bar-walk + SL + cost logic is exercised
in isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import trade_simulator as ts_mod
from app.services.trade_simulator import (
    LegSlSpec,
    PathSnapshot,
    TradeLegSpec,
    TradeResult,
    simulate_trade_path,
)
from app.services.costs import CONTRACT_VALUE


# ── Synthetic data helpers ────────────────────────────────────────────────────

def _epoch(y, m, d, h=0, mi=0) -> int:
    return int(datetime(y, m, d, h, mi, tzinfo=timezone.utc).timestamp())


def _bars(start_ts: int, marks_by_ts: dict[int, float]) -> list[dict]:
    """Return a list of {time, close} bars. Caller controls the schedule."""
    return [{"time": ts, "close": mk} for ts, mk in sorted(marks_by_ts.items())]


@pytest.fixture
def patch_data(monkeypatch):
    """Monkey-patch the data accessors. Returns a controller object."""
    state = {
        "spot": {},          # ts -> spot
        "marks": {},         # (expiry, strike, opt_type, ts) -> mark
        "series": {},        # (expiry, strike, opt_type) -> list of bars
    }

    def fake_spot(ts):
        keys = sorted(k for k in state["spot"] if k <= ts)
        return state["spot"][keys[-1]] if keys else 0.0

    def fake_mark(expiry, strike, opt_type, ts):
        ks = [
            t for (e, s, o, t) in state["marks"]
            if e == expiry and s == strike and o == opt_type and t <= ts
        ]
        if not ks:
            return 0.0, ts
        t_use = max(ks)
        return state["marks"][(expiry, strike, opt_type, t_use)], t_use

    def fake_series(expiry, strike, opt_type, start, end, tf):
        bars = state["series"].get((expiry, strike, opt_type), [])
        return [b for b in bars if start <= int(b["time"]) <= end]

    monkeypatch.setattr(ts_mod, "get_spot_at_or_before", fake_spot)
    monkeypatch.setattr(ts_mod, "get_mark_at_or_before", fake_mark)
    monkeypatch.setattr(ts_mod, "load_leg_series", fake_series)

    # Disable margin (it tries to call into margin_v2 with synthetic IVs that may fail)
    monkeypatch.setattr(ts_mod, "compute_portfolio_margin", lambda legs, spot: None)
    return state


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_returns_none_when_no_spot(patch_data):
    """Bare entry-time spot lookup empty → None (caller skips this trade)."""
    legs = [TradeLegSpec("2025-12-05", 100000, "CE", "SELL", 1)]
    out = simulate_trade_path(legs, _epoch(2025, 11, 28, 17, 30), _epoch(2025, 11, 29, 4, 30))
    assert out is None


def test_simple_short_strangle_no_sl_no_costs(patch_data):
    """11h overnight short strangle, both legs decay to half value, no SL, no costs.

    Expected: positive net_pnl from premium decay; max_mtm reflects entry-slip-baked
    convention (no slip here, so equals raw max).
    """
    entry = _epoch(2025, 11, 28, 17, 30)   # Fri 23:00 IST = 17:30 UTC
    exit_ = _epoch(2025, 11, 29, 4, 30)    # Sat 10:00 IST = 04:30 UTC
    expiry = "2025-12-05"

    patch_data["spot"][entry] = 100_000.0
    patch_data["spot"][exit_] = 100_000.0
    # Mark at entry = $200 each leg, mark at exit = $100 each (50% decay)
    for strike, opt in [(105_000, "CE"), (95_000, "PE")]:
        patch_data["marks"][(expiry, strike, opt, entry)] = 200.0
        patch_data["marks"][(expiry, strike, opt, exit_)] = 100.0
        # Bar series: every hour, smooth decay 200 → 100 over 11 bars
        bars_dict = {entry + i * 3600: 200.0 - i * (100.0 / 11) for i in range(12)}
        patch_data["series"][(expiry, strike, opt)] = _bars(entry, bars_dict)

    legs = [
        TradeLegSpec(expiry, 105_000, "CE", "SELL", 100),
        TradeLegSpec(expiry, 95_000,  "PE", "SELL", 100),
    ]
    res = simulate_trade_path(legs, entry, exit_, cost_cfg={}, margin_compute=False)

    assert res is not None
    # Gross: per leg (100 - 200) × 100 × CV × -1 = +100 × 100 × CV → 2 legs
    expected_gross = 2 * (100.0 - 200.0) * 100 * CONTRACT_VALUE * -1
    assert res.gross_pnl == pytest.approx(expected_gross, abs=0.01)
    assert res.net_pnl == pytest.approx(expected_gross, abs=0.01)   # no costs
    assert res.exit_reason == "TimeStop"
    assert res.breaching_leg_idx is None
    assert res.entry_slip == 0.0
    assert res.exit_slip == 0.0


def test_leg_sl_pct_100_triggers_when_mark_doubles(patch_data):
    """Friday-overnight playbook: leg SL = 100% per leg (mark ≥ 2× entry).

    CE leg's mark spikes to 410 at t* (>200 = entry) → SL triggers,
    exit_reason='LegSL', breaching_leg_idx=0, exit_ts==t*.
    """
    entry = _epoch(2025, 11, 28, 17, 30)
    exit_ = _epoch(2025, 11, 29, 4, 30)
    expiry = "2025-12-05"

    patch_data["spot"][entry] = 100_000.0
    patch_data["spot"][exit_] = 100_000.0
    for strike, opt in [(105_000, "CE"), (95_000, "PE")]:
        patch_data["marks"][(expiry, strike, opt, entry)] = 200.0
        patch_data["marks"][(expiry, strike, opt, exit_)] = 100.0

    # CE leg spikes to 410 at hour 5 (well above 2× 200 = 400 trigger)
    sl_t = entry + 5 * 3600
    ce_bars = {
        entry: 200.0,
        entry + 3600: 250.0,
        entry + 2 * 3600: 300.0,
        entry + 3 * 3600: 350.0,
        entry + 4 * 3600: 380.0,
        sl_t: 410.0,                 # > 2× entry → SL triggers here
        entry + 6 * 3600: 200.0,
        exit_: 100.0,
    }
    pe_bars = {entry + i * 3600: 200.0 - i * 5 for i in range(12)}
    patch_data["series"][(expiry, 105_000, "CE")] = _bars(entry, ce_bars)
    patch_data["series"][(expiry, 95_000,  "PE")] = _bars(entry, pe_bars)

    legs = [
        TradeLegSpec(expiry, 105_000, "CE", "SELL", 100),
        TradeLegSpec(expiry, 95_000,  "PE", "SELL", 100),
    ]
    sl_specs = [LegSlSpec("pct", 100.0), LegSlSpec("pct", 100.0)]
    res = simulate_trade_path(
        legs, entry, exit_, leg_sl_configs=sl_specs, cost_cfg={},
        margin_compute=False,
    )

    assert res is not None
    assert res.exit_reason == "LegSL"
    assert res.breaching_leg_idx == 0
    assert res.exit_ts == sl_t
    # exit_marks at SL = [410, ce_pe_at_t*]
    assert res.exit_marks[0] == 410.0


def test_record_snapshots_hourly_cadence(patch_data):
    """record_snapshots=True with 3600s cadence over 11h → exactly 11 snapshots."""
    entry = _epoch(2025, 11, 28, 17, 30)
    exit_ = _epoch(2025, 11, 29, 4, 30)
    expiry = "2025-12-05"

    patch_data["spot"][entry] = 100_000.0
    patch_data["spot"][exit_] = 100_000.0
    for strike, opt in [(105_000, "CE"), (95_000, "PE")]:
        patch_data["marks"][(expiry, strike, opt, entry)] = 200.0
        patch_data["marks"][(expiry, strike, opt, exit_)] = 150.0
        # Hourly bars
        bars_dict = {entry + i * 3600: 200.0 - i * (50.0 / 11) for i in range(12)}
        patch_data["series"][(expiry, strike, opt)] = _bars(entry, bars_dict)

    legs = [
        TradeLegSpec(expiry, 105_000, "CE", "SELL", 100),
        TradeLegSpec(expiry, 95_000,  "PE", "SELL", 100),
    ]
    res = simulate_trade_path(
        legs, entry, exit_, cost_cfg={},
        record_snapshots=True, snapshot_cadence_s=3600,
        margin_compute=False,
    )

    assert res is not None
    assert res.snapshots is not None
    assert 10 <= len(res.snapshots) <= 12
    for i in range(1, len(res.snapshots)):
        assert res.snapshots[i].ts > res.snapshots[i-1].ts


def test_costs_applied_when_enabled(patch_data):
    """Slippage-enabled config produces non-zero entry_slip + exit_slip."""
    entry = _epoch(2025, 11, 28, 17, 30)
    exit_ = _epoch(2025, 11, 29, 4, 30)
    expiry = "2025-12-05"
    patch_data["spot"][entry] = 100_000.0
    patch_data["spot"][exit_] = 100_000.0
    for strike, opt in [(105_000, "CE"), (95_000, "PE")]:
        patch_data["marks"][(expiry, strike, opt, entry)] = 200.0
        patch_data["marks"][(expiry, strike, opt, exit_)] = 100.0
        patch_data["series"][(expiry, strike, opt)] = _bars(entry, {
            entry: 200.0, exit_: 100.0,
        })

    legs = [
        TradeLegSpec(expiry, 105_000, "CE", "SELL", 100),
        TradeLegSpec(expiry, 95_000,  "PE", "SELL", 100),
    ]
    cost_cfg = {
        "slippage": {"enabled": True, "mode": "smart", "flat_value": 5.0, "mult": 1.0},
        "brokerage": {"enabled": True, "rate": "offer", "referral": False},
    }
    res = simulate_trade_path(legs, entry, exit_, cost_cfg=cost_cfg,
                               margin_compute=False)
    assert res is not None
    assert res.entry_slip > 0
    assert res.exit_slip > 0
    assert res.entry_brk >= 0
    assert res.exit_brk >= 0
    assert res.net_pnl < res.gross_pnl   # costs reduce net


def test_mfe_mae_coherence(patch_data):
    """max_mtm >= net_pnl (both in $USD), min_mtm <= max_mtm."""
    entry = _epoch(2025, 11, 28, 17, 30)
    exit_ = _epoch(2025, 11, 29, 4, 30)
    expiry = "2025-12-05"
    patch_data["spot"][entry] = 100_000.0
    patch_data["spot"][exit_] = 100_000.0
    for strike, opt in [(105_000, "CE"), (95_000, "PE")]:
        patch_data["marks"][(expiry, strike, opt, entry)] = 200.0
        patch_data["marks"][(expiry, strike, opt, exit_)] = 150.0
        # Marks dip to 100 mid-trade then recover to 150 — MFE is at the dip
        patch_data["series"][(expiry, strike, opt)] = _bars(entry, {
            entry: 200.0,
            entry + 5 * 3600: 100.0,    # short side: this is MAX favorable
            exit_: 150.0,
        })

    legs = [
        TradeLegSpec(expiry, 105_000, "CE", "SELL", 100),
        TradeLegSpec(expiry, 95_000,  "PE", "SELL", 100),
    ]
    res = simulate_trade_path(legs, entry, exit_, cost_cfg={}, margin_compute=False)
    assert res is not None
    assert res.max_mtm >= res.min_mtm
    assert res.max_mtm >= res.net_pnl - 1e-6   # allow tiny float wiggle


def test_breaching_leg_pe_when_put_doubles(patch_data):
    """PE leg's mark doubles → breaching_leg_idx=1."""
    entry = _epoch(2025, 11, 28, 17, 30)
    exit_ = _epoch(2025, 11, 29, 4, 30)
    expiry = "2025-12-05"
    patch_data["spot"][entry] = 100_000.0
    patch_data["spot"][exit_] = 100_000.0
    for strike, opt in [(105_000, "CE"), (95_000, "PE")]:
        patch_data["marks"][(expiry, strike, opt, entry)] = 200.0
        patch_data["marks"][(expiry, strike, opt, exit_)] = 100.0
    # CE: smooth decay; PE: spikes to 420 at hour 4
    patch_data["series"][(expiry, 105_000, "CE")] = _bars(entry, {
        entry + i * 3600: 200.0 - i * 5 for i in range(12)
    })
    sl_t = entry + 4 * 3600
    patch_data["series"][(expiry, 95_000, "PE")] = _bars(entry, {
        entry: 200.0, entry + 3600: 250, entry + 2 * 3600: 300,
        entry + 3 * 3600: 350, sl_t: 420.0, exit_: 100.0,
    })
    legs = [
        TradeLegSpec(expiry, 105_000, "CE", "SELL", 100),
        TradeLegSpec(expiry, 95_000,  "PE", "SELL", 100),
    ]
    sl_specs = [LegSlSpec("pct", 100.0), LegSlSpec("pct", 100.0)]
    res = simulate_trade_path(legs, entry, exit_, leg_sl_configs=sl_specs,
                               cost_cfg={}, margin_compute=False)
    assert res is not None
    assert res.exit_reason == "LegSL"
    assert res.breaching_leg_idx == 1
    assert res.exit_ts == sl_t

"""Tests for /api/v1/m7/trade_diagnostic — single-trade diagnostic endpoint
plus the `losers_sample` companion on /losses_distribution.

Mirrors the test pattern from test_m7_cell_wvl.py: monkey-patch
`_derive_exits` to return synthetic universes so we don't touch parquet IO.
"""
from __future__ import annotations

import math
import pandas as pd
import pytest

from app.api import m7_results


def _row(**overrides):
    """Build a single trade row with a complete column set. Defaults yield
    a vanilla winning straddle with no hypothesis flags fired; overrides
    flip individual columns to exercise specific predicates."""
    base = {
        "trade_id": "1001",
        "friday_date_ist": "2025-01-10",
        "entry_ts_utc": 1_736_503_200,
        "exit_ts": 1_736_589_600,  # +24h
        "entry_hour_ist": 23,
        "expiry_bucket": "current (Sat)",
        "expiry_date": "2025-01-11",
        "delta_target": 0.30,
        "is_straddle": False,
        "exit_reason": "hard_cap",
        "loss_cause": None,
        "leg_winner": "both",
        "entry_atm_iv_band": "30-40",
        # PnL
        "credit_usd": 200.0,
        "margin_used_usd_at_entry": 800.0,
        "gross_pnl_usd": 50.0,
        "net_pnl_estimate_usd": 30.0,
        "is_win": True,
        "max_mtm_usd": 60.0,
        "min_mtm_usd": -10.0,
        "exit_mtm_usd": 35.0,
        "rel_time_max_mtm": 0.6,
        "rel_time_min_mtm": 0.2,
        "pct_return_on_credit": 0.15,
        "pct_return_on_margin": 0.0375,
        "leg_pnl_diff_usd": 5.0,
        # Costs
        "entry_slippage_call_usd": 1.5, "entry_slippage_put_usd": 1.5,
        "entry_brokerage_call_usd": 0.5, "entry_brokerage_put_usd": 0.5,
        "exit_slippage_call_usd": 2.0,  "exit_slippage_put_usd": 2.0,
        "exit_brokerage_call_usd": 0.5, "exit_brokerage_put_usd": 0.5,
        "total_entry_cost_usd": 4.0, "total_exit_cost_usd": 5.0,
        # Per-leg
        "call_strike": 105_000, "put_strike": 95_000,
        "call_entry_iv": 0.45, "put_entry_iv": 0.45,
        "call_entry_delta": 0.30, "put_entry_delta": -0.30,
        "call_entry_gamma": 0.001, "put_entry_gamma": 0.001,
        "call_entry_theta": -1.5, "put_entry_theta": -1.5,
        "call_entry_vega": 50.0, "put_entry_vega": 50.0,
        "call_entry_mark": 100.0, "put_entry_mark": 100.0,
        "exit_call_mark": 90.0,   "exit_put_mark": 90.0,
        "call_leg_pnl_usd": 15.0, "put_leg_pnl_usd": 15.0,
        "call_leg_max_mtm_usd": 30.0, "call_leg_min_mtm_usd": -5.0,
        "put_leg_max_mtm_usd": 30.0,  "put_leg_min_mtm_usd": -5.0,
        # Skew
        "delta_skew": 0.0, "iv_skew_pct": 0.0,
        "premium_skew_usd": 0.0, "premium_skew_pct": 0.0,
        "iv_skew_bucket": "neutral", "delta_skew_bucket": "neutral",
        "premium_skew_bucket": "neutral",
        # Vol regime
        "entry_atm_iv": 0.45,
        "ctx_atm_iv_7d": 0.46, "ctx_atm_iv_14d": 0.47,
        "ctx_atm_iv_30d": 0.48, "ctx_atm_iv_60d": 0.50,
        "ctx_ivp_atm_7d_90d": 50.0, "ctx_ivp_atm_14d_90d": 50.0,
        "ctx_ivp_atm_30d_90d": 50.0, "ctx_ivp_4h": 50.0,
        "ctx_rv_7d": 40.0, "ctx_rv_14d": 42.0, "ctx_rv_30d": 44.0,
        "ctx_iv_rv_spread_7d": 0.05, "ctx_iv_rv_spread_30d": 0.04,
        "ctx_iv_rv_ratio_7d": 1.12,
        "ctx_vrp_pct_7d": 0.05, "ctx_rvp_4h": 50.0,
        "ivp_4h_delta_24h": 0.0, "ivp_4h_delta_48h": 0.0,
        "iv_change_stdev_7d": 0.01, "vov_ratio": 0.03,
        "atm_iv_at_min_mtm": 0.46,
        "min_atm_iv_in_window": 0.44,
        "max_atm_iv_in_window": 0.47,
        # Skew smile
        "ctx_risk_reversal_25d": 0.05,
        "ctx_butterfly_25d": 0.01,
        "ctx_wing_atm_ratio": 1.05,
        "ctx_term_slope_7_30": -0.02,
        # Spot regime
        "spot_at_entry": 100_000, "exit_spot": 100_500,
        "spot_at_min_mtm": 100_200,
        "min_spot_in_window": 99_800, "max_spot_in_window": 100_700,
        "entry_rsi_14_5m": 50.0, "entry_rsi_14_4h": 55.0,
        "entry_macd_hist_5m": 5.0,
        "entry_bb_pct_b_5m": 0.5, "entry_atr_pct_5m": 0.4,
        "ctx_atr_pct_4h": 1.5, "ctx_adx_14_4h": 22.0,
        "ctx_pcr_oi": 1.0, "ctx_total_gex": 1_000_000.0,
        # Expected move
        "expected_move_1sigma_7d": 1800.0,
        "expected_move_1sigma_14d": 2500.0,
        "expected_move_1sigma_30d": 3700.0,
        # Greeks ratios
        "theta_per_vega_call": -0.03,
        "theta_per_vega_put": -0.03,
        "theta_per_vega_combined": -0.03,
        "theta_per_vega_at_min_mtm": -0.025,
        "net_delta_at_min_mtm": 0.05,
        # Premium context
        "fair_credit_at_ivp": 180.0, "structural_credit_pct": 0.0018,
        "iv_regime_premium_pct": 0.0,
        "excess_over_fair_pct": 0.11,
        "pattern_winrate": 0.7, "expectancy_per_credit_pct": 0.05,
        "bucket_overall_winrate": 0.65, "bucket_sl_hit_rate": 0.1,
    }
    base.update(overrides)
    return base


@pytest.fixture
def patched_derive(monkeypatch):
    """Replace `_derive_exits` with a synthetic universe (one row by default).
    Tests overwrite `state['rows']` with their own list."""
    state = {"rows": [_row()]}

    def _stub(filters: dict, exit_rule: dict, **kwargs) -> pd.DataFrame:
        return pd.DataFrame(state["rows"])

    monkeypatch.setattr(m7_results, "_derive_exits", _stub)
    return state


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_unknown_trade_id_returns_404(patched_derive):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        m7_results.get_trade_diagnostic(trade_id="does-not-exist", exit_rule=None)
    assert exc.value.status_code == 404


def test_known_trade_returns_all_sections(patched_derive):
    out = m7_results.get_trade_diagnostic(trade_id="1001", exit_rule=None)
    expected_top_keys = {
        "identity", "pnl", "costs", "per_leg", "vol_regime",
        "skew_smile", "spot_regime", "expected_move", "greeks_ratios",
        "context_premium", "hypotheses",
    }
    assert expected_top_keys.issubset(out.keys()), \
        f"missing keys: {expected_top_keys - out.keys()}"
    # Identity passes through
    assert out["identity"]["trade_id"] == "1001"
    assert out["identity"]["entry_atm_iv_band"] == "30-40"
    # Skew smile carries R_25 / BF_25
    assert out["skew_smile"]["ctx_risk_reversal_25d"] == 0.05
    assert out["skew_smile"]["ctx_butterfly_25d"] == 0.01
    # Per-leg includes all 4 Greeks
    for side in ("call", "put"):
        for k in ("entry_delta", "entry_gamma", "entry_theta", "entry_vega"):
            assert out["per_leg"][side][k] is not None


def test_derived_ratios_correct_on_synthetic_input(patched_derive):
    # entry_iv=0.50, max_iv_in_window=0.60 → iv_jump_pct=0.20
    # spot_in=100_000, spot_min=98_000 → spot_move_pct = -0.02
    # expected_move_1sigma_7d=2000, |spot_min-spot_in|=2000 → ratio=1.0
    # net_d at entry = 0.30 + (-0.30) = 0.00 → net_d_drift = 0.05 - 0 = 0.05
    patched_derive["rows"] = [_row(
        trade_id="2002",
        entry_atm_iv=0.50, max_atm_iv_in_window=0.60,
        spot_at_entry=100_000, spot_at_min_mtm=98_000,
        expected_move_1sigma_7d=2000.0,
        net_delta_at_min_mtm=0.05,
    )]
    out = m7_results.get_trade_diagnostic(trade_id="2002", exit_rule=None)
    assert out["vol_regime"]["iv_jump_pct"] == pytest.approx(0.20, abs=1e-6)
    assert out["spot_regime"]["spot_move_pct"] == pytest.approx(-0.02, abs=1e-6)
    assert out["expected_move"]["actual_move_usd"] == pytest.approx(2000.0, abs=1e-2)
    assert out["expected_move"]["actual_vs_1sigma_7d_ratio"] == pytest.approx(1.0, abs=1e-6)
    assert out["expected_move"]["exceeded_1sigma_7d"] is False
    assert out["greeks_ratios"]["net_delta_at_entry"] == pytest.approx(0.0, abs=1e-9)
    assert out["greeks_ratios"]["delta_drift"] == pytest.approx(0.05, abs=1e-9)


def test_hypothesis_iv_driven_fires_on_synthetic_jump(patched_derive):
    # IV jumped +20% at trough AND +30% peak → iv_driven fires.
    # spot move is small → directional does not.
    patched_derive["rows"] = [_row(
        trade_id="3003",
        is_win=False, leg_winner="neither",
        entry_atm_iv=0.50,
        atm_iv_at_min_mtm=0.60,        # +20% at trough
        max_atm_iv_in_window=0.65,     # +30% peak
        ctx_atr_pct_4h=2.0,
        spot_at_entry=100_000, spot_at_min_mtm=100_500,  # 0.5% — under threshold
        net_pnl_estimate_usd=-50.0,
    )]
    out = m7_results.get_trade_diagnostic(trade_id="3003", exit_rule=None)
    flags = {h["flag"]: h for h in out["hypotheses"]}
    assert flags["iv_driven"]["fired"] is True
    assert flags["directional"]["fired"] is False


def test_hypothesis_directional_fires_when_spot_exceeds_atr_threshold(patched_derive):
    # 3% spot move vs 1.5% × 1.5 = 2.25% threshold → directional fires.
    patched_derive["rows"] = [_row(
        trade_id="4004",
        is_win=False,
        spot_at_entry=100_000, spot_at_min_mtm=103_000,
        ctx_atr_pct_4h=1.5,
    )]
    out = m7_results.get_trade_diagnostic(trade_id="4004", exit_rule=None)
    flags = {h["flag"]: h for h in out["hypotheses"]}
    assert flags["directional"]["fired"] is True


def test_hypothesis_gamma_squeeze_fires_on_early_sl_with_delta_blowout(patched_derive):
    # rule_trigger SL in first 30% of window AND |Δ_min| > 2× max(|call_d|,|put_d|).
    patched_derive["rows"] = [_row(
        trade_id="5005",
        is_win=False, exit_reason="rule_trigger",
        rel_time_min_mtm=0.10,
        call_entry_delta=0.30, put_entry_delta=-0.30,
        net_delta_at_min_mtm=0.80,   # 0.80 > 2 × 0.30 = 0.60
    )]
    out = m7_results.get_trade_diagnostic(trade_id="5005", exit_rule=None)
    flags = {h["flag"]: h for h in out["hypotheses"]}
    assert flags["gamma_squeezed"]["fired"] is True


def test_losses_distribution_include_trades_returns_losers(patched_derive):
    """losers_sample contains only is_win=False trades, sorted pnl_asc."""
    patched_derive["rows"] = [
        _row(trade_id="w1", is_win=True,  net_pnl_estimate_usd=50.0),
        _row(trade_id="L1", is_win=False, net_pnl_estimate_usd=-30.0,
             loss_cause="directional"),
        _row(trade_id="L2", is_win=False, net_pnl_estimate_usd=-100.0,
             loss_cause="vol_expansion"),
    ]
    out = m7_results.get_losses_distribution(
        dimensions=None, exit_rule=None, metric="avg_net_pnl",
        scope=None, ranking="credit",
        delta_target=None, is_straddle=None, expiry_date=None,
        expiry_bucket=None, entry_atm_iv_band=None, entry_hour_ist=None,
        dte_bucket=None, spot_bucket=None, ivp_bucket=None,
        ctx_pattern=None, ctx_gex_regime=None, friday_date_ist=None,
        loss_cause=None,
        include_trades=True, trades_limit=10, trades_offset=0,
        trades_sort="pnl_asc", only_sl_hits=False, cells=None,
    )
    sample = out["losers_sample"]
    assert out["losers_sample_total"] == 2
    assert len(sample) == 2
    # pnl_asc → worst first
    assert sample[0]["trade_id"] == "L2"
    assert sample[1]["trade_id"] == "L1"
    # No winners
    assert all(r["net_pnl_estimate_usd"] < 0 for r in sample)


def test_losses_distribution_only_sl_hits_filter(patched_derive):
    patched_derive["rows"] = [
        _row(trade_id="L1", is_win=False, net_pnl_estimate_usd=-30.0,
             exit_reason="rule_trigger", loss_cause="directional"),
        _row(trade_id="L2", is_win=False, net_pnl_estimate_usd=-50.0,
             exit_reason="hard_cap", loss_cause="path_dependent"),
    ]
    out = m7_results.get_losses_distribution(
        dimensions=None, exit_rule=None, metric="avg_net_pnl",
        scope=None, ranking="credit",
        delta_target=None, is_straddle=None, expiry_date=None,
        expiry_bucket=None, entry_atm_iv_band=None, entry_hour_ist=None,
        dte_bucket=None, spot_bucket=None, ivp_bucket=None,
        ctx_pattern=None, ctx_gex_regime=None, friday_date_ist=None,
        loss_cause=None,
        include_trades=True, trades_limit=10, trades_offset=0,
        trades_sort="pnl_asc", only_sl_hits=True, cells=None,
    )
    sample = out["losers_sample"]
    assert all(r["exit_reason"] == "rule_trigger" for r in sample)
    assert len(sample) == 1
    assert sample[0]["trade_id"] == "L1"

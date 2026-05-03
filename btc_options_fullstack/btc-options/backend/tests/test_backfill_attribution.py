"""Unit tests for M5 v2 backfill_attribution.

Synthetic m4_trades + tiny v1 calibration → verify per-bucket aggregations
match by-hand calculations.
"""

from __future__ import annotations

import json
import os

import pandas as pd
import pytest

from app.analytics import backfill_attribution as ba


def _trade(target_delta=0.10, dte_b="3-7", spot_b="60-90k", ivp_b="60-80",
           pattern="A", credit_pct=0.005, net_pnl=10.0, exit_reason="TimeStop",
           breaching="CE", net_pct_credit=0.4, net_pct_margin=0.05):
    """Build one synthetic trade row."""
    outcome = "win" if net_pnl > 0 else "loss"
    return {
        "trade_id": hash((target_delta, credit_pct, net_pnl)) & 0xFFFFFFFFFFFFFFFF,
        "target_delta": target_delta,
        "dte_bucket": dte_b,
        "spot_bucket": spot_b,
        "delta_target": f"{target_delta:.2f}",
        "ivp_bucket": ivp_b,
        "ctx_pattern": pattern,
        "credit_pct": credit_pct,
        "net_pnl_usd": net_pnl,
        "net_pnl_pct_credit": net_pct_credit,
        "net_pnl_pct_margin": net_pct_margin,
        "exit_reason": exit_reason,
        "breaching_leg": breaching,
        "outcome": outcome,
    }


def test_aggregate_bucket_basic():
    """4 trades in one bucket: 3 wins (pattern A: 2W/0L, B: 1W/1L) → expected stats."""
    grp = pd.DataFrame([
        _trade(pattern="A", credit_pct=0.005, net_pnl=10.0, net_pct_credit=0.40),
        _trade(pattern="A", credit_pct=0.006, net_pnl=15.0, net_pct_credit=0.50),
        _trade(pattern="B", credit_pct=0.004, net_pnl=8.0,  net_pct_credit=0.30),
        _trade(pattern="B", credit_pct=0.003, net_pnl=-5.0, net_pct_credit=-0.20),
    ])
    out = ba._aggregate_bucket(grp)
    assert out["n_trades"] == 4
    assert out["n_winners"] == 3
    assert out["overall_winrate"] == pytest.approx(0.75)

    # pattern_winrate: A=2/2=1.0, B=1/2=0.5
    pat_wr = json.loads(out["pattern_winrate"])
    assert pat_wr["A"] == pytest.approx(1.0)
    assert pat_wr["B"] == pytest.approx(0.5)

    pat_n = json.loads(out["pattern_n"])
    assert pat_n["A"] == 2
    assert pat_n["B"] == 2

    # z_winners: mean/std over [0.005, 0.006, 0.004]
    expected_mean = (0.005 + 0.006 + 0.004) / 3
    assert out["z_winners_mean"] == pytest.approx(expected_mean, abs=1e-6)

    # expectancy_per_credit_pct: mean of [0.40, 0.50, 0.30, -0.20]
    assert out["expectancy_per_credit_pct"] == pytest.approx(0.25)


def test_aggregate_bucket_all_losses():
    """All-loss bucket → n_winners=0, z_winners_mean is NaN."""
    grp = pd.DataFrame([
        _trade(net_pnl=-5.0, net_pct_credit=-0.30),
        _trade(net_pnl=-10.0, net_pct_credit=-0.50),
    ])
    out = ba._aggregate_bucket(grp)
    assert out["n_trades"] == 2
    assert out["n_winners"] == 0
    assert out["overall_winrate"] == 0.0
    import math
    assert math.isnan(out["z_winners_mean"])


def test_aggregate_bucket_sl_hit_rate():
    """Mix of TimeStop + LegSL exits → sl_hit_rate computed correctly.

    For TimeStop exits, M4 sets breaching_leg=None — only LegSL trades
    contribute to the median_breaching_leg mode.
    """
    grp = pd.DataFrame([
        _trade(exit_reason="TimeStop", net_pnl=5.0,  breaching=None),
        _trade(exit_reason="TimeStop", net_pnl=10.0, breaching=None),
        _trade(exit_reason="LegSL",    net_pnl=-15.0, breaching="PE"),
    ])
    out = ba._aggregate_bucket(grp)
    assert out["sl_hit_rate"] == pytest.approx(1/3, abs=1e-6)
    assert out["median_breaching_leg"] == "PE"


def test_run_writes_v2_parquet(tmp_path, monkeypatch):
    """End-to-end: synthetic trades + v1 calibration → v2 parquet with merged cols."""
    trades_path = tmp_path / "m4_trades.parquet"
    calib_path  = tmp_path / "calibration.parquet"
    out_path    = tmp_path / "calibration_v2.parquet"

    # Synthetic v1 calibration: 2 buckets, schema mirrors what calibration_builder produces
    calib = pd.DataFrame([
        {"dte_bucket": "3-7", "spot_bucket": "60-90k", "delta_target": "0.10",
         "ivp_bucket": "60-80", "n_samples": 100,
         "credit_pct_median": 0.005, "credit_pct_mean": 0.005, "credit_pct_std": 0.001,
         "atm_iv_median": 0.4, "atm_iv_std": 0.05, "structural_baseline": 0.004},
        {"dte_bucket": "7-14", "spot_bucket": "90-120k", "delta_target": "0.15",
         "ivp_bucket": "40-60", "n_samples": 50,
         "credit_pct_median": 0.008, "credit_pct_mean": 0.008, "credit_pct_std": 0.002,
         "atm_iv_median": 0.5, "atm_iv_std": 0.06, "structural_baseline": 0.006},
    ])
    calib.to_parquet(calib_path, index=False)

    # Synthetic m4 trades — 3 trades in bucket #1 (2 wins / 1 loss), 0 in bucket #2
    trades = pd.DataFrame([
        _trade(target_delta=0.10, dte_b="3-7", spot_b="60-90k", ivp_b="60-80",
               pattern="A", net_pnl=10.0, credit_pct=0.005),
        _trade(target_delta=0.10, dte_b="3-7", spot_b="60-90k", ivp_b="60-80",
               pattern="B", net_pnl=15.0, credit_pct=0.006),
        _trade(target_delta=0.10, dte_b="3-7", spot_b="60-90k", ivp_b="60-80",
               pattern="A", net_pnl=-8.0, credit_pct=0.004),
    ])
    trades.to_parquet(trades_path, index=False)

    monkeypatch.setattr(ba, "TRADES_IN_PATH", str(trades_path))
    monkeypatch.setattr(ba, "CALIB_IN_PATH",  str(calib_path))

    import argparse
    args = argparse.Namespace(out=str(out_path))
    ba.run(args)

    assert out_path.exists()
    v2 = pd.read_parquet(out_path)
    assert len(v2) == 2  # both v1 buckets preserved
    assert (v2["version"] == 2).all()

    # Bucket #1 has trade data populated
    b1 = v2[(v2["dte_bucket"] == "3-7") & (v2["delta_target"] == "0.10")]
    assert len(b1) == 1
    assert b1.iloc[0]["n_trades"] == 3
    assert b1.iloc[0]["n_winners"] == 2
    assert b1.iloc[0]["overall_winrate"] == pytest.approx(2/3, abs=1e-6)

    # Bucket #2 has no trades — n_trades is NaN
    b2 = v2[(v2["dte_bucket"] == "7-14")]
    assert len(b2) == 1
    import math
    assert math.isnan(b2.iloc[0]["n_trades"])

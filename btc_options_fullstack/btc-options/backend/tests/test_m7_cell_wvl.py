"""Tests for /api/v1/m7/cell_winners_vs_losers — Chunk 3.

Cell-level indicator gap analysis. Backend uses synthetic in-memory
fixtures by directly invoking `get_cell_winners_vs_losers` with a
manually-constructed _derive_exits cache, so no parquet IO is needed.
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest

from app.api import m7_results


def _make_derived_fixture(rows: list[dict]) -> pd.DataFrame:
    """Build a derived DataFrame matching the shape that `_derive_exits`
    would return. Defaults fill in plausible values for cols the endpoint
    references."""
    defaults = {
        "is_win": True,
        "entry_atm_iv_band": "30-40",
        "entry_hour_ist": 23,
        "expiry_bucket": "current (Sat)",
        "delta_target": 0.30,
        "ctx_atm_iv_7d": 0.45, "ctx_atm_iv_14d": 0.46,
        "ctx_atm_iv_30d": 0.47, "ctx_atm_iv_60d": 0.48,
        "ctx_ivp_atm_7d_90d": 50.0, "ctx_ivp_atm_14d_90d": 50.0,
        "ctx_ivp_atm_30d_90d": 50.0, "ctx_ivp_4h": 50.0,
        "ivp_4h_delta_24h": 0.0, "ivp_4h_delta_48h": 0.0,
        "iv_change_stdev_7d": 0.01, "vov_ratio": 0.03,
        "ctx_rv_7d": 50.0, "ctx_rv_14d": 50.0, "ctx_rv_30d": 50.0,
        "ctx_iv_rv_spread_7d": 0.0, "ctx_iv_rv_spread_30d": 0.0,
        "ctx_iv_rv_ratio_7d": 1.0, "ctx_vrp_pct_7d": 0.0, "ctx_rvp_4h": 50.0,
        "ctx_risk_reversal_25d": 0.0, "ctx_butterfly_25d": 0.0,
        "ctx_wing_atm_ratio": 1.0, "ctx_term_slope_7_30": 0.0,
        "ctx_adx_14_4h": 25.0, "ctx_atr_pct_4h": 1.5,
        "entry_rsi_14_5m": 50.0, "entry_macd_hist_5m": 0.0,
        "entry_bb_pct_b_5m": 0.5, "entry_atr_pct_5m": 0.5,
        "entry_rsi_14_4h": 50.0,
        "expected_move_1sigma_7d": 1800.0,
        "expected_move_1sigma_14d": 2500.0,
        "expected_move_1sigma_30d": 3700.0,
        "ctx_pcr_oi": 1.0, "ctx_total_gex": 0.0,
        "fair_credit_at_ivp": 200.0, "structural_credit_pct": 0.05,
        "iv_regime_premium_pct": 0.0, "excess_over_fair_pct": 0.0,
        "theta_per_vega_call": 1.0, "theta_per_vega_put": 1.0,
        "theta_per_vega_combined": 1.0,
        "delta_skew": 0.0, "iv_skew_pct": 0.0, "premium_skew_pct": 0.0,
    }
    out = []
    for r in rows:
        merged = {**defaults, **r}
        out.append(merged)
    return pd.DataFrame(out)


@pytest.fixture
def patched_derive(monkeypatch):
    """Replace `_derive_exits` with a stub that returns a DataFrame the
    test injects. The endpoint computes σ on this same DataFrame, so it
    becomes the synthetic universe scale too."""
    df_holder = {"df": None}

    def _stub(filters: dict, exit_rule: dict, **kwargs) -> pd.DataFrame:
        return df_holder["df"].copy()

    monkeypatch.setattr(m7_results, "_derive_exits", _stub)
    return df_holder


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_returns_4xx_for_malformed_cell_json(patched_derive):
    patched_derive["df"] = _make_derived_fixture([])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        m7_results.get_cell_winners_vs_losers(cell="not json")
    assert e.value.status_code == 400


def test_returns_4xx_when_required_keys_missing(patched_derive):
    patched_derive["df"] = _make_derived_fixture([])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        m7_results.get_cell_winners_vs_losers(
            cell=json.dumps({"entry_atm_iv_band": "30-40"})
        )
    assert e.value.status_code == 400
    assert "missing" in str(e.value.detail).lower()


def test_empty_cell_returns_zero_with_pool_suggestions(patched_derive):
    # Universe has rows in a different cell — our cell is empty.
    patched_derive["df"] = _make_derived_fixture([
        {"entry_atm_iv_band": "70-80", "entry_hour_ist": 21, "is_win": True},
    ])
    out = m7_results.get_cell_winners_vs_losers(
        cell=json.dumps({"entry_atm_iv_band": "30-40", "entry_hour_ist": 23,
                         "expiry_bucket": "current (Sat)", "delta_target": 0.50}),
        discriminate_sigma=0.5, min_n_per_side=3,
    )
    assert out["n_trades"] == 0
    assert out["low_confidence"] is True
    assert isinstance(out["pool_suggestions"], list)
    assert len(out["pool_suggestions"]) >= 2


def test_indicator_gap_with_deterministic_separation(patched_derive):
    # Construct 6 winners + 4 losers with ctx_atm_iv_7d separated:
    # winners avg=0.40, losers avg=0.60. Gap = -0.20.
    # σ on the universe = std of [0.40]*6 + [0.60]*4 = ~0.098.
    rows = []
    for _ in range(6):
        rows.append({"is_win": True, "ctx_atm_iv_7d": 0.40})
    for _ in range(4):
        rows.append({"is_win": False, "ctx_atm_iv_7d": 0.60})
    patched_derive["df"] = _make_derived_fixture(rows)
    out = m7_results.get_cell_winners_vs_losers(
        cell=json.dumps({"entry_atm_iv_band": "30-40", "entry_hour_ist": 23,
                         "expiry_bucket": "current (Sat)", "delta_target": 0.30}),
        discriminate_sigma=0.5, min_n_per_side=3,
    )
    assert out["n_trades"] == 10
    assert out["n_win"] == 6
    assert out["n_loss"] == 4
    assert out["win_rate"] == 0.6
    # Find the ctx_atm_iv_7d row in indicator output.
    iv7d = next(r for r in out["rows"] if r["indicator"] == "ctx_atm_iv_7d")
    assert iv7d["avg_win"] == pytest.approx(0.40, abs=1e-6)
    assert iv7d["avg_loss"] == pytest.approx(0.60, abs=1e-6)
    assert iv7d["gap"] == pytest.approx(-0.20, abs=1e-6)
    # Effect size 0.20/0.098 = 2.04 — definitely > 0.5σ → discriminating
    assert iv7d["discriminating"] is True


def test_low_confidence_fires_for_small_n_per_side(patched_derive):
    # 5 winners + 1 loser → loss side below default min_n_per_side=3.
    rows = []
    for _ in range(5):
        rows.append({"is_win": True, "ctx_atm_iv_7d": 0.40})
    rows.append({"is_win": False, "ctx_atm_iv_7d": 0.60})
    patched_derive["df"] = _make_derived_fixture(rows)
    out = m7_results.get_cell_winners_vs_losers(
        cell=json.dumps({"entry_atm_iv_band": "30-40", "entry_hour_ist": 23,
                         "expiry_bucket": "current (Sat)", "delta_target": 0.30}),
        discriminate_sigma=0.5, min_n_per_side=3,
    )
    assert out["n_trades"] == 6
    assert out["low_confidence"] is True
    assert len(out["pool_suggestions"]) >= 2  # surfaced when low-confidence


def test_response_includes_required_keys(patched_derive):
    rows = []
    for _ in range(5):
        rows.append({"is_win": True})
    for _ in range(5):
        rows.append({"is_win": False})
    patched_derive["df"] = _make_derived_fixture(rows)
    out = m7_results.get_cell_winners_vs_losers(
        cell=json.dumps({"entry_atm_iv_band": "30-40", "entry_hour_ist": 23,
                         "expiry_bucket": "current (Sat)", "delta_target": 0.30}),
        discriminate_sigma=0.5, min_n_per_side=3,
    )
    for k in ("cell", "n_trades", "n_win", "n_loss", "win_rate",
              "low_confidence", "pool_suggestions", "rows"):
        assert k in out
    # Each row has the documented shape.
    for r in out["rows"]:
        for k in ("indicator", "label", "category", "avg_win", "avg_loss",
                  "gap", "sigma", "discriminating", "p_value_t",
                  "n_win", "n_loss"):
            assert k in r


def test_rows_sorted_discriminating_first(patched_derive):
    # Construct 2 indicators with different effect sizes:
    #   ctx_atm_iv_7d: winners=0.40, losers=0.60, gap=-0.20
    #   ctx_rv_7d:     winners=49,   losers=51,   gap=-2.0  (small relative to σ)
    rows = []
    for _ in range(5):
        rows.append({"is_win": True, "ctx_atm_iv_7d": 0.40, "ctx_rv_7d": 49.0})
    for _ in range(5):
        rows.append({"is_win": False, "ctx_atm_iv_7d": 0.60, "ctx_rv_7d": 51.0})
    patched_derive["df"] = _make_derived_fixture(rows)
    out = m7_results.get_cell_winners_vs_losers(
        cell=json.dumps({"entry_atm_iv_band": "30-40", "entry_hour_ist": 23,
                         "expiry_bucket": "current (Sat)", "delta_target": 0.30}),
        discriminate_sigma=0.5, min_n_per_side=3,
    )
    discriminating_indicators = [r["indicator"] for r in out["rows"][:3]]
    assert "ctx_atm_iv_7d" in discriminating_indicators

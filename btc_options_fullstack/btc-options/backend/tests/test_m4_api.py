"""Unit tests for /api/v1/m4 endpoints.

Synthetic m4_trades + m4_paths DataFrames installed into the module-level
cache to bypass parquet I/O. Verifies filtering, aggregation, scatter, path
fetch, quality calibration, and trade_id round-trip.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api import m4_results
from app.main import app


# ── Synthetic data ──────────────────────────────────────────────────────────

def _trade_row(*, trade_id, target_delta, dte_b, ivp_b, pattern, credit_pct,
               net_pnl, exit_reason="TimeStop", breaching="None",
               max_mtm=12.0, min_mtm=-3.0, margin=120.0, dte_days=7.0,
               entry_ts=1700000000, entry_date_ist="2025-01-01"):
    return {
        "trade_id": trade_id, "entry_ts": entry_ts,
        "entry_date_ist": entry_date_ist,
        "expiry_date": "2025-01-08", "expiry_unix": entry_ts + 7 * 86400,
        "dte_days": dte_days, "target_delta": target_delta,
        "call_strike": 100000, "put_strike": 95000,
        "spot_at_entry": 100000.0, "spot_at_exit": 100100.0,
        "call_entry_mark": 100.0, "put_entry_mark": 100.0,
        "call_entry_iv": 0.6, "put_entry_iv": 0.6,
        "call_entry_delta": 0.30, "put_entry_delta": -0.30,
        "total_credit": 200.0,
        "credit_usd": credit_pct * 100000.0,
        "credit_pct": credit_pct,
        "credit_pct_normalized": credit_pct,
        "exit_ts": entry_ts + 11 * 3600,
        "exit_reason": exit_reason, "breaching_leg": breaching,
        "call_exit_mark": 90.0, "put_exit_mark": 90.0,
        "gross_pnl_usd": net_pnl + 2.0, "slippage_usd": 1.5,
        "brokerage_usd": 0.5, "net_pnl_usd": net_pnl,
        "net_pnl_pct_credit": net_pnl / 20.0,
        "net_pnl_pct_margin": net_pnl / margin,
        "max_mtm_usd": max_mtm, "max_mtm_ts": entry_ts + 3600,
        "min_mtm_usd": min_mtm, "min_mtm_ts": entry_ts + 7200,
        "margin_used_usd": margin,
        "outcome": "win" if net_pnl > 0 else "loss",
        "schema_version": 1,
        "ctx_atm_iv_7d": 0.6, "ctx_atm_iv_14d": 0.62, "ctx_atm_iv_30d": 0.65,
        "ctx_atm_iv_60d": 0.7,
        "ctx_ivp_atm_7d_90d": 70.0, "ctx_ivp_atm_14d_90d": 65.0,
        "ctx_ivp_atm_30d_90d": 60.0, "ctx_ivp_4h": 75.0,
        "ctx_rv_7d": 50.0, "ctx_rv_14d": 55.0, "ctx_rv_30d": 60.0,
        "ctx_iv_rv_spread_7d": 0.10, "ctx_iv_rv_spread_30d": 0.05,
        "ctx_iv_rv_ratio_7d": 1.2, "ctx_vrp_pct_7d": 20.0,
        "ctx_risk_reversal_25d": -0.05, "ctx_butterfly_25d": 0.03,
        "ctx_wing_atm_ratio": 1.1, "ctx_term_slope_7_30": 0.05,
        "ctx_rvp_4h": 50.0, "ctx_adx_14_4h": 18.0, "ctx_atr_pct_4h": 0.01,
        "ctx_pcr_oi": 1.0, "ctx_total_gex": 1e6, "ctx_gex_regime": "STABLE",
        "ctx_pattern": pattern,
        "dte_bucket": dte_b, "spot_bucket": "60-90k",
        "delta_target": f"{target_delta:.2f}", "ivp_bucket": ivp_b,
        "flt_ivp_gt50": True, "flt_iv_rv_spread_pos": True,
        "flt_adx_lt30": True, "flt_dte_5_14": True, "dominant_greek": "theta",
    }


def _make_trades_df():
    return pd.DataFrame([
        _trade_row(trade_id=1, target_delta=0.10, dte_b="3-7",  ivp_b="60-80",
                    pattern="A", credit_pct=0.005, net_pnl=10.0),
        _trade_row(trade_id=2, target_delta=0.10, dte_b="3-7",  ivp_b="60-80",
                    pattern="A", credit_pct=0.006, net_pnl=12.0),
        _trade_row(trade_id=3, target_delta=0.30, dte_b="7-14", ivp_b="60-80",
                    pattern="B", credit_pct=0.012, net_pnl=-5.0,
                    exit_reason="SL", breaching="CE"),
        _trade_row(trade_id=4, target_delta=0.30, dte_b="7-14", ivp_b="40-60",
                    pattern="B", credit_pct=0.011, net_pnl=8.0),
        _trade_row(trade_id=5, target_delta=0.50, dte_b="0-3",  ivp_b="40-60",
                    pattern="C", credit_pct=0.020, net_pnl=-2.0),
    ])


def _make_paths_df():
    return pd.DataFrame([
        {"trade_id": 1, "ts": 1700000000 + 3600 * h,
         "spot": 100000.0, "call_mark": 100.0, "put_mark": 100.0,
         "call_delta": 0.30, "put_delta": -0.30,
         "call_iv": 0.6, "put_iv": 0.6,
         "pnl_gross_usd": h * 1.0, "pnl_net_usd": h * 0.8}
        for h in range(11)
    ])


@pytest.fixture(autouse=True)
def install_synthetic_caches(monkeypatch):
    """Inject synthetic DataFrames into m4_results module-level cache so
    endpoints don't try to load real parquets."""
    monkeypatch.setattr(m4_results, "_TRADES_DF", _make_trades_df())
    monkeypatch.setattr(m4_results, "_PATHS_DF", _make_paths_df())
    yield


@pytest.fixture
def client():
    return TestClient(app)


# ── Tests ────────────────────────────────────────────────────────────────────

def test_summary_unfiltered(client):
    r = client.get("/api/v1/m4/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["n_trades"] == 5
    assert d["n_winners"] == 3            # ids 1, 2, 4
    assert d["win_rate"] == pytest.approx(0.6)
    assert d["sl_hit_rate"] == pytest.approx(0.2)
    assert d["total_net_pnl_usd"] == pytest.approx(23.0)


def test_summary_filtered_by_pattern(client):
    r = client.get("/api/v1/m4/summary?ctx_pattern=A")
    d = r.json()
    assert d["n_trades"] == 2
    assert d["n_winners"] == 2
    assert d["win_rate"] == pytest.approx(1.0)


def test_trades_pagination(client):
    r = client.get("/api/v1/m4/trades?limit=2&offset=0&sort_by=net_pnl_usd&sort_desc=true")
    d = r.json()
    assert d["total"] == 5
    assert d["limit"] == 2
    assert len(d["rows"]) == 2
    # Sort desc on net_pnl_usd → first is trade_id 2 (net 12), second is 1 (net 10)
    assert d["rows"][0]["trade_id"] == "2"
    assert d["rows"][1]["trade_id"] == "1"


def test_trades_trade_id_serializes_as_string(client):
    """trade_id is uint64 in real data — must be string in JSON to avoid
    JS precision loss."""
    r = client.get("/api/v1/m4/trades?limit=1")
    d = r.json()
    assert isinstance(d["rows"][0]["trade_id"], str)


def test_aggregate_single_dim(client):
    r = client.get("/api/v1/m4/aggregate?dimension=delta_target&metric=win_rate")
    d = r.json()
    assert d["dimension"] == ["delta_target"]
    assert d["metric"] == "win_rate"
    by_delta = {r["delta_target"]: r["win_rate"] for r in d["rows"]}
    assert by_delta["0.10"] == pytest.approx(1.0)   # 2 wins / 2
    assert by_delta["0.30"] == pytest.approx(0.5)   # 1 win / 2
    assert by_delta["0.50"] == pytest.approx(0.0)


def test_aggregate_multi_dim(client):
    """Heatmap-style two-dim group-by."""
    r = client.get("/api/v1/m4/aggregate?dimension=dte_bucket"
                   "&dimension=delta_target&metric=count")
    d = r.json()
    assert d["dimension"] == ["dte_bucket", "delta_target"]
    cells = {(r["dte_bucket"], r["delta_target"]): r["count"] for r in d["rows"]}
    assert cells[("3-7", "0.10")] == 2
    assert cells[("7-14", "0.30")] == 2
    assert cells[("0-3", "0.50")] == 1


def test_aggregate_unknown_dim_400(client):
    r = client.get("/api/v1/m4/aggregate?dimension=bogus_col&metric=win_rate")
    assert r.status_code == 400


def test_aggregate_unknown_metric_400(client):
    r = client.get("/api/v1/m4/aggregate?dimension=delta_target&metric=bogus")
    assert r.status_code == 400


def test_scatter_basic(client):
    r = client.get("/api/v1/m4/scatter?x=credit_pct&y=net_pnl_usd&color_by=outcome")
    d = r.json()
    assert d["x"] == "credit_pct"
    assert d["y"] == "net_pnl_usd"
    assert d["color_by"] == "outcome"
    assert d["n"] == 5
    # All rows have outcome
    assert all("outcome" in p for p in d["points"])


def test_scatter_unknown_axis_400(client):
    r = client.get("/api/v1/m4/scatter?x=bogus&y=net_pnl_usd")
    assert r.status_code == 400


def test_path_fetch(client):
    r = client.get("/api/v1/m4/path?trade_id=1")
    d = r.json()
    assert d["n"] == 11
    assert all(s["trade_id"] == "1" for s in d["snapshots"])
    # Sorted by ts ascending
    ts_list = [s["ts"] for s in d["snapshots"]]
    assert ts_list == sorted(ts_list)


def test_path_unknown_trade_404(client):
    r = client.get("/api/v1/m4/path?trade_id=99999999")
    assert r.status_code == 404


def test_quality_calibration(client):
    r = client.get("/api/v1/m4/quality_calibration?n_buckets=3")
    d = r.json()
    # With only 5 rows + 3 buckets we may lose buckets to duplicates,
    # but every row's bucket should be assigned to exactly one bucket
    # and totals should sum to 5.
    total = sum(b["n"] for b in d["buckets"])
    assert total == 5
    # Each bucket has plausible win_rate
    for b in d["buckets"]:
        assert 0.0 <= b["win_rate"] <= 1.0

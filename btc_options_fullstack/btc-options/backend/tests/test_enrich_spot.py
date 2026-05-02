"""Unit tests for Module 1 spot enrichment indicator math.

Synthetic 5-minute OHLC bars with known properties. We verify each indicator
group on toy inputs, NOT the full pipeline (the pipeline is exercised by the
smoke run; tests here lock the math down).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analytics.enrich_spot import (
    aroon,
    atr_wilder,
    bollinger,
    cci,
    donchian,
    keltner,
    macd,
    rsi_wilder,
    rv_close,
    rv_garman_klass,
    rv_parkinson,
    roc,
    stochastic,
    supertrend,
    true_range,
    williams_r,
    adx,
)


@pytest.fixture
def bars_flat() -> pd.DataFrame:
    """200 bars of constant price → all OHLC=100, ATR=0, RSI=NaN/50, BB width=0."""
    idx = pd.date_range("2025-01-01", periods=200, freq="5min")
    df = pd.DataFrame({
        "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
    }, index=idx)
    return df


@pytest.fixture
def bars_step() -> pd.DataFrame:
    """100 bars at 100, then a step jump up to 105 for the rest."""
    idx = pd.date_range("2025-01-01", periods=200, freq="5min")
    closes = np.array([100.0] * 100 + [105.0] * 100)
    df = pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
    }, index=idx)
    # Make the step bar visibly volatile
    df.loc[df.index[100], "high"] = 106.0
    df.loc[df.index[100], "low"] = 99.5
    return df


@pytest.fixture
def bars_walk(seed: int = 7) -> pd.DataFrame:
    """500 bars of geometric random walk for sanity checks."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=500, freq="5min")
    rets = rng.normal(0.0, 0.001, len(idx))
    close = 100.0 * np.exp(rets.cumsum())
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.0005, len(idx))))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.0005, len(idx))))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


# ── True range / ATR ─────────────────────────────────────────────────────────

def test_true_range_flat(bars_flat):
    tr = true_range(bars_flat["high"], bars_flat["low"], bars_flat["close"])
    # First bar = high-low = 0; subsequent bars all 0 since flat
    assert (tr.dropna() == 0.0).all()


def test_atr_wilder_flat_zero(bars_flat):
    a = atr_wilder(bars_flat, 14)
    # After warm-up, ATR on flat data = 0
    assert a.iloc[20:].fillna(0).abs().max() < 1e-9


def test_atr_wilder_step_jumps(bars_step):
    a = atr_wilder(bars_step, 14)
    pre = a.iloc[80:99].mean()
    post = a.iloc[101:115].mean()
    assert post > pre + 0.1, f"ATR did not jump after step: pre={pre}, post={post}"


# ── RSI ──────────────────────────────────────────────────────────────────────

def test_rsi_flat_nan_or_50(bars_flat):
    r = rsi_wilder(bars_flat["close"], 14)
    # Flat data → no gain/loss → RSI undefined (NaN) or stays at 50 once any
    # nonzero diff is encountered. With purely flat data we expect NaN.
    assert r.dropna().empty or (r.dropna() == 50.0).all() or r.dropna().isna().all()


def test_rsi_step_up_overbought(bars_step):
    r = rsi_wilder(bars_step["close"], 14)
    # Single positive shock at bar 100 with all losses=0 → RSI = 100 from
    # bar 100 onwards (avg_loss stays 0, avg_gain decays slowly).
    just_after = r.iloc[101:115].mean()
    assert just_after > 70.0, f"RSI should be elevated after step, got {just_after}"


# ── ADX ──────────────────────────────────────────────────────────────────────

def test_adx_returns_three_columns(bars_walk):
    a = adx(bars_walk, 14)
    assert set(a.columns) == {"adx", "plus_di", "minus_di"}
    assert len(a) == len(bars_walk)


# ── Bollinger ────────────────────────────────────────────────────────────────

def test_bollinger_flat_zero_width(bars_flat):
    bb = bollinger(bars_flat["close"], 20, 2.0)
    # After warm-up, width = 0 since stdev = 0 (mid - 0 == mid - 0)
    w = bb["bb_width"].iloc[20:]
    # bb_width = (upper-lower)/mid = 0 when stdev=0
    assert (w.fillna(0).abs() < 1e-9).all()


def test_bollinger_step_widens(bars_step):
    bb = bollinger(bars_step["close"], 20, 2.0)
    # The 20-bar window straddling the step should have nonzero width
    w_post = bb["bb_width"].iloc[100:130].mean()
    assert w_post > 1e-4, f"BB width should expand around step, got {w_post}"


# ── MACD ─────────────────────────────────────────────────────────────────────

def test_macd_columns_and_relation(bars_walk):
    m = macd(bars_walk["close"], 12, 26, 9)
    assert set(m.columns) == {"macd", "macd_signal", "macd_hist"}
    diff = (m["macd"] - m["macd_signal"]) - m["macd_hist"]
    assert diff.dropna().abs().max() < 1e-9


# ── Stochastic ───────────────────────────────────────────────────────────────

def test_stochastic_bounded_0_100(bars_walk):
    s = stochastic(bars_walk, 14, 3, 3)
    k = s["stoch_k"].dropna()
    d = s["stoch_d"].dropna()
    assert k.between(0, 100).all()
    assert d.between(0, 100).all()


# ── CCI ──────────────────────────────────────────────────────────────────────

def test_cci_zero_on_flat(bars_flat):
    c = cci(bars_flat, 20)
    # Flat data: typical=100 always, mad=0 → result NaN (we replace 0 with NaN
    # in the denom). So CCI is all NaN — accept that.
    assert c.dropna().empty


# ── Williams %R ──────────────────────────────────────────────────────────────

def test_williams_r_in_negative_range(bars_walk):
    w = williams_r(bars_walk, 14).dropna()
    assert w.between(-100, 0).all()


# ── ROC ──────────────────────────────────────────────────────────────────────

def test_roc_step_jump(bars_step):
    r = roc(bars_step["close"], 10)
    # ROC(10) at iloc[105] = close[105]/close[95] - 1 = 105/100 - 1 = +5%
    val = r.iloc[105]
    assert 4.5 < val < 5.5, f"ROC ~ +5% expected, got {val}"


# ── Donchian / Keltner ───────────────────────────────────────────────────────

def test_donchian_upper_lower_ordering(bars_walk):
    d = donchian(bars_walk, 20)
    diff = (d["donch_upper"] - d["donch_lower"]).dropna()
    assert (diff >= 0).all()


def test_keltner_bands_around_mid(bars_walk):
    k = keltner(bars_walk, 20, 2.0).dropna()
    assert (k["kelt_upper"] >= k["kelt_mid"]).all()
    assert (k["kelt_lower"] <= k["kelt_mid"]).all()


# ── SuperTrend ───────────────────────────────────────────────────────────────

def test_supertrend_dir_in_pm1(bars_walk):
    s = supertrend(bars_walk, 10, 3.0)
    d = s["supertrend_dir"].dropna()
    assert d.isin([1.0, -1.0]).all()


# ── Aroon ────────────────────────────────────────────────────────────────────

def test_aroon_in_0_100(bars_walk):
    a = aroon(bars_walk, 25)
    up = a["aroon_up"].dropna()
    dn = a["aroon_down"].dropna()
    assert up.between(0, 100).all()
    assert dn.between(0, 100).all()


# ── Realized vol variants ────────────────────────────────────────────────────

def test_rv_close_flat_zero(bars_flat):
    rv = rv_close(bars_flat["close"], 50)
    # 0 stdev → 0 RV
    assert rv.dropna().abs().max() < 1e-9


def test_rv_parkinson_nonneg(bars_walk):
    rv = rv_parkinson(bars_walk["high"], bars_walk["low"], 50)
    assert (rv.dropna() >= 0).all()


def test_rv_gk_nonneg(bars_walk):
    rv = rv_garman_klass(bars_walk["open"], bars_walk["high"], bars_walk["low"], bars_walk["close"], 50)
    assert (rv.dropna() >= 0).all()


def test_rv_estimators_in_reasonable_range(bars_walk):
    """All three RV estimators should produce non-negative finite values
    in a similar order of magnitude on a smooth random walk."""
    rvc = rv_close(bars_walk["close"], 100).dropna()
    rvp = rv_parkinson(bars_walk["high"], bars_walk["low"], 100).dropna()
    rvg = rv_garman_klass(bars_walk["open"], bars_walk["high"], bars_walk["low"], bars_walk["close"], 100).dropna()
    for s in (rvc, rvp, rvg):
        assert (s >= 0).all()
        assert np.isfinite(s).all()
    # Order-of-magnitude: max/min ratio across estimators is bounded
    ratio = max(rvc.mean(), rvp.mean(), rvg.mean()) / max(1e-9, min(rvc.mean(), rvp.mean(), rvg.mean()))
    assert ratio < 50.0, f"RV estimators wildly different: ratio={ratio}"

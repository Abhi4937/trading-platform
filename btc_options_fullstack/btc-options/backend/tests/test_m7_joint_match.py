"""Tests for the M7 joint delta+price strike picker.

Cases:
  a. Skewed IV chain → joint picker selects different put_strike than delta-only,
     and resulting |call_mark - put_mark| / mean_mark <= 0.15.
  b. Joint picker returns None when cheapest pair's price gap exceeds tolerance.
  c. Straddle (target_delta=0.50) → joint picker matches existing pick_strikes.
  d. Low-delta target (0.05) with sparse OTM strikes → still finds a pair
     via adaptive delta_tol widening.
  e. Empty chain → None.
  f. Chain missing one option type → None.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.analytics.m7_batch_backtester import pick_strikes
from app.analytics.m7_strike_picker_joint import (
    JOINT_DELTA_TOL,
    JOINT_PRICE_TOL_PCT,
    _effective_delta_tol,
    pick_strikes_joint,
)
from app.core.greeks import compute_greeks


def _bs_price(S: float, K: float, T: float, sigma: float, opt: str) -> float:
    """Black-Scholes price using project's compute_greeks (r=0)."""
    g = compute_greeks(S, K, T, 0.0, sigma, opt)
    return float(g.price_bs)


def _make_chain(strikes: list[int], ce_ivs: list[float], pe_ivs: list[float],
                S: float, T: float) -> pd.DataFrame:
    """Build a tidy long-form chain df with columns [strike, opt_type, mark_close].
    ce_ivs / pe_ivs are decimals (e.g. 0.60 = 60%). Marks priced via BS at r=0.
    """
    assert len(strikes) == len(ce_ivs) == len(pe_ivs)
    rows = []
    for K, civ, piv in zip(strikes, ce_ivs, pe_ivs):
        rows.append({"strike": int(K), "opt_type": "CE",
                     "mark_close": _bs_price(S, K, T, civ, "call")})
        rows.append({"strike": int(K), "opt_type": "PE",
                     "mark_close": _bs_price(S, K, T, piv, "put")})
    return pd.DataFrame(rows)


# ── Case a — skewed chain, joint picker improves price match ──────────────────

def test_joint_picker_picks_different_put_under_skew():
    """Put IV skewed higher than call IV → delta-only picks an equally-spaced
    pair with unequal premiums. Joint picker walks the PE strike OTM so the
    two leg premiums match within tolerance."""
    S = 100_000.0
    T = 7.0 / 365.0
    strikes = [88_000, 90_000, 92_000, 94_000, 96_000, 98_000, 100_000,
               102_000, 104_000, 106_000, 108_000, 110_000, 112_000]
    # Flat-ish CE IV around 0.60, PE IV skewed up to 0.80 (negative-skew BTC profile).
    ce_ivs = [0.60] * len(strikes)
    pe_ivs = [0.80, 0.78, 0.76, 0.74, 0.72, 0.70, 0.68,
              0.66, 0.64, 0.62, 0.60, 0.58, 0.56]
    chain = _make_chain(strikes, ce_ivs, pe_ivs, S, T)

    target = 0.20
    base = pick_strikes(chain, S, T, target)
    joint = pick_strikes_joint(chain, S, T, target,
                               delta_tol=JOINT_DELTA_TOL,
                               price_tol_pct=JOINT_PRICE_TOL_PCT)

    assert base is not None
    assert joint is not None
    # Joint should walk the put strike further OTM (lower K, since |put_delta| ↓
    # as K ↓ for PE) compared to delta-only OR walk the call strike to find a
    # premium match — at minimum the joint pair MUST balance the premiums.
    cm = joint["call_mark"]
    pm = joint["put_mark"]
    mean_mark = (cm + pm) / 2.0
    assert mean_mark > 0
    diff_pct = abs(cm - pm) / mean_mark
    assert diff_pct <= JOINT_PRICE_TOL_PCT, f"price diff {diff_pct:.3f} > tol"

    # And at least one strike differs from delta-only pick (the picker can
    # walk either leg to match premiums; under this skew shape it's typically
    # the PE leg that moves).
    assert (joint["call_strike"] != base["call_strike"]
            or joint["put_strike"] != base["put_strike"]), (
        "joint picker produced identical strikes to delta-only under skew")


# ── Case b — no joint pair fits → returns None ────────────────────────────────

def test_joint_picker_returns_none_when_price_gap_too_wide():
    """Construct a chain where the cheapest joint pair within the delta window
    still has >25% price gap → picker rejects and returns None."""
    S = 100_000.0
    T = 7.0 / 365.0
    # Two strikes only, jammed with extreme IV gap so PE >> CE at every strike.
    strikes = [90_000, 110_000]
    ce_ivs = [0.30, 0.30]
    pe_ivs = [1.50, 1.50]
    chain = _make_chain(strikes, ce_ivs, pe_ivs, S, T)

    res = pick_strikes_joint(chain, S, T, target_delta=0.20,
                             delta_tol=0.50,  # wide enough that both strikes qualify
                             price_tol_pct=0.05)  # tight gap requirement
    assert res is None


# ── Case c — straddle: joint == delta-only ────────────────────────────────────

def test_joint_picker_straddle_matches_delta_picker():
    """target_delta >= 0.495 → joint picker picks the ATM strike, same as
    existing pick_strikes()."""
    S = 100_000.0
    T = 7.0 / 365.0
    strikes = [94_000, 96_000, 98_000, 100_000, 102_000, 104_000, 106_000]
    ivs = [0.60] * len(strikes)
    chain = _make_chain(strikes, ivs, ivs, S, T)

    base = pick_strikes(chain, S, T, target_delta=0.50)
    joint = pick_strikes_joint(chain, S, T, target_delta=0.50)
    assert base is not None
    assert joint is not None
    assert joint["call_strike"] == base["call_strike"]
    assert joint["put_strike"] == base["put_strike"]
    assert joint["call_strike"] == joint["put_strike"]  # true straddle


# ── Case d — low-delta target with adaptive widening ──────────────────────────

def test_effective_delta_tol_widens_at_low_delta():
    """Verify the adaptive-widening rule directly: target_delta <= 0.10 →
    effective tol = max(delta_tol, 0.5 * target). Above 0.10 → pass-through."""
    # Low-delta: widens up
    assert _effective_delta_tol(0.05, 0.01) == pytest.approx(0.025)
    assert _effective_delta_tol(0.05, 0.05) == pytest.approx(0.05)
    assert _effective_delta_tol(0.10, 0.02) == pytest.approx(0.05)
    # Above 0.10: no widening
    assert _effective_delta_tol(0.20, 0.05) == pytest.approx(0.05)
    assert _effective_delta_tol(0.50, 0.05) == pytest.approx(0.05)


def test_joint_picker_low_delta_adaptive_widening_finds_pair():
    """target_delta=0.05 with sigma=0.50, 60-day DTE, dense wide-strike grid.
    Both CE OTM wing (K=140-145k) and PE OTM wing (K=70-75k) have strikes
    that land near |delta|=0.05. Adaptive widening (tol=max(0.01, 0.025)
    = 0.025) keeps both pools populated.

    Premium tolerance set permissive (50%) because flat-IV OTM wings have
    a residual premium asymmetry independent of delta — this test verifies
    the search PATH works at low delta, not premium-balance correctness."""
    S = 100_000.0
    T = 60.0 / 365.0
    strikes = [60_000, 65_000, 70_000, 75_000, 80_000, 85_000, 90_000, 95_000,
               100_000, 105_000, 110_000, 115_000, 120_000, 125_000, 130_000,
               135_000, 140_000, 145_000, 150_000]
    ivs = [0.50] * len(strikes)
    chain = _make_chain(strikes, ivs, ivs, S, T)

    res = pick_strikes_joint(chain, S, T, target_delta=0.05,
                             delta_tol=0.01,  # tiny — adaptive widens to 0.025
                             price_tol_pct=0.50)
    assert res is not None, ("adaptive widening should let the picker find "
                              "a low-delta pair when target ≤ 0.10")
    # Effective tol = max(0.01, 0.5 * 0.05) = 0.025 → pool window [0.025, 0.075]
    assert 0.025 - 1e-6 <= abs(res["call_delta"]) <= 0.075 + 1e-6
    assert 0.025 - 1e-6 <= abs(res["put_delta"]) <= 0.075 + 1e-6


# ── Case e — empty chain → None ───────────────────────────────────────────────

def test_joint_picker_empty_chain_returns_none():
    chain = pd.DataFrame(columns=["strike", "opt_type", "mark_close"])
    assert pick_strikes_joint(chain, 100_000.0, 7.0 / 365.0, 0.20) is None


# ── Case f — single-side chain → None ─────────────────────────────────────────

def test_joint_picker_only_calls_returns_none():
    chain = pd.DataFrame([
        {"strike": 100_000, "opt_type": "CE", "mark_close": 500.0},
        {"strike": 102_000, "opt_type": "CE", "mark_close": 400.0},
    ])
    assert pick_strikes_joint(chain, 100_000.0, 7.0 / 365.0, 0.20) is None


def test_joint_picker_only_puts_returns_none():
    chain = pd.DataFrame([
        {"strike": 98_000, "opt_type": "PE", "mark_close": 500.0},
        {"strike": 96_000, "opt_type": "PE", "mark_close": 400.0},
    ])
    assert pick_strikes_joint(chain, 100_000.0, 7.0 / 365.0, 0.20) is None


# ── Case g — low-premium floor forces fallback ────────────────────────────────

def test_joint_picker_low_premium_floor():
    """When the mean leg premium falls below $1.00, the price-tolerance ratio
    is numerically unstable (cents of mark-noise become percent-level signal).
    The picker must short-circuit to None so the caller falls back to the
    delta-only picker."""
    # Build a chain with sub-$1 marks across the board. The strangle path's
    # cartesian search will land on the cheapest pair; both legs < $1, mean
    # < $1, so the floor must kick in BEFORE the price-tolerance comparison.
    rows = [
        {"strike": 80_000, "opt_type": "CE", "mark_close": 0.40},
        {"strike": 80_000, "opt_type": "PE", "mark_close": 0.20},
        {"strike": 120_000, "opt_type": "CE", "mark_close": 0.30},
        {"strike": 120_000, "opt_type": "PE", "mark_close": 0.10},
    ]
    chain = pd.DataFrame(rows)
    # Wide delta tolerance + very loose price tolerance so the only thing
    # that can reject the pair is the mean-mark floor.
    res = pick_strikes_joint(chain, 100_000.0, 7.0 / 365.0,
                             target_delta=0.10,
                             delta_tol=0.5,
                             price_tol_pct=1.0)
    assert res is None, ("low-premium floor (<$1 mean mark) must force "
                          "fallback to delta-only picker")


# ── Case h — capital_sl_pct rule predicate produces SQL ───────────────────────

def test_exit_rule_predicate_includes_capital_sl():
    """capital_sl_pct should produce a SQL fragment that fires when the
    LOSS (-pnl_after_slip) reaches pct% of margin_used_usd_at_entry."""
    from app.api.m7_results import _exit_rule_sql_predicate
    rule = {"capital_sl_pct": 15}
    sql = _exit_rule_sql_predicate(rule)
    assert "margin_used_usd_at_entry" in sql, "capital_sl must reference margin"
    assert "-t.margin_used_usd_at_entry * 0.15" in sql, (
        f"capital_sl must compare against -margin × 0.15; got: {sql}")
    # Empty rule still returns empty
    assert _exit_rule_sql_predicate({}) == ""
    # Combined with premium_sl: both predicates joined with OR
    combo = _exit_rule_sql_predicate({"capital_sl_pct": 10, "premium_sl_pct": 50})
    assert " OR " in combo
    assert "margin_used_usd_at_entry" in combo
    assert "call_entry_mark" in combo


# ── Case i — _rule_variants is dataset-aware ──────────────────────────────────

def test_rule_variants_dataset_aware():
    """delta_match has 105 variants (3 SLs × 35 sub-variants);
    price_match adds 138 hybrid variants for 243 total:
      - 3 standalone capital_sl baselines
      - 9 cap_sl × premium_sl 2-way hybrids
      - 126 cap_sl × premium_sl × exit_hr 3-way hybrids
    """
    from app.api.m7_best_combo import _rule_variants
    delta_rules = _rule_variants("delta_match")
    price_rules = _rule_variants("price_match")
    assert len(delta_rules) == 105, f"delta_match: expected 105, got {len(delta_rules)}"
    assert len(price_rules) == 243, f"price_match: expected 243, got {len(price_rules)}"
    # 3 standalone capital_sl baselines
    standalone = [r for r in price_rules if r[0].startswith("cap") and "_baseline" in r[0]]
    assert len(standalone) == 3
    assert {r[0] for r in standalone} == {"cap10_baseline", "cap15_baseline", "cap20_baseline"}
    # 9 cap_sl × premium_sl 2-way hybrids
    two_way = [r for r in price_rules
               if r[0].startswith("sl") and "_cap" in r[0] and "_exit_hr_" not in r[0]]
    assert len(two_way) == 9
    # Each 2-way rule must have both keys
    for label, rd in two_way:
        assert "premium_sl_pct" in rd
        assert "capital_sl_pct" in rd
        assert "fixed_exit_hour_ist" not in rd
    # 126 cap_sl × premium_sl × exit_hr 3-way hybrids
    three_way = [r for r in price_rules
                 if r[0].startswith("sl") and "_cap" in r[0] and "_exit_hr_" in r[0]]
    assert len(three_way) == 126
    for label, rd in three_way:
        assert "premium_sl_pct" in rd
        assert "capital_sl_pct" in rd
        assert "fixed_exit_hour_ist" in rd
    # No max_profit / margin_target × cap_sl combos (excluded per user spec)
    for label, rd in price_rules:
        if "_cap" in label or label.startswith("cap"):
            assert "max_profit_pct" not in rd, f"unexpected max_profit in {label}"
            assert "margin_target_pct" not in rd, f"unexpected margin_target in {label}"


# ── Case j — rule_category tagging ────────────────────────────────────────────

def test_rule_category_tagging():
    """Every rule must map to a known category for the filter chip."""
    from app.api.m7_best_combo import _rule_variants, _rule_category
    price_rules = _rule_variants("price_match")
    categories = {}
    for label, _rd in price_rules:
        cat = _rule_category(label)
        categories.setdefault(cat, 0)
        categories[cat] += 1
        assert cat != "unknown", f"rule {label} has no category"
    # Expected counts for price_match (243 total)
    expected = {
        "single_baseline":              3,    # sl50/75/100 _baseline
        "single_max_profit":            30,   # 3 SLs × 10 pcts
        "single_margin_target":         30,   # 3 SLs × 10 pcts
        "single_exit_hr":               42,   # 3 SLs × 14 hours
        "single_capital_sl_standalone": 3,    # cap10/15/20 _baseline
        "hybrid_2way_sl":               9,    # 3 cap × 3 sl
        "hybrid_3way":                  126,  # 3 cap × 3 sl × 14 hr
    }
    for cat, n in expected.items():
        assert categories.get(cat, 0) == n, (
            f"category {cat}: expected {n}, got {categories.get(cat, 0)}")
    assert sum(expected.values()) == 243

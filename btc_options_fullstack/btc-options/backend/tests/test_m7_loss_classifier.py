"""Tests for `_classify_loss_cause` — the Chunk 1 loss-cause classifier.

Exercises the priority order, the per-category predicates, and the
total-count invariant. All tests use synthetic in-memory DataFrames; no
parquet IO required.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.api.m7_results import _classify_loss_cause


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_trade_row(
    *,
    is_win: bool,
    leg_winner: str = "both",
    exit_reason: str = "hard_cap",
    call_pnl: float = 0.0,
    put_pnl: float = 0.0,
    call_d: float = 0.30,
    put_d: float = -0.30,
    rel_min_mtm: float = 0.5,
    rel_max_mtm: float = 0.5,
    net_delta_min: float = 0.0,
    spot_in: float = 100_000.0,
    spot_min: float = 100_000.0,
    entry_iv: float = 0.50,
    atm_iv_min: float = 0.50,
    atm_iv_max_w: float = 0.50,
    atr_pct: float = 1.5,
    max_mtm: float = 0.0,
    exit_mtm: float = 0.0,
    credit: float = 200.0,
) -> dict:
    return {
        "is_win": is_win, "leg_winner": leg_winner, "exit_reason": exit_reason,
        "call_leg_pnl_usd": call_pnl, "put_leg_pnl_usd": put_pnl,
        "call_entry_delta": call_d, "put_entry_delta": put_d,
        "rel_time_min_mtm": rel_min_mtm, "rel_time_max_mtm": rel_max_mtm,
        "net_delta_at_min_mtm": net_delta_min,
        "spot_at_entry": spot_in, "spot_at_min_mtm": spot_min,
        "entry_atm_iv": entry_iv, "atm_iv_at_min_mtm": atm_iv_min,
        "max_atm_iv_in_window": atm_iv_max_w,
        "ctx_atr_pct_4h": atr_pct,
        "max_mtm_usd": max_mtm, "exit_mtm_usd": exit_mtm,
        "credit_usd": credit,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_winners_have_no_cause():
    df = pd.DataFrame([_make_trade_row(is_win=True)])
    _classify_loss_cause(df)
    assert df["loss_cause"].iloc[0] is None
    # All share-* indicator cols should be 0 for winners
    for k in ("_is_directional", "_is_vol_expansion", "_is_path_dependent",
              "_is_gamma_squeeze", "_is_skew_flip", "_is_unclassified"):
        assert df[k].iloc[0] == 0.0


def test_directional_cause_fires():
    # Spot moved 3%, ATR is 1% → 3% > 1.5 × 1% → directional
    row = _make_trade_row(
        is_win=False, leg_winner="call_only",
        spot_in=100_000.0, spot_min=97_000.0, atr_pct=1.0,
    )
    df = pd.DataFrame([row])
    _classify_loss_cause(df)
    assert df["loss_cause"].iloc[0] == "directional"


def test_vol_expansion_cause_fires():
    # IV at trough = 60% vs entry 50% (20% jump); peak in window = 65%
    row = _make_trade_row(
        is_win=False, leg_winner="neither",
        entry_iv=0.50, atm_iv_min=0.60, atm_iv_max_w=0.65,
        # Don't trigger directional: spot move 0
        spot_in=100_000.0, spot_min=100_000.0,
    )
    df = pd.DataFrame([row])
    _classify_loss_cause(df)
    assert df["loss_cause"].iloc[0] == "vol_expansion"


def test_gamma_squeeze_cause_fires():
    # SL fired at 10% of trade, |net_delta_at_min| > 2× max(|entry_d|)
    row = _make_trade_row(
        is_win=False, leg_winner="call_only",
        exit_reason="rule_trigger", rel_min_mtm=0.10,
        call_d=0.30, put_d=-0.30, net_delta_min=0.80,  # 0.80 > 2*0.30
        # Don't trigger vol_expansion or directional
        entry_iv=0.50, atm_iv_min=0.50, atm_iv_max_w=0.50,
        spot_in=100_000.0, spot_min=100_000.0, atr_pct=1.5,
    )
    df = pd.DataFrame([row])
    _classify_loss_cause(df)
    assert df["loss_cause"].iloc[0] == "gamma_squeeze"


def test_path_dependent_cause_fires():
    # Got to 35% of credit profit before midpoint, ended at -$50 net
    row = _make_trade_row(
        is_win=False, leg_winner="both",
        max_mtm=70.0,    # 35% of credit=200
        exit_mtm=-50.0,
        rel_max_mtm=0.40,
        credit=200.0,
        # Skip earlier predicates: no IV jump, no ATR breach, no early SL
        entry_iv=0.50, atm_iv_min=0.50, atm_iv_max_w=0.50,
        spot_in=100_000.0, spot_min=100_000.0, atr_pct=1.5,
        exit_reason="hard_cap",
    )
    df = pd.DataFrame([row])
    _classify_loss_cause(df)
    assert df["loss_cause"].iloc[0] == "path_dependent"


def test_skew_flip_cause_fires():
    # Both legs lost (leg_winner='neither'); imbalance > 50%; direction flipped.
    # call_pnl = -10, put_pnl = -100 → |diff|/min = 90/10 = 9.0 > 0.5
    # entry: call_d+put_d = +0.50 (>0); at min net_delta = -0.80 → flipped
    row = _make_trade_row(
        is_win=False, leg_winner="neither",
        call_pnl=-10.0, put_pnl=-100.0,
        call_d=0.40, put_d=0.10,    # sum > 0
        net_delta_min=-0.80,         # flipped
        # Defeat earlier predicates: tame IV, tame ATR, no early SL
        exit_reason="hard_cap", rel_min_mtm=0.50,
        entry_iv=0.50, atm_iv_min=0.50, atm_iv_max_w=0.50,
        spot_in=100_000.0, spot_min=100_000.0, atr_pct=10.0,
    )
    df = pd.DataFrame([row])
    _classify_loss_cause(df)
    assert df["loss_cause"].iloc[0] == "skew_flip"


def test_unclassified_when_no_predicate_fires():
    # Loser with bland numbers — none of the 5 predicates fire.
    row = _make_trade_row(
        is_win=False, leg_winner="call_only",
        # No IV jump, no spot move, no early SL, never went into profit
        entry_iv=0.50, atm_iv_min=0.50, atm_iv_max_w=0.50,
        spot_in=100_000.0, spot_min=100_000.0, atr_pct=1.5,
        exit_reason="hard_cap", rel_min_mtm=0.50,
        max_mtm=10.0, exit_mtm=-5.0, credit=200.0,  # max_mtm only 5% of credit
    )
    df = pd.DataFrame([row])
    _classify_loss_cause(df)
    assert df["loss_cause"].iloc[0] == "unclassified"


def test_priority_order_skew_flip_beats_vol_expansion():
    # A trade that satisfies BOTH skew_flip and vol_expansion → must label
    # skew_flip (priority order #1).
    row = _make_trade_row(
        is_win=False, leg_winner="neither",
        call_pnl=-5.0, put_pnl=-200.0,
        call_d=0.30, put_d=0.10, net_delta_min=-0.50,
        # Also satisfy vol_expansion: IV jumped from 50% to 65%
        entry_iv=0.50, atm_iv_min=0.65, atm_iv_max_w=0.70,
        spot_in=100_000.0, spot_min=100_000.0, atr_pct=10.0,
    )
    df = pd.DataFrame([row])
    _classify_loss_cause(df)
    assert df["loss_cause"].iloc[0] == "skew_flip"


def test_priority_order_vol_expansion_beats_directional():
    # A loser that satisfies BOTH vol_expansion AND directional →
    # vol_expansion wins (priority #3 over #4).
    row = _make_trade_row(
        is_win=False, leg_winner="call_only",
        # Vol jump
        entry_iv=0.50, atm_iv_min=0.62, atm_iv_max_w=0.70,
        # Directional move too
        spot_in=100_000.0, spot_min=97_000.0, atr_pct=1.0,
        # Defeat skew_flip and gamma_squeeze
        call_pnl=0.0, put_pnl=-100.0,
        exit_reason="hard_cap", rel_min_mtm=0.50,
    )
    df = pd.DataFrame([row])
    _classify_loss_cause(df)
    assert df["loss_cause"].iloc[0] == "vol_expansion"


def test_total_count_invariant_across_six_synthetic_losers():
    rows = [
        # 1 winner
        _make_trade_row(is_win=True),
        # 1 of each cause
        _make_trade_row(is_win=False, leg_winner="neither",
                        call_pnl=-5.0, put_pnl=-200.0,
                        call_d=0.30, put_d=0.10, net_delta_min=-0.50),  # skew_flip
        _make_trade_row(is_win=False, leg_winner="call_only",
                        exit_reason="rule_trigger", rel_min_mtm=0.10,
                        call_d=0.30, put_d=-0.30, net_delta_min=0.80),  # gamma_squeeze
        _make_trade_row(is_win=False, leg_winner="neither",
                        entry_iv=0.50, atm_iv_min=0.60, atm_iv_max_w=0.65,
                        spot_in=100_000.0, spot_min=100_000.0),  # vol_expansion
        _make_trade_row(is_win=False, leg_winner="call_only",
                        spot_in=100_000.0, spot_min=97_000.0, atr_pct=1.0),  # directional
        _make_trade_row(is_win=False, leg_winner="both",
                        max_mtm=70.0, exit_mtm=-50.0,
                        rel_max_mtm=0.40, credit=200.0),  # path_dependent
        _make_trade_row(is_win=False, leg_winner="call_only",  # unclassified
                        max_mtm=5.0, exit_mtm=-5.0,
                        atr_pct=10.0, credit=200.0),
    ]
    df = pd.DataFrame(rows)
    _classify_loss_cause(df)

    # All 6 losers classified, winner gets None
    assert df["loss_cause"].isna().sum() == 1
    assert df.loc[df["is_win"], "loss_cause"].isna().all()

    # Each cause appears exactly once
    cause_counts = df["loss_cause"].value_counts().to_dict()
    expected = {"skew_flip": 1, "gamma_squeeze": 1, "vol_expansion": 1,
                "directional": 1, "path_dependent": 1, "unclassified": 1}
    assert cause_counts == expected

    # share-* invariant: per-row indicator cols sum to 1 for losers, 0 for winners
    causes = ["directional", "vol_expansion", "path_dependent",
              "gamma_squeeze", "skew_flip", "unclassified"]
    indicator_sum = df[[f"_is_{c}" for c in causes]].sum(axis=1)
    assert (indicator_sum[df["is_win"]] == 0.0).all()
    assert (indicator_sum[~df["is_win"]] == 1.0).all()


def test_classifier_handles_empty_dataframe():
    df = pd.DataFrame()
    _classify_loss_cause(df)
    assert "loss_cause" in df.columns
    for c in ("directional", "vol_expansion", "path_dependent",
              "gamma_squeeze", "skew_flip", "unclassified"):
        assert f"_is_{c}" in df.columns


def test_classifier_handles_missing_optional_columns():
    # When `leg_winner` and per-leg pnl cols are missing (old paths schema),
    # the classifier should not crash and should mark everything unclassified
    # (or whatever the simpler predicates pick up).
    df = pd.DataFrame([{
        "is_win": False,
        "exit_reason": "hard_cap",
        "call_entry_delta": 0.30, "put_entry_delta": -0.30,
        "rel_time_min_mtm": 0.50, "rel_time_max_mtm": 0.50,
        "net_delta_at_min_mtm": 0.0,
        "spot_at_entry": 100_000.0, "spot_at_min_mtm": 100_000.0,
        "entry_atm_iv": 0.50, "atm_iv_at_min_mtm": 0.50,
        "max_atm_iv_in_window": 0.50,
        "ctx_atr_pct_4h": 1.5,
        "max_mtm_usd": 0.0, "exit_mtm_usd": -10.0, "credit_usd": 200.0,
    }])
    _classify_loss_cause(df)
    # Should be unclassified (no predicate fires on this bland row)
    assert df["loss_cause"].iloc[0] == "unclassified"

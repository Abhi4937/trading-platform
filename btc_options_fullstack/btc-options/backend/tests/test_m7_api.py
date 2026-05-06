"""Tests for M7 results API — exit-rule derivation and filter parsing.

These tests exercise pure helpers without requiring the full m7 parquets.
End-to-end DuckDB queries are smoke-tested separately once the backfill completes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.api.m7_results import (
    _add_entry_skew_columns,
    _apply_filters,
    _exit_rule_sql_predicate,
    _parse_exit_rule,
    _to_records,
)


def test_parse_exit_rule_empty_returns_empty_dict():
    assert _parse_exit_rule(None) == {}
    assert _parse_exit_rule("") == {}


def test_parse_exit_rule_basic():
    assert _parse_exit_rule('{"max_profit_pct":30}') == {"max_profit_pct": 30}
    assert _parse_exit_rule('{"max_profit_pct":30,"premium_sl_pct":50}') \
        == {"max_profit_pct": 30, "premium_sl_pct": 50}


def test_exit_rule_sql_predicate_max_profit():
    # max_profit_pct compares gross PnL post-entry-slippage against
    # credit_usd × pct/100 (matches what the trader sees on screen).
    pred = _exit_rule_sql_predicate({"max_profit_pct": 30})
    assert "p.gross_pnl_usd - t.entry_slippage_call_usd - t.entry_slippage_put_usd" in pred
    assert "t.credit_usd * 0.3" in pred
    assert "t.credit_usd > 0" in pred


def test_exit_rule_sql_predicate_combined_uses_or():
    pred = _exit_rule_sql_predicate({
        "max_profit_pct": 30, "margin_target_pct": 20, "premium_sl_pct": 50,
    })
    assert " OR " in pred
    # Both profit-side predicates compare gross-after-slip vs target
    assert "t.credit_usd * 0.3" in pred
    assert "t.margin_used_usd_at_entry * 0.2" in pred
    # premium SL: leg mark >= entry mark × 1.5
    assert "p.call_mark >= t.call_entry_mark * 1.5" in pred
    assert "p.put_mark >= t.put_entry_mark * 1.5" in pred


def test_exit_rule_sql_predicate_empty_returns_empty():
    assert _exit_rule_sql_predicate({}) == ""
    assert _exit_rule_sql_predicate({"fixed_exit_ts": 123}) == ""


def test_apply_filters_string_isin():
    df = pd.DataFrame({
        "delta_target": [0.05, 0.10, 0.30, 0.50],
        "is_straddle": [False, False, False, True],
        "expiry_date": ["2024-01-12", "2024-01-19", "2024-01-12", "2024-01-12"],
    })
    out = _apply_filters(df, {"delta_target": "0.10,0.30"})
    assert sorted(out["delta_target"].tolist()) == [0.10, 0.30]


def test_apply_filters_boolean():
    df = pd.DataFrame({
        "is_straddle": [False, True, False, True],
        "delta_target": [0.10, 0.50, 0.20, 0.50],
    })
    out = _apply_filters(df, {"is_straddle": "true"})
    assert all(out["is_straddle"])


def test_apply_filters_skips_unknown_column():
    df = pd.DataFrame({"foo": [1, 2]})
    out = _apply_filters(df, {"unknown_col": "x"})
    assert len(out) == 2


def test_to_records_handles_nan_and_trade_id_as_str():
    df = pd.DataFrame({
        "trade_id": [123456789012345, 987654321098765],
        "value": [1.0, float("nan")],
    })
    recs = _to_records(df)
    assert recs[0]["trade_id"] == "123456789012345"
    assert recs[0]["value"] == 1.0
    assert recs[1]["value"] is None


# ── Chunk 1 — leg attribution ────────────────────────────────────────────────

def _make_skew_fixture() -> pd.DataFrame:
    """Synthetic trades covering each skew direction so bucket assignments
    can be verified."""
    return pd.DataFrame({
        # Row 0: balanced — both legs at same Δ, same IV, same mark
        # Row 1: strong put skew — put has higher Δ, higher IV, richer premium
        # Row 2: strong call skew — call has higher Δ, higher IV, richer premium
        "call_entry_delta": [0.30, 0.20, 0.40],
        "put_entry_delta":  [-0.30, -0.40, -0.20],
        "call_entry_iv":    [0.50, 0.45, 0.55],
        "put_entry_iv":     [0.50, 0.55, 0.45],
        "call_entry_mark":  [200.0, 150.0, 300.0],
        "put_entry_mark":   [200.0, 300.0, 150.0],
    })


def test_add_entry_skew_columns_balanced_row():
    df = _make_skew_fixture()
    _add_entry_skew_columns(df)
    # Row 0: |0.30| − |−0.30| = 0; iv 0.50−0.50 = 0; mark 200−200 = 0
    assert abs(df.loc[0, "delta_skew"]) < 1e-9
    assert abs(df.loc[0, "iv_skew_pct"]) < 1e-9
    assert abs(df.loc[0, "premium_skew_usd"]) < 1e-9
    assert df.loc[0, "delta_skew_bucket"] == "balanced"
    assert df.loc[0, "iv_skew_bucket"] == "balanced"
    assert df.loc[0, "premium_skew_bucket"] == "balanced"


def test_add_entry_skew_columns_put_richer_row():
    df = _make_skew_fixture()
    _add_entry_skew_columns(df)
    # Row 1: put has higher abs delta + higher IV + richer premium
    assert df.loc[1, "delta_skew"] < 0           # |0.20| − |−0.40| = −0.20
    assert df.loc[1, "iv_skew_pct"] < 0          # 45 − 55 = −10
    assert df.loc[1, "premium_skew_usd"] < 0     # 150 − 300 = −150
    # Bucket labels reflect "put richer / put IV / put premium" direction
    assert df.loc[1, "delta_skew_bucket"] == "put_richer_strong"  # |Δ| < −0.05
    assert df.loc[1, "iv_skew_bucket"] == "put_iv_strong"         # < −5pp
    assert df.loc[1, "premium_skew_bucket"] == "put_premium_strong"  # < −0.30


def test_add_entry_skew_columns_call_richer_row():
    df = _make_skew_fixture()
    _add_entry_skew_columns(df)
    assert df.loc[2, "delta_skew"] > 0
    assert df.loc[2, "iv_skew_pct"] > 0
    assert df.loc[2, "premium_skew_usd"] > 0
    assert df.loc[2, "delta_skew_bucket"] == "call_richer_strong"
    assert df.loc[2, "iv_skew_bucket"] == "call_iv_strong"
    assert df.loc[2, "premium_skew_bucket"] == "call_premium_strong"


def test_add_entry_skew_columns_missing_inputs_is_noop():
    """If the per-leg cols aren't present (older parquet), the helper must
    be a no-op so callers don't crash."""
    df = pd.DataFrame({"foo": [1, 2]})
    _add_entry_skew_columns(df)
    # No skew cols added; foo unchanged
    assert "delta_skew" not in df.columns
    assert "iv_skew_pct" not in df.columns
    assert df["foo"].tolist() == [1, 2]


def test_apply_filters_recognizes_leg_attribution_columns():
    """The new skew/leg_winner filter columns must be honored by _apply_filters
    so the dashboard's chips actually narrow the dataset."""
    df = pd.DataFrame({
        "iv_skew_bucket":      ["balanced", "call_iv", "put_iv_strong"],
        "delta_skew_bucket":   ["balanced", "call_richer", "put_richer"],
        "premium_skew_bucket": ["balanced", "call_premium", "put_premium"],
        "leg_winner":          ["both", "call_only", "neither"],
    })
    assert len(_apply_filters(df, {"iv_skew_bucket": "call_iv"})) == 1
    assert len(_apply_filters(df, {"delta_skew_bucket": "balanced,call_richer"})) == 2
    assert len(_apply_filters(df, {"leg_winner": "neither"})) == 1

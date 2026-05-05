"""Tests for M7 results API — exit-rule derivation and filter parsing.

These tests exercise pure helpers without requiring the full m7 parquets.
End-to-end DuckDB queries are smoke-tested separately once the backfill completes.
"""
from __future__ import annotations

import pandas as pd

from app.api.m7_results import (
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
    pred = _exit_rule_sql_predicate({"max_profit_pct": 30})
    assert "pnl_pct_of_credit >= 30" in pred


def test_exit_rule_sql_predicate_combined_uses_or():
    pred = _exit_rule_sql_predicate({
        "max_profit_pct": 30, "margin_target_pct": 20, "premium_sl_pct": 50,
    })
    assert " OR " in pred
    assert "pnl_pct_of_credit >= 30" in pred
    assert "pnl_pct_of_margin >= 20" in pred
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

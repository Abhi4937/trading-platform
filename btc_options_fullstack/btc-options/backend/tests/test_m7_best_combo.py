"""Tests for M7 best-combo per IV band — pure helpers operating on
synthetic DataFrames. No parquet IO; the FastAPI endpoint is exercised
separately once parquets are present.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.api import m7_best_combo as bc


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(*, friday: str, iv_band: str, expiry: str, delta: float,
         net: float, exit_offset_min: int, gross: float | None = None,
         credit: float = 200.0, margin: float = 150.0) -> dict:
    """One synthetic derived-trade row with the columns _compute_cell_metrics
    cares about. exit_ts / entry_ts_utc are encoded as a unix-pair so that
    `(exit_ts - entry_ts_utc) / 60` returns `exit_offset_min`.
    """
    entry = 1_700_000_000
    exit_ts = entry + exit_offset_min * 60
    g = gross if gross is not None else net
    return {
        "trade_id": f"{friday}-{iv_band}-{expiry}-{delta}",
        "friday_date_ist": friday,
        "entry_atm_iv_band": iv_band,
        "expiry_bucket": expiry,
        "delta_target": float(delta),
        "entry_ts_utc": float(entry),
        "exit_ts": float(exit_ts),
        "exit_reason": "rule_trigger" if g != net else "hard_cap",
        "is_win": net > 0,
        "net_pnl_estimate_usd": float(net),
        "gross_pnl_usd": float(g),
        "credit_usd": float(credit),
        "margin_used_usd_at_entry": float(margin),
        "pct_return_on_credit": float(net) / float(credit) * 100.0,
        "pct_return_on_margin": float(net) / float(margin) * 100.0,
        "exit_mtm_usd": float(net),
        "max_mtm_usd": max(float(net), 0.0),
        "min_mtm_usd": min(float(net), 0.0),
    }


def _make_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ── _rule_variants ────────────────────────────────────────────────────────────

def test_rule_variants_count_and_shape():
    variants = bc._rule_variants()
    # 1 baseline + 10 max_profit + 10 margin_target = 21
    assert len(variants) == 21
    labels = [v[0] for v in variants]
    assert labels[0] == "baseline_sl100"
    assert "max_profit_30" in labels
    assert "margin_target_75" in labels
    # Every variant has premium_sl_pct=100
    for label, rule in variants:
        assert rule["premium_sl_pct"] == 100


def test_rule_variants_no_dual_rule_combos():
    """No variant sets BOTH max_profit_pct AND margin_target_pct — they
    should be separate variants per the plan."""
    for label, rule in bc._rule_variants():
        has_mp = rule.get("max_profit_pct") is not None
        has_mg = rule.get("margin_target_pct") is not None
        assert not (has_mp and has_mg), \
            f"variant {label} sets both max_profit_pct and margin_target_pct"


# ── _compute_cell_metrics — exit time math ────────────────────────────────────

def test_cell_exit_time_means_split_winners_losers():
    sub = _make_df([
        _row(friday="2025-01-03", iv_band="30-40", expiry="weekly (7d)",
             delta=0.30, net=+50.0, exit_offset_min=600),   # winner @ 600m
        _row(friday="2025-01-10", iv_band="30-40", expiry="weekly (7d)",
             delta=0.30, net=+30.0, exit_offset_min=900),   # winner @ 900m
        _row(friday="2025-01-17", iv_band="30-40", expiry="weekly (7d)",
             delta=0.30, net=-40.0, exit_offset_min=300),   # loser  @ 300m
    ])
    m = bc._compute_cell_metrics(sub)
    # avg over all 3
    assert m["avg_exit_offset_minutes"] == pytest.approx((600 + 900 + 300) / 3, abs=0.1)
    # avg over winners
    assert m["avg_winner_exit_offset_minutes"] == pytest.approx((600 + 900) / 2, abs=0.1)
    # avg over loser (single)
    assert m["avg_loser_exit_offset_minutes"] == pytest.approx(300.0, abs=0.1)
    assert m["n_trades"] == 3


def test_cell_exit_time_null_when_no_losers():
    sub = _make_df([
        _row(friday="2025-01-03", iv_band="50-60", expiry="current (Sat)",
             delta=0.50, net=+10.0, exit_offset_min=500),
        _row(friday="2025-01-10", iv_band="50-60", expiry="current (Sat)",
             delta=0.50, net=+20.0, exit_offset_min=700),
    ])
    m = bc._compute_cell_metrics(sub)
    assert m["avg_loser_exit_offset_minutes"] is None
    assert m["avg_winner_exit_offset_minutes"] == pytest.approx(600.0, abs=0.1)


def test_cell_exit_time_null_when_no_winners():
    sub = _make_df([
        _row(friday="2025-01-03", iv_band="80-90", expiry="biweekly (14d)",
             delta=0.10, net=-15.0, exit_offset_min=200),
        _row(friday="2025-01-10", iv_band="80-90", expiry="biweekly (14d)",
             delta=0.10, net=-25.0, exit_offset_min=400),
    ])
    m = bc._compute_cell_metrics(sub)
    assert m["avg_winner_exit_offset_minutes"] is None
    assert m["avg_loser_exit_offset_minutes"] == pytest.approx(300.0, abs=0.1)


# ── _pick_best_per_band ───────────────────────────────────────────────────────

def _grid_row(iv_band: str, expiry: str, delta: float, rule: str,
              ret_credit: float, ret_margin: float, n: int = 10) -> dict:
    return {
        "iv_band": iv_band, "expiry_bucket": expiry,
        "delta_target": delta, "rule_label": rule, "rule": {},
        "avg_pct_return_on_credit": ret_credit,
        "avg_pct_return_on_margin": ret_margin,
        "n_trades": n,
    }


def test_pick_best_credit_and_margin_can_diverge():
    """Same IV band, different cells max each ranking — verify the picker
    selects independently per ranking."""
    grid = pd.DataFrame([
        _grid_row("30-40", "weekly (7d)",  0.30, "max_profit_30",
                  ret_credit=35.0, ret_margin=20.0),
        _grid_row("30-40", "current (Sat)", 0.50, "max_profit_50",
                  ret_credit=20.0, ret_margin=45.0),
        _grid_row("30-40", "biweekly (14d)", 0.15, "margin_target_25",
                  ret_credit=10.0, ret_margin=30.0),
    ])
    best_c = bc._pick_best_per_band(grid, "credit")
    best_m = bc._pick_best_per_band(grid, "margin")
    assert len(best_c) == 1 and best_c.iloc[0]["rule_label"] == "max_profit_30"
    assert len(best_m) == 1 and best_m.iloc[0]["rule_label"] == "max_profit_50"


def test_pick_best_one_row_per_iv_band():
    grid = pd.DataFrame([
        _grid_row("0-20",  "weekly (7d)", 0.30, "max_profit_30",
                  ret_credit=10.0, ret_margin=8.0),
        _grid_row("0-20",  "current (Sat)", 0.50, "max_profit_50",
                  ret_credit=12.0, ret_margin=11.0),
        _grid_row("30-40", "weekly (7d)", 0.30, "max_profit_30",
                  ret_credit=35.0, ret_margin=20.0),
        _grid_row("30-40", "current (Sat)", 0.50, "max_profit_50",
                  ret_credit=22.0, ret_margin=44.0),
    ])
    best_c = bc._pick_best_per_band(grid, "credit")
    assert sorted(best_c["iv_band"].tolist()) == ["0-20", "30-40"]
    # 0-20: max_profit_50 (12.0 > 10.0); 30-40: max_profit_30 (35.0 > 22.0)
    by_band = dict(zip(best_c["iv_band"], best_c["rule_label"]))
    assert by_band == {"0-20": "max_profit_50", "30-40": "max_profit_30"}


def test_pick_best_skips_band_with_all_null_metric():
    """If a band has no valid ranking metric, it's dropped (no row in best)."""
    grid = pd.DataFrame([
        _grid_row("0-20", "weekly (7d)", 0.30, "max_profit_30",
                  ret_credit=10.0, ret_margin=5.0),
        # 100+ band: only one cell, NaN credit metric
        {**_grid_row("100+", "current (Sat)", 0.50, "baseline_sl100",
                     ret_credit=0.0, ret_margin=0.0),
         "avg_pct_return_on_credit": float("nan")},
    ])
    best_c = bc._pick_best_per_band(grid, "credit")
    # "100+" excluded (NaN); "0-20" kept
    assert best_c["iv_band"].tolist() == ["0-20"]


def test_pick_best_handles_empty_grid():
    grid = pd.DataFrame()
    best = bc._pick_best_per_band(grid, "credit")
    assert best.empty


def test_pick_best_sorts_iv_bands_naturally():
    """Output rows ordered 0-20, 20-30, …, 90-100, 100+."""
    grid = pd.DataFrame([
        _grid_row("100+",  "current (Sat)", 0.50, "max_profit_30",
                  ret_credit=80.0, ret_margin=60.0),
        _grid_row("0-20",  "weekly (7d)",   0.30, "max_profit_30",
                  ret_credit=10.0, ret_margin=8.0),
        _grid_row("50-60", "biweekly (14d)", 0.50, "max_profit_50",
                  ret_credit=40.0, ret_margin=30.0),
    ])
    best = bc._pick_best_per_band(grid, "credit")
    assert best["iv_band"].tolist() == ["0-20", "50-60", "100+"]

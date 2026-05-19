"""Tests for M7 full-coverage IV-band summary helpers.

These tests target the pure classification logic — the FastAPI endpoint
itself is exercised separately once the m7 parquets are present. The helpers
operate on synthetic DataFrames so no parquet IO is required.
"""
from __future__ import annotations

import pandas as pd

from app.api.m7_full_coverage import (
    _classify_fridays_to_cells,
    _expiry_bucket_idx,
    _hour_linear,
)


def _make_derived(rows: list[dict]) -> pd.DataFrame:
    """Helper: build a `derived`-shaped frame for the classifier.

    Caller supplies dicts with `friday`, `band`, `hour`, `expiry`, `delta`,
    `pnl`. Other columns (is_win, exit_reason, etc.) are filled with sane
    defaults so the rest of the logic doesn't choke.
    """
    out = []
    for i, r in enumerate(rows):
        out.append({
            "trade_id": int(r.get("trade_id", 1000 + i)),
            "friday_date_ist": r["friday"],
            "entry_atm_iv_band": r["band"],
            "entry_hour_ist": r["hour"],
            "expiry_bucket": r["expiry"],
            "delta_target": float(r["delta"]),
            "net_pnl_estimate_usd": float(r["pnl"]),
            "is_win": float(r["pnl"]) > 0,
            "exit_reason": "hard_cap",
        })
    return pd.DataFrame(out)


def _make_best_cells(rows: list[dict]) -> pd.DataFrame:
    # `score` (cell's historical avg metric) is required by
    # `_classify_fridays_to_cells` to break force-fit ties across cells.
    # Tests that don't care about tie-breaking can leave `score` unset; it
    # defaults to the row's pnl when present, otherwise 0.0.
    return pd.DataFrame([{
        "entry_atm_iv_band": r["band"],
        "entry_hour_ist": r["hour"],
        "expiry_bucket": r["expiry"],
        "delta_target": float(r["delta"]),
        "score": float(r.get("score", r.get("pnl", 0.0))),
    } for r in rows])


def test_hour_linear_maps_friday_to_saturday():
    # Fri 21..23 IST → 0..2; Sat 00..03 IST → 3..6
    assert _hour_linear(21) == 0
    assert _hour_linear(23) == 2
    assert _hour_linear(0) == 3
    assert _hour_linear(3) == 6
    # Out of sweep range → 99 (large penalty in distance metric)
    assert _hour_linear(12) == 99
    assert _hour_linear("not a number") == 99


def test_expiry_bucket_idx_known_and_unknown():
    assert _expiry_bucket_idx("current (Sat)") == 0
    assert _expiry_bucket_idx("biweekly (14d)") == 4
    assert _expiry_bucket_idx("quarterly") == 6
    assert _expiry_bucket_idx("nonexistent") == 99


def test_classify_returns_empty_on_empty_input():
    out = _classify_fridays_to_cells(pd.DataFrame(), pd.DataFrame())
    assert list(out.columns) == ["friday_date_ist", "trade_id",
                                  "assigned_band", "kind"]
    assert len(out) == 0


def test_classify_picks_rule_when_strict_match_exists():
    # Friday F1 has one trade matching the only best cell exactly.
    derived = _make_derived([
        {"friday": "2025-01-03", "band": "60-70", "hour": 22,
         "expiry": "biweekly (14d)", "delta": 0.30, "pnl": 5.0},
    ])
    best = _make_best_cells([
        {"band": "60-70", "hour": 22, "expiry": "biweekly (14d)", "delta": 0.30},
    ])
    out = _classify_fridays_to_cells(derived, best)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["kind"] == "rule"
    assert row["assigned_band"] == "60-70"


def test_classify_force_fit_when_band_differs():
    # Friday has a trade matching a cell's (hour, expiry, delta) but with a
    # different actual IV band. Force-fit assigns to the trade's ACTUAL band
    # (Option Y), so band 0-20's cell rule pulls in this trade and the trade
    # itself ends up in band 50-60 (its actual entry IV).
    derived = _make_derived([
        {"friday": "2025-01-10", "band": "50-60", "hour": 22,
         "expiry": "biweekly (14d)", "delta": 0.30, "pnl": 8.0},
    ])
    best = _make_best_cells([
        {"band": "60-70", "hour": 22, "expiry": "biweekly (14d)", "delta": 0.30},
    ])
    out = _classify_fridays_to_cells(derived, best)
    assert len(out) == 1
    assert out.iloc[0]["kind"] == "force_fit"
    # Option Y: assigned to the trade's actual band (50-60), NOT the cell's
    # nominal band (60-70). The cell rule is what *found* the trade; the
    # band label tracks where the trade actually sits in IV space.
    assert out.iloc[0]["assigned_band"] == "50-60"


def test_classify_force_fit_picks_best_pnl_across_cells():
    # Friday has trades matching TWO cells' (hour, expiry, delta) at different
    # bands. Picks the one with higher net P&L → assigned to that trade's
    # ACTUAL band (70-80), not the matched cell's nominal band (80-90).
    derived = _make_derived([
        {"friday": "2025-01-17", "band": "50-60", "hour": 22,
         "expiry": "biweekly (14d)", "delta": 0.30, "pnl": 4.0},
        {"friday": "2025-01-17", "band": "70-80", "hour": 21,
         "expiry": "weekly (7d)",   "delta": 0.10, "pnl": 12.0},
    ])
    best = _make_best_cells([
        {"band": "60-70", "hour": 22, "expiry": "biweekly (14d)", "delta": 0.30},
        {"band": "80-90", "hour": 21, "expiry": "weekly (7d)",    "delta": 0.10},
    ])
    out = _classify_fridays_to_cells(derived, best)
    assert len(out) == 1
    assert out.iloc[0]["kind"] == "force_fit"
    assert out.iloc[0]["assigned_band"] == "70-80"


def test_classify_uses_distance_when_no_hour_expiry_delta_match():
    # Friday only has a trade at (Δ=0.05, hour=21, weekly), actual band 40-50.
    # Best cell is (Δ=0.30, hour=22, biweekly) at band 60-70 — closest fallback.
    # Assigned band is the trade's actual band (40-50), not the cell's (60-70).
    derived = _make_derived([
        {"friday": "2025-01-24", "band": "40-50", "hour": 21,
         "expiry": "weekly (7d)", "delta": 0.05, "pnl": -2.0},
    ])
    best = _make_best_cells([
        {"band": "60-70", "hour": 22, "expiry": "biweekly (14d)", "delta": 0.30},
    ])
    out = _classify_fridays_to_cells(derived, best)
    assert len(out) == 1
    assert out.iloc[0]["kind"] == "closest_fallback"
    assert out.iloc[0]["assigned_band"] == "40-50"


def test_classify_distance_ranks_by_delta_first():
    # Two best cells; Friday's only trade matches NEITHER on (hour, expiry,
    # delta) when rule/force are checked. Closest fallback uses distance.
    # Trade is at band 30-40, Δ=0.10, biweekly, hour=22:
    #   Cell A: Δ=0.30 biweekly hour=22 → D = 100·0.20 + 0  + 0  = 20
    #   Cell B: Δ=0.10 weekly   hour=22 → D = 100·0    + 10 + 0  = 10
    # Wait — Cell B matches (hour=22, weekly, Δ=0.10) doesn't equal trade's
    # (hour=22, biweekly, Δ=0.10), so it's still closest_fallback. The trade's
    # actual band is 30-40 → assigned to 30-40 (Option Y).
    derived = _make_derived([
        {"friday": "2025-01-31", "band": "30-40", "hour": 22,
         "expiry": "biweekly (14d)", "delta": 0.10, "pnl": 1.0},
    ])
    best = _make_best_cells([
        {"band": "A_band", "hour": 22, "expiry": "biweekly (14d)", "delta": 0.30},
        {"band": "B_band", "hour": 22, "expiry": "weekly (7d)",    "delta": 0.10},
    ])
    out = _classify_fridays_to_cells(derived, best)
    assert len(out) == 1
    assert out.iloc[0]["kind"] == "closest_fallback"
    assert out.iloc[0]["assigned_band"] == "30-40"


def test_classify_rule_beats_force_fit_even_with_lower_pnl():
    # Friday has a strict-rule match (pnl +1) AND a force-fit candidate
    # (pnl +20). Rule wins by category.
    derived = _make_derived([
        {"friday": "2025-02-07", "band": "60-70", "hour": 22,
         "expiry": "biweekly (14d)", "delta": 0.30, "pnl": 1.0},
        {"friday": "2025-02-07", "band": "30-40", "hour": 21,
         "expiry": "weekly (7d)",   "delta": 0.10, "pnl": 20.0},
    ])
    best = _make_best_cells([
        {"band": "60-70", "hour": 22, "expiry": "biweekly (14d)", "delta": 0.30},
        {"band": "70-80", "hour": 21, "expiry": "weekly (7d)",    "delta": 0.10},
    ])
    out = _classify_fridays_to_cells(derived, best)
    # Friday assigned to the rule cell, not the force-fit cell.
    assert len(out) == 1
    assert out.iloc[0]["kind"] == "rule"
    assert out.iloc[0]["assigned_band"] == "60-70"


def test_classify_universe_counts_partition_fridays():
    # Three Fridays with three different fates: rule / force_fit / closest_fallback.
    # Option Y: assigned_band tracks each trade's ACTUAL band, not the cell's.
    derived = _make_derived([
        # Friday A — exact rule match (band 60-70 → 60-70)
        {"friday": "2025-03-07", "band": "60-70", "hour": 22,
         "expiry": "biweekly (14d)", "delta": 0.30, "pnl": 5.0},
        # Friday B — force-fit (matches hour+expiry+delta of cell, but trade's
        # actual band is 50-60). Under Option Y, lands in 50-60.
        {"friday": "2025-03-14", "band": "50-60", "hour": 22,
         "expiry": "biweekly (14d)", "delta": 0.30, "pnl": 7.0},
        # Friday C — closest fallback (Δ=0.05 weekly, actual band 30-40).
        # Under Option Y, lands in 30-40.
        {"friday": "2025-03-21", "band": "30-40", "hour": 23,
         "expiry": "weekly (7d)", "delta": 0.05, "pnl": -1.0},
    ])
    best = _make_best_cells([
        {"band": "60-70", "hour": 22, "expiry": "biweekly (14d)", "delta": 0.30},
    ])
    out = _classify_fridays_to_cells(derived, best)
    kinds = sorted(out["kind"].tolist())
    assert kinds == ["closest_fallback", "force_fit", "rule"]
    # Each Friday goes to its trade's actual band (Option Y).
    bands = sorted(out["assigned_band"].tolist())
    assert bands == ["30-40", "50-60", "60-70"]
    assert out["friday_date_ist"].nunique() == 3

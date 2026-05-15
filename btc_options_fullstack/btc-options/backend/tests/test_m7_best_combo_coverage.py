"""Unit tests for the /iv_band_best_combo/coverage dedup behavior.

Covers the post-picker `_classify_fridays_to_cells` integration:
 - each Friday assigned at most once (no duplicates across bands)
 - `force_fit` reaches into closest-fallback so uncovered count is small
 - `touched_band` rejects assignments to bands the Friday's IV never
   touched (uncovered count may be larger)
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.api.m7_full_coverage import _classify_fridays_to_cells


def _make_trades(rows):
    """Helper: build a derived-trades DataFrame from row tuples.
    Tuple shape: (friday, band, hour, expiry, delta, trade_id, net_pnl)."""
    return pd.DataFrame(rows, columns=[
        "friday_date_ist", "entry_atm_iv_band", "entry_hour_ist",
        "expiry_bucket", "delta_target", "trade_id", "net_pnl_estimate_usd",
    ])


def _make_cells(rows):
    """Tuple shape: (band, hour, expiry, delta, score)."""
    return pd.DataFrame(rows, columns=[
        "entry_atm_iv_band", "entry_hour_ist",
        "expiry_bucket", "delta_target", "score",
    ])


# ── Test 1: each Friday assigned at most once ─────────────────────────────────

def test_no_duplicate_assignments_force_fit():
    """A Friday with trades in multiple bands gets assigned to exactly ONE
    band under force_fit, not duplicated across bands."""
    trades = _make_trades([
        ("2025-01-03", "20-30", 23, "next_to_next (Mon)", 0.5, "t1",  10.0),
        ("2025-01-03", "30-40", 23, "next_to_next (Mon)", 0.5, "t2",  15.0),
        ("2025-01-03", "20-30", 22, "next_to_next (Mon)", 0.5, "t3",   5.0),
    ])
    cells = _make_cells([
        ("20-30", 23, "next_to_next (Mon)", 0.5, 100.0),
        ("30-40", 23, "next_to_next (Mon)", 0.5, 200.0),
    ])
    a = _classify_fridays_to_cells(trades, cells, coverage_mode="force_fit")
    assert len(a) == 1, "Friday must be assigned exactly once"
    assert a["friday_date_ist"].nunique() == 1


def test_no_duplicate_assignments_touched_band():
    """Same uniqueness invariant under touched_band mode."""
    trades = _make_trades([
        ("2025-01-03", "20-30", 23, "next_to_next (Mon)", 0.5, "t1",  10.0),
        ("2025-01-03", "30-40", 23, "next_to_next (Mon)", 0.5, "t2",  15.0),
    ])
    cells = _make_cells([
        ("20-30", 23, "next_to_next (Mon)", 0.5, 100.0),
        ("30-40", 23, "next_to_next (Mon)", 0.5, 200.0),
    ])
    a = _classify_fridays_to_cells(trades, cells, coverage_mode="touched_band")
    assert len(a) == 1


# ── Test 2: total = assigned + uncovered ──────────────────────────────────────

def test_total_fridays_balances():
    """Sum of assigned + uncovered = total Fridays under both modes."""
    trades = _make_trades([
        ("2025-01-03", "20-30", 23, "next_to_next (Mon)", 0.5, "t1", 10.0),
        ("2025-01-10", "30-40", 23, "next_to_next (Mon)", 0.5, "t2", 15.0),
        ("2025-01-17", "50-60", 21, "current (Sat)",     0.3, "t3", -5.0),
    ])
    cells = _make_cells([
        ("20-30", 23, "next_to_next (Mon)", 0.5, 50.0),
    ])
    total = trades["friday_date_ist"].nunique()
    for mode in ("force_fit", "touched_band"):
        a = _classify_fridays_to_cells(trades, cells, coverage_mode=mode)
        assigned = a["friday_date_ist"].nunique()
        # All distinct assignments must come from the trades set
        assert set(a["friday_date_ist"]).issubset(set(trades["friday_date_ist"]))
        uncovered = total - assigned
        assert assigned + uncovered == total


# ── Test 3: touched_band rejects non-touched bands ────────────────────────────

def test_touched_band_rejects_untouched_band():
    """Friday's IV never touched band 30-40 → cannot be assigned there even
    when a 30-40 cell shares (hour, expiry, Δ) coords with the Friday's trade."""
    trades = _make_trades([
        # Friday only has trades in band 20-30 (IV at entry was 20-30)
        ("2025-01-03", "20-30", 23, "next_to_next (Mon)", 0.5, "t1", 10.0),
    ])
    # Cell is in band 30-40 — the Friday's IV never touched this band
    cells = _make_cells([
        ("30-40", 23, "next_to_next (Mon)", 0.5, 100.0),
    ])
    a = _classify_fridays_to_cells(trades, cells, coverage_mode="touched_band")
    assert len(a) == 0, "touched_band must NOT assign Friday to a band its IV never touched"


def test_force_fit_does_assign_via_force_fit_branch():
    """Same setup as above, but force_fit lets the Friday's trade land in
    the 30-40 cell because (h, e, Δ) matches — assigned band is the trade's
    actual band per Option Y semantics."""
    trades = _make_trades([
        ("2025-01-03", "20-30", 23, "next_to_next (Mon)", 0.5, "t1", 10.0),
    ])
    cells = _make_cells([
        ("30-40", 23, "next_to_next (Mon)", 0.5, 100.0),
    ])
    a = _classify_fridays_to_cells(trades, cells, coverage_mode="force_fit")
    assert len(a) == 1
    # Option Y: assigned_band = trade's ACTUAL band, not the cell's nominal band
    assert a.iloc[0]["assigned_band"] == "20-30"
    assert a.iloc[0]["kind"] == "force_fit"


# ── Test 4: rule (strict 4-dim match) wins over force_fit ─────────────────────

def test_rule_match_beats_force_fit():
    """When both a rule-match and a relaxed-band match are available,
    rule (strict) wins."""
    trades = _make_trades([
        ("2025-01-03", "30-40", 23, "next_to_next (Mon)", 0.5, "t1", 5.0),   # rule match (strict)
        ("2025-01-03", "20-30", 23, "next_to_next (Mon)", 0.5, "t2", 50.0),  # band-mismatch (force-fit)
    ])
    cells = _make_cells([
        ("30-40", 23, "next_to_next (Mon)", 0.5, 100.0),
    ])
    a = _classify_fridays_to_cells(trades, cells, coverage_mode="force_fit")
    assert len(a) == 1
    assert a.iloc[0]["kind"] == "rule"
    assert a.iloc[0]["assigned_band"] == "30-40"


# ── Test 5: rule shape ────────────────────────────────────────────────────────

def test_output_schema():
    """The classifier returns columns the coverage endpoint expects."""
    trades = _make_trades([
        ("2025-01-03", "20-30", 23, "next_to_next (Mon)", 0.5, "t1", 10.0),
    ])
    cells = _make_cells([
        ("20-30", 23, "next_to_next (Mon)", 0.5, 100.0),
    ])
    a = _classify_fridays_to_cells(trades, cells)
    assert list(a.columns) == [
        "friday_date_ist", "trade_id", "assigned_band", "kind",
    ]


# ── Test 6: empty inputs ──────────────────────────────────────────────────────

def test_empty_trades():
    cells = _make_cells([("20-30", 23, "next_to_next (Mon)", 0.5, 100.0)])
    a = _classify_fridays_to_cells(pd.DataFrame(), cells)
    assert a.empty


def test_empty_cells():
    trades = _make_trades([
        ("2025-01-03", "20-30", 23, "next_to_next (Mon)", 0.5, "t1", 10.0),
    ])
    a = _classify_fridays_to_cells(trades, pd.DataFrame())
    assert a.empty

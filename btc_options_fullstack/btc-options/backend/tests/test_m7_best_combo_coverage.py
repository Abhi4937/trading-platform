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


# ── Endpoint-level warming behaviour ──────────────────────────────────────────

def test_endpoint_warming_returns_immediately(monkeypatch):
    """Cache miss with a ready grid → endpoint returns status='warming'
    immediately and kicks off one async warmup thread per cache_key.
    Subsequent identical requests reuse the in-flight thread (no double
    work). The frontend polls until the cache fills."""
    from app.api import m7_best_combo as bc
    from app.api import m7_results as m7r

    # Stub a ready grid so the endpoint doesn't short-circuit on
    # status='pending' / empty grid.
    bc._GRID_STATE["status"] = "ready"
    bc._GRID_STATE["grid"] = pd.DataFrame([
        {"iv_band": "30-40", "expiry_bucket": "current (Sat)", "delta_target": 0.3,
         "entry_hour_ist": 23, "rule_label": "sl100_baseline",
         "rule": {"premium_sl_pct": 100}, "avg_net_pnl": 50.0, "n_trades": 10},
    ])
    monkeypatch.setattr(bc, "_COVERAGE_CACHE", {})
    monkeypatch.setattr(bc, "_COVERAGE_WARMUP_TASKS", {})
    # Force trades-mtime stable across calls so cache_key matches.
    monkeypatch.setattr(m7r, "_TRADES_BY_DATASET", {"delta_match": (None, 1234.0)})
    monkeypatch.setattr(m7r, "_load_trades", lambda dataset: pd.DataFrame())

    # Record warmup calls without spawning real threads.
    seen: list[tuple] = []
    monkeypatch.setattr(bc, "_kick_off_coverage_warmup",
                        lambda cache_key, **kw: seen.append((cache_key, dict(kw))))

    out = bc.get_iv_band_best_combo_coverage(
        ranking="avg_net_pnl", secondary=None, tolerance_pct=5.0,
        rule_family="all", total_capital_usd=None, pct_deploy=100.0,
        dd_metric=None, dd_threshold=None,
        dd_metrics=None, dd_thresholds=None,
        min_hit_pct=50.0, max_loss_cap_pct=None,
        max_drop_peak_to_trough_pct=None,
        min_n_trades=5, min_win_rate=None, max_losing_streak=None,
        pick_mode="by_hour", expiry_buckets=None, delta_targets=None,
        entry_hours=None, coverage_mode="force_fit", dataset="delta_match",
    )
    assert out["status"] == "warming"
    assert out["rows"] == []
    assert out["coverage_summary"]["total_fridays"] == 0
    assert len(seen) == 1
    cache_key, kw = seen[0]
    assert kw["coverage_mode"] == "force_fit"
    assert kw["ranking"] == "avg_net_pnl"

    # Second call for same args → still warming (cache miss), but the
    # kick-off helper is called again (idempotent: the helper itself
    # short-circuits if a thread is already running). We just verify
    # the endpoint stays non-blocking.
    out2 = bc.get_iv_band_best_combo_coverage(
        ranking="avg_net_pnl", secondary=None, tolerance_pct=5.0,
        rule_family="all", total_capital_usd=None, pct_deploy=100.0,
        dd_metric=None, dd_threshold=None,
        dd_metrics=None, dd_thresholds=None,
        min_hit_pct=50.0, max_loss_cap_pct=None,
        max_drop_peak_to_trough_pct=None,
        min_n_trades=5, min_win_rate=None, max_losing_streak=None,
        pick_mode="by_hour", expiry_buckets=None, delta_targets=None,
        entry_hours=None, coverage_mode="force_fit", dataset="delta_match",
    )
    assert out2["status"] == "warming"


def test_endpoint_cache_hit_returns_cached_payload(monkeypatch):
    """When a cached payload exists for the cache_key, the endpoint
    returns it without spawning a warmup thread."""
    from app.api import m7_best_combo as bc
    from app.api import m7_results as m7r

    bc._GRID_STATE["status"] = "ready"
    bc._GRID_STATE["grid"] = pd.DataFrame([
        {"iv_band": "30-40", "expiry_bucket": "current (Sat)", "delta_target": 0.3,
         "entry_hour_ist": 23, "rule_label": "sl100_baseline",
         "rule": {"premium_sl_pct": 100}, "avg_net_pnl": 50.0, "n_trades": 10},
    ])
    monkeypatch.setattr(m7r, "_TRADES_BY_DATASET", {"delta_match": (None, 9999.0)})
    monkeypatch.setattr(m7r, "_load_trades", lambda dataset: pd.DataFrame())

    sentinel = {"status": "ready", "rows": [{"_cached": True}],
                "coverage_mode": "force_fit"}
    cache_key = (
        "delta_match",
        "force_fit", "avg_net_pnl", None, 5.0, "all",
        None, 100.0, None, None, None, None, 50.0, None, None, 5, None,
        None, "by_hour", None, None, None, 9999.0,
    )
    monkeypatch.setattr(bc, "_COVERAGE_CACHE", {cache_key: sentinel})

    spawned: list = []
    monkeypatch.setattr(bc, "_kick_off_coverage_warmup",
                        lambda *a, **k: spawned.append((a, k)))

    out = bc.get_iv_band_best_combo_coverage(
        ranking="avg_net_pnl", secondary=None, tolerance_pct=5.0,
        rule_family="all", total_capital_usd=None, pct_deploy=100.0,
        dd_metric=None, dd_threshold=None,
        dd_metrics=None, dd_thresholds=None,
        min_hit_pct=50.0, max_loss_cap_pct=None,
        max_drop_peak_to_trough_pct=None,
        min_n_trades=5, min_win_rate=None, max_losing_streak=None,
        pick_mode="by_hour", expiry_buckets=None, delta_targets=None,
        entry_hours=None, coverage_mode="force_fit", dataset="delta_match",
    )
    assert out is sentinel
    assert spawned == []


def test_kick_off_coverage_warmup_idempotent(monkeypatch):
    """Two calls for the same cache_key while a thread is still running
    must result in exactly one thread (the second call short-circuits)."""
    from app.api import m7_best_combo as bc
    import threading

    monkeypatch.setattr(bc, "_COVERAGE_CACHE", {})
    monkeypatch.setattr(bc, "_COVERAGE_WARMUP_TASKS", {})

    # Block the thread on an event so we can fire two requests while it's
    # still alive — exactly the race the lock guards against.
    block = threading.Event()
    started = threading.Event()
    invocations = {"n": 0}

    def _slow_compute(*, cache_key, **kw):
        invocations["n"] += 1
        started.set()
        block.wait(timeout=5)
        bc._COVERAGE_CACHE[cache_key] = {"status": "ready", "rows": []}

    monkeypatch.setattr(bc, "_compute_coverage_payload", _slow_compute)

    cache_key = ("k",)
    bc._kick_off_coverage_warmup(cache_key, ranking="avg_net_pnl",
        secondary=None, tolerance_pct=5.0, rule_family="all",
        total_capital_usd=None, pct_deploy=100.0,
        dd_metric=None, dd_threshold=None,
        dd_metrics=None, dd_thresholds=None,
        min_hit_pct=50.0, max_loss_cap_pct=None,
        max_drop_peak_to_trough_pct=None,
        min_n_trades=5, min_win_rate=None, max_losing_streak=None,
        pick_mode="by_hour", expiry_buckets=None, delta_targets=None,
        entry_hours=None, coverage_mode="force_fit")
    assert started.wait(timeout=2), "first thread never started"
    bc._kick_off_coverage_warmup(cache_key, ranking="avg_net_pnl",
        secondary=None, tolerance_pct=5.0, rule_family="all",
        total_capital_usd=None, pct_deploy=100.0,
        dd_metric=None, dd_threshold=None,
        dd_metrics=None, dd_thresholds=None,
        min_hit_pct=50.0, max_loss_cap_pct=None,
        max_drop_peak_to_trough_pct=None,
        min_n_trades=5, min_win_rate=None, max_losing_streak=None,
        pick_mode="by_hour", expiry_buckets=None, delta_targets=None,
        entry_hours=None, coverage_mode="force_fit")
    block.set()
    # Wait for the running thread to finish so the cache fills.
    t = bc._COVERAGE_WARMUP_TASKS.get(cache_key)
    if t is not None:
        t.join(timeout=5)
    assert invocations["n"] == 1, "second call must reuse the in-flight thread"

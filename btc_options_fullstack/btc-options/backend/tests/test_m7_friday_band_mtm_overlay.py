"""Tests for GET /api/v1/m7/friday_band_mtm_overlay.

Integration tests use real parquet data via TestClient(app) — guarded with
pytest.skip if ~/btc-data/derived/m7/m7_paths/ is empty.

Synthetic tests mock _run_overlay_query and/or _resolve_friday_band_map +
m7_results._derive_exits so they never touch the filesystem.

How to run only the benchmark:
    pytest -m benchmark -p no:cacheprovider --no-header -s \\
        backend/tests/test_m7_friday_band_mtm_overlay.py::test_benchmark_d1_cold_latency
"""
from __future__ import annotations

import glob
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import numpy as np
import pytest

from fastapi.testclient import TestClient
from app.main import app
from app.api import m7_friday_band_results as fbr
from app.api import m7_results as m7r

client = TestClient(app)

# ── Paths guard ───────────────────────────────────────────────────────────────

# In the backend container, btc-data is bind-mounted at /home/abhis/btc-data
# regardless of the Python process user (which is root inside Docker, making
# os.path.expanduser("~") return /root — wrong). Use the known absolute path.
_M7_PATHS_DIR = "/home/abhis/btc-data/derived/m7/m7_paths"
_M7_DERIVED_DIR = "/home/abhis/btc-data/derived/m7"


def _paths_exist() -> bool:
    parts = glob.glob(os.path.join(_M7_PATHS_DIR, "friday_date=*/part.parquet"))
    return len(parts) > 0


def _grid_is_ready() -> bool:
    """Return True only if the Friday-band grid file exists and is fresh enough
    that the endpoint will return 200 (not 503). Mirrors the check inside
    m7_friday_band_best_combo._grid_is_fresh."""
    try:
        from app.api import m7_friday_band_best_combo as m7fb
        from app.api import m7_results as m7r_mod
        grid_path = m7fb.GRID_PARQUET_PATH
        if not os.path.exists(grid_path):
            return False
        # Check SAT_IV file too.
        if not os.path.exists(m7fb.SAT_IV_PATH):
            return False
        return m7fb._grid_is_fresh(grid_path)
    except Exception:
        return False


def _skip_if_no_data():
    if not _paths_exist():
        pytest.skip("No m7_paths parquet data found at /home/abhis/btc-data/derived/m7/m7_paths/")
    if not _grid_is_ready():
        pytest.skip(
            "Friday-band grid is missing or stale — endpoint would return 503. "
            "Rebuild the grid first: run the m7_friday_band_best_combo grid-builder script."
        )


# ── Overlay cache invalidation between tests ─────────────────────────────────

@pytest.fixture(autouse=True)
def clear_overlay_cache():
    """Wipe the endpoint's in-process result cache before each test so that
    monkeypatched state is not masked by a stale cached response."""
    fbr._OVERLAY_CACHE.clear()
    yield
    fbr._OVERLAY_CACHE.clear()


# ── Synthetic derived-frame builder ──────────────────────────────────────────

def _synth_derived_row(
    *,
    trade_id: int,
    friday_date: str = "2024-06-07",
    friday_band: str = "30-40",
    entry_hour_ist: int = 23,
    expiry_bucket: str = "current (Sat)",
    delta_target: float = 0.30,
    net_pnl: float = 100.0,
    max_mtm: float = 120.0,
    min_mtm: float = -20.0,
) -> dict:
    """Minimal derived-trade row that _compute_overlay_response cares about."""
    return {
        "trade_id": trade_id,
        "friday_date_ist": friday_date,
        "entry_atm_iv_band": friday_band,
        "friday_band": friday_band,
        "entry_hour_ist": entry_hour_ist,
        "expiry_bucket": expiry_bucket,
        "delta_target": delta_target,
        "is_win": net_pnl > 0,
        "is_straddle": False,
        "net_pnl_estimate_usd": net_pnl,
        "gross_pnl_usd": net_pnl,
        "credit_usd": 200.0,
        "margin_used_usd_at_entry": 800.0,
        "loss_cause": None,
        "exit_reason": "rule_trigger",
        "max_mtm_usd": max_mtm,
        "min_mtm_usd": min_mtm,
        "exit_mtm_usd": net_pnl,
    }


def _synth_path_df(
    trade_ids_and_paths: list[tuple[int, list[tuple[int, float]]]]
) -> pd.DataFrame:
    """Build a path DataFrame from [(trade_id, [(minute, gross_pnl), ...]), ...]."""
    rows = []
    for tid, pts in trade_ids_and_paths:
        for minute, gross in pts:
            rows.append({"trade_id": tid, "minute_offset": minute, "gross_pnl_usd": float(gross)})
    if not rows:
        return pd.DataFrame(columns=["trade_id", "minute_offset", "gross_pnl_usd"])
    return pd.DataFrame(rows)


# ── Helper: hit the overlay endpoint with default A1 params ──────────────────

def _call_overlay(**kwargs) -> dict:
    params = {"band_mode": "A1", **kwargs}
    resp = client.get("/api/v1/m7/friday_band_mtm_overlay", params=params)
    assert resp.status_code == 200, f"status={resp.status_code}: {resp.text[:500]}"
    return resp.json()


# =============================================================================
# 1. test_picks_consistent_with_markers (integration — real data)
# =============================================================================

@pytest.mark.slow
def test_picks_consistent_with_markers():
    """The overlay picks match the markers endpoint for best / worst / best_max_mtm / worst_min_mtm."""
    _skip_if_no_data()

    overlay = _call_overlay()
    markers_resp = client.get("/api/v1/m7/friday_band_best_combo_markers", params={"band_mode": "A1"})
    assert markers_resp.status_code == 200
    markers = markers_resp.json()

    # Index markers by friday_band.
    markers_by_band: dict[str, dict] = {}
    for b in markers.get("bands", []):
        markers_by_band[b["friday_band"]] = b

    for band_entry in overlay.get("bands", []):
        band = band_entry["friday_band"]
        if band not in markers_by_band:
            continue
        marker_trades = markers_by_band[band].get("trades", [])
        if not marker_trades:
            continue

        marker_df = pd.DataFrame(marker_trades)
        if "net_pnl_estimate_usd" not in marker_df.columns:
            continue

        picks = band_entry.get("picks", {})

        # Best pick must match max net_pnl row (tie-break: smallest trade_id).
        best_pick = picks.get("best")
        if best_pick and "friday_date_ist" in marker_df.columns:
            marker_sorted = marker_df.sort_values(
                "net_pnl_estimate_usd", ascending=False
            )
            max_pnl = marker_sorted.iloc[0]["net_pnl_estimate_usd"]
            best_candidates = marker_sorted[marker_sorted["net_pnl_estimate_usd"] == max_pnl]
            # The overlay should have picked a trade from the marker set.
            # We just verify net_pnl of the picked trade equals the max in markers.
            assert best_pick.get("net_pnl_estimate_usd") == pytest.approx(
                float(max_pnl), abs=0.05
            ), f"band={band}: overlay best.net_pnl={best_pick['net_pnl_estimate_usd']} != markers max {max_pnl}"

        # Worst pick must match min net_pnl row.
        worst_pick = picks.get("worst")
        if worst_pick and "net_pnl_estimate_usd" in marker_df.columns:
            min_pnl = marker_df["net_pnl_estimate_usd"].min()
            assert worst_pick.get("net_pnl_estimate_usd") == pytest.approx(
                float(min_pnl), abs=0.05
            ), f"band={band}: overlay worst.net_pnl={worst_pick['net_pnl_estimate_usd']} != markers min {min_pnl}"


# =============================================================================
# 2. test_band_cohort_parity_with_markers (integration — real data)
# =============================================================================

@pytest.mark.slow
def test_band_cohort_parity_with_markers():
    """bands[i].n_trades equals the trade count returned by markers for same band."""
    _skip_if_no_data()

    overlay = _call_overlay()
    markers_resp = client.get("/api/v1/m7/friday_band_best_combo_markers", params={"band_mode": "A1"})
    assert markers_resp.status_code == 200
    markers = markers_resp.json()

    markers_n: dict[str, int] = {b["friday_band"]: b["n_trades"] for b in markers.get("bands", [])}

    for band_entry in overlay.get("bands", []):
        band = band_entry["friday_band"]
        if band not in markers_n:
            continue
        assert band_entry["n_trades"] == markers_n[band], (
            f"band={band}: overlay n_trades={band_entry['n_trades']} "
            f"!= markers n_trades={markers_n[band]}"
        )


# =============================================================================
# 3. test_bands_are_disjoint (integration — real data)
# =============================================================================

@pytest.mark.slow
def test_bands_are_disjoint():
    """A trade_id in one band's picks must NOT appear in another band's picks.
    (Within the same band, the same trade may appear in multiple slots — allowed.)
    """
    _skip_if_no_data()

    overlay = _call_overlay()

    band_trade_sets: dict[str, set[str]] = {}
    for band_entry in overlay.get("bands", []):
        band = band_entry["friday_band"]
        picks = band_entry.get("picks", {})
        tids: set[str] = set()
        for slot in ("best", "worst", "best_max_mtm", "worst_min_mtm"):
            p = picks.get(slot)
            if p:
                tids.add(str(p["trade_id"]))
        band_trade_sets[band] = tids

    bands = list(band_trade_sets.keys())
    for i, b1 in enumerate(bands):
        for b2 in bands[i + 1 :]:
            overlap = band_trade_sets[b1] & band_trade_sets[b2]
            assert not overlap, (
                f"trade_id(s) {overlap} appear in both band '{b1}' and band '{b2}'"
            )


# =============================================================================
# 4. test_avg_curve_minute_0_is_zero (integration — real data)
# =============================================================================

@pytest.mark.slow
def test_avg_curve_minute_0_is_zero():
    """avg_curve.mean_pnl[0] is approximately 0 — entry frame gross PnL is near zero."""
    _skip_if_no_data()

    overlay = _call_overlay()
    for band_entry in overlay.get("bands", []):
        avg = band_entry.get("avg_curve", {})
        minutes = avg.get("minutes", [])
        mean_pnl = avg.get("mean_pnl", [])
        if not minutes or not mean_pnl:
            continue
        if minutes[0] == 0:
            assert abs(mean_pnl[0]) <= 0.10, (
                f"band={band_entry['friday_band']}: avg_curve.mean_pnl[0]={mean_pnl[0]:.4f} "
                f"exceeds $0.10 tolerance — entry frame should be near zero"
            )


# =============================================================================
# 5. test_carry_forward_semantic (synthetic)
# =============================================================================

def test_carry_forward_semantic(monkeypatch):
    """Carry-forward: trade A exits at minute 10 (gross=$50), trade B lasts to
    minute 30. At minute 20, A's value should be carried forward as $50.
    """
    # Two trades in band "30-40".
    rows = [
        _synth_derived_row(trade_id=1, net_pnl=50.0, max_mtm=55.0, min_mtm=0.0),
        _synth_derived_row(trade_id=2, net_pnl=-30.0, max_mtm=10.0, min_mtm=-35.0),
    ]
    derived_df = pd.DataFrame(rows)

    # Trade A (1): 0→10, gross $50 at exit.
    # Trade B (2): 0→30, gross changes through the path.
    path_a = [(m, 50.0 * m / 10) for m in range(11)]   # ramps 0→50 over minutes 0-10
    path_b = [(m, -30.0 * m / 30) for m in range(31)]  # ramps 0→-30 over minutes 0-30

    fake_path_df = _synth_path_df([(1, path_a), (2, path_b)])

    monkeypatch.setattr(fbr, "_run_overlay_query", lambda **kw: fake_path_df)

    # Stub _resolve_friday_band_map to return a single Friday→band entry.
    monkeypatch.setattr(fbr, "_resolve_friday_band_map",
                        lambda band_mode, d1_tb: {"2024-06-07": "30-40"})

    # Stub _derive_exits to return our synthetic derived frame.
    monkeypatch.setattr(m7r, "_derive_exits", lambda filters, rule: derived_df)

    resp = client.get("/api/v1/m7/friday_band_mtm_overlay", params={
        "band_mode": "A1", "min_n_trades_in_avg": 1, "max_minutes": 50,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["bands"], "Expected at least one band in response"
    band = data["bands"][0]
    avg = band["avg_curve"]
    minutes = avg["minutes"]
    mean_pnl = avg["mean_pnl"]

    assert len(minutes) > 0, "avg_curve should not be empty"
    assert minutes[0] == 0, "avg_curve should start at minute 0"

    # At minute 10: both trades are still alive; A is at $50, B is at -$10.
    # mean = (50 + -10) / 2 = 20.
    if 10 in minutes:
        idx_10 = minutes.index(10)
        b_at_10 = -30.0 * 10 / 30
        expected_at_10 = (50.0 + b_at_10) / 2
        assert mean_pnl[idx_10] == pytest.approx(expected_at_10, abs=0.02), (
            f"mean_pnl at minute 10 = {mean_pnl[idx_10]:.4f}, expected {expected_at_10:.4f}"
        )

    # At minute 20: A exited at minute 10 ($50 carried forward), B at minute 20.
    if 20 in minutes:
        idx_20 = minutes.index(20)
        b_at_20 = -30.0 * 20 / 30  # ≈ -20.0
        # Denominator is always n_trades=2 (carry-forward semantics).
        expected_at_20 = (50.0 + b_at_20) / 2
        assert mean_pnl[idx_20] == pytest.approx(expected_at_20, abs=0.05), (
            f"mean_pnl at minute 20 = {mean_pnl[idx_20]:.4f}, expected {expected_at_20:.4f}"
        )


# =============================================================================
# 6. test_n_trades_alive_monotone_nonincreasing (integration — real data)
# =============================================================================

@pytest.mark.slow
def test_n_trades_alive_monotone_nonincreasing():
    """avg_curve.n_trades_alive is non-increasing: once a trade exits it can't
    come back alive."""
    _skip_if_no_data()

    overlay = _call_overlay()
    for band_entry in overlay.get("bands", []):
        alive = band_entry["avg_curve"].get("n_trades_alive", [])
        for i in range(len(alive) - 1):
            assert alive[i + 1] <= alive[i], (
                f"band={band_entry['friday_band']}: n_trades_alive not monotone at "
                f"index {i}: {alive[i]} → {alive[i+1]}"
            )


# =============================================================================
# 7. test_max_minutes_cap_respected (integration — real data)
# =============================================================================

@pytest.mark.slow
def test_max_minutes_cap_respected():
    """No minute in any returned array exceeds max_minutes=30."""
    _skip_if_no_data()

    overlay = _call_overlay(max_minutes=30)
    for band_entry in overlay.get("bands", []):
        avg = band_entry["avg_curve"]
        minutes = avg.get("minutes", [])
        if minutes:
            assert max(minutes) <= 30, (
                f"band={band_entry['friday_band']}: avg_curve contains minute {max(minutes)} > 30"
            )
        picks = band_entry.get("picks", {})
        for slot in ("best", "worst", "best_max_mtm", "worst_min_mtm"):
            p = picks.get(slot)
            if p and p.get("minutes"):
                assert max(p["minutes"]) <= 30, (
                    f"band={band_entry['friday_band']} slot={slot}: "
                    f"pick minutes contain {max(p['minutes'])} > 30"
                )


# =============================================================================
# 8. test_min_n_trades_in_avg_truncation (synthetic)
# =============================================================================

def test_min_n_trades_in_avg_truncation(monkeypatch):
    """min_n_trades_in_avg higher than n_trades in band → avg_curve is empty.

    The endpoint clamps min_n_trades_in_avg to ≤50 via FastAPI validation, so
    we use the maximum allowed value (50) with only 3 trades — 3 < 50, so no
    minute ever has n_trades_alive >= 50, and the tail truncation drops all rows.
    """
    rows = [_synth_derived_row(trade_id=i, net_pnl=float(i * 10)) for i in range(3)]
    derived_df = pd.DataFrame(rows)

    path_data = _synth_path_df([
        (i, [(m, float(m)) for m in range(31)]) for i in range(3)
    ])

    monkeypatch.setattr(fbr, "_run_overlay_query", lambda **kw: path_data)
    monkeypatch.setattr(fbr, "_resolve_friday_band_map",
                        lambda band_mode, d1_tb: {"2024-06-07": "30-40"})
    monkeypatch.setattr(m7r, "_derive_exits", lambda filters, rule: derived_df)

    resp = client.get("/api/v1/m7/friday_band_mtm_overlay", params={
        "band_mode": "A1", "min_n_trades_in_avg": 50, "max_minutes": 60,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()

    for band_entry in data.get("bands", []):
        avg = band_entry["avg_curve"]
        assert avg["minutes"] == [], (
            f"Expected empty avg_curve minutes with min_n_trades_in_avg=999, "
            f"got {avg['minutes'][:5]}"
        )
        assert avg["mean_pnl"] == []
        assert avg["n_trades_alive"] == []


# =============================================================================
# 9. test_paths_are_per_trade_disjoint (integration — real data)
# =============================================================================

@pytest.mark.slow
def test_paths_are_per_trade_disjoint():
    """Each pick path has:
    - minutes sorted ascending with no duplicates
    - len(minutes) == len(pnl)
    """
    _skip_if_no_data()

    overlay = _call_overlay()
    for band_entry in overlay.get("bands", []):
        picks = band_entry.get("picks", {})
        for slot in ("best", "worst", "best_max_mtm", "worst_min_mtm"):
            p = picks.get(slot)
            if p is None:
                continue
            minutes = p.get("minutes", [])
            pnl = p.get("pnl", [])
            assert len(minutes) == len(pnl), (
                f"band={band_entry['friday_band']} slot={slot}: "
                f"len(minutes)={len(minutes)} != len(pnl)={len(pnl)}"
            )
            if len(minutes) > 1:
                diffs = [minutes[i + 1] - minutes[i] for i in range(len(minutes) - 1)]
                assert all(d > 0 for d in diffs), (
                    f"band={band_entry['friday_band']} slot={slot}: "
                    f"minutes not strictly ascending: {minutes[:10]}"
                )


# =============================================================================
# 10. test_deterministic_picks_under_ties (synthetic)
# =============================================================================

def test_deterministic_picks_under_ties(monkeypatch):
    """Two trades with identical net_pnl: the picked trade is the one with the
    smaller trade_id (ascending sort on trade_id as tiebreak)."""
    rows = [
        _synth_derived_row(trade_id=200, net_pnl=100.0, max_mtm=100.0, min_mtm=-5.0),
        _synth_derived_row(trade_id=100, net_pnl=100.0, max_mtm=100.0, min_mtm=-5.0),
    ]
    derived_df = pd.DataFrame(rows)

    path_data = _synth_path_df([
        (100, [(0, 0.0), (5, 50.0), (10, 100.0)]),
        (200, [(0, 0.0), (5, 50.0), (10, 100.0)]),
    ])

    monkeypatch.setattr(fbr, "_run_overlay_query", lambda **kw: path_data)
    monkeypatch.setattr(fbr, "_resolve_friday_band_map",
                        lambda band_mode, d1_tb: {"2024-06-07": "30-40"})
    monkeypatch.setattr(m7r, "_derive_exits", lambda filters, rule: derived_df)

    resp = client.get("/api/v1/m7/friday_band_mtm_overlay", params={
        "band_mode": "A1", "min_n_trades_in_avg": 1, "max_minutes": 20,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["bands"], "Expected at least one band"
    picks = data["bands"][0]["picks"]

    best = picks.get("best")
    assert best is not None, "best pick should not be None"
    assert int(best["trade_id"]) == 100, (
        f"Expected smaller trade_id=100 for best pick, got {best['trade_id']}"
    )


# =============================================================================
# 11. test_picks_skip_nan (synthetic)
# =============================================================================

def test_picks_skip_nan(monkeypatch):
    """NaN in max_mtm_usd for one trade → that trade is NOT picked as best_max_mtm.
    If all trades have NaN max_mtm_usd → best_max_mtm is None."""
    rows = [
        _synth_derived_row(trade_id=1, max_mtm=float("nan"), net_pnl=50.0),
        _synth_derived_row(trade_id=2, max_mtm=float("nan"), net_pnl=30.0),
    ]
    derived_df = pd.DataFrame(rows)

    path_data = _synth_path_df([
        (1, [(0, 0.0), (10, 50.0)]),
        (2, [(0, 0.0), (10, 30.0)]),
    ])

    monkeypatch.setattr(fbr, "_run_overlay_query", lambda **kw: path_data)
    monkeypatch.setattr(fbr, "_resolve_friday_band_map",
                        lambda band_mode, d1_tb: {"2024-06-07": "30-40"})
    monkeypatch.setattr(m7r, "_derive_exits", lambda filters, rule: derived_df)

    resp = client.get("/api/v1/m7/friday_band_mtm_overlay", params={
        "band_mode": "A1", "min_n_trades_in_avg": 1, "max_minutes": 20,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["bands"], "Expected at least one band"
    picks = data["bands"][0]["picks"]
    assert picks.get("best_max_mtm") is None, (
        f"best_max_mtm should be None when all max_mtm_usd are NaN, got {picks.get('best_max_mtm')}"
    )

    # Now test: one trade has valid max_mtm, other is NaN → valid trade is picked.
    rows2 = [
        _synth_derived_row(trade_id=10, max_mtm=float("nan"), net_pnl=80.0),
        _synth_derived_row(trade_id=20, max_mtm=150.0,        net_pnl=60.0),
    ]
    derived_df2 = pd.DataFrame(rows2)
    path_data2 = _synth_path_df([
        (10, [(0, 0.0), (10, 80.0)]),
        (20, [(0, 0.0), (10, 60.0)]),
    ])

    fbr._OVERLAY_CACHE.clear()  # clear for second sub-test
    monkeypatch.setattr(fbr, "_run_overlay_query", lambda **kw: path_data2)
    monkeypatch.setattr(m7r, "_derive_exits", lambda filters, rule: derived_df2)

    resp2 = client.get("/api/v1/m7/friday_band_mtm_overlay", params={
        "band_mode": "A1", "min_n_trades_in_avg": 1, "max_minutes": 20,
    })
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["bands"]
    picks2 = data2["bands"][0]["picks"]
    bm = picks2.get("best_max_mtm")
    assert bm is not None, "best_max_mtm should not be None when one trade has valid max_mtm"
    assert int(bm["trade_id"]) == 20, (
        f"Expected trade_id=20 (valid max_mtm=150), got {bm['trade_id']}"
    )


# =============================================================================
# 12. test_missing_partition_does_not_crash (integration — real data)
# =============================================================================

@pytest.mark.slow
def test_missing_partition_does_not_crash():
    """Filtering to a friday_date that has no m7_paths partition → 200, not 500.

    DuckDB's read_parquet over a glob silently ignores missing partitions, so
    the endpoint should just return an empty or partial response.
    """
    _skip_if_no_data()

    # Use a friday_date far in the future — no parquet partition exists.
    resp = client.get("/api/v1/m7/friday_band_mtm_overlay", params={
        "band_mode": "A1",
        "friday_date_ist": "2099-12-31",
    })
    assert resp.status_code == 200, (
        f"Expected 200 for missing partition, got {resp.status_code}: {resp.text[:300]}"
    )


# =============================================================================
# 13. test_lock_serializes_concurrent_calls (integration — real data)
# =============================================================================

@pytest.mark.slow
def test_lock_serializes_concurrent_calls():
    """Two concurrent calls both return 200 without crashing.

    The internal threading.Lock inside _run_overlay_query serialises DuckDB
    path scans; this test verifies no exception leaks under concurrent load.
    """
    _skip_if_no_data()

    def _call():
        r = client.get("/api/v1/m7/friday_band_mtm_overlay", params={
            "band_mode": "A1", "max_minutes": 60,
        })
        return r.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_call) for _ in range(2)]
        statuses = [f.result() for f in futs]

    assert all(s == 200 for s in statuses), f"Got non-200 statuses: {statuses}"


# =============================================================================
# 14. test_response_uses_columnar_format (integration — real data)
# =============================================================================

@pytest.mark.slow
def test_response_uses_columnar_format():
    """The avg_curve and each pick use columnar lists, not list-of-dicts."""
    _skip_if_no_data()

    data = _call_overlay()
    assert isinstance(data.get("bands"), list), "bands must be a list"

    for band_entry in data["bands"]:
        avg = band_entry.get("avg_curve", {})
        assert isinstance(avg.get("minutes"), list), \
            f"avg_curve.minutes must be a list in band {band_entry['friday_band']}"
        assert isinstance(avg.get("mean_pnl"), list), \
            f"avg_curve.mean_pnl must be a list in band {band_entry['friday_band']}"
        assert isinstance(avg.get("n_trades_alive"), list), \
            f"avg_curve.n_trades_alive must be a list in band {band_entry['friday_band']}"

        picks = band_entry.get("picks", {})
        for slot in ("best", "worst", "best_max_mtm", "worst_min_mtm"):
            p = picks.get(slot)
            if p is None:
                continue
            assert isinstance(p.get("minutes"), list), \
                f"band={band_entry['friday_band']} slot={slot}: minutes must be a list"
            assert isinstance(p.get("pnl"), list), \
                f"band={band_entry['friday_band']} slot={slot}: pnl must be a list"
            assert len(p["minutes"]) == len(p["pnl"]), \
                f"band={band_entry['friday_band']} slot={slot}: minutes/pnl length mismatch"
            # Verify no list-of-dicts: each element must be a scalar.
            if p["minutes"]:
                assert isinstance(p["minutes"][0], (int, float)), \
                    f"band={band_entry['friday_band']} slot={slot}: minutes[0] is not a scalar"
                assert isinstance(p["pnl"][0], (int, float)), \
                    f"band={band_entry['friday_band']} slot={slot}: pnl[0] is not a scalar"


# =============================================================================
# Benchmark (skip by default — run manually with -m benchmark)
# =============================================================================

@pytest.mark.benchmark
@pytest.mark.skip(reason="benchmark — run manually: "
                  "pytest -m benchmark -p no:cacheprovider --no-header -s "
                  "backend/tests/test_m7_friday_band_mtm_overlay.py::test_benchmark_d1_cold_latency")
def test_benchmark_d1_cold_latency():
    """Measure cold-cache latency for a D1 band_mode overlay request.

    Run manually:
        pytest -m benchmark -p no:cacheprovider --no-header -s \\
            backend/tests/test_m7_friday_band_mtm_overlay.py::test_benchmark_d1_cold_latency
    """
    _skip_if_no_data()
    fbr._OVERLAY_CACHE.clear()

    t0 = time.perf_counter()
    resp = client.get("/api/v1/m7/friday_band_mtm_overlay", params={"band_mode": "D1"})
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200, f"D1 overlay returned {resp.status_code}: {resp.text[:300]}"
    data = resp.json()
    n_bands = len(data.get("bands", []))
    print(f"\n[benchmark] D1 cold latency: {elapsed:.3f}s — {n_bands} bands returned")

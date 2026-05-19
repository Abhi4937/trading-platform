"""Tests for the cell_friday_detail endpoint helper — verifies the four
Friday-level buckets (losers / worst_winner / largest_win / winners below
avg min MTM) match the aggregates the rest of the grid uses.

The endpoint dispatches to `_compute_cell_friday_detail`, which calls
`m7_results._derive_exits()`. We monkeypatch that function to return a
deterministic per-trade frame so we don't need real parquet files.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.api import m7_best_combo as bc
from app.api import m7_results as m7r


def _trade(*, friday: str, iv_band: str, expiry: str, delta: float, hour: int,
           net: float, min_mtm: float, max_mtm: float,
           exit_reason: str = "rule_trigger") -> dict:
    return {
        "trade_id": f"{friday}-{iv_band}-{delta}-{hour}",
        "friday_date_ist": friday,
        "entry_atm_iv_band": iv_band,
        "expiry_bucket": expiry,
        "delta_target": float(delta),
        "entry_hour_ist": int(hour),
        "is_win": bool(net > 0),
        "net_pnl_estimate_usd": float(net),
        "min_mtm_usd": float(min_mtm),
        "max_mtm_usd": float(max_mtm),
        "exit_reason": exit_reason,
    }


CELL_KW = dict(
    band="30-40",
    expiry_bucket="current (Sat)",
    delta_target=0.30,
    entry_hour_ist=10,
    rule_label="sl75_baseline",
    dataset="delta_match",
)


def _make_trades_in_cell() -> pd.DataFrame:
    """6 trades in target cell + 1 noise row outside the cell.

    Winners: 4 (nets +50, +120, +800, +200). Losers: 2 (nets -200, -90).
    Winners min_mtm: -50, -100, -400, -500.  avg = -262.5.
    → 2 winners below avg (-400 and -500).
    → worst-MTM winner: row with min_mtm = -500 (friday 2025-01-31).
    → largest win: row with net = +800 (friday 2025-01-24).
    """
    base = dict(iv_band="30-40", expiry="current (Sat)", delta=0.30, hour=10)
    return pd.DataFrame([
        _trade(friday="2025-01-03", **base, net=+50.0,  min_mtm=-50.0,  max_mtm=+60.0),
        _trade(friday="2025-01-10", **base, net=+120.0, min_mtm=-100.0, max_mtm=+150.0),
        _trade(friday="2025-01-17", **base, net=-200.0, min_mtm=-300.0, max_mtm=+30.0, exit_reason="premium_sl"),
        _trade(friday="2025-01-24", **base, net=+800.0, min_mtm=-400.0, max_mtm=+900.0),
        _trade(friday="2025-01-31", **base, net=+200.0, min_mtm=-500.0, max_mtm=+250.0),
        _trade(friday="2025-02-07", **base, net=-90.0,  min_mtm=-180.0, max_mtm=+20.0, exit_reason="premium_sl"),
        # Noise: same delta+hour but different band — must be filtered out.
        _trade(friday="2025-02-14", iv_band="80-90", expiry="current (Sat)", delta=0.30, hour=10,
               net=+9999.0, min_mtm=-1.0, max_mtm=+9999.0),
    ])


@pytest.fixture
def patched_derive(monkeypatch):
    """Make `_derive_exits` return a fixed in-cell trade set and the rule
    label resolvable as a known baseline rule."""
    monkeypatch.setattr(
        m7r, "_derive_exits",
        lambda filters, rule, dataset="delta_match": _make_trades_in_cell(),
    )
    return monkeypatch


def test_label_to_rule_resolves_known_and_unknown():
    rule = bc._label_to_rule("sl75_baseline")
    assert rule == {"premium_sl_pct": 75}
    assert bc._label_to_rule("nonsense_rule") is None


def test_unknown_rule_returns_status(patched_derive):
    out = bc._compute_cell_friday_detail(
        **{**CELL_KW, "rule_label": "definitely_not_a_real_rule"},
    )
    assert out["status"] == "unknown_rule"
    assert out["losers"] == []
    assert out["worst_winner"] is None
    assert out["largest_win"] is None
    assert out["winners_below_avg_min_mtm"] == []


def test_losers_match_n_losses_and_are_all_losing(patched_derive):
    out = bc._compute_cell_friday_detail(**CELL_KW)
    assert out["status"] == "ok"
    assert out["cell"]["n_losses"] == 2
    assert len(out["losers"]) == 2
    # Losers should be sorted by net P&L ascending (worst first).
    nets = [r["net_pnl_estimate_usd"] for r in out["losers"]]
    assert nets == sorted(nets)
    # Every loser has negative net.
    for r in out["losers"]:
        assert r["net_pnl_estimate_usd"] < 0
    fridays = {r["friday_date_ist"] for r in out["losers"]}
    assert fridays == {"2025-01-17", "2025-02-07"}


def test_worst_winner_is_winner_with_min_min_mtm(patched_derive):
    out = bc._compute_cell_friday_detail(**CELL_KW)
    ww = out["worst_winner"]
    assert ww is not None
    # Most-negative min_mtm among winners → friday 2025-01-31 (min_mtm=-500).
    assert ww["friday_date_ist"] == "2025-01-31"
    assert ww["min_mtm_usd"] == pytest.approx(-500.0)
    # Must equal the cell aggregate min_mtm_winners.
    assert ww["min_mtm_usd"] == pytest.approx(out["cell"]["min_mtm_winners"])


def test_largest_win_is_winner_with_max_net(patched_derive):
    out = bc._compute_cell_friday_detail(**CELL_KW)
    lw = out["largest_win"]
    assert lw is not None
    assert lw["friday_date_ist"] == "2025-01-24"
    assert lw["net_pnl_estimate_usd"] == pytest.approx(800.0)
    assert lw["net_pnl_estimate_usd"] == pytest.approx(out["cell"]["max_win_usd"])


def test_winners_below_avg_min_mtm_matches_count_and_filter(patched_derive):
    out = bc._compute_cell_friday_detail(**CELL_KW)
    threshold = out["cell"]["avg_min_mtm_winners"]
    # avg of winners' min_mtm = (-50 + -100 + -400 + -500) / 4 = -262.5
    assert threshold == pytest.approx(-262.5)
    rows = out["winners_below_avg_min_mtm"]
    assert len(rows) == out["cell"]["n_winners_below_avg_min_mtm"]
    assert len(rows) == 2  # rows with min_mtm in {-400, -500}
    fridays = {r["friday_date_ist"] for r in rows}
    assert fridays == {"2025-01-24", "2025-01-31"}
    for r in rows:
        assert r["min_mtm_usd"] < threshold


def test_noise_outside_cell_is_filtered(patched_derive):
    # The 2025-02-14 row is in iv_band 80-90 (not 30-40) — must not appear
    # anywhere in the response.
    out = bc._compute_cell_friday_detail(**CELL_KW)
    all_fridays = (
        {r["friday_date_ist"] for r in out["losers"]}
        | {r["friday_date_ist"] for r in out["winners_below_avg_min_mtm"]}
        | ({out["worst_winner"]["friday_date_ist"]} if out["worst_winner"] else set())
        | ({out["largest_win"]["friday_date_ist"]} if out["largest_win"] else set())
    )
    assert "2025-02-14" not in all_fridays


def test_friday_set_overrides_band_filter(patched_derive):
    """Friday-Band variant passes friday_set instead of band; band filter
    must NOT be applied so trades in any iv_band can land in the cell as
    long as their friday is in friday_set."""
    out = bc._compute_cell_friday_detail(
        **{**CELL_KW, "band": "30-40"},
        friday_set={"2025-02-14"},  # noise-row friday
    )
    # The 2025-02-14 row is in band 80-90 but should be included now.
    assert out["status"] == "ok"
    assert out["cell"]["n_trades"] == 1
    assert out["largest_win"] is not None
    assert out["largest_win"]["friday_date_ist"] == "2025-02-14"


def test_no_trades_returns_empty(monkeypatch):
    monkeypatch.setattr(
        m7r, "_derive_exits",
        lambda filters, rule, dataset="delta_match": pd.DataFrame(),
    )
    out = bc._compute_cell_friday_detail(**CELL_KW)
    assert out["status"] == "no_trades"
    assert out["losers"] == []
    assert out["worst_winner"] is None
    assert out["largest_win"] is None
    assert out["winners_below_avg_min_mtm"] == []


def test_cell_with_only_winners_has_no_losers(monkeypatch):
    base = dict(iv_band="30-40", expiry="current (Sat)", delta=0.30, hour=10)
    df = pd.DataFrame([
        _trade(friday="2025-01-03", **base, net=+50.0, min_mtm=-10.0, max_mtm=+60.0),
        _trade(friday="2025-01-10", **base, net=+90.0, min_mtm=-30.0, max_mtm=+120.0),
    ])
    monkeypatch.setattr(
        m7r, "_derive_exits",
        lambda filters, rule, dataset="delta_match": df,
    )
    out = bc._compute_cell_friday_detail(**CELL_KW)
    assert out["status"] == "ok"
    assert out["losers"] == []
    assert out["cell"]["n_losses"] == 0
    # Both rows are winners; one of them is the largest, one is the worst.
    assert out["largest_win"]["friday_date_ist"] == "2025-01-10"
    assert out["worst_winner"]["friday_date_ist"] == "2025-01-10"


def test_cell_with_only_losers_has_no_winners(monkeypatch):
    base = dict(iv_band="30-40", expiry="current (Sat)", delta=0.30, hour=10)
    df = pd.DataFrame([
        _trade(friday="2025-01-03", **base, net=-50.0, min_mtm=-80.0, max_mtm=+10.0),
        _trade(friday="2025-01-10", **base, net=-150.0, min_mtm=-200.0, max_mtm=+5.0),
    ])
    monkeypatch.setattr(
        m7r, "_derive_exits",
        lambda filters, rule, dataset="delta_match": df,
    )
    out = bc._compute_cell_friday_detail(**CELL_KW)
    assert out["status"] == "ok"
    assert len(out["losers"]) == 2
    assert out["worst_winner"] is None
    assert out["largest_win"] is None
    assert out["winners_below_avg_min_mtm"] == []
    assert out["cell"]["avg_min_mtm_winners"] is None

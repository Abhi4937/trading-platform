"""Tests for the `scope` parameter on /api/v1/m7/losses_distribution.

Covers three scopes:
  - default (None) → universe pass-through
  - 'full_coverage' → restricted to per-band best-cell strict trades; filter
    flow-through reshapes the candidate pool and therefore the trade set
  - 'best_combo'    → restricted to per-band best (expiry, delta, rule) trade
    set; ranking flip changes the selection; loss_cause filter applies on top

All tests use synthetic in-memory DataFrames via monkeypatched `_derive_exits`
and a stubbed `m7_best_combo._GRID_STATE`. No parquet IO.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.api import m7_results


def _call(**kwargs):
    """Invoke get_losses_distribution with FastAPI Query defaults pre-unwrapped.
    (Direct Python calls don't trigger FastAPI's parameter resolution, so the
    Query wrapper slips through. Pass plain values here.)"""
    defaults = dict(
        dimensions=None, exit_rule=None, metric="avg_net_pnl",
        scope=None, ranking="credit",
        delta_target=None, is_straddle=None, expiry_date=None,
        expiry_bucket=None, entry_atm_iv_band=None, entry_hour_ist=None,
        dte_bucket=None, spot_bucket=None, ivp_bucket=None,
        ctx_pattern=None, ctx_gex_regime=None, friday_date_ist=None,
        loss_cause=None,
        include_trades=False, trades_limit=50, trades_offset=0,
        trades_sort="pnl_asc", only_sl_hits=False,
    )
    defaults.update(kwargs)
    return m7_results.get_losses_distribution(**defaults)


# ── Synthetic universe builder ────────────────────────────────────────────────

def _trade(
    *, trade_id: str, friday: str, band: str, hour: int, expiry: str,
    delta: float, is_win: bool, net_pnl: float,
) -> dict:
    """One synthetic derived trade. Fills in the cols the endpoint reads."""
    return {
        "trade_id": trade_id,
        "friday_date_ist": friday,
        "entry_atm_iv_band": band,
        "entry_hour_ist": hour,
        "expiry_bucket": expiry,
        "delta_target": delta,
        "is_win": is_win,
        "is_straddle": False,
        "net_pnl_estimate_usd": net_pnl,
        "credit_usd": 200.0,
        "margin_usd": 800.0,
        "loss_cause": None if is_win else "directional",
        "exit_reason": "rule_trigger",
    }


def _make_universe() -> pd.DataFrame:
    """Two IV bands × two cells per band × multiple Fridays.

    Band 30-40:
      cell A (h=21, exp=current (Sat), Δ=0.30): 4 trades, mostly winners (best for credit-style metrics)
      cell B (h=22, exp=next (Sun), Δ=0.50):    4 trades, mostly losers
    Band 70-80:
      cell C (h=21, exp=current (Sat), Δ=0.30): 4 trades, all winners
      cell D (h=23, exp=next (Sun), Δ=0.50):    4 trades, all losers
    """
    rows: list[dict] = []
    # Band 30-40, cell A — 4 winners
    for i, fri in enumerate(["2024-01-05", "2024-01-12", "2024-01-19", "2024-01-26"]):
        rows.append(_trade(trade_id=f"a{i}", friday=fri, band="30-40", hour=21,
                           expiry="current (Sat)", delta=0.30,
                           is_win=True, net_pnl=120.0))
    # Band 30-40, cell B — 1 winner + 3 losers
    for i, fri in enumerate(["2024-02-02", "2024-02-09", "2024-02-16", "2024-02-23"]):
        rows.append(_trade(trade_id=f"b{i}", friday=fri, band="30-40", hour=22,
                           expiry="next (Sun)", delta=0.50,
                           is_win=(i == 0), net_pnl=50.0 if i == 0 else -90.0))
    # Band 70-80, cell C — 4 winners
    for i, fri in enumerate(["2024-03-01", "2024-03-08", "2024-03-15", "2024-03-22"]):
        rows.append(_trade(trade_id=f"c{i}", friday=fri, band="70-80", hour=21,
                           expiry="current (Sat)", delta=0.30,
                           is_win=True, net_pnl=180.0))
    # Band 70-80, cell D — 4 losers
    for i, fri in enumerate(["2024-04-05", "2024-04-12", "2024-04-19", "2024-04-26"]):
        rows.append(_trade(trade_id=f"d{i}", friday=fri, band="70-80", hour=23,
                           expiry="next (Sun)", delta=0.50,
                           is_win=False, net_pnl=-150.0))
    return pd.DataFrame(rows)


@pytest.fixture
def patched_derive(monkeypatch):
    """Replace `_derive_exits` with a synthetic universe + filter pass-through.
    The synthetic frame is filtered the same way `_apply_filters` would, so
    filter-flow-through tests behave correctly."""
    state = {"df": _make_universe()}

    def _stub(filters: dict, exit_rule: dict) -> pd.DataFrame:
        df = state["df"].copy()
        for col, raw in (filters or {}).items():
            if raw in (None, ""):
                continue
            if col not in df.columns:
                continue
            vals = [v.strip() for v in str(raw).split(",") if v.strip()]
            if not vals:
                continue
            # Coerce to column dtype where simple
            if df[col].dtype.kind in ("i", "u"):
                vals_c = [int(v) for v in vals]
            elif df[col].dtype.kind == "f":
                vals_c = [float(v) for v in vals]
            elif df[col].dtype == bool:
                vals_c = [v.lower() in ("true", "1", "yes") for v in vals]
            else:
                vals_c = vals
            df = df[df[col].isin(vals_c)]
        return df.reset_index(drop=True)

    monkeypatch.setattr(m7_results, "_derive_exits", _stub)
    return state


@pytest.fixture
def patched_best_combo(monkeypatch):
    """Stub `m7_best_combo._GRID_STATE` with a ready synthetic grid.

    Grid mirrors the synthetic universe: one cell per (band, expiry, delta)
    pairing with two rules to exercise ranking-driven selection. Per band:
      - 30-40: cell A wins on credit, cell B wins on margin
      - 70-80: cell C wins on credit, cell D wins on margin
    """
    from app.api import m7_best_combo as bc

    grid = pd.DataFrame([
        # Band 30-40
        {"iv_band": "30-40", "expiry_bucket": "current (Sat)", "delta_target": 0.30,
         "rule_label": "baseline_sl100", "rule": {"premium_sl_pct": 100},
         "avg_pct_return_on_credit": 0.60, "avg_pct_return_on_margin": 0.10,
         "n_trades": 4},
        {"iv_band": "30-40", "expiry_bucket": "next (Sun)", "delta_target": 0.50,
         "rule_label": "max_profit_30", "rule": {"premium_sl_pct": 100, "max_profit_pct": 30},
         "avg_pct_return_on_credit": 0.20, "avg_pct_return_on_margin": 0.30,
         "n_trades": 4},
        # Band 70-80
        {"iv_band": "70-80", "expiry_bucket": "current (Sat)", "delta_target": 0.30,
         "rule_label": "baseline_sl100", "rule": {"premium_sl_pct": 100},
         "avg_pct_return_on_credit": 0.90, "avg_pct_return_on_margin": 0.15,
         "n_trades": 4},
        {"iv_band": "70-80", "expiry_bucket": "next (Sun)", "delta_target": 0.50,
         "rule_label": "max_profit_30", "rule": {"premium_sl_pct": 100, "max_profit_pct": 30},
         "avg_pct_return_on_credit": 0.10, "avg_pct_return_on_margin": 0.50,
         "n_trades": 4},
    ])
    state = {
        "status": "ready",
        "grid": grid,
        "rules_done": 21, "rules_total": 21,
        "started_at": 0, "finished_at": 0, "error": None,
    }
    monkeypatch.setattr(bc, "_GRID_STATE", state)
    return state


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_scope_null_matches_universe(patched_derive):
    out = _call()
    assert out["n_total"] == 16
    assert out["n_losses"] == 7  # 3 (cell B) + 4 (cell D)
    assert out["scope_summary"]["scope"] is None
    assert out["scope_summary"]["n_in_scope"] == 16
    assert out["scope_summary"]["exit_rule_overridden"] is False


def test_scope_full_coverage_picks_strict_best_cells(patched_derive):
    """With metric=avg_net_pnl, the strict-pick winner is cell A in band 30-40
    (avg 120) and cell C in band 70-80 (avg 180). Each has 4 trades.
    Scope returns ALL trades in those cells (matches the FC table's n_trades
    per band, summed across all best cells)."""
    out = _call(scope="full_coverage", metric="avg_net_pnl")
    assert out["scope_summary"]["scope"] == "full_coverage"
    assert out["n_total"] == 8
    assert out["n_losses"] == 0  # Both best cells are all-winners
    assert out["scope_summary"]["n_in_scope"] == 8
    # per_band_rules should list both bands with their cell coordinates
    rules = out["scope_summary"]["per_band_rules"]
    assert len(rules) == 2
    bands = {r["band"]: r for r in rules}
    assert "30-40" in bands and bands["30-40"]["n_trades"] == 4
    assert "70-80" in bands and bands["70-80"]["n_trades"] == 4


def test_scope_full_coverage_band_filter_reshapes_list(patched_derive):
    """Filter to only band 30-40: full coverage list becomes just cell A
    (the band's best). 4 trades, all winners."""
    out = _call(scope="full_coverage",
                                              metric="avg_net_pnl",
                                              entry_atm_iv_band="30-40")
    assert out["n_total"] == 4
    assert out["n_losses"] == 0
    # Confirm the filter narrowed to band 30-40 only — no 70-80 trades present.
    assert "70-80" not in out["by_band"]


def test_scope_full_coverage_avg_loss_usd_metric_picks_loser_cells(patched_derive):
    """With metric=avg_loss_usd (less-negative is "best"), neither all-winner
    cell qualifies (their avg_loss_usd is NaN — no losers). Strict pick falls
    back to whichever cell has the least-bad avg loss. Cell B's losers avg
    -90; cell D's avg -150. So band 30-40 picks cell B, band 70-80 picks D."""
    out = _call(scope="full_coverage",
                                              metric="avg_loss_usd")
    # Cell B = 4 trades (1 win, 3 losers), cell D = 4 losers → 8 total, 7 losers
    assert out["n_total"] == 8
    assert out["n_losses"] == 7


def test_scope_full_coverage_includes_metric_in_summary(patched_derive):
    out = _call(scope="full_coverage",
                                              metric="avg_net_pnl")
    assert out["scope_summary"]["metric"] == "avg_net_pnl"
    # ranking is irrelevant for full_coverage
    assert out["scope_summary"]["ranking"] is None


def test_scope_best_combo_credit_picks_high_credit_cells(patched_derive,
                                                          patched_best_combo):
    """ranking=credit picks band 30-40 cell A (credit=0.60) and band 70-80
    cell C (credit=0.90). Both are 4-trade winner cells → 8 trades, 0 losers."""
    out = _call(scope="best_combo",
                                              ranking="credit")
    assert out["scope_summary"]["scope"] == "best_combo"
    assert out["scope_summary"]["ranking"] == "credit"
    assert out["scope_summary"]["exit_rule_overridden"] is True
    assert out["n_total"] == 8
    assert out["n_losses"] == 0


def test_scope_best_combo_margin_differs_from_credit(patched_derive,
                                                      patched_best_combo):
    """ranking=margin picks band 30-40 cell B (margin=0.30) and band 70-80
    cell D (margin=0.50). Cell B = 4 trades (3 losers); cell D = 4 losers →
    8 total, 7 losers. This proves the override is plumbed through."""
    out = _call(scope="best_combo",
                                              ranking="margin")
    assert out["n_total"] == 8
    assert out["n_losses"] == 7


def test_scope_best_combo_response_includes_per_band_rules(patched_derive,
                                                            patched_best_combo):
    out = _call(scope="best_combo",
                                              ranking="credit")
    rules = out["scope_summary"]["per_band_rules"]
    assert len(rules) == 2
    bands = {r["band"] for r in rules}
    assert bands == {"30-40", "70-80"}
    for r in rules:
        assert "rule_label" in r and r["rule_label"]
        assert "rule_dict" in r and isinstance(r["rule_dict"], dict)
        assert r["rule_dict"].get("premium_sl_pct") == 100


def test_scope_best_combo_loss_cause_filter_applies(patched_derive,
                                                     patched_best_combo):
    """ranking=margin scopes to cells B and D (7 losers). All synthetic losers
    are tagged loss_cause='directional'. Filter loss_cause=vol_expansion →
    zero losers in scope; filter loss_cause=directional → all 7."""
    out_directional = _call(
        scope="best_combo", ranking="margin", loss_cause="directional")
    assert out_directional["n_losses"] == 7

    out_volexp = _call(
        scope="best_combo", ranking="margin", loss_cause="vol_expansion")
    assert out_volexp["n_losses"] == 0


def test_scope_best_combo_warming_returns_empty_with_progress(monkeypatch,
                                                               patched_derive):
    """When grid is still warming, return empty payload with warming hint."""
    from app.api import m7_best_combo as bc
    state = {
        "status": "warming",
        "grid": None,
        "rules_done": 5, "rules_total": 21,
        "started_at": 0, "finished_at": None, "error": None,
    }
    monkeypatch.setattr(bc, "_GRID_STATE", state)
    out = _call(scope="best_combo",
                                              ranking="credit")
    assert out["n_total"] == 0
    assert out["scope_summary"]["scope"] == "best_combo"
    assert out["scope_summary"].get("warming") is True
    assert out["scope_summary"]["rules_done"] == 5
    assert out["scope_summary"]["rules_total"] == 21

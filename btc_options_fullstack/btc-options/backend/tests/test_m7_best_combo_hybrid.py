"""Tests for the hybrid 243-rule menu in m7_best_combo_hybrid.py.

These tests are pure rule-menu validation — no DuckDB / no data /
no grid build. Fast (~1s total).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from app.api.m7_best_combo_hybrid import (
    _CAPITAL_SL_PCTS,
    _checkpoint_path,
    _flatten_for_parquet,
    _label_to_category,
    _load_checkpoint,
    _rule_variants_hybrid,
    _unflatten_after_load,
    _write_checkpoint,
)


# ── Rule count + category breakdown ───────────────────────────────────────────

def test_rule_count_is_243():
    variants = _rule_variants_hybrid()
    assert len(variants) == 243, (
        f"Expected 243 rules, got {len(variants)}. "
        f"Layer math: 105 singles + 3 cap_sl + 9 (2-way) + 126 (3-way) = 243"
    )


def test_category_breakdown_matches_layer_math():
    """The 243 rules should distribute across 7 categories exactly as
    the rule-layer arithmetic dictates."""
    variants = _rule_variants_hybrid()
    counts = {}
    for _, _, cat in variants:
        counts[cat] = counts.get(cat, 0) + 1

    expected = {
        "single_baseline": 3,                     # 3 SLs × 1 baseline
        "single_max_profit": 30,                  # 3 SLs × 10 max_profit
        "single_margin_target": 30,               # 3 SLs × 10 margin_target
        "single_exit_hr": 42,                     # 3 SLs × 14 exit_hr
        "single_capital_sl_standalone": 3,        # 3 cap_sl × 1
        "hybrid_2way_sl": 9,                      # 3 cap_sl × 3 SLs
        "hybrid_3way": 126,                       # 3 cap_sl × 3 SLs × 14 exit_hr
    }
    assert counts == expected, (
        f"Category breakdown mismatch.\n  expected: {expected}\n  actual:   {counts}"
    )


def test_total_matches_categories_sum():
    """243 should be the sum of every layer's count."""
    variants = _rule_variants_hybrid()
    assert len(variants) == 3 + 30 + 30 + 42 + 3 + 9 + 126


# ── Excluded combinations — user's explicit constraint ───────────────────────

def test_no_cap_sl_with_max_profit():
    """User excluded max_profit from hybrid combinations with cap_sl."""
    for label, rd, _cat in _rule_variants_hybrid():
        if rd.get("capital_sl_pct") is not None:
            assert rd.get("max_profit_pct") is None, (
                f"Rule {label} mixes capital_sl + max_profit — user excluded "
                f"this combination from hybrid scope"
            )


def test_no_cap_sl_with_margin_target():
    """User excluded margin_target from hybrid combinations with cap_sl."""
    for label, rd, _cat in _rule_variants_hybrid():
        if rd.get("capital_sl_pct") is not None:
            assert rd.get("margin_target_pct") is None, (
                f"Rule {label} mixes capital_sl + margin_target — user "
                f"excluded this combination from hybrid scope"
            )


# ── Standalone cap_sl semantics ──────────────────────────────────────────────

def test_standalone_cap_sl_has_no_premium_sl():
    """The 3 standalone cap_sl rules are 'cap_sl ONLY' — no premium_sl."""
    standalone = [(lbl, rd) for lbl, rd, cat in _rule_variants_hybrid()
                  if cat == "single_capital_sl_standalone"]
    assert len(standalone) == 3
    for label, rd in standalone:
        assert "premium_sl_pct" not in rd, (
            f"Standalone {label} should NOT have premium_sl_pct")
        assert "max_profit_pct" not in rd
        assert "margin_target_pct" not in rd
        assert "fixed_exit_hour_ist" not in rd
        assert rd["capital_sl_pct"] in _CAPITAL_SL_PCTS


def test_standalone_cap_sl_labels():
    """Standalone cap_sl labels should be 'capital_sl_10/15/20' — no SL prefix."""
    labels = sorted(lbl for lbl, _, cat in _rule_variants_hybrid()
                    if cat == "single_capital_sl_standalone")
    assert labels == ["capital_sl_10", "capital_sl_15", "capital_sl_20"]


# ── Existing 105 singles unchanged ───────────────────────────────────────────

def test_existing_105_singles_unchanged():
    """L1 (105 existing singles) should match m7_best_combo._rule_variants()."""
    from app.api.m7_best_combo import _rule_variants
    canonical_105 = _rule_variants("delta_match")
    assert len(canonical_105) == 105, "Canonical _rule_variants should return 105"

    hybrid_l1 = [(lbl, rd) for lbl, rd, cat in _rule_variants_hybrid()
                 if cat in {"single_baseline", "single_max_profit",
                            "single_margin_target", "single_exit_hr"}]
    assert len(hybrid_l1) == 105

    # Labels + rule_dicts must match
    can_dict = dict((lbl, rd) for lbl, rd in canonical_105)
    hyb_dict = dict((lbl, rd) for lbl, rd in hybrid_l1)
    assert can_dict == hyb_dict, (
        "Hybrid Layer 1 must exactly mirror canonical 105-rule menu"
    )


# ── Hybrid 2-way combos ──────────────────────────────────────────────────────

def test_hybrid_2way_combos():
    """L3 = 9 cap_sl × premium_sl 2-way combos. No other triggers."""
    twoway = [(lbl, rd) for lbl, rd, cat in _rule_variants_hybrid()
              if cat == "hybrid_2way_sl"]
    assert len(twoway) == 9
    for label, rd in twoway:
        assert "premium_sl_pct" in rd
        assert "capital_sl_pct" in rd
        assert "max_profit_pct" not in rd
        assert "margin_target_pct" not in rd
        assert "fixed_exit_hour_ist" not in rd
        # Label format: sl{X}_capital_sl_{Y}
        assert label.startswith("sl")
        assert "_capital_sl_" in label
        assert "_exit_hr_" not in label


def test_hybrid_3way_combos():
    """L4 = 126 cap_sl × premium_sl × exit_hr 3-way. All three triggers."""
    threeway = [(lbl, rd) for lbl, rd, cat in _rule_variants_hybrid()
                if cat == "hybrid_3way"]
    assert len(threeway) == 126
    for label, rd in threeway:
        assert "premium_sl_pct" in rd
        assert "capital_sl_pct" in rd
        assert "fixed_exit_hour_ist" in rd
        assert "max_profit_pct" not in rd
        assert "margin_target_pct" not in rd
        # Label format: sl{X}_capital_sl_{Y}_exit_hr_{H}
        assert label.startswith("sl")
        assert "_capital_sl_" in label
        assert "_exit_hr_" in label


# ── Label → category reverse lookup ──────────────────────────────────────────

def test_label_to_category_roundtrip():
    """Every (label, rule_dict, category) should satisfy
    _label_to_category(label) == category."""
    for label, _rd, expected_cat in _rule_variants_hybrid():
        got = _label_to_category(label)
        assert got == expected_cat, (
            f"Label {label!r}: expected category {expected_cat!r}, got {got!r}"
        )


# ── Uniqueness ───────────────────────────────────────────────────────────────

def test_labels_unique():
    labels = [lbl for lbl, _, _ in _rule_variants_hybrid()]
    assert len(labels) == len(set(labels)), (
        "Duplicate labels detected — rule menu has overlap"
    )


def test_rule_dicts_unique():
    """No two rules should have identical rule_dicts."""
    seen_dicts = set()
    for label, rd, _ in _rule_variants_hybrid():
        key = tuple(sorted(rd.items()))
        assert key not in seen_dicts, (
            f"Duplicate rule_dict for label {label}: {rd}"
        )
        seen_dicts.add(key)


# ── 17:29 fixed-hour preservation ────────────────────────────────────────────

def test_fixed_exit_hour_1729_in_3way_hybrids():
    """The 17:29 exit (17.4833) should appear in 3-way hybrids, labelled
    as '_exit_hr_1729'. Guards against int-coercion bugs."""
    threeway_1729 = [(lbl, rd) for lbl, rd, cat in _rule_variants_hybrid()
                     if cat == "hybrid_3way" and lbl.endswith("_exit_hr_1729")]
    assert len(threeway_1729) == 9, (
        f"Expected 9 (3 SL × 3 cap_sl) with 17:29 exit, got {len(threeway_1729)}"
    )
    for label, rd in threeway_1729:
        assert rd["fixed_exit_hour_ist"] == pytest.approx(17.4833), (
            f"Rule {label} should have fixed_exit_hour_ist=17.4833, "
            f"got {rd['fixed_exit_hour_ist']}"
        )


# ── Parquet round-trip ───────────────────────────────────────────────────────

def test_flatten_unflatten_roundtrip(tmp_path):
    """Flatten the rule menu to a parquet, read it back, verify rule
    dicts round-trip losslessly — especially the 17.4833 fractional hour."""
    variants = _rule_variants_hybrid()
    rows = [{"rule_label": lbl, "rule": rd, "rule_category": cat,
             "iv_band": "30-40", "expiry_bucket": "current_sat",
             "delta_target": 0.30, "entry_hour_ist": 23,
             "avg_net_pnl": 0.0, "n_trades": 1}
            for lbl, rd, cat in variants]
    df = pd.DataFrame(rows)

    flat = _flatten_for_parquet(df)
    out_path = tmp_path / "hybrid_grid.parquet"
    flat.to_parquet(out_path, compression="zstd", index=False)

    loaded = pd.read_parquet(out_path)
    restored = _unflatten_after_load(loaded)

    assert "rule" in restored.columns
    assert len(restored) == 243

    # Match label → rule dict before vs after round-trip
    before = {r["rule_label"]: r["rule"] for _, r in df.iterrows()}
    after = {r["rule_label"]: r["rule"] for _, r in restored.iterrows()}
    assert before.keys() == after.keys()
    for lbl in before:
        b_rule, a_rule = before[lbl], after[lbl]
        # Same keys
        assert set(b_rule.keys()) == set(a_rule.keys()), (
            f"Rule {lbl}: keys differ. before={b_rule}, after={a_rule}"
        )
        for k, v in b_rule.items():
            assert a_rule[k] == pytest.approx(v), (
                f"Rule {lbl} field {k}: before={v}, after={a_rule[k]}"
            )


def test_flatten_unflatten_preserves_1729_hour(tmp_path):
    """Explicit guard: fixed_exit_hour_ist=17.4833 must round-trip without
    int truncation (canonical _unflatten_after_load has a latent bug here
    that this module's version fixes)."""
    rule = {"premium_sl_pct": 100.0, "capital_sl_pct": 15.0,
            "fixed_exit_hour_ist": 17.4833}
    df = pd.DataFrame([{"rule_label": "sl100_capital_sl_15_exit_hr_1729",
                        "rule": rule, "rule_category": "hybrid_3way"}])
    flat = _flatten_for_parquet(df)
    out_path = tmp_path / "h.parquet"
    flat.to_parquet(out_path, index=False)
    loaded = _unflatten_after_load(pd.read_parquet(out_path))
    restored_rule = loaded.iloc[0]["rule"]
    assert restored_rule["fixed_exit_hour_ist"] == pytest.approx(17.4833, abs=1e-5)


# ── Checkpoint round-trip ────────────────────────────────────────────────────

def _sample_rows(rule_labels):
    """Build a list of cell-row dicts for a set of rule labels."""
    return [
        {"rule_label": lbl,
         "rule": {"premium_sl_pct": 50.0},
         "rule_category": "single_baseline",
         "iv_band": "30-40", "expiry_bucket": "current_sat",
         "delta_target": 0.30, "entry_hour_ist": 23,
         "avg_net_pnl": 1.5, "n_trades": 10}
        for lbl in rule_labels
    ]


def test_checkpoint_path_is_partial_suffix(tmp_path):
    """Default checkpoint path is the grid path with .partial appended."""
    cp = _checkpoint_path("/foo/bar.parquet")
    assert cp == "/foo/bar.parquet.partial"


def test_write_then_load_checkpoint_roundtrip(tmp_path):
    """Write 3 rules' rows, load — get back the same labels + payload."""
    cp = str(tmp_path / "grid.parquet.partial")
    rows = _sample_rows(["sl50_baseline", "sl75_baseline", "capital_sl_15"])
    _write_checkpoint(rows, cp)
    assert os.path.exists(cp)

    completed, loaded_rows = _load_checkpoint(cp)
    assert completed == {"sl50_baseline", "sl75_baseline", "capital_sl_15"}
    assert len(loaded_rows) == 3
    # Each loaded row has a `rule` dict reconstructed from flat columns
    for r in loaded_rows:
        assert isinstance(r["rule"], dict)
        assert r["rule"].get("premium_sl_pct") == 50.0


def test_load_checkpoint_missing_returns_empty(tmp_path):
    """Loading a non-existent checkpoint returns empty set + list."""
    cp = str(tmp_path / "nonexistent.partial")
    completed, rows = _load_checkpoint(cp)
    assert completed == set()
    assert rows == []


def test_load_checkpoint_empty_df_returns_empty(tmp_path):
    """An empty .partial parquet should produce empty resume state."""
    cp = str(tmp_path / "grid.parquet.partial")
    pd.DataFrame().to_parquet(cp, index=False)
    completed, rows = _load_checkpoint(cp)
    assert completed == set()
    assert rows == []


def test_write_checkpoint_empty_rows_is_noop(tmp_path):
    """Writing an empty row list doesn't create a partial file."""
    cp = str(tmp_path / "grid.parquet.partial")
    _write_checkpoint([], cp)
    assert not os.path.exists(cp)


def test_checkpoint_is_atomic(tmp_path):
    """Second write fully replaces the first — no leftover .tmp file."""
    cp = str(tmp_path / "grid.parquet.partial")
    _write_checkpoint(_sample_rows(["sl50_baseline"]), cp)
    _write_checkpoint(_sample_rows(["sl50_baseline", "sl75_baseline"]), cp)

    completed, _rows = _load_checkpoint(cp)
    assert completed == {"sl50_baseline", "sl75_baseline"}
    # No stray .tmp left behind
    assert not os.path.exists(cp + ".tmp")

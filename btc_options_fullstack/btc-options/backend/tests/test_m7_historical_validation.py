"""Slow historical-validation tests against the full M7 dataset.

These tests exercise the new analytics chunks against the actual 34,166-trade
backfill on disk (`m7_trades_enriched.parquet` + `m7_paths/`). They have
quantitative pass/fail bars defined in
`/home/abhis/.claude/plans/phase-1-defining-the-witty-dawn.md`.

Run with:
    pytest tests/test_m7_historical_validation.py -m slow -v

They are excluded from the default run because each test does a full
DuckDB scan over 121 path partitions (~5–15s/test).
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from app.api.m7_results import _derive_exits

M7_TRADES_ENRICHED = "/home/abhis/btc-data/derived/m7/m7_trades_enriched.parquet"


pytestmark = pytest.mark.slow


def _have_full_dataset() -> bool:
    return os.path.exists(M7_TRADES_ENRICHED)


@pytest.fixture(scope="module")
def all_exits() -> pd.DataFrame:
    """Derive exits with the empty rule (= hard cap = Sat 17:30 IST) over
    the full dataset. Cached at module scope so the multi-test scan only
    runs once."""
    if not _have_full_dataset():
        pytest.skip(f"Missing {M7_TRADES_ENRICHED}; run M7 backfill first")
    df = _derive_exits({}, {})
    assert not df.empty, "Empty derive_exits — full dataset should produce ~34k rows"
    return df


# ── Chunk 1: Per-leg attribution ─────────────────────────────────────────────

def test_chunk1_leg_pnl_sum_identity(all_exits):
    """For every trade, call_leg_pnl + put_leg_pnl == gross_pnl_usd within FP.
    Pass bar: max abs diff ≤ $0.05 per trade."""
    df = all_exits
    assert "call_leg_pnl_usd" in df.columns, "Chunk 1 leg PnL not computed"
    diff = (df["call_leg_pnl_usd"] + df["put_leg_pnl_usd"] - df["gross_pnl_usd"]).abs()
    max_diff = diff.max()
    assert max_diff < 0.05, (
        f"Per-trade leg PnL identity violated: max |sum − gross| = ${max_diff:.4f}. "
        f"Expected ≤ $0.05."
    )


def test_chunk1_leg_winner_classification_consistent(all_exits):
    """leg_winner must agree with the sign of call_leg_pnl + put_leg_pnl per row.
    Sanity gate: no row may have leg_winner='both' with a negative leg, etc."""
    df = all_exits
    c_pos = df["call_leg_pnl_usd"] > 0
    p_pos = df["put_leg_pnl_usd"] > 0
    expected = pd.Series("neither", index=df.index, dtype=object)
    expected[c_pos & p_pos] = "both"
    expected[c_pos & ~p_pos] = "call_only"
    expected[~c_pos & p_pos] = "put_only"
    mismatches = (df["leg_winner"] != expected).sum()
    assert mismatches == 0, f"{mismatches}/{len(df)} rows have inconsistent leg_winner"


def test_chunk1_call_only_share_responds_to_iv_skew(all_exits):
    """As IV skew shifts toward "put has higher IV" (more negative), the put
    leg decays faster relative to its rich starting price → put-only outcomes
    become more frequent. Equivalently, call_only should be MORE frequent
    when the CALL has higher IV (positive skew bucket).

    Pass bar: call_only_share is HIGHER in 'call_iv' / 'call_iv_strong' buckets
    than in 'put_iv' / 'put_iv_strong' buckets, controlling for delta_target.
    """
    df = all_exits
    # Fix delta_target=0.30 (most populous bucket); look across IV-skew buckets
    sub = df[df["delta_target"] == 0.30]
    by_bucket = sub.groupby("iv_skew_bucket")["leg_winner"].apply(
        lambda s: (s == "call_only").mean()
    )
    # Buckets we care about (some may be absent in narrow datasets)
    call_side = [b for b in ("call_iv", "call_iv_strong") if b in by_bucket.index]
    put_side  = [b for b in ("put_iv", "put_iv_strong")  if b in by_bucket.index]
    assert call_side, "No call-IV-skew bucket present at Δ=0.30"
    assert put_side,  "No put-IV-skew bucket present at Δ=0.30"
    avg_call_side = by_bucket[call_side].mean()
    avg_put_side  = by_bucket[put_side].mean()
    # Pass bar: call_only_share strictly higher when call IV is richer
    assert avg_call_side > avg_put_side, (
        f"Expected higher call_only_share in call-IV buckets vs put-IV buckets. "
        f"Got call_side={avg_call_side:.4f}, put_side={avg_put_side:.4f}"
    )


def test_chunk1_skew_bucket_universe_complete(all_exits):
    """All five buckets (put_strong, put, balanced, call, call_strong) must
    have AT LEAST ONE trade for each of the three skew columns. Otherwise
    the heatmap will have unpopulated rows/cols."""
    df = all_exits
    for col, expected in [
        ("delta_skew_bucket",
         {"put_richer_strong", "put_richer", "balanced", "call_richer", "call_richer_strong"}),
        ("iv_skew_bucket",
         {"put_iv_strong", "put_iv", "balanced", "call_iv", "call_iv_strong"}),
        ("premium_skew_bucket",
         {"put_premium_strong", "put_premium", "balanced", "call_premium", "call_premium_strong"}),
    ]:
        present = set(df[col].dropna().unique())
        missing = expected - present
        assert not missing, f"{col} missing buckets: {missing}"


def test_chunk1_per_leg_max_min_mtm_within_bounds(all_exits):
    """Per-leg max MTM during hold must be ≥ leg PnL at exit (entered up at
    some minute), and per-leg min MTM must be ≤ leg PnL at exit. Logical
    sanity, not a profit claim."""
    df = all_exits
    assert "call_leg_max_mtm_usd" in df.columns

    # Allow $0.10 FP tolerance for the comparison
    tol = 0.10

    bad_call_max = ((df["call_leg_max_mtm_usd"] + tol < df["call_leg_pnl_usd"])).sum()
    bad_call_min = ((df["call_leg_min_mtm_usd"] - tol > df["call_leg_pnl_usd"])).sum()
    bad_put_max  = ((df["put_leg_max_mtm_usd"]  + tol < df["put_leg_pnl_usd"])).sum()
    bad_put_min  = ((df["put_leg_min_mtm_usd"]  - tol > df["put_leg_pnl_usd"])).sum()

    assert bad_call_max == 0, f"{bad_call_max} trades have call_max_mtm < call_pnl"
    assert bad_call_min == 0, f"{bad_call_min} trades have call_min_mtm > call_pnl"
    assert bad_put_max == 0,  f"{bad_put_max} trades have put_max_mtm < put_pnl"
    assert bad_put_min == 0,  f"{bad_put_min} trades have put_min_mtm > put_pnl"

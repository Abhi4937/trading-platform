"""M7 joint delta + price strike picker.

Picks (CE, PE) strikes that minimize premium asymmetry while keeping each
leg's |delta| within a tolerance window of the target delta.

For target_delta >= 0.495 (straddle): identical behaviour to the existing
`pick_strikes()` in `m7_batch_backtester.py`.

For target_delta < 0.495 (strangle):
- Build CE/PE candidate pools where `target - delta_tol <= |delta| <= target + delta_tol`.
- For every (ce_idx, pe_idx) pair, score = |call_mark - put_mark|.
- Pick the pair with the smallest score.
- Accept if `score / mean(call_mark, put_mark) <= price_tol_pct`; else None.

Adaptive low-delta widening: when target_delta <= 0.10, effective delta_tol
becomes max(delta_tol, 0.5 * target_delta) — sparse OTM tails otherwise empty
one side of the candidate pool.

Returns None when no joint pair fits; caller falls back to delta-only.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from app.analytics.m7_batch_backtester import _bs_delta_vec, _solve_iv_vec
from app.core.greeks import compute_greeks, implied_vol

JOINT_DELTA_TOL = 0.05
JOINT_PRICE_TOL_PCT = 0.15

# When mean leg premium falls below this floor, the price-tolerance ratio
# becomes numerically unstable (e.g. a $0.30 gap on a $0.50 mean mark is 60%,
# but the dollar gap is meaningless). Below this floor we force a fallback
# to the delta-only picker so deep-OTM trades aren't rejected for spurious
# price asymmetry. $1.00 mirrors the contract slippage minimum.
_JOINT_MIN_MEAN_MARK_USD = 1.0


def _effective_delta_tol(target_delta: float, delta_tol: float) -> float:
    if target_delta <= 0.10:
        return max(delta_tol, 0.5 * target_delta)
    return delta_tol


def pick_strikes_joint(
    chain_at_ts: pd.DataFrame,
    spot: float,
    T_years: float,
    target_delta: float,
    delta_tol: float = JOINT_DELTA_TOL,
    price_tol_pct: float = JOINT_PRICE_TOL_PCT,
) -> Optional[dict]:
    """Joint delta + price strike picker.

    Returns dict with the same shape as `pick_strikes()` plus
    `match_mode='joint'`, `price_diff_usd`, `price_diff_pct`,
    `delta_diff_call`, `delta_diff_put`, `joint_picker_tolerance_delta`,
    `joint_picker_tolerance_price_pct`, or None on failure.
    """
    if chain_at_ts.empty or spot <= 0 or T_years <= 0:
        return None

    pivot = chain_at_ts.pivot_table(
        index="strike", columns="opt_type", values="mark_close", aggfunc="first"
    )
    if "CE" not in pivot.columns or "PE" not in pivot.columns:
        return None
    pivot = pivot.dropna(subset=["CE", "PE"], how="all").sort_index()
    if pivot.empty:
        return None
    strikes_arr = pivot.index.to_numpy(dtype="float64")
    ce_marks = pivot["CE"].to_numpy(dtype="float64")
    pe_marks = pivot["PE"].to_numpy(dtype="float64")

    if target_delta >= 0.495:
        idx = int(np.argmin(np.abs(strikes_arr - spot)))
        if (np.isnan(ce_marks[idx]) or np.isnan(pe_marks[idx]) or
                ce_marks[idx] <= 0 or pe_marks[idx] <= 0):
            return None
        K = float(strikes_arr[idx])
        cm, pm = float(ce_marks[idx]), float(pe_marks[idx])
        try:
            ci = implied_vol(cm, spot, K, T_years, 0.0, "call") or 0.0
            pi = implied_vol(pm, spot, K, T_years, 0.0, "put") or 0.0
            cd = compute_greeks(spot, K, T_years, 0.0, ci, "call").delta if ci > 0 else 0.0
            pd_ = compute_greeks(spot, K, T_years, 0.0, pi, "put").delta if pi > 0 else 0.0
        except Exception:
            return None
        diff = abs(cm - pm)
        mean_mark = (cm + pm) / 2.0
        # Low-premium floor: tiny premiums make price_diff_pct meaningless
        # (a few cents of mark noise dominates). Force fallback.
        if mean_mark < _JOINT_MIN_MEAN_MARK_USD:
            return None
        diff_pct = (diff / mean_mark) if mean_mark > 0 else float("nan")
        return {
            "call_strike": int(K), "put_strike": int(K),
            "call_mark": cm, "put_mark": pm,
            "call_iv": float(ci), "put_iv": float(pi),
            "call_delta": float(cd), "put_delta": float(pd_),
            "match_mode": "joint",
            "price_diff_usd": float(diff),
            "price_diff_pct": float(diff_pct),
            "delta_diff_call": float(abs(cd) - target_delta),
            "delta_diff_put": float(abs(pd_) - target_delta),
            "joint_picker_tolerance_delta": float(delta_tol),
            "joint_picker_tolerance_price_pct": float(price_tol_pct),
        }

    # Strangle path — joint cartesian search within delta window.
    eff_tol = _effective_delta_tol(target_delta, delta_tol)
    n = len(strikes_arr)
    is_call_arr = np.array([True] * n + [False] * n)
    Ks_all = np.concatenate([strikes_arr, strikes_arr])
    marks_all = np.concatenate([ce_marks, pe_marks])
    ivs = _solve_iv_vec(marks_all, spot, Ks_all, T_years, is_call_arr)
    deltas = _bs_delta_vec(spot, Ks_all, T_years, ivs, is_call_arr)
    call_deltas = deltas[:n]
    put_deltas = deltas[n:]
    call_ivs = ivs[:n]
    put_ivs = ivs[n:]

    target = abs(target_delta)
    lo = target - eff_tol
    hi = target + eff_tol

    ce_pool: list[int] = []
    for i in range(n):
        if np.isnan(call_deltas[i]) or ce_marks[i] <= 0:
            continue
        ad = abs(call_deltas[i])
        if lo <= ad <= hi:
            ce_pool.append(i)
    pe_pool: list[int] = []
    for i in range(n):
        if np.isnan(put_deltas[i]) or pe_marks[i] <= 0:
            continue
        ad = abs(put_deltas[i])
        if lo <= ad <= hi:
            pe_pool.append(i)
    if not ce_pool or not pe_pool:
        return None

    best_pair: Optional[tuple[int, int, float]] = None
    for ci_idx in ce_pool:
        cm = float(ce_marks[ci_idx])
        for pi_idx in pe_pool:
            pm = float(pe_marks[pi_idx])
            score = abs(cm - pm)
            if best_pair is None or score < best_pair[2]:
                best_pair = (ci_idx, pi_idx, score)
    if best_pair is None:
        return None
    ci_idx, pi_idx, score = best_pair
    cm = float(ce_marks[ci_idx])
    pm = float(pe_marks[pi_idx])
    mean_mark = (cm + pm) / 2.0
    if mean_mark <= 0:
        return None
    # Low-premium floor: below $1 mean mark, the price-tolerance ratio is
    # dominated by mark-noise and produces spurious rejections. Force the
    # caller to fall back to the delta-only picker.
    if mean_mark < _JOINT_MIN_MEAN_MARK_USD:
        return None
    diff_pct = score / mean_mark
    if diff_pct > price_tol_pct:
        return None

    cd = float(call_deltas[ci_idx])
    pd_ = float(put_deltas[pi_idx])
    return {
        "call_strike": int(strikes_arr[ci_idx]),
        "put_strike":  int(strikes_arr[pi_idx]),
        "call_mark":   cm,
        "put_mark":    pm,
        "call_iv":     float(call_ivs[ci_idx]) if not np.isnan(call_ivs[ci_idx]) else 0.0,
        "put_iv":      float(put_ivs[pi_idx])  if not np.isnan(put_ivs[pi_idx])  else 0.0,
        "call_delta":  cd,
        "put_delta":   pd_,
        "match_mode":  "joint",
        "price_diff_usd": float(score),
        "price_diff_pct": float(diff_pct),
        "delta_diff_call": float(abs(cd) - target_delta),
        "delta_diff_put":  float(abs(pd_) - target_delta),
        "joint_picker_tolerance_delta": float(delta_tol),
        "joint_picker_tolerance_price_pct": float(price_tol_pct),
    }

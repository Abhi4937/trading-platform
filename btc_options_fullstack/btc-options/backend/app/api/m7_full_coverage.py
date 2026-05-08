"""M7 IV-band full-coverage summary — every Friday assigned to one band.

Same headline shape as `/iv_band_summary` from `m7_results.py`, but with two
metric blocks per row so the user can see how the metrics drift once every
Friday is forced into one of the 10 best-cell rules.

Friday classification (one band per Friday):
  1. rule              — Friday has a trade matching some cell on all 4 dims
                         (band, hour, expiry, delta). Tie-broken by
                         net_pnl_estimate_usd.
  2. force_fit         — Friday has trade(s) matching some cell's
                         (hour, expiry, delta) but a different actual band.
                         Tie-broken by net_pnl_estimate_usd.
  3. closest_fallback  — Friday has no (hour, expiry, delta) match at any
                         cell. Pick (cell, friday_trade) with smallest
                         distance D = 100·|Δ_diff| + 10·|expiry_idx_diff|
                         + |hour_diff|; ties broken by net_pnl_estimate_usd.
  4. uncovered         — Friday has no trades in `derived` at all (filters
                         wiped it out). Counted at the response level only.

Endpoint:
  GET /api/v1/m7/iv_band_full_coverage
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from fastapi import APIRouter

from app.api.m7_results import (
    _derive_exits,
    _metric_score,
    _parse_exit_rule,
    _query_filters,
    _round_score,
    _to_records,
)

router = APIRouter()
log = logging.getLogger(__name__)


# Order constants for the closest-fallback distance metric.
EXPIRY_BUCKET_ORDER = [
    "current (Sat)",
    "next (Sun)",
    "next_to_next (Mon)",
    "weekly (7d)",
    "biweekly (14d)",
    "monthly (30d)",
    "quarterly",
]
# Fri 21:00 → Sat 03:00 IST mapped to a linear 0..6 axis so |hour_diff| is
# meaningful across the midnight wrap.
HOUR_LINEAR_ORDER = {21: 0, 22: 1, 23: 2, 0: 3, 1: 4, 2: 5, 3: 6}


# Same metric set as /iv_band_summary in m7_results.py — duplicated here to
# avoid coupling to a private module-level constant.
EXTRA_METRICS = [
    "avg_net_pnl", "win_rate",
    "avg_loss_usd", "avg_win_usd",
    "avg_credit", "avg_margin",
    "avg_pct_return_on_margin", "avg_pct_return_on_credit",
    "avg_pct_return_on_margin_winners", "avg_pct_return_on_credit_winners",
    "avg_max_mtm_winners", "avg_min_mtm_winners",
    "max_mtm_winners", "min_mtm_winners",
    "avg_max_mtm_losers", "avg_min_mtm_losers",
    "max_mtm_losers", "min_mtm_losers",
    "max_loss_usd", "max_win_usd",
    "avg_exit_mtm",
    "avg_win_mtm", "largest_win_mtm", "total_win_mtm",
    "avg_loss_mtm", "largest_loss_mtm", "total_loss_mtm",
    "avg_pct_max_mtm_on_credit", "avg_pct_min_mtm_on_credit",
    "n_rule_trigger", "n_hard_cap", "n_losses", "n_wins",
    "max_consec_losses", "max_consec_wins", "max_consec_sl_hits",
    "n_winners_below_avg_min_mtm", "n_losers_above_avg_max_mtm",
]


def _expiry_bucket_idx(eb) -> int:
    try:
        return EXPIRY_BUCKET_ORDER.index(str(eb))
    except ValueError:
        return 99


def _hour_linear(h) -> int:
    try:
        return HOUR_LINEAR_ORDER.get(int(h), 99)
    except (TypeError, ValueError):
        return 99


def _classify_fridays_to_cells(
    derived: pd.DataFrame,
    best_cells: pd.DataFrame,
    coverage_mode: str = "force_fit",
) -> pd.DataFrame:
    """Assign each Friday in `derived` to exactly one best-cell band.

    coverage_mode:
      - "force_fit" (default): step 2 relaxes band entirely; tiebreak by
        the trade's own net_pnl. Step 3 closest_fallback runs.
      - "touched_band": step 2 only considers cells whose band the
        Friday's IV actually touched at SOME hour during the day; tiebreak
        by the cell's historical avg_net_pnl ('score' col on best_cells).
        Closest_fallback is SKIPPED — Fridays with no touched-band match
        become uncovered.

    Returns one row per Friday with columns:
        friday_date_ist, trade_id, assigned_band, kind
    where kind ∈ {'rule', 'force_fit', 'touched_band', 'closest_fallback'}.
    """
    if derived.empty or best_cells.empty:
        return pd.DataFrame(columns=["friday_date_ist", "trade_id",
                                      "assigned_band", "kind"])

    cells = best_cells[["entry_atm_iv_band", "entry_hour_ist",
                        "expiry_bucket", "delta_target", "score"]].to_dict("records")

    out_rows: list[tuple] = []
    for friday, grp in derived.groupby("friday_date_ist", sort=False):
        # The chosen trade's ACTUAL `entry_atm_iv_band` is always the assigned
        # band — the cell rule is just used to *find* the right trade.
        # 1. Rule — strict 4-dim match (cell.band == trade.actual_band by definition)
        rule_candidates: list[tuple] = []
        for cell in cells:
            mask = (
                (grp["entry_atm_iv_band"] == cell["entry_atm_iv_band"]) &
                (grp["entry_hour_ist"] == cell["entry_hour_ist"]) &
                (grp["expiry_bucket"] == cell["expiry_bucket"]) &
                (grp["delta_target"] == cell["delta_target"])
            )
            if mask.any():
                t = grp[mask].iloc[0]
                pnl = float(t["net_pnl_estimate_usd"]) if pd.notna(t["net_pnl_estimate_usd"]) else float("-inf")
                rule_candidates.append((pnl, t["entry_atm_iv_band"], t["trade_id"]))
        if rule_candidates:
            rule_candidates.sort(key=lambda x: x[0], reverse=True)
            _, band, tid = rule_candidates[0]
            out_rows.append((str(friday), tid, band, "rule"))
            continue

        # 2. Relaxed-band match — keep (hour, expiry, delta), relax band.
        # In force_fit mode: any band; tiebreak by trade pnl.
        # In touched_band mode: only bands the Friday's IV touched at SOME
        # hour; tiebreak by cell historical avg.
        if coverage_mode == "touched_band":
            touched_bands = set(
                str(b) for b in grp["entry_atm_iv_band"].dropna().unique()
            )
            tb_candidates: list[tuple] = []
            for cell in cells:
                if str(cell["entry_atm_iv_band"]) not in touched_bands:
                    continue
                mask = (
                    (grp["entry_hour_ist"] == cell["entry_hour_ist"]) &
                    (grp["expiry_bucket"] == cell["expiry_bucket"]) &
                    (grp["delta_target"] == cell["delta_target"])
                )
                if mask.any():
                    t = grp[mask].iloc[0]
                    cell_score = (float(cell["score"])
                                  if pd.notna(cell["score"]) else float("-inf"))
                    tb_candidates.append(
                        (cell_score, t["entry_atm_iv_band"], t["trade_id"]))
            if tb_candidates:
                tb_candidates.sort(key=lambda x: x[0], reverse=True)
                _, band, tid = tb_candidates[0]
                out_rows.append((str(friday), tid, band, "touched_band"))
            # In touched_band mode, no closest_fallback — uncovered.
            continue

        # force_fit mode (default): relax band entirely.
        force_candidates: list[tuple] = []
        for cell in cells:
            mask = (
                (grp["entry_hour_ist"] == cell["entry_hour_ist"]) &
                (grp["expiry_bucket"] == cell["expiry_bucket"]) &
                (grp["delta_target"] == cell["delta_target"])
            )
            if mask.any():
                t = grp[mask].iloc[0]
                pnl = float(t["net_pnl_estimate_usd"]) if pd.notna(t["net_pnl_estimate_usd"]) else float("-inf")
                force_candidates.append((pnl, t["entry_atm_iv_band"], t["trade_id"]))
        if force_candidates:
            force_candidates.sort(key=lambda x: x[0], reverse=True)
            _, band, tid = force_candidates[0]
            out_rows.append((str(friday), tid, band, "force_fit"))
            continue

        # 3. Closest fallback — pick (cell, friday-trade) with min distance.
        # Same Y-rule: assigned band is the trade's actual band.
        fallback: list[tuple] = []
        for cell in cells:
            cell_h = _hour_linear(cell["entry_hour_ist"])
            cell_eb = _expiry_bucket_idx(cell["expiry_bucket"])
            cell_d = float(cell["delta_target"])
            for _, t in grp.iterrows():
                t_h = _hour_linear(t["entry_hour_ist"])
                t_eb = _expiry_bucket_idx(t["expiry_bucket"])
                t_d = float(t["delta_target"])
                dist = (
                    100 * abs(t_d - cell_d)
                    + 10 * abs(t_eb - cell_eb)
                    + abs(t_h - cell_h)
                )
                pnl = float(t["net_pnl_estimate_usd"]) if pd.notna(t["net_pnl_estimate_usd"]) else float("-inf")
                fallback.append((dist, -pnl, t["entry_atm_iv_band"], t["trade_id"]))
        if fallback:
            fallback.sort()
            _, _, band, tid = fallback[0]
            out_rows.append((str(friday), tid, band, "closest_fallback"))

    return pd.DataFrame(out_rows, columns=["friday_date_ist", "trade_id",
                                            "assigned_band", "kind"])


def _compute_metrics_for_subset(sub: pd.DataFrame, prefix: str = "") -> dict:
    """Run every EXTRA_METRIC against a single-row subset; return as dict.
    `prefix` lets us namespace into 'all_<metric>'."""
    out: dict = {}
    if sub.empty:
        for m in EXTRA_METRICS:
            out[f"{prefix}{m}"] = float("nan")
        return out
    sub_grp = sub.groupby(lambda _: 0)
    for m in EXTRA_METRICS:
        try:
            val = float(_metric_score(sub_grp, m).iloc[0])
        except Exception:
            val = float("nan")
        out[f"{prefix}{m}"] = val
    return out


@router.get("/iv_band_full_coverage")
def get_iv_band_full_coverage(
    exit_rule: Optional[str] = None,
    metric: str = "avg_net_pnl",
    delta_target: Optional[str] = None,
    is_straddle: Optional[str] = None,
    expiry_date: Optional[str] = None,
    expiry_bucket: Optional[str] = None,
    entry_atm_iv_band: Optional[str] = None,
    entry_hour_ist: Optional[str] = None,
    dte_bucket: Optional[str] = None,
    spot_bucket: Optional[str] = None,
    ivp_bucket: Optional[str] = None,
    ctx_pattern: Optional[str] = None,
    ctx_gex_regime: Optional[str] = None,
    friday_date_ist: Optional[str] = None,
    loss_cause: Optional[str] = None,
    coverage_mode: str = "force_fit",
):
    """Full-coverage IV-band summary: every Friday is attributed to exactly
    one of the 10 best cells. Each row carries TWO metric blocks:

      - `<metric>` (flat):           strict (band, hour, expiry, delta) subset
                                     — this matches /iv_band_summary today.
      - `all_<metric>` (prefixed):   strict ∪ force-fit ∪ closest-fallback.

    Plus per-row counts: `n_trades` (rule subset), `n_all`, `n_force_fit`,
    `n_closest_fallback`. Top-level: `n_total_fridays`, `n_rule_fridays`,
    `n_force_fit_fridays`, `n_closest_fallback_fridays`, `n_uncovered_fridays`.
    """
    filters = _query_filters(delta_target, is_straddle, expiry_date,
                              entry_atm_iv_band, entry_hour_ist, dte_bucket,
                              spot_bucket, ivp_bucket, ctx_pattern,
                              ctx_gex_regime, friday_date_ist, expiry_bucket,
                              loss_cause=loss_cause)
    rule = _parse_exit_rule(exit_rule)
    derived = _derive_exits(filters, rule)
    if derived.empty:
        return {
            "rows": [], "metric": metric,
            "n_total_fridays": 0, "n_rule_fridays": 0,
            "n_force_fit_fridays": 0, "n_closest_fallback_fridays": 0,
            "n_uncovered_fridays": 0,
        }

    # Best-cell selection — same rule as /iv_band_summary.
    dims = ["entry_atm_iv_band", "entry_hour_ist", "expiry_bucket", "delta_target"]
    grp = derived.groupby(dims, dropna=False)
    score = _metric_score(grp, metric)
    n = grp.size()
    df = pd.DataFrame({"score": score, "n_trades": n}).reset_index()

    df_valid = df.dropna(subset=["score"])
    strict = df_valid[df_valid["n_trades"] >= 3]
    strict_idx = (strict.groupby("entry_atm_iv_band", dropna=False)["score"].idxmax()
                  if not strict.empty else pd.Index([]))
    strict_best = strict.loc[strict_idx] if len(strict_idx) else strict.iloc[0:0]
    covered = set(strict_best["entry_atm_iv_band"].dropna().tolist())
    fallback = df_valid[~df_valid["entry_atm_iv_band"].isin(covered)]
    if not fallback.empty:
        fb_idx = fallback.groupby("entry_atm_iv_band", dropna=False)["score"].idxmax()
        fallback_best = fallback.loc[fb_idx]
        best = pd.concat([strict_best, fallback_best], ignore_index=True)
    else:
        best = strict_best.reset_index(drop=True)

    # Step A: rule_only metrics — strict subset (today's behavior).
    for m in EXTRA_METRICS:
        best[m] = float("nan")
        best[f"all_{m}"] = float("nan")
    for i, row in best.iterrows():
        sub = derived[
            (derived["entry_atm_iv_band"] == row["entry_atm_iv_band"]) &
            (derived["entry_hour_ist"] == row["entry_hour_ist"]) &
            (derived["expiry_bucket"] == row["expiry_bucket"]) &
            (derived["delta_target"] == row["delta_target"])
        ]
        rule_metrics = _compute_metrics_for_subset(sub, prefix="")
        for m in EXTRA_METRICS:
            best.at[i, m] = rule_metrics[m]

    # Step B: classify Fridays + all_fridays metrics.
    classified = _classify_fridays_to_cells(derived, best, coverage_mode=coverage_mode)
    if not classified.empty:
        # trade_id types must match for .isin to work safely
        classified["trade_id"] = classified["trade_id"].astype(
            derived["trade_id"].dtype, copy=False)

    best["n_all"] = 0
    best["n_force_fit"] = 0
    best["n_touched_band"] = 0
    best["n_closest_fallback"] = 0
    for i, row in best.iterrows():
        band = row["entry_atm_iv_band"]
        if classified.empty:
            continue
        band_rows = classified.loc[classified["assigned_band"] == band]
        band_tids = set(band_rows["trade_id"])
        sub = derived[derived["trade_id"].isin(band_tids)]
        best.at[i, "n_all"] = int(len(sub))
        best.at[i, "n_force_fit"] = int((band_rows["kind"] == "force_fit").sum())
        best.at[i, "n_touched_band"] = int((band_rows["kind"] == "touched_band").sum())
        best.at[i, "n_closest_fallback"] = int((band_rows["kind"] == "closest_fallback").sum())
        all_metrics = _compute_metrics_for_subset(sub, prefix="all_")
        for m in EXTRA_METRICS:
            best.at[i, f"all_{m}"] = all_metrics[f"all_{m}"]

    # Sort IV bands in natural order (0-20, 20-30, …, 100+).
    def _band_sort_key(b):
        if b == "100+":
            return 1000
        try:
            return int(str(b).split("-")[0])
        except (ValueError, AttributeError):
            return 9999

    best = best.sort_values("entry_atm_iv_band",
                            key=lambda s: s.map(_band_sort_key)).reset_index(drop=True)
    best["score"] = best["score"].apply(lambda v: _round_score(metric, v))
    for m in EXTRA_METRICS:
        if m in best.columns:
            best[m] = best[m].apply(lambda v: _round_score(m, v))
        if f"all_{m}" in best.columns:
            best[f"all_{m}"] = best[f"all_{m}"].apply(lambda v: _round_score(m, v))

    # Universe counts.
    n_total_fridays = int(derived["friday_date_ist"].astype(str).nunique())
    n_classified = int(len(classified))
    n_rule = int((classified["kind"] == "rule").sum()) if not classified.empty else 0
    n_force = int((classified["kind"] == "force_fit").sum()) if not classified.empty else 0
    n_touched = int((classified["kind"] == "touched_band").sum()) if not classified.empty else 0
    n_close = int((classified["kind"] == "closest_fallback").sum()) if not classified.empty else 0
    n_uncovered = max(0, n_total_fridays - n_classified)

    for c in ("n_all", "n_force_fit", "n_touched_band", "n_closest_fallback"):
        if c in best.columns:
            best[c] = best[c].fillna(0).astype(int)

    return {
        "rows": _to_records(best),
        "metric": metric,
        "coverage_mode": coverage_mode,
        "n_total_fridays": n_total_fridays,
        "n_rule_fridays": n_rule,
        "n_force_fit_fridays": n_force,
        "n_touched_band_fridays": n_touched,
        "n_closest_fallback_fridays": n_close,
        "n_uncovered_fridays": int(n_uncovered),
    }

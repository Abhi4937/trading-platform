"""Stage-1 Partial Exit — Per-Band 2D Sweep EV Analysis.

Runs a 4×5 grid (4 exit_fracs × 5 trigger_levels = 20 cells per band)
against existing exit-cache parquets. No path-walking, no simulator changes.

Usage:
    docker compose run --rm backend python -m app.scripts.stage1_partial_exit_per_band_2d \\
        [--input-json PATH]

Default PATH: /home/abhis/btc-data/derived/m7/stage1_analysis/per_band_rules.json

JSON shape:
{
  "dataset": "delta_match",
  "per_band_rules": {
    "<iv_band_label>": {
      "rule_label": "<display string>",
      "rule_dict": {
        "premium_sl_pct":    <number> | null,
        "max_profit_pct":    <number> | null,
        "margin_target_pct": <number> | null,
        "capital_sl_pct":    <number> | null,
        "fixed_exit_hr":     <number> | null
      },
      "expiry_bucket":  "<string>" | null,
      "delta_target":   <number>   | null,
      "entry_hour_ist": <number>   | null
    }
  }
}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

import numpy as np
import pandas as pd

from app.api.m7_results import _derive_exits
from app.api.m7_best_combo import _attach_risk_adjusted, _attach_composite_score_v2
from app.api.m7_ranking_config import LOW_N_THRESHOLD

# ─── Configuration ────────────────────────────────────────────────────────────

DEFAULT_INPUT_JSON = (
    "/home/abhis/btc-data/derived/m7/stage1_analysis/per_band_rules.json"
)
OUTPUT_DIR = "/home/abhis/btc-data/derived/m7/stage1_analysis"

EXIT_FRACS: list[float] = [0.25, 0.50, 0.75, 1.00]
TRIGGER_NAMES: list[str] = ["25_of_sl", "50_of_sl", "75_of_sl", "L_avg", "W_avg"]

# Composite suppressed for bands with fewer ranked cells than this.
MIN_RANKED_CELLS_FOR_COMPOSITE = 5

# Censoring warning thresholds.
SL_CENSORING_PCT_WARN = 0.30   # 75_of_sl: ≥30% of SL-hit troughs near SL_avg
L_AVG_CENSORING_PCT_WARN = 0.50  # L_avg: ≥50% of losers are SL-hit

CAVEATS = """
CAVEATS:
  1. Hypothetical P&L assumes fills at exactly trigger_MTM (no slippage or queue risk).
     EV numbers are upper bounds.
  2. Linear leg-movement assumption: closing exit_frac at trigger_MTM vs full position
     riding to net_pnl produces per-trade delta = exit_frac × (trigger_MTM − net_pnl).
     Non-linear intra-day paths may deviate.
  3. 75_of_sl trigger sits inside the censored zone — SL-hit troughs are biased toward
     0 because the SL fires at leg-mark = 2× credit. See per-band WARN lines.
  4. L_avg trigger is contaminated when a large fraction of losers are SL-hit (pct_losers_sl_hit).
     In that case L_avg ≈ SL_avg × that fraction, making L_avg behave like a fractional SL.
  5. Composite normalisation is per-band only (20 cells per band). Cross-band composite
     deltas are NOT comparable.
"""

# ─── JSON loading ─────────────────────────────────────────────────────────────

def _load_and_validate_json(path: str) -> dict:
    EXPECTED_SHAPE = """{
  "dataset": "delta_match",
  "per_band_rules": {
    "<band>": {
      "rule_label": "...", "rule_dict": {...},
      "expiry_bucket": null, "delta_target": null, "entry_hour_ist": null
    }
  }
}"""
    if not os.path.exists(path):
        print(f"\nERROR: input JSON not found at {path}\n\nExpected shape:\n{EXPECTED_SHAPE}")
        sys.exit(1)
    with open(path) as fh:
        data = json.load(fh)
    if "dataset" not in data or "per_band_rules" not in data:
        print(f"\nERROR: JSON missing 'dataset' or 'per_band_rules'.\n\nExpected shape:\n{EXPECTED_SHAPE}")
        sys.exit(1)
    if not isinstance(data["per_band_rules"], dict) or not data["per_band_rules"]:
        print("\nERROR: 'per_band_rules' must be a non-empty dict keyed by iv_band_label.")
        sys.exit(1)
    for band, cfg in data["per_band_rules"].items():
        for key in ("rule_label", "rule_dict"):
            if key not in cfg:
                print(f"\nERROR: band '{band}' missing '{key}'.")
                sys.exit(1)
    return data


# ─── Reference level computation ─────────────────────────────────────────────

def _compute_reference_levels(df: pd.DataFrame) -> dict:
    """SL_avg, L_avg, W_avg per band. All are None if n < LOW_N_THRESHOLD."""
    sl_mask = df["is_premium_sl_hit"].astype(bool)
    loser_mask = df["net_pnl_estimate_usd"] < 0
    winner_mask = ~loser_mask

    def _avg(mask: pd.Series) -> Optional[float]:
        sub = df.loc[mask, "min_mtm_usd"].dropna()
        return float(sub.mean()) if len(sub) >= LOW_N_THRESHOLD else None

    return {
        "SL_avg": _avg(sl_mask),
        "L_avg":  _avg(loser_mask),
        "W_avg":  _avg(winner_mask),
        "n_sl_hit":  int(sl_mask.sum()),
        "n_losers":  int(loser_mask.sum()),
        "n_winners": int(winner_mask.sum()),
    }


def _trigger_mtm(ref: dict, name: str) -> Optional[float]:
    """Resolve trigger level name to a negative USD threshold. None → undefined."""
    if name == "25_of_sl":
        return 0.25 * ref["SL_avg"] if ref["SL_avg"] is not None else None
    if name == "50_of_sl":
        return 0.50 * ref["SL_avg"] if ref["SL_avg"] is not None else None
    if name == "75_of_sl":
        return 0.75 * ref["SL_avg"] if ref["SL_avg"] is not None else None
    if name == "L_avg":
        return ref["L_avg"]
    if name == "W_avg":
        return ref["W_avg"]
    raise ValueError(f"Unknown trigger: {name}")


# ─── Case classification (per band × trigger_level) ──────────────────────────

def _classify_cases(df: pd.DataFrame, trigger_mtm_val: float) -> pd.DataFrame:
    """Adds 'case' column: 'A', 'B_reliable', 'B_unreliable', 'C'."""
    fires = df["min_mtm_usd"] <= trigger_mtm_val
    is_winner = df["net_pnl_estimate_usd"] >= 0
    # rel_time_max_mtm > rel_time_min_mtm means peak came AFTER trough → reliable
    peak_after_trough = df["rel_time_max_mtm"] > df["rel_time_min_mtm"]

    case = pd.Series("A", index=df.index, dtype=object)
    case[fires & is_winner & peak_after_trough]  = "B_reliable"
    case[fires & is_winner & ~peak_after_trough] = "B_unreliable"
    case[fires & ~is_winner]                      = "C"
    return case


# ─── Per-cell statistics ──────────────────────────────────────────────────────

def _max_consec_losses_sorted(is_win_arr: np.ndarray) -> int:
    """Max consecutive losses in a pre-sorted (chronological) bool array."""
    if len(is_win_arr) == 0:
        return 0
    not_win = ~is_win_arr.astype(bool)
    changes = np.diff(not_win.astype(int), prepend=0, append=0) != 0
    bounds = np.where(changes)[0]
    lengths = np.diff(bounds)
    loss_lengths = lengths[not_win[bounds[:-1]]]
    return int(loss_lengths.max()) if len(loss_lengths) > 0 else 0


def _cvar_95(arr: np.ndarray) -> float:
    """CVaR: mean of values ≤ 5th percentile. NaN if n < 2."""
    s = arr[np.isfinite(arr)]
    if len(s) < 2:
        return float("nan")
    threshold = float(np.quantile(s, 0.05))
    tail = s[s <= threshold]
    return float(tail.mean()) if len(tail) > 0 else float("nan")


def _cell_metrics(
    df: pd.DataFrame,
    case: pd.Series,
    trigger_mtm_val: float,
    exit_frac: float,
) -> dict:
    """Compute all statistics for one (trigger_level, exit_frac) cell.

    Linear leg-movement assumption: closing exit_frac at trigger_MTM vs riding
    to net_pnl produces per-trade delta = exit_frac × (trigger_MTM − net_pnl).
    """
    n_total = len(df)
    pnl = df["net_pnl_estimate_usd"].to_numpy(dtype=float)

    n_A             = int((case == "A").sum())
    n_B_reliable    = int((case == "B_reliable").sum())
    n_B_unreliable  = int((case == "B_unreliable").sum())
    n_C             = int((case == "C").sum())

    # Signed per-trade delta: exit_frac × (trigger_MTM − net_pnl).
    #   >0 → partial exit saves money (e.g. loser ends deeper than trigger)
    #   <0 → partial exit locks in a worse outcome (winner gives up profit, or
    #          loser hits trigger then recovers above it in the C sub-case)
    # Using signed deltas throughout keeps ev_total_audited == total_delta_pnl
    # by construction, resolving the reconciliation gate.
    c_idx    = case == "C"
    b_rel_idx = case == "B_reliable"
    b_unrel_idx = case == "B_unreliable"
    fired_idx = case != "A"

    delta_c      = exit_frac * (trigger_mtm_val - pnl[c_idx.to_numpy()])
    delta_b_rel  = exit_frac * (trigger_mtm_val - pnl[b_rel_idx.to_numpy()])
    delta_b_unrel = exit_frac * (trigger_mtm_val - pnl[b_unrel_idx.to_numpy()])

    # ev_total: sum of signed deltas over B_reliable + C (B_unreliable excluded).
    ev_total = float(np.sum(delta_c) + np.sum(delta_b_rel)) if (n_C + n_B_reliable) > 0 else 0.0
    ev_per_trade = ev_total / n_total

    # Audited EV: include B_unreliable so ev_total_audited == total_delta_pnl.
    ev_total_audited = float(ev_total + np.sum(delta_b_unrel))

    # avg_saved: mean SIGNED delta for C trades (positive = actual save; negative = hurt).
    avg_saved    = float(delta_c.mean())    if n_C > 0         else float("nan")
    # avg_given_up: mean |delta| for B_reliable trades (always a cost); stored as positive.
    avg_given_up = float(-delta_b_rel.mean()) if n_B_reliable > 0 else float("nan")

    pct_B_unreliable = (
        (n_B_unreliable / (n_B_reliable + n_B_unreliable))
        if (n_B_reliable + n_B_unreliable) > 0 else 0.0
    )

    # Hypothetical P&L per trade.
    fired_mask = case != "A"
    hyp = pnl.copy()
    fired_idx = fired_mask.to_numpy()
    hyp[fired_idx] = (
        exit_frac * trigger_mtm_val + (1.0 - exit_frac) * pnl[fired_idx]
    )

    # Aggregate hypothetical stats.
    avg_hyp   = float(np.nanmean(hyp))
    std_hyp   = float(np.nanstd(hyp, ddof=1)) if n_total > 1 else float("nan")
    wr_hyp    = float(np.mean(hyp >= 0))
    max_loss  = float(np.nanmin(hyp))   # most negative trade

    loser_hyp = hyp[hyp < 0]
    std_losses = float(np.std(loser_hyp, ddof=1)) if len(loser_hyp) >= 2 else float("nan")

    cvar_hyp  = _cvar_95(hyp)

    # Return on margin (using actual per-trade margin, unaffected by exit timing).
    if "margin_used_usd_at_entry" in df.columns:
        margin = df["margin_used_usd_at_entry"].to_numpy(dtype=float)
        valid  = (margin > 0) & np.isfinite(margin) & np.isfinite(hyp)
        avg_rom = float(np.mean((hyp[valid] / margin[valid]) * 100.0)) if valid.any() else float("nan")
    else:
        avg_rom = float("nan")

    # avg_credit (baseline, not modified by hypothetical).
    avg_credit = (
        float(df["credit_usd"].mean()) if "credit_usd" in df.columns else float("nan")
    )

    # Max consecutive losses (chronological, hypothetical).
    if "friday_date_ist" in df.columns:
        order = df["friday_date_ist"].argsort(kind="stable").to_numpy()
        hyp_sorted = hyp[order]
        max_consec = _max_consec_losses_sorted(hyp_sorted >= 0)
    else:
        max_consec = 0

    # Total delta P&L (actual → hypothetical) — must equal ev_total_audited.
    total_delta_pnl = float(np.sum(hyp - pnl))

    return {
        "n_total":          n_total,
        "n_A":              n_A,
        "n_B_reliable":     n_B_reliable,
        "n_B_unreliable":   n_B_unreliable,
        "n_C":              n_C,
        "pct_B_unreliable": pct_B_unreliable,
        "avg_saved":        avg_saved,
        "avg_given_up":     avg_given_up,
        "ev_per_trade":     ev_per_trade,
        "ev_total":         ev_total,
        "ev_total_audited": ev_total_audited,
        "total_delta_pnl":  total_delta_pnl,
        # Hypothetical aggregate columns (named to match _attach_risk_adjusted / _attach_composite_score_v2).
        "avg_net_pnl":              avg_hyp,
        "stdev_net_pnl":            std_hyp,
        "stdev_losses_only":        std_losses,
        "max_loss_usd":             max_loss,
        "win_rate":                 wr_hyp,
        "cvar_95_net":              cvar_hyp,
        "avg_pct_return_on_margin": avg_rom,
        "avg_credit":               avg_credit,
        "max_consec_losses":        max_consec,
        # Actual baseline avg (for delta_from_actual reporting).
        "actual_avg_net_pnl":       float(np.nanmean(pnl)),
    }


# ─── Per-band processing ──────────────────────────────────────────────────────

def _process_band(
    band: str,
    df: pd.DataFrame,
    cfg: dict,
    ref: dict,
) -> tuple[list[dict], list[dict]]:
    """Return (all_cells_rows, distribution_rows) for one band.

    Classification is computed once per (band, trigger_level) and shared
    across all 4 exit_frac values — 50 classifications, not 200.
    """
    n_total = len(df)
    baseline_rule_label = cfg["rule_label"]
    actual_avg_pnl = float(df["net_pnl_estimate_usd"].mean())

    # ── Per-band censoring transparency ──────────────────────────────────────
    sl_mask   = df["is_premium_sl_hit"].astype(bool)
    loser_mask = df["net_pnl_estimate_usd"] < 0
    n_losers   = int(loser_mask.sum())
    pct_losers_sl_hit = (
        float((loser_mask & sl_mask).sum()) / n_losers
        if n_losers > 0 else 0.0
    )

    # ── 75_of_sl censoring check (will warn later) ───────────────────────────
    warn_75sl = False
    pct_sl_near_avg = 0.0
    if ref["SL_avg"] is not None and ref["n_sl_hit"] > 0:
        sl_troughs = df.loc[sl_mask, "min_mtm_usd"].dropna()
        near = ((sl_troughs - ref["SL_avg"]).abs() / abs(ref["SL_avg"])) <= 0.10
        pct_sl_near_avg = float(near.mean())
        warn_75sl = pct_sl_near_avg >= SL_CENSORING_PCT_WARN

    all_cells_rows: list[dict] = []

    # Classify once per (trigger_level). Store cases for reuse across exit_fracs.
    trigger_case_cache: dict[str, Optional[pd.Series]] = {}
    for tname in TRIGGER_NAMES:
        tmtm = _trigger_mtm(ref, tname)
        if tmtm is None:
            trigger_case_cache[tname] = None
        else:
            trigger_case_cache[tname] = _classify_cases(df, tmtm)

    # Track which (exit_frac, trigger_level) cells to use for distribution.
    best_avg_pnl_cell: Optional[tuple[float, str]] = None
    best_composite_cell: Optional[tuple[float, str]] = None  # set after composite

    # Build all 20 cells.
    for tname in TRIGGER_NAMES:
        tmtm = _trigger_mtm(ref, tname)
        case = trigger_case_cache[tname]

        for ef in EXIT_FRACS:
            row: dict = {
                "band": band,
                "baseline_rule_label": baseline_rule_label,
                "exit_frac": ef,
                "trigger_level": tname,
                "trigger_mtm": tmtm if tmtm is not None else float("nan"),
            }

            if case is None:
                row["status"] = "trigger_undefined"
                row["warning"] = f"{tname}: reference level undefined (n < {LOW_N_THRESHOLD})"
                # Fill NaN for all metric columns.
                for col in ("n_total", "n_A", "n_B_reliable", "n_B_unreliable", "n_C",
                             "pct_B_unreliable", "avg_saved", "avg_given_up",
                             "ev_per_trade", "ev_total", "ev_total_audited", "total_delta_pnl",
                             "avg_net_pnl", "stdev_net_pnl", "stdev_losses_only", "max_loss_usd",
                             "win_rate", "cvar_95_net", "avg_pct_return_on_margin", "avg_credit",
                             "max_consec_losses", "actual_avg_net_pnl"):
                    row[col] = float("nan") if col not in ("n_total",) else n_total
                row["n_total"] = n_total
                all_cells_rows.append(row)
                continue

            metrics = _cell_metrics(df, case, tmtm, ef)
            row.update(metrics)

            warnings: list[str] = []
            if tname == "75_of_sl" and warn_75sl:
                warnings.append(
                    f"75_of_sl SL-censoring: {pct_sl_near_avg:.0%} of SL-hit troughs cluster "
                    f"near SL_avg — trigger may be biased upward"
                )
            if metrics["pct_B_unreliable"] >= 0.30:
                warnings.append(
                    f"B_unreliable_rate={metrics['pct_B_unreliable']:.0%} — "
                    f"peak-before-trough fraction is high"
                )
            row["status"] = "ok"
            row["warning"] = "; ".join(warnings)
            all_cells_rows.append(row)

    # ── Attach risk-adjusted + composite to the 20-cell band frame ───────────
    cells_df = pd.DataFrame(all_cells_rows)
    ranked_mask = cells_df["status"] == "ok"
    # All ranked cells get rank_status="ranked" — no hard gates in this script.
    cells_df["rank_status"] = np.where(ranked_mask, "ranked", "filtered")
    cells_df["iv_band"] = cells_df["band"]  # column alias for _attach_composite_score_v2

    if ranked_mask.sum() > 0:
        cells_df = _attach_risk_adjusted(cells_df)
        if ranked_mask.sum() >= MIN_RANKED_CELLS_FOR_COMPOSITE:
            cells_df = _attach_composite_score_v2(cells_df, group_keys=("iv_band",))
        else:
            cells_df["composite_score_v2"] = float("nan")
            cells_df["rank_in_band"] = pd.NA
            cells_df["composite_score_v2_components_used"] = pd.NA
            cells_df["score_components"] = ""
    else:
        for col in ("sortino_per_trade", "calmar_like", "sharpe_per_trade",
                    "composite_score_v2", "rank_in_band",
                    "composite_score_v2_components_used", "score_components"):
            cells_df[col] = float("nan")

    # ── avg_pnl rank within band ──────────────────────────────────────────────
    ok_idx = cells_df.index[cells_df["status"] == "ok"]
    if len(ok_idx) > 0:
        avg_pnl_vals = pd.to_numeric(cells_df.loc[ok_idx, "avg_net_pnl"], errors="coerce")
        avg_pnl_rank = avg_pnl_vals.rank(method="dense", ascending=False).astype("Int64")
        cells_df["avg_pnl_rank_in_band"] = pd.NA
        cells_df.loc[ok_idx, "avg_pnl_rank_in_band"] = avg_pnl_rank
    else:
        cells_df["avg_pnl_rank_in_band"] = pd.NA

    # ── Best-cell selection per band ─────────────────────────────────────────
    cells_df["is_best_by_avg_pnl"]      = False
    cells_df["is_best_by_composite_v2"] = False

    def _best_by(col: str, prefer_small_ef: bool = True) -> Optional[int]:
        """Return index of best ranked cell, tie-break by smaller exit_frac."""
        sub = cells_df.loc[ok_idx].copy()
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
        sub = sub.dropna(subset=[col])
        if sub.empty:
            return None
        best_val = sub[col].max()
        candidates = sub[sub[col] >= best_val - 1e-9]
        if prefer_small_ef:
            candidates = candidates.sort_values("exit_frac")
        return int(candidates.index[0])

    best_avg_idx  = _best_by("avg_net_pnl")
    best_comp_idx = _best_by("composite_score_v2")

    if best_avg_idx is not None:
        cells_df.loc[best_avg_idx, "is_best_by_avg_pnl"] = True
    if best_comp_idx is not None:
        cells_df.loc[best_comp_idx, "is_best_by_composite_v2"] = True

    # ── Cross-metric ranks for best_cells.csv ────────────────────────────────
    best_avg_composite_rank = None
    best_comp_avg_pnl_rank  = None
    if best_avg_idx is not None and "rank_in_band" in cells_df.columns:
        val = cells_df.at[best_avg_idx, "rank_in_band"]
        best_avg_composite_rank = int(val) if pd.notna(val) else None
    if best_comp_idx is not None and "avg_pnl_rank_in_band" in cells_df.columns:
        val = cells_df.at[best_comp_idx, "avg_pnl_rank_in_band"]
        best_comp_avg_pnl_rank = int(val) if pd.notna(val) else None

    cells_agree = (
        best_avg_idx is not None
        and best_comp_idx is not None
        and best_avg_idx == best_comp_idx
    )

    # Store band-level summary as side-channel (returned via mutation of cells_df).
    cells_df.attrs["best_avg_idx"]              = best_avg_idx
    cells_df.attrs["best_comp_idx"]             = best_comp_idx
    cells_df.attrs["cells_agree"]               = cells_agree
    cells_df.attrs["best_avg_composite_rank"]   = best_avg_composite_rank
    cells_df.attrs["best_comp_avg_pnl_rank"]    = best_comp_avg_pnl_rank
    cells_df.attrs["actual_avg_net_pnl"]        = actual_avg_pnl
    cells_df.attrs["pct_losers_sl_hit"]         = pct_losers_sl_hit
    cells_df.attrs["warn_75sl"]                 = warn_75sl
    cells_df.attrs["pct_sl_near_avg"]           = pct_sl_near_avg
    cells_df.attrs["ref"]                       = ref

    # ── Distribution rows ─────────────────────────────────────────────────────
    distribution_rows: list[dict] = []
    dist_cells: list[tuple[str, int]] = []  # (selector, idx)
    if best_avg_idx is not None:
        dist_cells.append(("best_by_avg_pnl", best_avg_idx))
    if best_comp_idx is not None and not cells_agree:
        dist_cells.append(("best_by_composite_v2", best_comp_idx))

    pnl_actual = df["net_pnl_estimate_usd"].to_numpy(dtype=float)

    for selector, cidx in dist_cells:
        row_c = cells_df.loc[cidx]
        ef    = float(row_c["exit_frac"])
        tmtm  = float(row_c["trigger_mtm"])
        tname = str(row_c["trigger_level"])
        case  = trigger_case_cache.get(tname)
        if case is None or not np.isfinite(tmtm):
            continue

        hyp = pnl_actual.copy()
        fired_idx = (case != "A").to_numpy()
        hyp[fired_idx] = ef * tmtm + (1.0 - ef) * pnl_actual[fired_idx]

        all_vals = np.concatenate([pnl_actual[np.isfinite(pnl_actual)],
                                    hyp[np.isfinite(hyp)]])
        lo, hi = float(all_vals.min()), float(all_vals.max())
        if hi <= lo:
            continue
        bins = np.linspace(lo, hi, 21)
        for i in range(20):
            bin_lo, bin_hi = bins[i], bins[i + 1]
            mid = 0.5 * (bin_lo + bin_hi)
            n_actual = int(((pnl_actual >= bin_lo) & (pnl_actual < bin_hi)).sum())
            n_hyp    = int(((hyp >= bin_lo) & (hyp < bin_hi)).sum())
            distribution_rows.append({
                "band": band,
                "selector": selector,
                "exit_frac": ef,
                "trigger_level": tname,
                "bin_index": i,
                "bin_mid": mid,
                "bin_lo": bin_lo,
                "bin_hi": bin_hi,
                "n_actual": n_actual,
                "n_hypothetical": n_hyp,
            })

    return cells_df, distribution_rows


# ─── Phase 7 validation gates ─────────────────────────────────────────────────

def _run_validation(
    all_cells_df: pd.DataFrame,
    per_band_dfs: dict[str, pd.DataFrame],
    per_band_frames: dict[str, pd.DataFrame],
) -> None:
    """Fail loudly on any gate miss."""
    print("\n── Phase 7: Validation gates ──────────────────────────────────────")

    # Gate 1: case count identity per (band, trigger_level).
    # per_band_dfs contains the 20-cell cells_df; n_total is in the row itself.
    ok = True
    for band in per_band_dfs:
        band_cells = all_cells_df[all_cells_df["band"] == band].copy()
        for tname in TRIGGER_NAMES:
            row = band_cells[
                (band_cells["trigger_level"] == tname) & (band_cells["exit_frac"] == 0.25)
            ]
            if row.empty or row.iloc[0]["status"] == "trigger_undefined":
                continue
            r = row.iloc[0]
            total    = int(r["n_A"]) + int(r["n_B_reliable"]) + int(r["n_B_unreliable"]) + int(r["n_C"])
            expected = int(r["n_total"])
            if total != expected:
                print(f"  FAIL gate 1: band={band} trigger={tname}: "
                      f"A+B_rel+B_unrel+C={total} != n_total={expected}")
                ok = False
    if ok:
        print("  PASS gate 1: n_A + n_B_reliable + n_B_unreliable + n_C == n_total (all bands)")

    # Gate 2: |total_delta_pnl − ev_total_audited| < $1 per ranked cell.
    ranked = all_cells_df[all_cells_df["status"] == "ok"].copy()
    for col in ("total_delta_pnl", "ev_total_audited"):
        ranked[col] = pd.to_numeric(ranked[col], errors="coerce")
    diff = (ranked["total_delta_pnl"] - ranked["ev_total_audited"]).abs()
    bad = diff[diff > 1.0]
    if len(bad) == 0:
        print("  PASS gate 2: |total_delta_pnl − ev_total_audited| < $1 for all ranked cells")
    else:
        for idx in bad.index:
            r = ranked.loc[idx]
            print(f"  FAIL gate 2: band={r['band']} trigger={r['trigger_level']} "
                  f"ef={r['exit_frac']}: delta_pnl={r['total_delta_pnl']:.2f} "
                  f"ev_audited={r['ev_total_audited']:.2f} diff={bad[idx]:.2f}")
        ok = False

    # Gate 3: at exit_frac=1.00, every fired trade's hypothetical == trigger_MTM.
    # Verify via manual recomputation for the first (band, trigger_level) with n_fired ≥ 20.
    spot_done = False
    for band, df in per_band_frames.items():
        for tname in TRIGGER_NAMES:
            row = all_cells_df[
                (all_cells_df["band"] == band)
                & (all_cells_df["trigger_level"] == tname)
                & (all_cells_df["exit_frac"] == 1.00)
            ]
            if row.empty or row.iloc[0]["status"] == "trigger_undefined":
                continue
            r    = row.iloc[0]
            tmtm = float(r["trigger_mtm"])
            pnl  = df["net_pnl_estimate_usd"].to_numpy(dtype=float)
            fired = df["min_mtm_usd"].to_numpy(dtype=float) <= tmtm
            n_fired = int(fired.sum())
            if n_fired < 20:
                continue
            # total_delta_pnl = Σ over fired trades of (trigger_MTM − net_pnl)
            manual_tdp = float(np.sum((tmtm - pnl[fired])))
            cell_tdp   = float(r["total_delta_pnl"])
            diff_spot  = abs(manual_tdp - cell_tdp)
            print(f"  Degenerate-edge spot check (exit_frac=1.00, band={band}, trigger={tname}, "
                  f"n_fired={n_fired}):")
            print(f"    manual total_delta_pnl = ${manual_tdp:.4f}")
            print(f"    cell   total_delta_pnl = ${cell_tdp:.4f}")
            print(f"    diff                   = ${diff_spot:.4f}")
            if diff_spot < 0.01:
                print("  PASS gate 3: degenerate-edge manual reconciliation")
            else:
                print("  FAIL gate 3: degenerate-edge spot check exceeds $0.01")
                ok = False
            spot_done = True
            break
        if spot_done:
            break
    if not spot_done:
        print("  SKIP gate 3: no (band, trigger_level) at exit_frac=1.00 has n_fired ≥ 20")

    # Gate 4: flags consistent in all_cells.csv.
    for band in all_cells_df["band"].unique():
        band_df = all_cells_df[all_cells_df["band"] == band]
        best_avg_rows = band_df[band_df["is_best_by_avg_pnl"]]
        best_comp_rows = band_df[band_df["is_best_by_composite_v2"]]
        if len(best_avg_rows) == 1 and len(best_comp_rows) == 1:
            if best_avg_rows.index[0] == best_comp_rows.index[0]:
                # They agree — verify both flags are True on the same row.
                idx = best_avg_rows.index[0]
                if not (all_cells_df.loc[idx, "is_best_by_avg_pnl"]
                        and all_cells_df.loc[idx, "is_best_by_composite_v2"]):
                    print(f"  FAIL gate 4: band={band} cells agree but flags inconsistent")
                    ok = False

    if ok:
        print("  PASS gate 4: best-cell flags consistent in all_cells.csv")

    if ok:
        print("  All validation gates passed.\n")
    else:
        print("\n  !! One or more validation gates FAILED — results may be incorrect !!\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-json", default=DEFAULT_INPUT_JSON,
        help="Path to per_band_rules.json (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── Load and validate JSON ────────────────────────────────────────────────
    data    = _load_and_validate_json(args.input_json)
    dataset = str(data["dataset"])
    bands   = data["per_band_rules"]

    print(f"\n=== Stage-1 Partial Exit — 2D Sweep ===")
    print(f"Input JSON : {args.input_json}")
    print(f"Dataset    : {dataset}")
    print(f"Bands      : {list(bands.keys())}")
    print(f"Exit fracs : {EXIT_FRACS}")
    print(f"Triggers   : {TRIGGER_NAMES}")
    print()

    # ── Phase 2: Load baseline frames ────────────────────────────────────────
    per_band_frames: dict[str, pd.DataFrame] = {}
    print("── Phase 2: Loading baseline frames ────────────────────────────────")
    for band, cfg in bands.items():
        rule_dict = {k: v for k, v in cfg["rule_dict"].items() if v is not None}
        filters: dict = {"entry_atm_iv_band": band}
        for fkey in ("expiry_bucket", "delta_target", "entry_hour_ist"):
            if cfg.get(fkey) is not None:
                filters[fkey] = cfg[fkey]

        df = _derive_exits(filters=filters, exit_rule=rule_dict, dataset=dataset)
        if df.empty:
            print(f"  SKIP band={band}: _derive_exits returned 0 rows for filters={filters}")
            continue
        per_band_frames[band] = df.reset_index(drop=True)
        print(f"  band={band}: n_total={len(df)}, rule={cfg['rule_label']}, filters={filters}")

    if not per_band_frames:
        print("\nERROR: no bands returned any data. Check JSON filters and dataset.")
        sys.exit(1)

    # ── Phase 3/4: Per-band sweep ─────────────────────────────────────────────
    print("\n── Phase 3/4: Per-band reference levels & 2D sweep ─────────────────")
    all_cells_rows: list[dict] = []
    all_distribution_rows: list[dict] = []
    band_summaries: list[dict] = []
    per_band_dfs: dict[str, pd.DataFrame] = {}  # band → cells_df (with attrs)

    for band, df in per_band_frames.items():
        cfg = bands[band]
        ref = _compute_reference_levels(df)
        pct_lsl = (
            float(((df["net_pnl_estimate_usd"] < 0) & df["is_premium_sl_hit"].astype(bool)).sum())
            / max(int((df["net_pnl_estimate_usd"] < 0).sum()), 1)
        )

        # Stdout: per-band input mapping.
        print(f"\n  band={band}  n={len(df)}")
        print(f"    rule        : {cfg['rule_label']}")
        print(f"    n_sl_hit    : {ref['n_sl_hit']}  n_losers={ref['n_losers']}  "
              f"n_winners={ref['n_winners']}")
        print(f"    SL_avg      : {ref['SL_avg']:.2f}" if ref['SL_avg'] is not None
              else "    SL_avg      : undefined (n < 10)")
        print(f"    L_avg       : {ref['L_avg']:.2f}" if ref['L_avg'] is not None
              else "    L_avg       : undefined")
        print(f"    W_avg       : {ref['W_avg']:.2f}" if ref['W_avg'] is not None
              else "    W_avg       : undefined")
        print(f"    pct_losers_sl_hit = {pct_lsl:.1%}")
        if pct_lsl >= L_AVG_CENSORING_PCT_WARN:
            print(f"    WARN: {pct_lsl:.0%} of losers are SL-hit — L_avg trigger is "
                  f"significantly censored and behaves close to SL_avg")

        cells_df, distribution_rows = _process_band(band, df, cfg, ref)
        per_band_dfs[band] = cells_df

        all_cells_rows.extend(cells_df.drop(columns=["iv_band"], errors="ignore").to_dict("records"))
        all_distribution_rows.extend(distribution_rows)

        # ── per-band best-cell stdout ─────────────────────────────────────────
        attrs         = cells_df.attrs
        best_avg_idx  = attrs.get("best_avg_idx")
        best_comp_idx = attrs.get("best_comp_idx")
        cells_agree   = attrs.get("cells_agree", False)
        actual_avg    = attrs.get("actual_avg_net_pnl", float("nan"))

        if attrs.get("warn_75sl"):
            pct_near = attrs.get("pct_sl_near_avg", 0.0)
            print(f"    WARN: {pct_near:.0%} of SL-hit troughs cluster near SL_avg "
                  f"— 75_of_sl cells may be biased upward by SL censoring")

        print(f"    actual_avg_net_pnl = ${actual_avg:.2f}")

        for selector, cidx, label in [
            ("best_by_avg_pnl", best_avg_idx, "Best by avg P&L"),
            ("best_by_composite_v2", best_comp_idx, "Best by composite"),
        ]:
            if cidx is None:
                print(f"    {label}: no ranked cells")
                continue
            r = cells_df.loc[cidx]
            print(
                f"    {label}: ef={r['exit_frac']} trigger={r['trigger_level']} "
                f"avg_hyp=${float(r['avg_net_pnl']):.2f} "
                f"(Δ=${float(r['avg_net_pnl']) - actual_avg:+.2f}) "
                f"composite={float(r['composite_score_v2']):.3f}"
                if pd.notna(r["composite_score_v2"]) else
                f"    {label}: ef={r['exit_frac']} trigger={r['trigger_level']} "
                f"avg_hyp=${float(r['avg_net_pnl']):.2f} "
                f"(Δ=${float(r['avg_net_pnl']) - actual_avg:+.2f}) composite=NaN"
            )
            # Print B_unreliable rate per (band, best trigger).
            tname = str(r["trigger_level"])
            for ef_row in cells_df[cells_df["trigger_level"] == tname].itertuples():
                if ef_row.status == "ok":
                    print(f"      (trigger={tname}) pct_B_unreliable={ef_row.pct_B_unreliable:.1%}")
                    break

        print(f"    cells_agree={cells_agree}")

        # Accumulate band summary row for best_cells.csv.
        def _cell_coord(cidx_) -> Optional[dict]:
            if cidx_ is None:
                return None
            r_ = cells_df.loc[cidx_]
            actual_avg_ = attrs["actual_avg_net_pnl"]
            return {
                "exit_frac": float(r_["exit_frac"]),
                "trigger_level": str(r_["trigger_level"]),
                "avg_pnl": float(r_["avg_net_pnl"]),
                "delta_avg_pnl": float(r_["avg_net_pnl"]) - actual_avg_,
                "composite_v2": float(r_["composite_score_v2"]) if pd.notna(r_["composite_score_v2"]) else None,
            }

        best_summaries: dict = {
            "band": band,
            "baseline_rule_label": cfg["rule_label"],
            "n_total": len(df),
            "actual_avg_net_pnl": actual_avg,
            "cells_agree": cells_agree,
            "best_avg_pnl_composite_rank": attrs.get("best_avg_composite_rank"),
            "best_composite_avg_pnl_rank": attrs.get("best_comp_avg_pnl_rank"),
        }
        avg_coord  = _cell_coord(best_avg_idx)
        comp_coord = _cell_coord(best_comp_idx)
        if avg_coord:
            for k, v in avg_coord.items():
                best_summaries[f"best_avg_{k}"] = v
        if comp_coord:
            for k, v in comp_coord.items():
                best_summaries[f"best_composite_{k}"] = v
        band_summaries.append(best_summaries)

    # ── Phase 5: Write CSVs ───────────────────────────────────────────────────
    print("\n── Phase 5: Writing CSVs ─────────────────────────────────────────────")
    all_cells_df = pd.DataFrame(all_cells_rows)
    # Drop internal iv_band alias if it leaked through.
    all_cells_df = all_cells_df.drop(columns=["iv_band"], errors="ignore")

    # CSV 1: all_cells (long format).
    p1 = os.path.join(OUTPUT_DIR, "stage1_2d__by_iv_band__all_cells.csv")
    all_cells_df.to_csv(p1, index=False)
    print(f"  [1] {p1}  ({len(all_cells_df)} rows)")

    # CSV 2: heatmap_ev (wide).
    heatmap_ev_rows: list[dict] = []
    for band in per_band_frames.keys():
        band_cells = all_cells_df[all_cells_df["band"] == band]
        row: dict = {"band": band}
        for ef in EXIT_FRACS:
            ef_pct = int(ef * 100)
            for tname in TRIGGER_NAMES:
                col_name = f"ev_{ef_pct}exit_{tname}"
                sub = band_cells[
                    (band_cells["exit_frac"] == ef) & (band_cells["trigger_level"] == tname)
                ]
                row[col_name] = float(sub["ev_per_trade"].iloc[0]) if not sub.empty else float("nan")
        heatmap_ev_rows.append(row)
    p2 = os.path.join(OUTPUT_DIR, "stage1_2d__by_iv_band__heatmap_ev.csv")
    pd.DataFrame(heatmap_ev_rows).to_csv(p2, index=False)
    print(f"  [2] {p2}  ({len(heatmap_ev_rows)} bands × 20 trigger/ef combos)")

    # CSV 3: heatmap_composite (wide).
    heatmap_comp_rows: list[dict] = []
    for band in per_band_frames.keys():
        band_cells = all_cells_df[all_cells_df["band"] == band]
        row = {"band": band}
        for ef in EXIT_FRACS:
            ef_pct = int(ef * 100)
            for tname in TRIGGER_NAMES:
                col_name = f"composite_{ef_pct}exit_{tname}"
                sub = band_cells[
                    (band_cells["exit_frac"] == ef) & (band_cells["trigger_level"] == tname)
                ]
                val = sub["composite_score_v2"].iloc[0] if not sub.empty else float("nan")
                row[col_name] = float(val) if pd.notna(val) else float("nan")
        heatmap_comp_rows.append(row)
    p3 = os.path.join(OUTPUT_DIR, "stage1_2d__by_iv_band__heatmap_composite.csv")
    pd.DataFrame(heatmap_comp_rows).to_csv(p3, index=False)
    print(f"  [3] {p3}  ({len(heatmap_comp_rows)} bands × 20 trigger/ef combos)")

    # CSV 4: best_cells.
    p4 = os.path.join(OUTPUT_DIR, "stage1_2d__by_iv_band__best_cells.csv")
    best_cells_df = pd.DataFrame(band_summaries)
    best_cells_df.to_csv(p4, index=False)
    print(f"  [4] {p4}  ({len(best_cells_df)} bands)")

    # CSV 5: distribution.
    p5 = os.path.join(OUTPUT_DIR, "stage1_2d__by_iv_band__distribution.csv")
    dist_df = pd.DataFrame(all_distribution_rows) if all_distribution_rows else pd.DataFrame()
    dist_df.to_csv(p5, index=False)
    print(f"  [5] {p5}  ({len(dist_df)} rows)")

    # ── Phase 6: Cross-band observations ──────────────────────────────────────
    print("\n── Phase 6: Cross-band observations ─────────────────────────────────")

    # Globally-good combo: best in ≥ half the bands.
    combo_counts: dict[tuple, int] = {}
    for bs in band_summaries:
        ef  = bs.get("best_avg_exit_frac")
        trig = bs.get("best_avg_trigger_level")
        if ef is not None and trig is not None:
            key = (ef, trig)
            combo_counts[key] = combo_counts.get(key, 0) + 1
    n_bands = len(band_summaries)
    global_winners = [(k, v) for k, v in combo_counts.items() if v >= n_bands / 2]
    if global_winners:
        print("  Globally-good (exit_frac, trigger_level) combos [best in ≥50% of bands]:")
        for (ef, trig), cnt in sorted(global_winners, key=lambda x: -x[1]):
            print(f"    ef={ef}  trigger={trig}  best-in {cnt}/{n_bands} bands")
    else:
        print("  No single (exit_frac, trigger_level) combo is best in ≥50% of bands.")

    # Bands where stage-1 hurts (best-by-avg-pnl has delta < 0).
    negative_bands = [
        bs["band"] for bs in band_summaries
        if bs.get("best_avg_delta_avg_pnl") is not None
        and bs["best_avg_delta_avg_pnl"] < 0
    ]
    if negative_bands:
        print(f"  Bands where best stage-1 cell HURTS avg P&L (disable stage-1 there): "
              f"{negative_bands}")
    else:
        print("  All bands have a stage-1 cell that improves avg P&L.")

    # Degenerate: exit_frac=1.00 wins.
    degenerate_wins = 0
    for band in per_band_frames.keys():
        best_avg_idx = per_band_dfs[band].attrs.get("best_avg_idx")
        if best_avg_idx is not None:
            r = per_band_dfs[band].loc[best_avg_idx]
            if float(r["exit_frac"]) == 1.00:
                degenerate_wins += 1
    print(f"  exit_frac=1.00 (tighter SL) is best-by-avg-pnl in {degenerate_wins}/{n_bands} bands.")

    # Disagreement bands.
    disagree = [bs["band"] for bs in band_summaries if not bs.get("cells_agree", True)]
    if disagree:
        print(f"  Bands where best-by-avg-pnl ≠ best-by-composite (risk/return tension): "
              f"{disagree}")
        for band in disagree:
            bs = next(b for b in band_summaries if b["band"] == band)
            print(f"    {band}: avg_pnl_composite_rank={bs.get('best_avg_pnl_composite_rank')} "
                  f"composite_avg_pnl_rank={bs.get('best_composite_avg_pnl_rank')}")

    # ── Validation ────────────────────────────────────────────────────────────
    _run_validation(all_cells_df, per_band_dfs, per_band_frames)

    # ── Caveats ───────────────────────────────────────────────────────────────
    print(CAVEATS)

    # ── Head of best_cells.csv ───────────────────────────────────────────────
    print("── Head of best_cells.csv ────────────────────────────────────────────")
    print(best_cells_df.to_string(index=False))
    print("\nDone. All CSVs written to", OUTPUT_DIR)


if __name__ == "__main__":
    main()

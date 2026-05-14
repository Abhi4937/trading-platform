"""Pre-build Friday-band grids for non-A1 modes — VECTORIZED.

Bypasses the per-cell `_compute_cell_metrics` loop (40+ pandas ops × 216K cells
took ~90 min) and uses a single big groupby + agg() (seconds).

Modes built:
  - B1 (modal band per Friday)
  - D1_earliest  (band at earliest entry hour)
  - D1_latest    (band at latest entry hour)
  - D1_median    (band at median IV reading)

Output: /home/abhis/btc-data/derived/m7/m7_friday_band_grid_<MODE>.parquet

Run via dedicated container:

    docker compose run -d --rm \\
        --name m7-friday-band-mode-grid-builder \\
        -v /home/abhis/btc-data/logs:/logs \\
        backend sh -c "python -m app.scripts.build_m7_friday_band_mode_grids \\
        > /logs/m7_friday_band_mode_grids_$(date +%s).log 2>&1"
"""
from __future__ import annotations

import glob
import os
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

SAT_IV_PATH = "/home/abhis/btc-data/derived/m7/m7_friday_sat_iv_v1.parquet"
PER_TRADE_DIR = "/home/abhis/btc-data/derived/m7/m7_friday_band_per_trade"
OUTPUT_DIR = "/home/abhis/btc-data/derived/m7"


# ── Friday-band assignment functions ─────────────────────────────────────────

def _band_lower_edge(band: str) -> int:
    if band == "100+":
        return 100
    try:
        return int(str(band).split("-")[0])
    except (ValueError, AttributeError):
        return 9999


def _hour_order() -> list[int]:
    return [21, 22, 23, 0, 1, 2, 3]


def _friday_band_b1(sat: pd.DataFrame) -> dict[str, str]:
    out: dict[str, str] = {}
    for friday, grp in sat.groupby("friday_date_ist"):
        counts = grp["sat_iv_band"].value_counts()
        max_count = counts.max()
        winners = counts[counts == max_count].index.tolist()
        winners.sort(key=_band_lower_edge)
        out[friday] = winners[0]
    return out


def _friday_band_earliest(sat: pd.DataFrame) -> dict[str, str]:
    order = _hour_order()
    out: dict[str, str] = {}
    for friday, grp in sat.groupby("friday_date_ist"):
        ix = {int(r["entry_hour_ist"]): r["sat_iv_band"] for _, r in grp.iterrows()}
        for h in order:
            if h in ix:
                out[friday] = ix[h]
                break
    return out


def _friday_band_latest(sat: pd.DataFrame) -> dict[str, str]:
    order = list(reversed(_hour_order()))
    out: dict[str, str] = {}
    for friday, grp in sat.groupby("friday_date_ist"):
        ix = {int(r["entry_hour_ist"]): r["sat_iv_band"] for _, r in grp.iterrows()}
        for h in order:
            if h in ix:
                out[friday] = ix[h]
                break
    return out


def _friday_band_median(sat: pd.DataFrame) -> dict[str, str]:
    out: dict[str, str] = {}
    for friday, grp in sat.groupby("friday_date_ist"):
        sorted_iv = grp.sort_values("sat_atm_iv_pct").reset_index(drop=True)
        mid = sorted_iv.iloc[len(sorted_iv) // 2]
        out[friday] = mid["sat_iv_band"]
    return out


def _friday_band_highest_entry_iv(sat: pd.DataFrame) -> dict[str, str]:
    """Per Friday: band of the highest Sat-IV reading."""
    out: dict[str, str] = {}
    for friday, grp in sat.groupby("friday_date_ist"):
        idx = grp["sat_atm_iv_pct"].idxmax()
        out[friday] = grp.loc[idx, "sat_iv_band"]
    return out


def _friday_band_lowest_entry_iv(sat: pd.DataFrame) -> dict[str, str]:
    out: dict[str, str] = {}
    for friday, grp in sat.groupby("friday_date_ist"):
        idx = grp["sat_atm_iv_pct"].idxmin()
        out[friday] = grp.loc[idx, "sat_iv_band"]
    return out


# Lookahead tiebreaker band-assignment functions. Each takes (sat, per_trade)
# and returns {friday → band}. The band is whichever touched band scores best
# under the given metric on that Friday's actual trades.

def _hour_to_band_map(sat: pd.DataFrame, friday: str) -> dict[int, str]:
    grp = sat[sat["friday_date_ist"] == friday]
    return {int(r["entry_hour_ist"]): r["sat_iv_band"] for _, r in grp.iterrows()}


def _per_friday_lookahead(sat: pd.DataFrame, per_trade: pd.DataFrame,
                           score_fn, direction: str = "max") -> dict[str, str]:
    """Generic helper: for each Friday, group its trades by hour-band, apply
    score_fn(sub_df) per band, pick band with max (or min) score.
    """
    out: dict[str, str] = {}
    pt = per_trade.copy()
    # Add hour-band per row via Sat-IV lookup
    sat_idx = sat.set_index(["friday_date_ist", "entry_hour_ist"])["sat_iv_band"]
    keys = list(zip(pt["friday_date_ist"], pt["entry_hour_ist"]))
    pt["_hour_band"] = sat_idx.reindex(keys).values
    for friday, grp in pt.groupby("friday_date_ist"):
        scores = grp.groupby("_hour_band").apply(score_fn, include_groups=False)
        if scores.empty:
            continue
        scores = scores.dropna()
        if scores.empty:
            continue
        idx = scores.idxmax() if direction == "max" else scores.idxmin()
        out[friday] = idx
    return out


def _friday_band_best_avg_net_pnl(sat: pd.DataFrame, per_trade: pd.DataFrame) -> dict[str, str]:
    return _per_friday_lookahead(sat, per_trade,
        lambda g: g["net_pnl_estimate_usd"].mean(), "max")


def _friday_band_best_total_net_pnl(sat: pd.DataFrame, per_trade: pd.DataFrame) -> dict[str, str]:
    return _per_friday_lookahead(sat, per_trade,
        lambda g: g["net_pnl_estimate_usd"].sum(), "max")


def _friday_band_best_gross_pnl(sat: pd.DataFrame, per_trade: pd.DataFrame) -> dict[str, str]:
    return _per_friday_lookahead(sat, per_trade,
        lambda g: g["gross_pnl_usd"].mean(), "max")


def _friday_band_best_win_rate(sat: pd.DataFrame, per_trade: pd.DataFrame) -> dict[str, str]:
    return _per_friday_lookahead(sat, per_trade,
        lambda g: g["is_win"].mean(), "max")


def _friday_band_best_max_loss(sat: pd.DataFrame, per_trade: pd.DataFrame) -> dict[str, str]:
    # Worst-case loss = min(net_pnl). Best = least-negative (max of mins).
    return _per_friday_lookahead(sat, per_trade,
        lambda g: g.loc[g["net_pnl_estimate_usd"] < 0, "net_pnl_estimate_usd"].min()
                  if (g["net_pnl_estimate_usd"] < 0).any() else 0.0, "max")


def _friday_band_worst_max_loss(sat: pd.DataFrame, per_trade: pd.DataFrame) -> dict[str, str]:
    return _per_friday_lookahead(sat, per_trade,
        lambda g: g.loc[g["net_pnl_estimate_usd"] < 0, "net_pnl_estimate_usd"].min()
                  if (g["net_pnl_estimate_usd"] < 0).any() else 0.0, "min")


def _friday_band_best_avg_loss(sat: pd.DataFrame, per_trade: pd.DataFrame) -> dict[str, str]:
    return _per_friday_lookahead(sat, per_trade,
        lambda g: g.loc[g["net_pnl_estimate_usd"] < 0, "net_pnl_estimate_usd"].mean()
                  if (g["net_pnl_estimate_usd"] < 0).any() else 0.0, "max")


def _friday_band_highest_credit(sat: pd.DataFrame, per_trade: pd.DataFrame) -> dict[str, str]:
    return _per_friday_lookahead(sat, per_trade,
        lambda g: g["credit_usd"].mean(), "max")


def _friday_band_lowest_margin(sat: pd.DataFrame, per_trade: pd.DataFrame) -> dict[str, str]:
    return _per_friday_lookahead(sat, per_trade,
        lambda g: g["margin_used_usd_at_entry"].mean(), "min")


# Modes that take only sat — no per-trade needed
SAT_ONLY_MODES = {
    "B1":                    _friday_band_b1,
    "D1_earliest":           _friday_band_earliest,
    "D1_latest":             _friday_band_latest,
    "D1_median":             _friday_band_median,
    "D1_highest_entry_iv":   _friday_band_highest_entry_iv,
    "D1_lowest_entry_iv":    _friday_band_lowest_entry_iv,
}

# Modes that need per-trade outcomes (lookahead)
LOOKAHEAD_MODES = {
    "D1_best_avg_net_pnl":   _friday_band_best_avg_net_pnl,
    "D1_best_total_net_pnl": _friday_band_best_total_net_pnl,
    "D1_best_gross_pnl":     _friday_band_best_gross_pnl,
    "D1_best_win_rate":      _friday_band_best_win_rate,
    "D1_best_max_loss":      _friday_band_best_max_loss,
    "D1_worst_max_loss":     _friday_band_worst_max_loss,
    "D1_best_avg_loss":      _friday_band_best_avg_loss,
    "D1_highest_credit":     _friday_band_highest_credit,
    "D1_lowest_margin":      _friday_band_lowest_margin,
}

# Note: 4 MTM-based lookahead tiebreakers (best_min_mtm, worst_min_mtm,
# best_max_mtm, smallest_mtm_range) are NOT pre-built — they need
# minute-by-minute MTM which is in m7_paths/, not per_trade. They fall
# through to in-process build in the endpoint (or get skipped silently
# if the columns aren't available).

MODES = {**SAT_ONLY_MODES, **LOOKAHEAD_MODES}


# ── Vectorized grid construction ─────────────────────────────────────────────

GROUP_KEYS = ["iv_band", "expiry_bucket", "delta_target", "entry_hour_ist", "rule_label"]


def _is_hard_cap(reason: pd.Series) -> pd.Series:
    return reason.astype(str).str.lower().eq("hard_cap")


def _is_rule_trigger(reason: pd.Series) -> pd.Series:
    return reason.astype(str).str.lower().eq("rule_trigger")


def _is_premium_sl(reason: pd.Series) -> pd.Series:
    return reason.astype(str).str.lower().str.contains("premium_sl|sl_hit", regex=True)


def _build_grid_vectorized(per_trade: pd.DataFrame, fb_map: dict[str, str]) -> pd.DataFrame:
    """Single-pass vectorized aggregation. ~seconds, not minutes."""
    df = per_trade.copy()
    df["iv_band"] = df["friday_date_ist"].map(fb_map)
    df = df[
        df["iv_band"].notna()
        & df["expiry_bucket"].notna()
        & df["delta_target"].notna()
        & df["entry_hour_ist"].notna()
        & df["gross_pnl_usd"].notna()
    ]
    if df.empty:
        return pd.DataFrame()

    # Pre-compute boolean columns once before groupby.
    df["is_win_b"] = df["net_pnl_estimate_usd"].astype(float) > 0
    df["is_loss_b"] = df["net_pnl_estimate_usd"].astype(float) < 0
    df["is_hard_cap_b"] = _is_hard_cap(df["exit_reason"])
    df["is_rule_trigger_b"] = _is_rule_trigger(df["exit_reason"])
    df["is_premium_sl_b"] = _is_premium_sl(df["exit_reason"])
    # Conditional columns for winner/loser specific aggregates.
    df["net_pnl_winner"] = np.where(df["is_win_b"], df["net_pnl_estimate_usd"], np.nan)
    df["net_pnl_loser"] = np.where(df["is_loss_b"], df["net_pnl_estimate_usd"], np.nan)
    df["pct_credit"] = np.where(df["credit_usd"] > 0,
                                  df["net_pnl_estimate_usd"] / df["credit_usd"],
                                  np.nan)
    df["pct_margin"] = np.where(df["margin_used_usd_at_entry"] > 0,
                                  df["net_pnl_estimate_usd"] / df["margin_used_usd_at_entry"],
                                  np.nan)

    grouped = df.groupby(GROUP_KEYS, dropna=False, sort=False, observed=True)

    # Single big agg dict.
    agg_spec = {
        "net_pnl_estimate_usd": ["mean", "sum", "max", "min", "std"],
        "gross_pnl_usd":        ["mean"],
        "credit_usd":           ["mean"],
        "margin_used_usd_at_entry": ["mean"],
        "is_win_b":             ["sum", "mean"],
        "is_loss_b":            ["sum"],
        "is_hard_cap_b":        ["sum"],
        "is_rule_trigger_b":    ["sum"],
        "is_premium_sl_b":      ["sum"],
        "net_pnl_winner":       ["mean", "max"],
        "net_pnl_loser":        ["mean", "min", "std"],
        "pct_credit":           ["mean"],
        "pct_margin":           ["mean"],
        "trade_id":             ["count"],  # n_trades
    }
    agg = grouped.agg(agg_spec)
    # Flatten multi-index columns
    agg.columns = ["_".join([c for c in col if c]).strip("_") for col in agg.columns]
    agg = agg.reset_index()

    # Rename to canonical metric names used by picker
    rename_map = {
        "net_pnl_estimate_usd_mean":  "avg_net_pnl",
        "net_pnl_estimate_usd_sum":   "sum_net_pnl",
        "net_pnl_estimate_usd_max":   "max_win_usd",
        "net_pnl_estimate_usd_min":   "max_loss_usd",
        "net_pnl_estimate_usd_std":   "stdev_net_pnl",
        "gross_pnl_usd_mean":         "avg_gross_pnl",
        "credit_usd_mean":            "avg_credit",
        "margin_used_usd_at_entry_mean": "avg_margin",
        "is_win_b_sum":               "n_wins",
        "is_win_b_mean":              "win_rate",
        "is_loss_b_sum":              "n_losses",
        "is_hard_cap_b_sum":          "n_hard_cap",
        "is_rule_trigger_b_sum":      "n_rule_trigger",
        "is_premium_sl_b_sum":        "n_premium_sl_hit",
        "net_pnl_winner_mean":        "avg_win_usd",
        "net_pnl_winner_max":         "max_win_usd_dup",  # already have it
        "net_pnl_loser_mean":         "avg_loss_usd",
        "net_pnl_loser_min":          "max_loss_usd_dup",
        "net_pnl_loser_std":          "stdev_losses_only",
        "pct_credit_mean":            "avg_pct_return_on_credit",
        "pct_margin_mean":            "avg_pct_return_on_margin",
        "trade_id_count":             "n_trades",
    }
    agg = agg.rename(columns=rename_map)
    agg = agg.drop(columns=[c for c in ["max_win_usd_dup", "max_loss_usd_dup"] if c in agg.columns])

    # Cast ints
    for col in ["n_wins", "n_losses", "n_hard_cap", "n_rule_trigger", "n_premium_sl_hit", "n_trades"]:
        if col in agg.columns:
            agg[col] = pd.to_numeric(agg[col], errors="coerce").fillna(0).astype(int)

    # Round for the wire
    for col in ["avg_net_pnl", "sum_net_pnl", "max_win_usd", "max_loss_usd",
                "avg_win_usd", "avg_loss_usd", "avg_gross_pnl",
                "avg_credit", "avg_margin", "stdev_net_pnl", "stdev_losses_only"]:
        if col in agg.columns:
            agg[col] = pd.to_numeric(agg[col], errors="coerce").round(4)
    for col in ["win_rate", "avg_pct_return_on_credit", "avg_pct_return_on_margin"]:
        if col in agg.columns:
            agg[col] = pd.to_numeric(agg[col], errors="coerce").round(6)

    # Rule dict reconstruction — leave as NaN (frontend doesn't need it for picks)
    agg["rule_premium_sl_pct"] = agg["rule_label"].str.extract(r"sl(\d+)").astype(float)
    agg["rule_max_profit_pct"] = agg["rule_label"].str.extract(r"_max_profit_(\d+)").astype(float)
    agg["rule_margin_target_pct"] = agg["rule_label"].str.extract(r"_margin_target_(\d+)").astype(float)

    return agg


def _load_per_trade() -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(PER_TRADE_DIR, "*_trades.parquet")))
    print(f"  Loading {len(files)} per-trade parquets...", flush=True)
    t0 = time.time()
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    print(f"  Loaded {len(df):,} trades in {time.time()-t0:.1f}s", flush=True)
    return df


def main() -> int:
    if not os.path.exists(SAT_IV_PATH):
        print(f"ERROR: Sat-IV missing at {SAT_IV_PATH}", flush=True)
        return 1
    if not os.path.isdir(PER_TRADE_DIR):
        print(f"ERROR: Per-trade dir missing", flush=True)
        return 1

    t_global = time.time()
    sat = pd.read_parquet(SAT_IV_PATH)
    per_trade = _load_per_trade()

    for mode, fn in MODES.items():
        out_path = os.path.join(OUTPUT_DIR, f"m7_friday_band_grid_{mode}.parquet")
        if os.path.exists(out_path):
            print(f"\n[SKIP] {mode} — already exists", flush=True)
            continue
        print(f"\n[BUILD] {mode}", flush=True)
        # Lookahead modes need per_trade; sat-only modes don't.
        if mode in LOOKAHEAD_MODES:
            fb_map = fn(sat, per_trade)
        else:
            fb_map = fn(sat)
        band_counts = Counter(fb_map.values())
        print(f"  Per-Friday band: {dict(sorted(band_counts.items(), key=lambda x: _band_lower_edge(x[0])))}", flush=True)
        t0 = time.time()
        grid = _build_grid_vectorized(per_trade, fb_map)
        print(f"  Built {len(grid):,} cells in {time.time()-t0:.1f}s", flush=True)
        grid.to_parquet(out_path, index=False)
        print(f"  Wrote → {out_path}", flush=True)

    print(f"\nDone in {(time.time()-t_global)/60:.1f}min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

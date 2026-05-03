"""
M5 v2 enrichment — backfill_attribution.

Reads:
  /home/abhis/btc-data/derived/m4_trades.parquet
  /home/abhis/btc-data/derived/calibration.parquet

Writes:
  /home/abhis/btc-data/derived/calibration_v2.parquet

For each calibration bucket key (dte_bucket × spot_bucket × delta_target × ivp_bucket),
join all M4 trades that fall in that bucket and compute:

  n_trades                    int    — total trades in the bucket
  n_winners                   int    — count where outcome == "win"
  overall_winrate             float  — n_winners / n_trades
  pattern_winrate             json   — {"A": 0.62, "B": 0.51, ...} per ctx_pattern
  pattern_n                   json   — {"A": 12, "B": 7, ...}
  z_winners_mean              float  — mean(credit_pct) across winners only
  z_winners_std               float  — std(credit_pct) across winners only
  expectancy_per_credit_pct   float  — mean(net_pnl_pct_credit)
  expectancy_per_margin_pct   float  — mean(net_pnl_pct_margin)
  sl_hit_rate                 float  — mean(exit_reason == "LegSL")
  median_breaching_leg        str    — mode of breaching_leg ("CE"/"PE"/None)
  version                     int    — 2

Run:
    python -m app.analytics.backfill_attribution
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time as _time

import numpy as np
import pandas as pd

from app.analytics.enrich_options import DERIVED_DIR

TRADES_IN_PATH = os.path.join(DERIVED_DIR, "m4_trades.parquet")
CALIB_IN_PATH  = os.path.join(DERIVED_DIR, "calibration.parquet")
CALIB_V2_PATH  = os.path.join(DERIVED_DIR, "calibration_v2.parquet")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def _bucket_key_cols() -> list[str]:
    return ["dte_bucket", "spot_bucket", "delta_target", "ivp_bucket"]


def _aggregate_bucket(grp: pd.DataFrame) -> pd.Series:
    """One-bucket aggregation."""
    n = len(grp)
    winners = grp[grp["outcome"] == "win"]
    n_win = int(len(winners))

    # pattern_winrate per pattern
    pattern_wr: dict[str, float] = {}
    pattern_n:  dict[str, int]   = {}
    for pat, sub in grp.groupby("ctx_pattern", dropna=False):
        if pd.isna(pat):
            pat = "Other"
        pat = str(pat)
        sub_n = int(len(sub))
        sub_w = int((sub["outcome"] == "win").sum())
        pattern_wr[pat] = sub_w / sub_n if sub_n > 0 else 0.0
        pattern_n[pat] = sub_n

    # z-winners stats: mean/std of credit_pct across winners
    if n_win > 0:
        z_w_mean = float(winners["credit_pct"].mean())
        z_w_std  = float(winners["credit_pct"].std(ddof=0)) if n_win > 1 else 0.0
    else:
        z_w_mean = float("nan")
        z_w_std  = float("nan")

    expect_credit = float(grp["net_pnl_pct_credit"].mean()) if n > 0 else float("nan")
    expect_margin = float(grp["net_pnl_pct_margin"].mean()) if n > 0 else float("nan")
    sl_rate = float((grp["exit_reason"] == "LegSL").sum() / n) if n > 0 else float("nan")

    # Mode of breaching_leg ignoring None
    bl = grp["breaching_leg"].dropna()
    median_bl = bl.mode().iloc[0] if not bl.empty else None

    return pd.Series({
        "n_trades": n,
        "n_winners": n_win,
        "overall_winrate": (n_win / n) if n > 0 else float("nan"),
        "pattern_winrate": json.dumps(pattern_wr),
        "pattern_n": json.dumps(pattern_n),
        "z_winners_mean": z_w_mean,
        "z_winners_std": z_w_std,
        "expectancy_per_credit_pct": expect_credit,
        "expectancy_per_margin_pct": expect_margin,
        "sl_hit_rate": sl_rate,
        "median_breaching_leg": median_bl,
    })


def run(args: argparse.Namespace) -> None:
    t0 = _time.time()
    if not os.path.exists(TRADES_IN_PATH):
        log.error(f"Missing M4 output at {TRADES_IN_PATH}. "
                  f"Run `python -m app.analytics.m4_batch_backtester` first.")
        sys.exit(1)
    if not os.path.exists(CALIB_IN_PATH):
        log.error(f"Missing v1 calibration at {CALIB_IN_PATH}. "
                  f"Run `python -m app.analytics.calibration_builder --rebuild` first.")
        sys.exit(1)

    log.info(f"Reading M4 trades from {TRADES_IN_PATH}...")
    trades = pd.read_parquet(TRADES_IN_PATH)
    log.info(f"  {len(trades):,} trades")

    log.info(f"Reading v1 calibration from {CALIB_IN_PATH}...")
    calib = pd.read_parquet(CALIB_IN_PATH)
    log.info(f"  {len(calib):,} v1 buckets")

    # Aggregate trades per bucket
    log.info("Aggregating per-bucket outcomes...")
    bucket_cols = _bucket_key_cols()
    agg = (trades.groupby(bucket_cols, dropna=False)
                  .apply(_aggregate_bucket, include_groups=False)
                  .reset_index())
    log.info(f"  {len(agg):,} populated buckets")

    # Left-join onto v1 calibration so every v1 bucket gets v2 cols (NaN where empty)
    v2 = calib.merge(agg, on=bucket_cols, how="left")
    v2["version"] = 2

    # Write atomically (.tmp + rename)
    out_path = args.out or CALIB_V2_PATH
    tmp = out_path + ".tmp"
    v2.to_parquet(tmp, compression="snappy", index=False)
    os.replace(tmp, out_path)

    elapsed = _time.time() - t0
    log.info(f"Wrote v2 calibration → {out_path} ({len(v2):,} buckets) in {elapsed:.1f}s")

    # Quick sanity log
    n_filled = int(v2["n_trades"].notna().sum())
    log.info(f"  {n_filled}/{len(v2)} buckets have M4 trade data; "
             f"others fall back to v1 stats only")
    if n_filled > 0:
        avg_wr = float(v2["overall_winrate"].dropna().mean())
        log.info(f"  avg overall_winrate (across populated buckets): {avg_wr*100:.1f}%")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=str, default=None,
                   help=f"Output parquet path. Default: {CALIB_V2_PATH}.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run(args)
        return 0
    except Exception as e:
        log.exception(f"backfill_attribution failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Backfill m7_trades.parquet with calibration_v2 join columns.

Adds (per trade):
  - fair_credit_at_ivp        — calibration_v2 bucket median credit_pct
  - structural_credit_pct     — pure-structure baseline (no IV regime)
  - iv_regime_premium_pct     — fair − structural
  - excess_over_fair_pct      — actual_credit − fair
  - pattern_winrate           — bucket winrate by pattern (string)
  - expectancy_per_credit_pct — bucket expectancy
  - n_trades_in_bucket        — bucket sample size

M7 trades already carry per-leg greeks (computed in the simulator), so the
greek-recompute step that backfill_m4_enriched.py does is NOT needed here.

Reads:  /home/abhis/btc-data/derived/m7/m7_trades.parquet
        /home/abhis/btc-data/derived/calibration_v2.parquet
Writes: /home/abhis/btc-data/derived/m7/m7_trades_enriched.parquet

Run:
  python3 scripts/backfill_m7_enriched.py
"""
from pathlib import Path

import pandas as pd

DERIVED = Path("/home/abhis/btc-data/derived")
SRC = DERIVED / "m7" / "m7_trades.parquet"
CALIB = DERIVED / "calibration_v2.parquet"
OUT = DERIVED / "m7" / "m7_trades_enriched.parquet"

# M7 stores delta as both float (delta_target) and string (delta_target_bucket).
# calibration_v2 uses the string form, so join on delta_target_bucket → delta_target.
JOIN_KEYS_TRADES = ["dte_bucket", "spot_bucket", "delta_target_bucket", "ivp_bucket"]
JOIN_KEYS_CALIB = ["dte_bucket", "spot_bucket", "delta_target", "ivp_bucket"]


def main() -> None:
    print(f"reading {SRC}")
    trades = pd.read_parquet(SRC)
    n_in = len(trades)
    print(f"  → {n_in} rows × {len(trades.columns)} cols")

    print(f"reading {CALIB}")
    calib = pd.read_parquet(CALIB)
    print(f"  → {len(calib)} buckets × {len(calib.columns)} cols")

    keep_cols = JOIN_KEYS_CALIB + [
        "credit_pct_median", "structural_baseline",
        "pattern_winrate", "expectancy_per_credit_pct",
        "overall_winrate", "n_trades", "sl_hit_rate",
    ]
    keep_cols = [c for c in keep_cols if c in calib.columns]
    calib_slim = calib[keep_cols].rename(columns={
        "credit_pct_median": "fair_credit_at_ivp",
        "structural_baseline": "structural_credit_pct",
        "overall_winrate": "bucket_overall_winrate",
        "n_trades": "n_trades_in_bucket",
        "sl_hit_rate": "bucket_sl_hit_rate",
        "delta_target": "delta_target_bucket",  # rename for join
    })

    enriched = trades.merge(calib_slim, on=JOIN_KEYS_TRADES, how="left")
    assert len(enriched) == n_in, f"left join changed row count: {len(enriched)} != {n_in}"

    enriched["iv_regime_premium_pct"] = (
        enriched["fair_credit_at_ivp"] - enriched["structural_credit_pct"]
    )
    enriched["excess_over_fair_pct"] = (
        enriched["credit_pct_of_spot"] - enriched["fair_credit_at_ivp"]
    )

    n_with_calib = enriched["fair_credit_at_ivp"].notna().sum()
    print(
        f"  → {n_with_calib}/{n_in} trades matched a calibration bucket "
        f"({n_in - n_with_calib} left null)"
    )

    excess_mean = enriched["excess_over_fair_pct"].dropna().mean()
    excess_median = enriched["excess_over_fair_pct"].dropna().median()
    print(
        f"  excess_over_fair_pct  mean={excess_mean:.6f}  "
        f"median={excess_median:.6f}  (matched-bucket rows)"
    )

    print(f"writing {OUT}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_parquet(OUT, compression="zstd", index=False)
    print(f"  → {len(enriched)} rows × {len(enriched.columns)} cols, "
          f"{OUT.stat().st_size / 1024 / 1024:.2f} MB")
    print("done.")


if __name__ == "__main__":
    main()

"""Backfill m4_trades.parquet with IV-premium decomposition + per-leg Greeks.

Adds (per trade):
  - fair_credit_at_ivp        — calibration bucket median credit_pct
  - structural_credit_pct     — pure-structure baseline (no IV regime)
  - iv_regime_premium_pct     — fair − structural
  - excess_over_fair_pct      — actual_credit − fair
  - {call,put}_{theta,vega,gamma}  — recomputed from BS at entry
  - theta_per_vega_{call,put,combined}

Reads:  /home/abhis/btc-data/derived/m4_trades.parquet
        /home/abhis/btc-data/derived/calibration_v2.parquet
Writes: /home/abhis/btc-data/derived/m4_trades_enriched.parquet

Run inside the backend container (needs app.core.greeks):
  docker exec -i docker-backend-1 python3 /app/scripts/backfill_m4_enriched.py
or via the host-mounted path:
  docker exec -i docker-backend-1 python3 \
    /mnt/.../scripts/backfill_m4_enriched.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Make `app.*` importable regardless of where the script runs from.
sys.path.insert(0, "/app")
from app.core.greeks import compute_greeks  # noqa: E402

DERIVED = Path("/home/abhis/btc-data/derived")
SRC = DERIVED / "m4_trades.parquet"
CALIB = DERIVED / "calibration_v2.parquet"
OUT = DERIVED / "m4_trades_enriched.parquet"

JOIN_KEYS = ["dte_bucket", "spot_bucket", "delta_target", "ivp_bucket"]


def main() -> None:
    print(f"reading {SRC}")
    trades = pd.read_parquet(SRC)
    n_in = len(trades)
    print(f"  → {n_in} rows × {len(trades.columns)} cols")

    print(f"reading {CALIB}")
    calib = pd.read_parquet(CALIB)
    print(f"  → {len(calib)} buckets × {len(calib.columns)} cols")

    # --- IV-premium decomposition via left-join on bucket keys ---------------
    calib_slim = calib[JOIN_KEYS + ["credit_pct_median", "structural_baseline"]].rename(
        columns={
            "credit_pct_median": "fair_credit_at_ivp",
            "structural_baseline": "structural_credit_pct",
        }
    )
    enriched = trades.merge(calib_slim, on=JOIN_KEYS, how="left")
    assert len(enriched) == n_in, f"left join changed row count: {len(enriched)} != {n_in}"

    enriched["iv_regime_premium_pct"] = (
        enriched["fair_credit_at_ivp"] - enriched["structural_credit_pct"]
    )
    enriched["excess_over_fair_pct"] = (
        enriched["credit_pct"] - enriched["fair_credit_at_ivp"]
    )

    n_with_calib = enriched["fair_credit_at_ivp"].notna().sum()
    print(
        f"  → {n_with_calib}/{n_in} trades matched a calibration bucket "
        f"({n_in - n_with_calib} left null — bucket=nan trades)"
    )

    # --- Per-leg Greeks via BS recompute -------------------------------------
    # T in years; r=0 (matches enrich_options + trade_simulator convention).
    spot = enriched["spot_at_entry"].astype(float).to_numpy()
    cstrike = enriched["call_strike"].astype(float).to_numpy()
    pstrike = enriched["put_strike"].astype(float).to_numpy()
    civ = enriched["call_entry_iv"].astype(float).to_numpy()
    piv = enriched["put_entry_iv"].astype(float).to_numpy()
    T = (enriched["dte_days"].astype(float) / 365.0).to_numpy()

    # compute_greeks isn't vectorized — loop is fine for 5,274 rows (~1s).
    c_theta = np.full(n_in, np.nan)
    c_vega = np.full(n_in, np.nan)
    c_gamma = np.full(n_in, np.nan)
    p_theta = np.full(n_in, np.nan)
    p_vega = np.full(n_in, np.nan)
    p_gamma = np.full(n_in, np.nan)

    for i in range(n_in):
        if T[i] > 0 and civ[i] > 0:
            g = compute_greeks(spot[i], cstrike[i], T[i], 0.0, civ[i], "call")
            c_theta[i], c_vega[i], c_gamma[i] = g.theta, g.vega, g.gamma
        if T[i] > 0 and piv[i] > 0:
            g = compute_greeks(spot[i], pstrike[i], T[i], 0.0, piv[i], "put")
            p_theta[i], p_vega[i], p_gamma[i] = g.theta, g.vega, g.gamma

    enriched["call_theta"] = c_theta
    enriched["call_vega"] = c_vega
    enriched["call_gamma"] = c_gamma
    enriched["put_theta"] = p_theta
    enriched["put_vega"] = p_vega
    enriched["put_gamma"] = p_gamma

    # Theta/vega ratios — short-strangle "decay vs vol-risk" metric.
    # Use abs() so ratio is always positive (theta is negative for shorts'
    # underlying; vega is positive for the long position we'd be short).
    with np.errstate(divide="ignore", invalid="ignore"):
        enriched["theta_per_vega_call"] = np.abs(c_theta) / np.where(
            np.abs(c_vega) > 1e-9, np.abs(c_vega), np.nan
        )
        enriched["theta_per_vega_put"] = np.abs(p_theta) / np.where(
            np.abs(p_vega) > 1e-9, np.abs(p_vega), np.nan
        )
        combined_theta = np.abs(c_theta) + np.abs(p_theta)
        combined_vega = np.abs(c_vega) + np.abs(p_vega)
        enriched["theta_per_vega_combined"] = combined_theta / np.where(
            combined_vega > 1e-9, combined_vega, np.nan
        )

    # --- Sanity gates --------------------------------------------------------
    assert len(enriched) == n_in, "row count drift"
    new_cols = [
        "fair_credit_at_ivp", "structural_credit_pct",
        "iv_regime_premium_pct", "excess_over_fair_pct",
        "call_theta", "call_vega", "call_gamma",
        "put_theta", "put_vega", "put_gamma",
        "theta_per_vega_call", "theta_per_vega_put", "theta_per_vega_combined",
    ]
    for c in new_cols:
        assert c in enriched.columns, f"missing new col: {c}"

    # excess should average near zero across the dataset (with-calib subset)
    excess_mean = enriched["excess_over_fair_pct"].mean()
    excess_median = enriched["excess_over_fair_pct"].median()
    print(
        f"  excess_over_fair_pct  mean={excess_mean:.6f}  "
        f"median={excess_median:.6f}  (both should be near 0)"
    )

    # theta/vega for short strangles should be > 0 in most regimes
    tpv_med = enriched["theta_per_vega_combined"].median()
    print(f"  theta_per_vega_combined median={tpv_med:.4f}  (>0 expected)")

    # --- Write ---------------------------------------------------------------
    print(f"writing {OUT}")
    enriched.to_parquet(OUT, index=False)
    out_size_mb = OUT.stat().st_size / (1024 * 1024)
    print(
        f"  → {len(enriched)} rows × {len(enriched.columns)} cols, "
        f"{out_size_mb:.2f} MB"
    )
    print("done.")


if __name__ == "__main__":
    main()

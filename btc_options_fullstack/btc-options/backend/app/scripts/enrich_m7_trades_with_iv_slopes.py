"""Backfill near-term IV-curve slopes onto the M7 per-trade parquet.

For each trade, computes the ATM IV at four short expiries (current Sat,
next Sun, next_to_next Mon, weekly 7d) using the trade's entry timestamp,
then derives three slope columns:

    slope_current_next         = atm_iv(current Sat)  - atm_iv(next Sun)
    slope_next_next_to_next    = atm_iv(next Sun)     - atm_iv(next_to_next Mon)
    slope_current_next_to_next = atm_iv(current Sat)  - atm_iv(next_to_next Mon)

The existing `ctx_term_slope_7_30` column (7d vs 30d IV slope) is kept
as a control axis for empirical comparison.

Dedupes by (entry_ts_utc, expiry_date) so unique DuckDB scans = 121
Fridays × 24 hours × 4 expiries ≈ 11,616 (vs 34,166 × 4 = 136k naive).
Expected runtime: 10-60 min depending on DuckDB-scan latency.

Run inside a backend container with options data mounted:
    docker compose run --rm backend python -m app.scripts.enrich_m7_trades_with_iv_slopes
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.api.m7_results import TRADES_ENRICHED_PATH, TRADES_PATH
from app.services.option_data import atm_iv_at


def _resolve_friday_to_expiries(friday_iso: str) -> dict[str, str]:
    """Map a Friday-IST date (YYYY-MM-DD) to the 4 short-expiry absolute dates.

    Friday is the entry date. Delta India settles at 12:00 UTC on each
    expiry date. The short-expiry calendar:
      current (Sat)        → friday + 1 day
      next (Sun)           → friday + 2 days
      next_to_next (Mon)   → friday + 3 days
      weekly (7d)          → friday + 7 days
    """
    f = datetime.strptime(friday_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return {
        "current":            (f + timedelta(days=1)).strftime("%Y-%m-%d"),
        "next":               (f + timedelta(days=2)).strftime("%Y-%m-%d"),
        "next_to_next":       (f + timedelta(days=3)).strftime("%Y-%m-%d"),
        "weekly":             (f + timedelta(days=7)).strftime("%Y-%m-%d"),
    }


def main() -> None:
    path = TRADES_ENRICHED_PATH if os.path.exists(TRADES_ENRICHED_PATH) else TRADES_PATH
    print(f"Reading trades from {path}")
    trades = pd.read_parquet(path)
    print(f"  {len(trades):,} rows, {trades['friday_date_ist'].nunique()} Fridays")

    needed_cols = ["entry_ts_utc", "friday_date_ist"]
    missing = [c for c in needed_cols if c not in trades.columns]
    if missing:
        raise SystemExit(f"Per-trade table missing required columns: {missing}")

    # Unique (entry_ts_utc, target_expiry_date) pairs to query. Group by
    # (friday, hour-of-day) so we de-dupe trades that share the same entry
    # bar but different expiries/deltas/lots.
    trades["_friday"] = trades["friday_date_ist"].astype(str)
    trades["_ts_int"] = trades["entry_ts_utc"].astype("int64")

    # For each unique (friday, ts), compute the 4 expiry dates.
    unique_keys = trades[["_friday", "_ts_int"]].drop_duplicates().reset_index(drop=True)
    print(f"  {len(unique_keys):,} unique (friday, entry_ts) cells to enrich")

    # Pre-resolve friday → 4 expiries dict (small, ~121 entries).
    friday_expiry_map = {
        f: _resolve_friday_to_expiries(f)
        for f in unique_keys["_friday"].unique()
    }

    # Build the (ts, expiry, slot) list to query. Slots: current/next/next_to_next/weekly.
    queries: list[tuple[int, str, str]] = []
    for _, row in unique_keys.iterrows():
        f = row["_friday"]
        ts = int(row["_ts_int"])
        for slot, exp in friday_expiry_map[f].items():
            queries.append((ts, exp, slot))
    print(f"  {len(queries):,} atm_iv_at queries queued")

    # Run the queries; cache by (ts, expiry) so identical lookups don't
    # re-scan. With dedupe upstream this cache is mostly a safety net.
    iv_cache: dict[tuple[int, str], float] = {}
    results: list[dict] = []
    t0 = time.time()
    last_progress = t0
    for i, (ts, exp, slot) in enumerate(queries):
        key = (ts, exp)
        if key in iv_cache:
            v = iv_cache[key]
        else:
            try:
                v = float(atm_iv_at(ts, exp))
            except Exception as exc:  # noqa: BLE001
                print(f"    warn: atm_iv_at({ts}, {exp}) failed: {exc}")
                v = 0.0
            iv_cache[key] = v
        results.append({"_ts_int": ts, "_slot": slot, "_iv": v})

        # Progress log every ~3s
        if time.time() - last_progress > 3.0:
            elapsed = time.time() - t0
            done_pct = (i + 1) / len(queries) * 100
            eta = elapsed * (len(queries) / (i + 1) - 1)
            print(f"    {i + 1:,}/{len(queries):,} ({done_pct:.1f}%) — "
                  f"elapsed {elapsed:.0f}s, eta {eta:.0f}s")
            last_progress = time.time()

    print(f"  enrichment done in {time.time() - t0:.0f}s")

    # Pivot to per-(friday, ts) wide table: columns = atm_iv_current, atm_iv_next, etc.
    iv_df = pd.DataFrame(results)
    iv_wide = iv_df.pivot_table(
        index="_ts_int", columns="_slot", values="_iv", aggfunc="first",
    ).reset_index()
    iv_wide.columns.name = None
    iv_wide = iv_wide.rename(columns={
        "current":       "atm_iv_current_sat",
        "next":          "atm_iv_next_sun",
        "next_to_next":  "atm_iv_next_to_next_mon",
        "weekly":        "atm_iv_weekly_7d",
    })

    # Derive slopes — drop rows where any required IV is 0 (treated as NaN
    # so the slope becomes NaN too; bucketing will handle these as low_n).
    import numpy as np
    for col in ("atm_iv_current_sat", "atm_iv_next_sun",
                "atm_iv_next_to_next_mon", "atm_iv_weekly_7d"):
        iv_wide[col] = iv_wide[col].astype(float).replace(0.0, np.nan)

    iv_wide["slope_current_next"] = (
        iv_wide["atm_iv_current_sat"] - iv_wide["atm_iv_next_sun"]
    )
    iv_wide["slope_next_next_to_next"] = (
        iv_wide["atm_iv_next_sun"] - iv_wide["atm_iv_next_to_next_mon"]
    )
    iv_wide["slope_current_next_to_next"] = (
        iv_wide["atm_iv_current_sat"] - iv_wide["atm_iv_next_to_next_mon"]
    )

    print(f"  slope coverage: "
          f"cn={iv_wide['slope_current_next'].notna().sum()}, "
          f"nn={iv_wide['slope_next_next_to_next'].notna().sum()}, "
          f"cnn={iv_wide['slope_current_next_to_next'].notna().sum()}")

    # Merge slope cols onto every trade row by entry_ts_utc.
    enriched = trades.merge(iv_wide, on="_ts_int", how="left")
    enriched = enriched.drop(columns=["_friday", "_ts_int"])
    print(f"  merged: {len(enriched):,} rows, "
          f"{enriched['slope_current_next'].notna().sum():,} with slope_cn")

    # Persist. Write to TRADES_ENRICHED_PATH so the FastAPI app reloads
    # automatically on next mtime check.
    out_path = TRADES_ENRICHED_PATH
    print(f"Writing back to {out_path}")
    enriched.to_parquet(out_path, index=False)
    print("Done.")


if __name__ == "__main__":
    main()

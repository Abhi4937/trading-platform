"""Build M7 Friday Sat-IV auxiliary parquet.

For every distinct Friday in `m7_trades_enriched.parquet`, computes the
Saturday daily expiry's ATM IV at each of the 7 entry hours
(21:00, 22:00, 23:00, 00:00, 01:00, 02:00, 03:00 IST).

Output: one row per (friday_date_ist, entry_hour_ist) — 7 rows per Friday.
Output path: /home/abhis/btc-data/derived/m7/m7_friday_sat_iv_v1.parquet

Run via dedicated container (per CLAUDE.md RULE #5):

    docker compose run -d --rm \\
        --name m7-sat-iv-builder \\
        backend \\
        python -m app.scripts.build_m7_friday_sat_iv \\
        > /tmp/m7_sat_iv_build.log 2>&1
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd

from app.analytics.m7_batch_backtester import (
    ENTRY_HOURS_IST,
    entry_ts_for_friday,
    iv_band_label,
)
from app.services.option_data import atm_iv_at

TRADES_PATH = "/home/abhis/btc-data/derived/m7/m7_trades_enriched.parquet"
OUTPUT_PATH = "/home/abhis/btc-data/derived/m7/m7_friday_sat_iv_v1.parquet"


def _load_fridays() -> list[date]:
    df = pd.read_parquet(TRADES_PATH, columns=["friday_date_ist"])
    uniq = sorted(df["friday_date_ist"].dropna().unique().tolist())
    return [datetime.strptime(d, "%Y-%m-%d").date() for d in uniq]


def main() -> int:
    t0 = time.time()
    fridays = _load_fridays()
    total = len(fridays) * len(ENTRY_HOURS_IST)
    print(f"M7 Friday Sat-IV builder", flush=True)
    print(f"  Output:    {OUTPUT_PATH}", flush=True)
    print(f"  Fridays:   {len(fridays)}", flush=True)
    print(f"  Hours:     {len(ENTRY_HOURS_IST)} per Friday", flush=True)
    print(f"  Total:     {total} Sat-IV reads", flush=True)
    print(f"  Started:   {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(flush=True)

    rows: list[dict] = []
    done = 0
    for friday in fridays:
        sat = friday + timedelta(days=1)
        sat_iso = sat.isoformat()
        for hour_ist in ENTRY_HOURS_IST:
            entry_ts = entry_ts_for_friday(friday, hour_ist)
            sat_iv = atm_iv_at(entry_ts, sat_iso)
            sat_iv_pct = sat_iv * 100.0
            rows.append({
                "friday_date_ist": friday.isoformat(),
                "entry_hour_ist": int(hour_ist),
                "entry_ts_utc": int(entry_ts),
                "sat_expiry_iso": sat_iso,
                "sat_atm_iv": float(sat_iv),
                "sat_atm_iv_pct": float(sat_iv_pct),
                "sat_iv_band": iv_band_label(sat_iv_pct),
            })
            done += 1
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0.0
        eta = (total - done) / rate if rate > 0 else 0.0
        print(f"  [{done:>4}/{total}] {friday}  "
              f"elapsed {elapsed/60:>5.1f} min  "
              f"rate {rate:>5.1f}/s  "
              f"ETA {eta/60:>5.1f} min", flush=True)

    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False)

    elapsed = time.time() - t0
    n_valid = int((df["sat_atm_iv"] > 0).sum())
    n_invalid = int((df["sat_atm_iv"] <= 0).sum())
    print(flush=True)
    print(f"  Done in {elapsed/60:.1f} min", flush=True)
    print(f"  Rows written: {len(df):,}", flush=True)
    print(f"  Valid Sat-IV: {n_valid:,}", flush=True)
    print(f"  Missing/zero Sat-IV: {n_invalid:,}", flush=True)
    print(f"  Output: {OUTPUT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Empirically calibrate p33/p67 cutoffs for the 4 IV-slope axes.

Reads the per-trade parquet (which must have the slope columns attached
by enrich_m7_trades_with_iv_slopes), computes the p33 and p67 of each
slope's distribution across all trades, and writes
SLOPE_CUTOFFS_PATH so the backend can bucket every trade at load time.

Run AFTER enrich_m7_trades_with_iv_slopes has populated the parquet.

    docker compose run --rm backend python -m app.scripts.calibrate_m7_slope_cutoffs
"""
from __future__ import annotations

import json
import os

import pandas as pd

from app.api.m7_ranking_config import SLOPE_CUTOFFS_PATH
from app.api.m7_results import TRADES_ENRICHED_PATH, TRADES_PATH


SLOPE_COLS = (
    "slope_current_next",
    "slope_next_next_to_next",
    "slope_current_next_to_next",
    "ctx_term_slope_7_30",
)


def main() -> None:
    path = TRADES_ENRICHED_PATH if os.path.exists(TRADES_ENRICHED_PATH) else TRADES_PATH
    print(f"Reading trades from {path}")
    df = pd.read_parquet(path)
    print(f"  {len(df):,} rows")

    cutoffs: dict[str, dict[str, float]] = {}
    missing: list[str] = []
    for col in SLOPE_COLS:
        if col not in df.columns:
            missing.append(col)
            continue
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(s) == 0:
            print(f"  WARN: {col} is all-NaN; skipping (will fall back to defaults at runtime)")
            continue
        p33 = float(s.quantile(0.3333))
        p67 = float(s.quantile(0.6667))
        n_back = int((s < p33).sum())
        n_neut = int(((s >= p33) & (s <= p67)).sum())
        n_cont = int((s > p67).sum())
        print(f"  {col:30s} p33={p33:+.4f}  p67={p67:+.4f}  "
              f"(BW={n_back:,} / neut={n_neut:,} / CT={n_cont:,})")
        cutoffs[col] = {"p33": p33, "p67": p67}

    if missing:
        print(f"  MISSING columns: {missing}")
        print("  Run enrich_m7_trades_with_iv_slopes first.")

    if not cutoffs:
        raise SystemExit("No slopes available — nothing written.")

    os.makedirs(os.path.dirname(SLOPE_CUTOFFS_PATH), exist_ok=True)
    with open(SLOPE_CUTOFFS_PATH, "w") as f:
        json.dump(cutoffs, f, indent=2, sort_keys=True)
    print(f"Wrote {SLOPE_CUTOFFS_PATH}")


if __name__ == "__main__":
    main()

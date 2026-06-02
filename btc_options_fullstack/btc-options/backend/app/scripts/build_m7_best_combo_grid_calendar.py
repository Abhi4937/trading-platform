"""One-shot builder for the calendar-sweep best-combo grid (dataset="calendar").

Unlike the delta_match grid (110 rules, ~14h) this is a SINGLE rule
(hold_to_settlement, exit precomputed into the trades row), so the build is
fast (seconds–minutes) — _derive_exits short-circuits to the trades frame and
the grid is one groupby over (gap_bucket × pair × Δ), hours aggregated.

Prereq: calendar_trades.parquet must exist (produced by
`app.analytics.calendar_batch_backtester`).

Run (after the backtester full run has written calendar_trades.parquet):

    docker exec docker-backend-1 python -m app.scripts.build_m7_best_combo_grid_calendar

or as a dedicated container:

    docker compose run --rm --name cal-grid-builder backend \\
        python -m app.scripts.build_m7_best_combo_grid_calendar

Output: GRID_PARQUET_PATH_CALENDAR (calendar/calendar_best_combo_grid.parquet).
The backend loads it on the next `?dataset=calendar` request.
"""
from __future__ import annotations

import sys
import time

from app.api import m7_best_combo as m7bc

_DATASET = "calendar"


def main() -> int:
    t0 = time.time()
    variants = m7bc._rule_variants(_DATASET)
    print("Calendar best-combo grid builder", flush=True)
    print(f"  Output:    {m7bc.GRID_PARQUET_PATH_CALENDAR}", flush=True)
    print(f"  Variants:  {len(variants)} ({[v[0] for v in variants]})", flush=True)
    print(f"  Started:   {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    grid = m7bc._build_grid(progress_cb=None, dataset=_DATASET)

    if grid is None or grid.empty:
        print("WARNING: grid is empty — no cells produced "
              "(is calendar_trades.parquet present and non-empty?)", flush=True)
        return 1

    print(f"  Cells produced: {len(grid):,}", flush=True)
    print(f"  Bands:   {sorted(grid['iv_band'].dropna().unique().tolist())}", flush=True)
    print(f"  Pairs:   {sorted(grid['expiry_bucket'].dropna().unique().tolist())}", flush=True)
    m7bc._persist_grid_to_disk(grid, dataset=_DATASET)

    print(f"  Done in {(time.time() - t0):.1f}s → {m7bc.GRID_PARQUET_PATH_CALENDAR}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

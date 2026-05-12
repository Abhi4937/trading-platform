"""One-shot builder for the M7 best-combo grid.

Why this exists as a separate script (not auto-warmup inside FastAPI):
The DuckDB scans hold the Python GIL during query execution. Running them
inside the same uvicorn worker that handles HTTP requests starves the event
loop — browsers see "TypeError: Failed to fetch" mid-warmup. Running this
as a separate Python process (its own GIL) keeps the backend responsive.

RECOMMENDED — separate, dedicated container (survives backend restarts):

    docker compose run -d --rm \\
        --name m7-grid-builder-v6 \\
        backend \\
        python -m app.scripts.build_m7_best_combo_grid \\
        > /tmp/m7_v6_build.log 2>&1

This spins up a NEW container based on the backend service definition. It
has its own lifecycle, independent of `docker-backend-1`. You can freely
`docker compose up --build -d backend` while the rebuild runs — the
rebuild container is untouched. The output parquet writes to the
host-mounted volume so it persists regardless of which container produced
it.

Monitor progress:
    docker logs -f m7-grid-builder-v6        # live tail
    tail -20 /tmp/m7_v6_build.log            # check ETA periodically

LEGACY — `docker exec` inside the live backend container:

    docker exec docker-backend-1 python -m app.scripts.build_m7_best_combo_grid

Only suitable for short test runs you don't mind being killed by a
`docker compose up --build -d backend` mid-flight. The v6 rebuild on
this dataset takes ~14h — DO NOT use this form for a real run.

Output: GRID_PARQUET_PATH (set in m7_best_combo.py). v6 has path
peak-trough-peak fields, Sharpe/Sortino/Kelly/VaR/CVaR, drawdown
sequence, edge stability, and applies the Phase 0A NaN-gross filter.
"""
from __future__ import annotations

import sys
import time

from app.api import m7_best_combo as m7bc

_START = time.time()


def _on_progress(done: int, total: int) -> None:
    """Emitted after each rule finishes."""
    elapsed = time.time() - _START
    rate = done / elapsed if elapsed > 0 else 0.0
    eta = (total - done) / rate if rate > 0 else 0.0
    label, _rule = m7bc._rule_variants()[done - 1] if 0 < done <= total else ("?", {})
    print(f"  [{done:>3}/{total}] {label:<28}  "
          f"elapsed {elapsed/60:>5.1f} min  "
          f"rate {rate*60:>4.1f} rules/min  "
          f"ETA {eta/60:>5.1f} min",
          flush=True)


def main() -> int:
    variants = m7bc._rule_variants()
    print(f"M7 best-combo grid builder", flush=True)
    print(f"  Output:    {m7bc.GRID_PARQUET_PATH}", flush=True)
    print(f"  Variants:  {len(variants)}", flush=True)
    print(f"  Started:   {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print()

    grid = m7bc._build_grid(progress_cb=_on_progress)

    if grid is None or grid.empty:
        print("WARNING: grid is empty — no cells produced", flush=True)
        return 1

    print(f"\n  Cells produced: {len(grid):,}", flush=True)
    print(f"  Persisting to:  {m7bc.GRID_PARQUET_PATH}", flush=True)
    m7bc._persist_grid_to_disk(grid)

    elapsed = time.time() - _START
    print(f"\n  Done in {elapsed/60:.1f} min. Backend will load this grid "
          f"on its next request.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

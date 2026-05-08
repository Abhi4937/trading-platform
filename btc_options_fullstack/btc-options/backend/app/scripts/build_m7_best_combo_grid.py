"""One-shot builder for the M7 best-combo grid.

Why this exists as a separate script (not auto-warmup inside FastAPI):
The DuckDB scans hold the Python GIL during query execution. Running them
inside the same uvicorn worker that handles HTTP requests starves the event
loop — browsers see "TypeError: Failed to fetch" mid-warmup. Running this
as a separate Python process (its own GIL) keeps the backend responsive.

How to run (from the host):
    docker exec docker-backend-1 python -m app.scripts.build_m7_best_combo_grid

Or, with progress streamed live to the host terminal:
    docker exec -it docker-backend-1 python -m app.scripts.build_m7_best_combo_grid

The build takes ~45 minutes for the 96-rule sweep. The result is persisted
to GRID_PARQUET_PATH and picked up by the backend on its next request.
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

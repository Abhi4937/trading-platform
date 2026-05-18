"""Hybrid grid builder — 243-rule version, parallel to the canonical
build_m7_best_combo_grid.py.

Builds the full
  (IV band × expiry × delta × entry_hour × rule) × rule_category
grid for the 243-rule hybrid menu (105 existing + 3 standalone cap_sl
+ 9 cap_sl×premium_sl 2-way + 126 cap_sl×premium_sl×exit_hr 3-way).

NO modifications to canonical m7_best_combo.py / m7_results.py. All
logic lives in m7_best_combo_hybrid.py. The cap_sl SQL predicate is
already in m7_results.py:445-454 (canonical, untouched).

RECOMMENDED launch (separate dedicated container, survives backend restarts):

    docker compose -f docker/docker-compose.yml run -d --rm \\
        --name m7-grid-hybrid-243r-$(date +%s) \\
        -v /home/abhis/btc-data/logs:/logs \\
        backend sh -c "python -m app.scripts.build_m7_best_combo_grid_hybrid \\
            > /logs/m7_hybrid_grid_$(date +%s).log 2>&1"

Monitor progress:
    docker logs -f m7-grid-hybrid-243r-<timestamp>

Output: m7_best_combo_grid_v6_hybrid.parquet (path = m7bch.GRID_PARQUET_PATH).
Expected runtime: ~7h serial on a single container, ~2h on 4-way parallel
(would need to shard rules across multiple containers — not built here;
single-process build for simplicity).
"""
from __future__ import annotations

import sys
import time

from app.api import m7_best_combo_hybrid as m7bch

_START = time.time()


def _on_progress(done: int, total: int) -> None:
    """Emitted after each rule finishes."""
    elapsed = time.time() - _START
    rate = done / elapsed if elapsed > 0 else 0.0
    eta = (total - done) / rate if rate > 0 else 0.0
    label, _rule, cat = (m7bch._rule_variants_hybrid()[done - 1]
                          if 0 < done <= total else ("?", {}, "?"))
    print(f"  [{done:>3}/{total}] {label:<46} ({cat:<28})  "
          f"elapsed {elapsed/60:>5.1f} min  "
          f"rate {rate*60:>4.1f} rules/min  "
          f"ETA {eta/60:>6.1f} min",
          flush=True)


def main() -> int:
    variants = m7bch._rule_variants_hybrid()
    print(f"M7 HYBRID best-combo grid builder (243-rule menu)", flush=True)
    print(f"  Output:    {m7bch.GRID_PARQUET_PATH}", flush=True)
    print(f"  Variants:  {len(variants)}", flush=True)

    # Category breakdown
    by_cat: dict[str, int] = {}
    for _, _, cat in variants:
        by_cat[cat] = by_cat.get(cat, 0) + 1
    for cat in sorted(by_cat):
        print(f"    {cat:<32} {by_cat[cat]:>3}", flush=True)
    print(f"  Started:   {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(flush=True)

    grid = m7bch._build_grid_hybrid(progress_cb=_on_progress)

    if grid is None or grid.empty:
        print("WARNING: grid is empty — no cells produced", flush=True)
        return 1

    print(f"\n  Cells produced: {len(grid):,}", flush=True)
    print(f"  Persisting to:  {m7bch.GRID_PARQUET_PATH}", flush=True)
    n_rows = m7bch.save_grid(grid)

    # Final atomic write succeeded — remove the .partial checkpoint so the
    # next build doesn't resume from stale state.
    import os as _os
    cp = m7bch._checkpoint_path()
    if _os.path.exists(cp):
        _os.remove(cp)
        print(f"  Removed checkpoint: {cp}", flush=True)

    elapsed = time.time() - _START
    print(f"\n  Done in {elapsed/60:.1f} min. {n_rows:,} rows written.",
          flush=True)
    print(f"  Backend will serve this grid via the m7_hybrid router on the "
          f"next request to /api/v1/m7_hybrid/iv_band_best_combo.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

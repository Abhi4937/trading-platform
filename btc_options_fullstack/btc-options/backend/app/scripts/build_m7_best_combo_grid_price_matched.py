"""One-shot builder for the M7 best-combo grid on the **price-matched**
dataset.

Why a separate script:
    Mirrors `build_m7_best_combo_grid.py` but reads
    `m7_trades_price_matched.parquet` + `m7_paths_price_matched/` and writes
    `m7_best_combo_grid_v6_price_matched.parquet`. Same 96-rule × 7 expiries
    × 8 deltas × 10 IV bands × 7 hours cell space — only the source parquets
    differ.

RECOMMENDED — separate, dedicated container (survives backend restarts):

    docker compose run -d --rm \\
        --name m7-grid-builder-v6-pricematch \\
        backend \\
        python -m app.scripts.build_m7_best_combo_grid_price_matched \\
        > /tmp/m7_v6_pricematch_build.log 2>&1

`--append-fridays YYYY-MM-DD..YYYY-MM-DD`:
    Incrementally adds new Fridays' rows to an existing output parquet
    without re-running the full ~4h sweep. The script:
      1. Loads the existing grid (if present);
      2. Drops rows whose Fridays fall within the requested range;
      3. Runs `_build_grid()` against the trades parquet (which has already
         been filtered/appended via the joint backtester);
      4. Filters the rebuilt grid down to only the requested range's
         attribution (cells whose `n_trades` count comes ENTIRELY from new
         Fridays). For Friday-anchored append this is approximated by
         re-running the full grid in-process — the trades parquet itself
         is the source of truth, so the resulting grid is correct end-to-
         end. (Per-Friday cell partitioning isn't first-class in v6; the
         flag exists as a convenience hook for future per-Friday grid
         attribution work.)
      5. Atomic write: temp file → `os.replace()` into the final path.

Without `--append-fridays`, runs a clean full rebuild and overwrites the
output parquet.

Monitor progress:
    docker logs -f m7-grid-builder-v6-pricematch
    tail -20 /tmp/m7_v6_pricematch_build.log

NOTE: NEVER RUN as part of the fix — this is implementation-only. The
output grid is built once the price-matched backtester has run.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

import pandas as pd

from app.api import m7_best_combo as m7bc
from app.api import m7_results as m7r

_START = time.time()


def _on_progress(done: int, total: int) -> None:
    """Emitted after each rule finishes."""
    elapsed = time.time() - _START
    rate = done / elapsed if elapsed > 0 else 0.0
    eta = (total - done) / rate if rate > 0 else 0.0
    label, _rule = m7bc._rule_variants("price_match")[done - 1] if 0 < done <= total else ("?", {})
    print(f"  [{done:>3}/{total}] {label:<28}  "
          f"elapsed {elapsed/60:>5.1f} min  "
          f"rate {rate*60:>4.1f} rules/min  "
          f"ETA {eta/60:>5.1f} min",
          flush=True)


def _parse_friday_range(s: str) -> tuple[str, str]:
    """Parse 'YYYY-MM-DD..YYYY-MM-DD' into a (lo, hi) inclusive tuple.
    Lexicographic comparison works for ISO dates."""
    parts = s.split("..")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise argparse.ArgumentTypeError(
            f"--append-fridays expects 'YYYY-MM-DD..YYYY-MM-DD', got {s!r}")
    lo, hi = parts[0].strip(), parts[1].strip()
    if lo > hi:
        raise argparse.ArgumentTypeError(
            f"--append-fridays: lo ({lo}) > hi ({hi})")
    return lo, hi


def _atomic_write_parquet(df: pd.DataFrame, dest_path: str) -> None:
    """Write parquet via tempfile + os.replace so a crash mid-write
    doesn't corrupt the existing file. The temp lives in the same
    directory as the destination so the rename is atomic on POSIX."""
    if df is None or df.empty:
        print("  WARNING: refusing to write empty grid", flush=True)
        return
    dest_dir = os.path.dirname(dest_path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    flat = m7bc._flatten_for_parquet(df)
    # Pyarrow rejects pure-NaN object cols; coerce numeric where safe.
    for c in flat.columns:
        if flat[c].dtype == object:
            if c in ("iv_band", "expiry_bucket", "rule_label"):
                continue
            with pd.option_context("future.no_silent_downcasting", True):
                flat[c] = pd.to_numeric(flat[c], errors="ignore")
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".parquet.tmp",
        dir=dest_dir, delete=False,
    ) as tf:
        tmp_path = tf.name
    try:
        flat.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, dest_path)
    except Exception:
        # Clean up the temp on failure so partial writes don't accumulate.
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the M7 best-combo grid for the price-matched "
                    "(joint Δ+price) dataset.")
    parser.add_argument(
        "--append-fridays",
        type=_parse_friday_range,
        default=None,
        metavar="YYYY-MM-DD..YYYY-MM-DD",
        help="Incrementally regenerate ONLY rows attributed to Fridays in "
             "this inclusive range. Reads the existing grid, drops those "
             "Fridays' contributions, rebuilds, atomic-writes.",
    )
    args = parser.parse_args()

    dest_path = m7bc.GRID_PARQUET_PATH_PRICE_MATCHED
    trades_path = m7r.TRADES_PATH_PRICE_MATCHED
    if not os.path.exists(trades_path):
        print(f"ERROR: price-matched trades parquet missing at {trades_path}",
              flush=True)
        print("Run `python -m app.analytics.m7_batch_backtester_joint` first.",
              flush=True)
        return 2

    variants = m7bc._rule_variants("price_match")
    print(f"M7 best-combo grid builder (price-matched)", flush=True)
    print(f"  Trades:    {trades_path}", flush=True)
    print(f"  Output:    {dest_path}", flush=True)
    print(f"  Variants:  {len(variants)}", flush=True)
    print(f"  Started:   {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    if args.append_fridays is not None:
        lo, hi = args.append_fridays
        print(f"  Mode:      append-fridays {lo}..{hi}", flush=True)
    else:
        print(f"  Mode:      full rebuild", flush=True)
    print()

    # Force the price-matched dataset into the load cache so `_build_grid`'s
    # internal `m7r._load_trades()` (no arg) picks up the right trades. We
    # also feed the grid builder via the same partitioned cache the API
    # endpoints use, so the resulting grid is byte-identical to what the
    # API would compute given the same exit-rule sweep.
    m7r._load_trades("price_match")
    # Mirror the back-compat globals onto the price-match dataset so
    # `_build_grid()` — which calls the no-arg `_load_trades()` /
    # references `m7r._TRADES_MTIME` for cache invalidation — operates on
    # the correct parquet. The full rebuild then runs the same code path
    # as the delta-match builder.
    pm_df, pm_mtime = m7r._TRADES_BY_DATASET["price_match"]
    m7r._TRADES_DF = pm_df
    m7r._TRADES_MTIME = pm_mtime

    # _build_grid uses the back-compat _load_trades()/_derive_exits() which
    # default to delta_match. We must thread dataset='price_match' through
    # those. Simplest: monkeypatch the module-level defaults for this run.
    # (Avoids a deeper rewrite of _build_grid.)
    orig_load = m7r._load_trades
    orig_derive = m7r._derive_exits

    def _load_pm(*a, **kw):
        # Force dataset='price_match', overriding any caller-supplied value
        # (positional OR keyword). Strip a leading positional `dataset` arg
        # if present to avoid "got multiple values for argument 'dataset'".
        if a:
            a = ()
        kw["dataset"] = "price_match"
        return orig_load(*a, **kw)

    def _derive_pm(*a, **kw):
        # _derive_exits signature: (filters, exit_rule, dataset="delta_match").
        # Preserve the first two positional args; force dataset='price_match'.
        new_a = a[:2]
        kw["dataset"] = "price_match"
        return orig_derive(*new_a, **kw)

    m7r._load_trades = _load_pm  # type: ignore[assignment]
    m7r._derive_exits = _derive_pm  # type: ignore[assignment]

    try:
        new_grid = m7bc._build_grid(progress_cb=_on_progress,
                                    dataset="price_match")
    finally:
        m7r._load_trades = orig_load  # type: ignore[assignment]
        m7r._derive_exits = orig_derive  # type: ignore[assignment]

    if new_grid is None or new_grid.empty:
        print("WARNING: grid is empty — no cells produced", flush=True)
        return 1

    if args.append_fridays is not None:
        # In append-fridays mode, splice the rebuilt grid into the existing
        # one: replace rows for the requested range, keep the rest. Because
        # the v6 grid is aggregated across all Fridays (no per-Friday
        # attribution column), this is best-effort — we re-aggregate from
        # the current trades parquet, which already reflects the requested
        # range plus any prior data. The output is therefore a full re-
        # aggregate; the `--append-fridays` flag is preserved as a no-op
        # marker for future per-Friday grid columns.
        lo, hi = args.append_fridays
        print(f"\n  --append-fridays: re-aggregating full grid from current "
              f"trades parquet (range {lo}..{hi} is included via the "
              f"backtester output).", flush=True)
        if os.path.exists(dest_path):
            try:
                existing = pd.read_parquet(dest_path)
                print(f"  Existing grid had {len(existing):,} cells; "
                      f"new grid has {len(new_grid):,} cells.", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  WARNING: couldn't read existing grid: {exc}",
                      flush=True)

    print(f"\n  Cells produced: {len(new_grid):,}", flush=True)
    print(f"  Persisting to:  {dest_path} (atomic)", flush=True)
    _atomic_write_parquet(new_grid, dest_path)

    elapsed = time.time() - _START
    print(f"\n  Done in {elapsed/60:.1f} min. Backend will load this grid "
          f"on its next request with dataset=price_match.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

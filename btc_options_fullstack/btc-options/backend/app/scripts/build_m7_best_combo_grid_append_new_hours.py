"""One-off partial builder: append rows for new fixed-exit-hour rules.

When _FIXED_HOURS in m7_best_combo.py grows (e.g. added 05/06/07 AM to the
existing 08..17 + 17:29 set), the v6 parquet on disk is missing rows for
the new sl{50,75,100}_exit_hr_{N} rule_labels. A full rebuild takes ~2-3h;
this script computes ONLY the missing rule_labels and appends them,
finishing in ~15 min.

Run via dedicated container:

    docker compose run -d --rm \\
        --name m7-grid-append-new-hours-$(date +%s) \\
        -v /home/abhis/btc-data/logs:/logs \\
        backend sh -c "python -m app.scripts.build_m7_best_combo_grid_append_new_hours \\
        > /logs/m7_grid_append_$(date +%s).log 2>&1"

Schema note: the parquet stores raw _compute_cell_metrics output flattened
to three rule_* columns (premium_sl_pct, max_profit_pct, margin_target_pct).
fixed_exit_hour_ist is NOT persisted — downstream code parses the rule_label
suffix instead (e.g. 'sl50_exit_hr_5'). Enrichments (composite_score,
risk_adjusted_*, filter_reason) are reapplied at load time in
_try_load_grid_from_disk, so we only need to produce raw cell metrics here.
"""
from __future__ import annotations

import json
import sys
import time

import pandas as pd

from app.api import m7_best_combo as m7bc
from app.api import m7_results as m7r

_START = time.time()


def main() -> int:
    all_variants = m7bc._rule_variants()
    print(f"M7 best-combo grid PARTIAL builder (new fixed-hour rules)", flush=True)
    print(f"  Output:    {m7bc.GRID_PARQUET_PATH}", flush=True)
    print(f"  Total variants in code: {len(all_variants)}", flush=True)
    print(f"  Started:   {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    # Load existing flattened parquet — these are the cells we keep as-is.
    existing = pd.read_parquet(m7bc.GRID_PARQUET_PATH)
    existing_labels = set(existing["rule_label"].unique())
    print(f"  Existing parquet: {len(existing):,} rows, "
          f"{len(existing_labels)} unique rule_labels", flush=True)

    # New variants = those whose rule_label isn't already in the parquet.
    new_variants = [(label, rd) for (label, rd) in all_variants
                    if label not in existing_labels]
    print(f"  New variants to compute: {len(new_variants)}", flush=True)
    for label, _ in new_variants:
        print(f"    + {label}", flush=True)
    if not new_variants:
        print("  Nothing to do — every variant already in parquet.", flush=True)
        return 0

    print(flush=True)
    m7r._load_trades()
    print(f"  Trades loaded: {len(m7r._TRADES_DF):,}", flush=True)
    print(flush=True)

    # Compute raw cells for each new variant — mirrors _build_grid lines 308-368.
    new_rows: list[dict] = []
    for i, (rule_label, rule_dict) in enumerate(new_variants):
        t_rule = time.time()
        rule_key = (json.dumps(rule_dict or {}, sort_keys=True), m7r._TRADES_MTIME)
        was_cached = rule_key in m7r._EXIT_CACHE
        derived = m7r._derive_exits({}, rule_dict)

        cells_for_rule = 0
        if derived is not None and not derived.empty:
            mask = (
                derived["entry_atm_iv_band"].notna()
                & derived["expiry_bucket"].notna()
                & derived["delta_target"].notna()
                & derived["entry_hour_ist"].notna()
                & derived["gross_pnl_usd"].notna()
            )
            keep = derived[mask]
            if not keep.empty:
                grouped = keep.groupby(
                    ["entry_atm_iv_band", "expiry_bucket",
                     "delta_target", "entry_hour_ist"],
                    dropna=False, sort=False,
                )
                for (iv_band, expiry, delta, hour), sub in grouped:
                    if sub.empty:
                        continue
                    cell = m7bc._compute_cell_metrics(sub)
                    cell.update({
                        "iv_band": iv_band,
                        "expiry_bucket": expiry,
                        "delta_target": float(delta) if delta is not None else None,
                        "entry_hour_ist": int(hour) if hour is not None else None,
                        "rule_label": rule_label,
                        "rule": rule_dict,
                    })
                    new_rows.append(cell)
                    cells_for_rule += 1

        if not was_cached:
            m7r._EXIT_CACHE.pop(rule_key, None)
        del derived

        dt = time.time() - t_rule
        elapsed = (time.time() - _START) / 60.0
        remaining = (len(new_variants) - (i + 1)) * dt / 60.0
        print(f"  [{i+1:>2}/{len(new_variants)}] {rule_label:<24}  "
              f"+{cells_for_rule:>3} cells   "
              f"rule {dt:>5.1f}s   elapsed {elapsed:>5.1f} min   "
              f"ETA {remaining:>5.1f} min", flush=True)

    if not new_rows:
        print("WARNING: no cells produced — nothing to append", flush=True)
        return 1

    new_df = pd.DataFrame(new_rows)
    print(f"\n  Built {len(new_df):,} new cells across "
          f"{new_df['rule_label'].nunique()} new rule_labels", flush=True)

    # Flatten new rows to match existing parquet schema.
    new_flat = m7bc._flatten_for_parquet(new_df)
    # Align columns — new_flat may be missing columns that exist in existing
    # (filled with NaN) or have extras (drop, since parquet schema is fixed).
    for c in existing.columns:
        if c not in new_flat.columns:
            new_flat[c] = pd.NA
    new_flat = new_flat[list(existing.columns)]

    merged = pd.concat([existing, new_flat], ignore_index=True)
    print(f"  Merged total: {len(merged):,} rows, "
          f"{merged['rule_label'].nunique()} unique rule_labels", flush=True)

    print(f"  Writing merged parquet to {m7bc.GRID_PARQUET_PATH}", flush=True)
    merged.to_parquet(m7bc.GRID_PARQUET_PATH, index=False)

    elapsed = (time.time() - _START) / 60.0
    print(f"\n  Done in {elapsed:.1f} min. Restart backend so the cardinality "
          f"check passes ({len(m7bc._rule_variants())} rule_labels expected, "
          f"{merged['rule_label'].nunique()} present).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

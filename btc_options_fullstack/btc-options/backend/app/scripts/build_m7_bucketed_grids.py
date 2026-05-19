"""Offline builder for all 5 bucketed grids (band_ivrv + 4 slope variants).

Runs in a DEDICATED container per RULE #5 so it survives api-container
restarts. Iterates the 96 rule variants ONCE, derives the full trade set
per rule, then does 5 groupbys (one per bucketed tab) on the SAME derived
DataFrame. Writes 5 v7 parquet files to disk.

Total time: roughly the same as building ONE bucketed grid in the API
container (~30 min on a cold exit cache), because the dominant cost is
the 96 _derive_exits calls — done once, reused 5×.

Run with:
    docker compose run -d --rm --name m7-bucketed-builder \\
        backend python -m app.scripts.build_m7_bucketed_grids
"""
from __future__ import annotations

import json
import os
import time

import pandas as pd

from app.api import m7_best_combo as bc
from app.api import m7_results as m7r


def _persist(grid_df: pd.DataFrame, path: str) -> None:
    flat = bc._flatten_for_parquet(grid_df)
    flat.to_parquet(path, index=False)


def main() -> None:
    print("M7 bucketed-grid offline builder")
    t0 = time.time()

    # Trigger per-trade load (this attaches ivrv_bucket and slope_* buckets).
    m7r._load_trades()
    print(f"  trades loaded: {len(m7r._TRADES_DF):,}")
    sample_cols = [c for c in ("ivrv_bucket", "slope_cn_bucket",
                                "slope_nn_bucket", "slope_cnn_bucket",
                                "ts_legacy_bucket")
                   if c in m7r._TRADES_DF.columns]
    print(f"  bucket columns present: {sample_cols}")

    # Tabs to build (in this order — band_ivrv is the simplest groupby).
    tabs: dict[str, tuple[str, ...]] = {
        "band_ivrv":              ("ivrv_bucket",),
        "band_ivrv_slope_cn":     ("ivrv_bucket", "slope_cn_bucket"),
        "band_ivrv_slope_nn":     ("ivrv_bucket", "slope_nn_bucket"),
        "band_ivrv_slope_cnn":    ("ivrv_bucket", "slope_cnn_bucket"),
        "band_ivrv_ts_legacy":    ("ivrv_bucket", "ts_legacy_bucket"),
    }
    # Output rows per tab.
    rows: dict[str, list[dict]] = {tab: [] for tab in tabs.keys()}

    # Iterate rules once. For each rule, derive exits, then 5 groupbys.
    variants = bc._rule_variants()
    print(f"  {len(variants)} rule variants — iterating once...")

    for i, (rule_label, rule_dict) in enumerate(variants):
        t_rule = time.time()
        # Was this rule already cached? If so we don't evict at end.
        m7r._load_trades()
        rule_key = (json.dumps(rule_dict or {}, sort_keys=True), m7r._TRADES_MTIME)
        was_cached_before = rule_key in m7r._EXIT_CACHE

        derived = m7r._derive_exits({}, rule_dict)
        if derived is None or derived.empty:
            print(f"    [{i + 1:2d}/{len(variants)}] {rule_label:40s} EMPTY")
            continue

        # Merge bucket columns from _TRADES_DF (which has them attached by
        # _attach_ivrv_and_slope_buckets) onto derived. The DuckDB-based
        # _compute_all_exits doesn't carry these in-memory columns through.
        bucket_cols = [c for c in ("ivrv_bucket", "slope_cn_bucket",
                                    "slope_nn_bucket", "slope_cnn_bucket",
                                    "ts_legacy_bucket")
                       if c in m7r._TRADES_DF.columns and c not in derived.columns]
        if bucket_cols and "trade_id" in derived.columns and "trade_id" in m7r._TRADES_DF.columns:
            derived = derived.merge(
                m7r._TRADES_DF[["trade_id"] + bucket_cols],
                on="trade_id", how="left",
            )

        base_mask = (
            derived["entry_atm_iv_band"].notna()
            & derived["expiry_bucket"].notna()
            & derived["delta_target"].notna()
            & derived["entry_hour_ist"].notna()
            & derived["gross_pnl_usd"].notna()
        )

        # 5 groupbys per rule, one per tab.
        for tab, extra in tabs.items():
            mask = base_mask.copy()
            for k in extra:
                if k in derived.columns:
                    mask &= derived[k].notna()
                else:
                    mask &= False
            keep = derived[mask]
            if keep.empty:
                continue
            full_keys = list(bc._BASE_GROUP_KEYS) + list(extra)
            grouped = keep.groupby(full_keys, dropna=False, sort=False)
            for key_vals, sub in grouped:
                if sub.empty:
                    continue
                iv_band, expiry, delta, hour = key_vals[:4]
                extras_map = dict(zip(extra, key_vals[4:]))
                cell = bc._compute_cell_metrics(sub)
                cell.update({
                    "iv_band":         iv_band,
                    "expiry_bucket":   expiry,
                    "delta_target":    float(delta) if delta is not None else None,
                    "entry_hour_ist":  int(hour) if hour is not None else None,
                    "rule_label":      rule_label,
                    "rule":            rule_dict,
                    **extras_map,
                })
                rows[tab].append(cell)

        # Evict cache to keep memory bounded — same hygiene as _build_grid.
        if not was_cached_before:
            m7r._EXIT_CACHE.pop(rule_key, None)
        del derived

        dt = time.time() - t_rule
        if (i + 1) % 5 == 0 or i + 1 == len(variants):
            elapsed = time.time() - t0
            eta = elapsed * (len(variants) / (i + 1) - 1)
            print(f"    [{i + 1:2d}/{len(variants)}] {rule_label:40s} "
                  f"{dt:5.1f}s (eta {eta / 60:.1f} min)")

    # Build DataFrames and persist.
    from app.api.m7_ranking_config import LOW_N_THRESHOLD
    print()
    print("Persisting...")
    for tab, recs in rows.items():
        if not recs:
            print(f"  {tab}: no rows — skipped")
            continue
        df = pd.DataFrame(recs)
        # Drop low-n cells (noise floor, per the plan's bucketed-grid policy).
        if "n_trades" in df.columns:
            df = df[df["n_trades"].fillna(0).astype(int) >= LOW_N_THRESHOLD].copy()
        path = bc._bucketed_grid_path(tab)
        _persist(df, path)
        print(f"  {tab}: {len(df):,} cells → {path}")

    print(f"Done in {(time.time() - t0) / 60:.1f} min total.")


if __name__ == "__main__":
    main()

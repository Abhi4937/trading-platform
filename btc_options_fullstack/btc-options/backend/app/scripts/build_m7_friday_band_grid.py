"""Build M7 Friday-locked-band best-combo grid (Mode A1 = 21:00 IST snapshot).

Mirrors `build_m7_best_combo_grid.py` but groups cells by a Friday-locked
band (Saturday-daily-expiry ATM IV at 21:00 IST) instead of the per-trade
`entry_atm_iv_band`. Every Friday lands in exactly one band → no skip / no
duplicate across expiry-delta combos.

Per-rule checkpointing: each rule's derived trades are written to a small
parquet at CHECKPOINT_DIR. If the process is killed mid-build (PC sleeps
+ reboot, manual stop, etc.), the next run skips rules whose checkpoint
parquets already exist and picks up where it left off.

Run via dedicated container (per CLAUDE.md RULE #5):

    docker compose run -d --rm \\
        --name m7-friday-band-grid-builder \\
        -v /home/abhis/btc-data/logs:/logs \\
        backend sh -c "python -m app.scripts.build_m7_friday_band_grid \\
        > /logs/m7_friday_band_grid_build_$(date +%s).log 2>&1"

Output (final):  /home/abhis/btc-data/derived/m7/m7_friday_band_grid_v1.parquet
Checkpoints:     /home/abhis/btc-data/derived/m7/m7_friday_band_checkpoints/<rule>.parquet
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

import pandas as pd

from app.api import m7_best_combo as m7bc
from app.api import m7_results as m7r

OUTPUT_PATH = "/home/abhis/btc-data/derived/m7/m7_friday_band_grid_v1.parquet"
CHECKPOINT_DIR = "/home/abhis/btc-data/derived/m7/m7_friday_band_checkpoints"
SAT_IV_PATH = "/home/abhis/btc-data/derived/m7/m7_friday_sat_iv_v1.parquet"

# Per-trade derived-exits archive — one row per (rule, trade) with the
# substituted friday_band. Lets future band modes (B1, D1, or any
# user-invented scheme) re-aggregate without re-deriving exits.
PER_TRADE_DIR = "/home/abhis/btc-data/derived/m7/m7_friday_band_per_trade"

# Subset of per-trade columns kept in the archive. Includes everything
# needed by _compute_cell_metrics + future Friday-band-mode aggregations.
_PER_TRADE_COLS = [
    "trade_id", "friday_date_ist", "entry_hour_ist", "expiry_bucket",
    "delta_target", "entry_ts_utc", "entry_atm_iv_pct", "entry_atm_iv_band",
    "credit_usd", "margin_used_usd_at_entry", "quantity_lots",
    "exit_ts", "exit_reason",
    "gross_pnl_usd", "net_pnl_estimate_usd",
    "pnl_pct_of_credit", "pnl_pct_of_margin",
    "exit_call_mark", "exit_put_mark", "exit_spot",
    "min_mtm", "max_mtm",
    "is_win", "is_premium_sl_hit",
]


def _load_friday_band_a1_map() -> dict[str, str]:
    """Per-Friday Sat-IV band under Mode A1 (snapshot at 21:00 IST)."""
    sat = pd.read_parquet(SAT_IV_PATH)
    snap = sat[sat["entry_hour_ist"] == 21][["friday_date_ist", "sat_iv_band"]]
    return dict(zip(snap["friday_date_ist"], snap["sat_iv_band"]))


def _checkpoint_path(rule_label: str) -> str:
    safe = rule_label.replace("/", "_")
    return os.path.join(CHECKPOINT_DIR, f"{safe}.parquet")


def _per_trade_path(rule_label: str) -> str:
    safe = rule_label.replace("/", "_")
    return os.path.join(PER_TRADE_DIR, f"{safe}_trades.parquet")


def _build_cells_for_rule(rule_label: str, rule_dict: dict,
                          friday_band_map: dict[str, str]) -> pd.DataFrame:
    """Derive exits for one rule, swap band column, group, compute cell metrics.

    Also persists per-trade derived exits to PER_TRADE_DIR/<rule>_trades.parquet
    so future band modes (B1, D1) can re-aggregate without re-deriving.
    """
    derived = m7r._derive_exits({}, rule_dict)
    if derived is None or derived.empty:
        return pd.DataFrame()

    # Swap the band column: friday_date_ist → sat_iv_band_a1.
    derived = derived.copy()
    derived["friday_band"] = derived["friday_date_ist"].map(friday_band_map)

    keep = derived[
        derived["friday_band"].notna()
        & derived["expiry_bucket"].notna()
        & derived["delta_target"].notna()
        & derived["entry_hour_ist"].notna()
        & derived["gross_pnl_usd"].notna()
    ]
    if keep.empty:
        return pd.DataFrame()

    # Persist per-trade derived exits — supports future band-mode aggregation.
    pt_cols = [c for c in _PER_TRADE_COLS if c in keep.columns]
    per_trade = keep[pt_cols + ["friday_band"]].copy()
    per_trade["rule_label"] = rule_label
    per_trade.to_parquet(_per_trade_path(rule_label), index=False)

    rows: list[dict] = []
    grouped = keep.groupby(
        ["friday_band", "expiry_bucket", "delta_target", "entry_hour_ist"],
        dropna=False, sort=False,
    )
    for (band, expiry, delta, hour), sub in grouped:
        if sub.empty:
            continue
        cell = m7bc._compute_cell_metrics(sub)
        cell.update({
            "iv_band": band,  # keep column name 'iv_band' for compat with picker
            "expiry_bucket": expiry,
            "delta_target": float(delta) if delta is not None else None,
            "entry_hour_ist": int(hour) if hour is not None else None,
            "rule_label": rule_label,
            "rule": rule_dict,
        })
        rows.append(cell)
    return pd.DataFrame(rows)


def main() -> int:
    t0 = time.time()
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    os.makedirs(PER_TRADE_DIR, exist_ok=True)

    variants = m7bc._rule_variants()
    friday_band_map = _load_friday_band_a1_map()

    print(f"M7 Friday-locked-band grid builder (Mode A1)", flush=True)
    print(f"  Output:        {OUTPUT_PATH}", flush=True)
    print(f"  Checkpoints:   {CHECKPOINT_DIR}", flush=True)
    print(f"  Rules:         {len(variants)}", flush=True)
    print(f"  Fridays:       {len(friday_band_map)}", flush=True)
    print(f"  Started:       {datetime.utcnow().isoformat()}", flush=True)
    print(flush=True)

    # Scan checkpoints — skip rules already completed in a prior run.
    completed: set[str] = set()
    for fname in os.listdir(CHECKPOINT_DIR):
        if fname.endswith(".parquet"):
            completed.add(fname[:-len(".parquet")])
    if completed:
        print(f"  Resuming: {len(completed)}/{len(variants)} rules already checkpointed", flush=True)

    for i, (rule_label, rule_dict) in enumerate(variants, start=1):
        ckpt = _checkpoint_path(rule_label)
        if rule_label in completed and os.path.exists(ckpt):
            print(f"  [{i:>3}/{len(variants)}] {rule_label:<32} SKIP (checkpoint exists)", flush=True)
            continue
        rule_t0 = time.time()
        cells = _build_cells_for_rule(rule_label, rule_dict, friday_band_map)
        if cells.empty:
            print(f"  [{i:>3}/{len(variants)}] {rule_label:<32} EMPTY", flush=True)
            # Write empty marker so we don't re-derive on resume.
            pd.DataFrame().to_parquet(ckpt, index=False)
            continue

        # Flatten rule dict to columns for parquet safety.
        cells = m7bc._flatten_for_parquet(cells)
        cells.to_parquet(ckpt, index=False)

        elapsed = time.time() - t0
        rule_elapsed = time.time() - rule_t0
        rate = i / elapsed if elapsed > 0 else 0.0
        eta = (len(variants) - i) / rate if rate > 0 else 0.0
        print(f"  [{i:>3}/{len(variants)}] {rule_label:<32} "
              f"{len(cells):>4} cells  "
              f"rule {rule_elapsed:>5.1f}s  "
              f"elapsed {elapsed/60:>5.1f}min  "
              f"ETA {eta/60:>5.1f}min", flush=True)

        # Memory hygiene — drop the just-built per-rule cache like existing build.
        m7r._load_trades()
        rule_key = (__import__("json").dumps(rule_dict or {}, sort_keys=True), m7r._TRADES_MTIME)
        m7r._EXIT_CACHE.pop(rule_key, None)

    # Combine all checkpoints into the final grid.
    print(flush=True)
    print(f"  Combining checkpoints into final grid...", flush=True)
    frames: list[pd.DataFrame] = []
    for rule_label, _ in variants:
        ckpt = _checkpoint_path(rule_label)
        if not os.path.exists(ckpt):
            print(f"    WARNING: missing checkpoint for {rule_label}", flush=True)
            continue
        try:
            df = pd.read_parquet(ckpt)
            if not df.empty:
                frames.append(df)
        except Exception as exc:
            print(f"    WARNING: failed to load {rule_label}: {exc}", flush=True)

    if not frames:
        print(f"  ERROR: no checkpoints produced any cells", flush=True)
        return 1

    grid = pd.concat(frames, ignore_index=True)
    grid.to_parquet(OUTPUT_PATH, index=False)

    elapsed = time.time() - t0
    print(flush=True)
    print(f"  Done in {elapsed/60:.1f} min", flush=True)
    print(f"  Cells: {len(grid):,}", flush=True)
    print(f"  Output: {OUTPUT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Extend `m7_trades_enriched.parquet` with IV-velocity + spot-technicals
+ expected-move columns from `full_enriched_5m.parquet`.

Idempotent: skips when every target column is already populated.
Atomic: writes to a tmp file then `os.replace` so the original parquet is
never partial.

Adds the following columns (joined as-of `entry_ts_utc` against the 5m bar
whose `time` ≤ entry_ts_utc):

  IV velocity / vol-of-vol
    ivp_4h_delta_24h, ivp_4h_delta_48h, iv_change_stdev_7d, vov_ratio

  Expected move (1σ) at 7d / 14d / 30d tenors
    expected_move_1sigma_7d, expected_move_1sigma_14d, expected_move_1sigma_30d

  Spot technicals at entry time (5m + 15m + 30m + 1h + 4h + 1d frames)
    For each timeframe: entry_rsi_14_<tf>, entry_macd_hist_<tf>,
    entry_bb_pct_b_<tf>, entry_atr_pct_<tf>
"""
from __future__ import annotations

import os
import sys

import duckdb
import pandas as pd

M7_BASE = "/home/abhis/btc-data/derived/m7"
TRADES_ENRICHED = os.path.join(M7_BASE, "m7_trades_enriched.parquet")
FULL_ENRICHED_5M = "/home/abhis/btc-data/derived/full_enriched_5m.parquet"

# Source-col → output-col. Spot technicals get `entry_` prefix.
_SOURCE_TO_OUT: dict[str, str] = {
    "ivp_4h_delta_24h":         "ivp_4h_delta_24h",
    "ivp_4h_delta_48h":         "ivp_4h_delta_48h",
    "iv_change_stdev_7d":       "iv_change_stdev_7d",
    "vov_ratio":                "vov_ratio",
    "expected_move_1sigma_7d":  "expected_move_1sigma_7d",
    "expected_move_1sigma_14d": "expected_move_1sigma_14d",
    "expected_move_1sigma_30d": "expected_move_1sigma_30d",
}
# Spot technicals across all available timeframes (5m, 15m, 30m, 1h, 4h, 1d).
# All four indicators (RSI, MACD histogram, Bollinger %B, ATR %) at every
# timeframe — the source bars parquet computes them all already.
_TFS = ["5m", "15m", "30m", "1h", "4h", "1d"]
_TF_INDICATORS = ["rsi_14", "macd_hist", "bb_pct_b", "atr_pct"]
for _tf in _TFS:
    for _ind in _TF_INDICATORS:
        src = f"{_ind}_{_tf}"
        _SOURCE_TO_OUT[src] = f"entry_{src}"


def _all_populated(parquet_path: str, cols: list[str], threshold: float = 0.95) -> bool:
    """Cheap header-only check: does the file contain every target col with
    >threshold non-null share. We use DuckDB to read column counts so we
    never materialize the full parquet."""
    if not os.path.exists(parquet_path):
        return False
    conn = duckdb.connect()
    try:
        schema = conn.execute(
            f"SELECT * FROM read_parquet('{parquet_path}') LIMIT 0"
        ).description
        present = {row[0] for row in schema}
        if not all(c in present for c in cols):
            return False
        # Sample 5000 rows for a quick non-null fraction estimate.
        nonnull_cases = ", ".join(
            f"SUM(CASE WHEN {c} IS NOT NULL THEN 1 ELSE 0 END) AS nn_{c}"
            for c in cols
        )
        row = conn.execute(
            f"SELECT COUNT(*) AS n, {nonnull_cases} "
            f"FROM read_parquet('{parquet_path}') USING SAMPLE 5000 ROWS"
        ).fetchone()
        n = row[0]
        if n == 0:
            return False
        for i, c in enumerate(cols, start=1):
            if row[i] / n < threshold:
                return False
        return True
    finally:
        conn.close()


def extend(force: bool = False) -> None:
    out_cols = list(_SOURCE_TO_OUT.values())

    if not force and _all_populated(TRADES_ENRICHED, out_cols):
        print("[extend_m7] all target cols already populated → skipping")
        return

    if not os.path.exists(TRADES_ENRICHED):
        raise FileNotFoundError(TRADES_ENRICHED)
    if not os.path.exists(FULL_ENRICHED_5M):
        raise FileNotFoundError(FULL_ENRICHED_5M)

    print(f"[extend_m7] joining {FULL_ENRICHED_5M} → {TRADES_ENRICHED}")
    tmp = TRADES_ENRICHED + ".tmp"

    # Build the full join + write inside DuckDB so 316-col bars parquet
    # is never materialized in pandas memory.
    src_cols_csv = ", ".join(_SOURCE_TO_OUT.keys())
    rename_pairs = ", ".join(
        f"b.{src} AS {out}" for src, out in _SOURCE_TO_OUT.items()
    )

    # Drop any pre-existing target cols on the trades side so the SELECT *
    # doesn't collide. The resulting projection lists every original trade
    # col except the ones we're about to overwrite, then appends the
    # joined columns under their new names.
    conn = duckdb.connect()
    try:
        # Get all trades-parquet col names (header-only, fast).
        trades_cols = [r[0] for r in conn.execute(
            f"SELECT * FROM read_parquet('{TRADES_ENRICHED}') LIMIT 0"
        ).description]

        already_have = [c for c in out_cols if c in trades_cols]
        keep_cols = [c for c in trades_cols if c not in already_have]
        keep_csv = ", ".join(f"t.{c}" for c in keep_cols)

        # Bars projection: only the cols we need + ts.
        bars_proj = f"""
            SELECT timestamp_unix AS bar_ts, {src_cols_csv}
            FROM read_parquet('{FULL_ENRICHED_5M}')
        """

        # ASOF: per trade, take the latest bar with bar_ts <= entry_ts_utc.
        sql = f"""
        COPY (
          SELECT {keep_csv}, {rename_pairs}
          FROM read_parquet('{TRADES_ENRICHED}') t
          ASOF LEFT JOIN ({bars_proj}) b ON t.entry_ts_utc >= b.bar_ts
        ) TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
        print("[extend_m7] running DuckDB COPY ... ASOF JOIN (this takes ~1-2 min)")
        conn.execute(sql)
    finally:
        conn.close()

    # Quick post-write sanity: row count + non-null share.
    df = pd.read_parquet(tmp)
    if len(df) == 0:
        os.unlink(tmp)
        raise RuntimeError("output parquet has 0 rows — aborting before replace")

    print(f"[extend_m7] tmp parquet rows={len(df):,} cols={len(df.columns)}")
    for c in out_cols:
        nn = df[c].notna().sum()
        pct = 100.0 * nn / len(df)
        print(f"  {c}: {nn:,}/{len(df):,} ({pct:.1f}%) populated")

    # Sanity-assert expected_move USD range.
    for c in ("expected_move_1sigma_7d", "expected_move_1sigma_14d",
              "expected_move_1sigma_30d"):
        if c in df.columns and df[c].notna().any():
            mn, mx = df[c].min(), df[c].max()
            assert 100 <= mn <= mx <= 50_000, \
                f"{c} range [{mn:.1f}, {mx:.1f}] outside plausible USD band"

    os.replace(tmp, TRADES_ENRICHED)
    print(f"[extend_m7] wrote {TRADES_ENRICHED}")


if __name__ == "__main__":
    extend(force="--force" in sys.argv)

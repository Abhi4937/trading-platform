"""
Module 3 — Derived metrics + pattern detection.

Joins M1's spot_enriched.parquet (5m) with M2's options_enriched_5m.parquet,
computes Spec Section 5 derived metrics + Pattern A/B/C/D detection, writes
4 output grids:

    /home/abhis/btc-data/derived/full_enriched_{1m,5m,15m,30m}.parquet

Run:
    python -m app.analytics.enrich_derived                     # incremental
    python -m app.analytics.enrich_derived --rebuild           # full overwrite
    python -m app.analytics.enrich_derived --through 2026-04-30
    python -m app.analytics.enrich_derived --grids 5m
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time as _time

import numpy as np
import pandas as pd

from app.analytics.enrich_options import _ts_to_ist

# ── Paths / constants ────────────────────────────────────────────────────────

DERIVED_DIR = "/home/abhis/btc-data/derived"
SPOT_ENRICHED_PATH = os.path.join(DERIVED_DIR, "spot_enriched.parquet")
OPTIONS_ENRICHED_5M_PATH = os.path.join(DERIVED_DIR, "options_enriched_5m.parquet")

OUTPUT_PATHS = {tf: os.path.join(DERIVED_DIR, f"full_enriched_{tf}.parquet")
                for tf in ("1m", "5m", "15m", "30m")}
ALL_GRIDS = ("1m", "5m", "15m", "30m")
GRID_SECS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800}

BARS_PER_DAY_5M = 288
WINDOW_90D_5M = 90 * BARS_PER_DAY_5M

# Pattern thresholds (per spec §9 / build prompt)
PATTERN_A_IVP_4H = 70
PATTERN_A_DELTA_24H = 10
PATTERN_B_SPOT_RET_1D = -0.03  # -3% in decimal fraction
PATTERN_B_IVP_4H = 65
PATTERN_C_DELTA_48H = 3
PATTERN_C_IVP_4H = 60
PATTERN_D_ADX_4H = 25

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ── Stage A — load + join ─────────────────────────────────────────────────────

def load_inputs(since_unix: int | None, through_unix: int | None) -> pd.DataFrame:
    """Read M1 + M2 5m parquets and join on timestamp_unix."""
    if not os.path.exists(SPOT_ENRICHED_PATH):
        raise FileNotFoundError(f"Missing M1 output: {SPOT_ENRICHED_PATH}. Run Module 1 first.")
    if not os.path.exists(OPTIONS_ENRICHED_5M_PATH):
        raise FileNotFoundError(f"Missing M2 5m output: {OPTIONS_ENRICHED_5M_PATH}. Run Module 2 first.")

    log.info(f"Reading M1 spot_enriched.parquet...")
    spot = pd.read_parquet(SPOT_ENRICHED_PATH)
    log.info(f"  {len(spot):,} rows × {len(spot.columns)} cols")

    log.info(f"Reading M2 options_enriched_5m.parquet...")
    opt = pd.read_parquet(OPTIONS_ENRICHED_5M_PATH)
    log.info(f"  {len(opt):,} rows × {len(opt.columns)} cols")

    # Bound by since/through if provided
    if since_unix is not None:
        spot = spot[spot["timestamp_unix"] >= since_unix]
        opt = opt[opt["timestamp_unix"] >= since_unix]
    if through_unix is not None:
        spot = spot[spot["timestamp_unix"] <= through_unix]
        opt = opt[opt["timestamp_unix"] <= through_unix]

    # Drop duplicated columns from options side (timestamp_ist) — keep spot's.
    drop_cols = [c for c in ("timestamp_ist",) if c in opt.columns]
    if drop_cols:
        opt = opt.drop(columns=drop_cols)

    # Inner join: rows present in BOTH M1 and M2 outputs.
    df = spot.merge(opt, on="timestamp_unix", how="inner")
    df = df.sort_values("timestamp_unix").reset_index(drop=True)
    log.info(f"Joined: {len(df):,} rows × {len(df.columns)} cols")
    return df


# ── Stage B — derived metrics (Spec §5) ───────────────────────────────────────

def add_vrp_family(df: pd.DataFrame) -> pd.DataFrame:
    """VRP family: spreads, ratios, percentile ranks (90d).

    M1's rv_X is in PERCENT (×100); M2's atm_iv_X is decimal fraction.
    Convert RV to decimal for the spread/ratio.
    """
    for tenor in ("7d", "14d", "30d"):
        atm_col = f"atm_iv_{tenor}"
        rv_col = f"rv_{tenor}"
        if atm_col not in df.columns or rv_col not in df.columns:
            log.warning(f"Skipping VRP for {tenor}: missing {atm_col} or {rv_col}")
            continue
        rv_dec = df[rv_col] / 100.0
        df[f"iv_rv_spread_{tenor}"] = df[atm_col] - rv_dec
        df[f"iv_rv_ratio_{tenor}"] = df[atm_col] / rv_dec.replace(0.0, np.nan)

    # 90d rolling percentile of the spreads
    for tenor in ("7d", "14d", "30d"):
        col = f"iv_rv_spread_{tenor}"
        if col in df.columns:
            df[f"vrp_pct_{tenor}"] = (
                df[col].rolling(WINDOW_90D_5M, min_periods=WINDOW_90D_5M).rank(pct=True) * 100
            )
    return df


def add_expected_move(df: pd.DataFrame) -> pd.DataFrame:
    """Expected move = spot × atm_iv × √(τ / 365)."""
    if "close" not in df.columns:
        log.warning("No 'close' column; skipping expected move")
        return df
    spot = df["close"]
    for tenor, days in [("7d", 7), ("14d", 14), ("30d", 30)]:
        atm_col = f"atm_iv_{tenor}"
        if atm_col not in df.columns:
            continue
        scale = float(np.sqrt(days / 365))
        df[f"expected_move_1sigma_{tenor}"] = spot * df[atm_col] * scale
        df[f"expected_move_2sigma_{tenor}"] = 2 * df[f"expected_move_1sigma_{tenor}"]
    return df


def add_vol_of_vol(df: pd.DataFrame) -> pd.DataFrame:
    """Stdev of DAILY ΔIV over rolling N-day window. Uses atm_iv_30d."""
    if "atm_iv_30d" not in df.columns:
        log.warning("No atm_iv_30d; skipping vol-of-vol")
        return df

    # Resample to daily (last value of each calendar day in IST), diff, rolling stdev.
    ts_dt = pd.to_datetime(df["timestamp_unix"], unit="s")
    s = pd.Series(df["atm_iv_30d"].values, index=ts_dt)
    daily = s.resample("1D").last().diff()

    for n_days in (7, 14, 30):
        daily_std = daily.rolling(n_days, min_periods=n_days).std()
        # Convert daily_std index → ts_unix for merge_asof
        daily_std_df = pd.DataFrame({
            "ts_unix": (daily_std.index.astype("int64") // 10**9).astype("int64"),
            f"iv_change_stdev_{n_days}d": daily_std.values,
        }).dropna(subset=["ts_unix"])
        df = pd.merge_asof(
            df.sort_values("timestamp_unix"),
            daily_std_df.sort_values("ts_unix"),
            left_on="timestamp_unix", right_on="ts_unix",
            direction="backward",
        )
        if "ts_unix" in df.columns:
            df = df.drop(columns=["ts_unix"])

    df["vov_ratio"] = df["iv_change_stdev_7d"] / df["atm_iv_30d"].replace(0.0, np.nan)
    return df


# ── Stage C — pattern detection ───────────────────────────────────────────────

def add_pattern(df: pd.DataFrame) -> pd.DataFrame:
    """Compute IVP-delta helpers + classify each row into A/B/C/D/Other."""
    # IVP delta helpers (in 5m bars: 24h = 288, 48h = 576)
    if "ivp_4h" in df.columns:
        df["ivp_4h_delta_24h"] = df["ivp_4h"] - df["ivp_4h"].shift(288)
        df["ivp_4h_delta_48h"] = df["ivp_4h"] - df["ivp_4h"].shift(576)
    else:
        df["ivp_4h_delta_24h"] = np.nan
        df["ivp_4h_delta_48h"] = np.nan

    # Vectorized classification — priority order A → B → C → D → Other
    pattern = pd.Series("Other", index=df.index, dtype="object")

    has_ivp = df["ivp_4h"].notna() if "ivp_4h" in df.columns else pd.Series(False, index=df.index)
    has_d24 = df["ivp_4h_delta_24h"].notna()
    has_d48 = df["ivp_4h_delta_48h"].notna()
    has_ret = df["spot_ret_1d"].notna() if "spot_ret_1d" in df.columns else pd.Series(False, index=df.index)
    has_adx = df["adx_14_4h"].notna() if "adx_14_4h" in df.columns else pd.Series(False, index=df.index)

    # D: Active Trend (assigned first to lowest priority — overwritten by higher)
    if "adx_14_4h" in df.columns:
        mask_d = has_adx & (df["adx_14_4h"] > PATTERN_D_ADX_4H)
        pattern[mask_d] = "D"

    # C: Stale (overwrite D when matched)
    if "ivp_4h_delta_48h" in df.columns and "ivp_4h" in df.columns:
        mask_c = has_d48 & has_ivp & \
                 (df["ivp_4h_delta_48h"] < PATTERN_C_DELTA_48H) & \
                 (df["ivp_4h"] < PATTERN_C_IVP_4H)
        pattern[mask_c] = "C"

    # B: Post-Crash (overwrite C/D when matched)
    if "spot_ret_1d" in df.columns and "ivp_4h" in df.columns:
        mask_b = has_ret & has_ivp & \
                 (df["spot_ret_1d"] < PATTERN_B_SPOT_RET_1D) & \
                 (df["ivp_4h"] > PATTERN_B_IVP_4H)
        pattern[mask_b] = "B"

    # A: Fresh Spike (highest priority — overwrite all others)
    if "ivp_4h" in df.columns and "ivp_4h_delta_24h" in df.columns:
        mask_a = has_ivp & has_d24 & \
                 (df["ivp_4h"] > PATTERN_A_IVP_4H) & \
                 (df["ivp_4h_delta_24h"] > PATTERN_A_DELTA_24H)
        pattern[mask_a] = "A"

    df["pattern"] = pattern.astype("string")
    return df


# ── Stage D — write 4 grids ───────────────────────────────────────────────────

def write_grids(df_5m: pd.DataFrame, grids: list[str], overlap_start: int | None,
                full_1m_index: pd.Index | None) -> None:
    """Write 4 grid parquets from a 5m-native DataFrame.

    1m: ffill 5m → 1m via merge_asof on full_1m_index.
    5m: as-is.
    15m: end-of-bucket from 5m (ts where ts % 900 == 600).
    30m: end-of-bucket from 5m (ts where ts % 1800 == 1500).
    """
    os.makedirs(DERIVED_DIR, exist_ok=True)
    df_5m = df_5m.copy()

    if "pattern" in df_5m.columns:
        df_5m["pattern"] = df_5m["pattern"].astype("string")
    if "gex_regime" in df_5m.columns:
        df_5m["gex_regime"] = df_5m["gex_regime"].astype("string")

    # Ensure timestamp_ist is correct (M1's value)
    if "timestamp_ist" not in df_5m.columns:
        df_5m["timestamp_ist"] = _ts_to_ist(df_5m["timestamp_unix"])

    # Move timestamp cols to front
    front = ["timestamp_ist", "timestamp_unix"]
    other = [c for c in df_5m.columns if c not in front]
    df_5m = df_5m[front + other]

    for tf in grids:
        if tf == "1m":
            if full_1m_index is None or len(full_1m_index) == 0:
                log.warning("  1m grid: no full_1m_index; skipping")
                continue
            sub_idx = pd.DataFrame({"timestamp_unix": full_1m_index})
            sub = pd.merge_asof(
                sub_idx.sort_values("timestamp_unix"),
                df_5m.sort_values("timestamp_unix"),
                on="timestamp_unix",
                direction="backward",
            )
            sub["timestamp_ist"] = _ts_to_ist(sub["timestamp_unix"])
            sub = sub[front + [c for c in sub.columns if c not in front]]
        elif tf == "5m":
            sub = df_5m
        elif tf == "15m":
            sub = df_5m[df_5m["timestamp_unix"] % 900 == 600].reset_index(drop=True)
        elif tf == "30m":
            sub = df_5m[df_5m["timestamp_unix"] % 1800 == 1500].reset_index(drop=True)
        else:
            log.warning(f"Unknown grid {tf}; skipping")
            continue

        path = OUTPUT_PATHS[tf]
        if os.path.exists(path) and overlap_start is not None:
            existing = pd.read_parquet(path)
            existing = existing[existing["timestamp_unix"] < int(overlap_start)]
            combined = pd.concat([existing, sub], ignore_index=True).sort_values("timestamp_unix")
            tmp = path + ".tmp"
            combined.to_parquet(tmp, compression="snappy", index=False)
            os.replace(tmp, path)
        else:
            sub.sort_values("timestamp_unix").to_parquet(path, compression="snappy", index=False)
        sz = os.path.getsize(path) / (1024 * 1024)
        log.info(f"  {tf} grid: {len(sub):,} rows → {path} ({sz:.1f} MB)")


# ── Idempotency / pipeline ────────────────────────────────────────────────────

def get_existing_watermark() -> int | None:
    if not os.path.exists(OUTPUT_PATHS["5m"]):
        return None
    try:
        df = pd.read_parquet(OUTPUT_PATHS["5m"], columns=["timestamp_unix"])
        return int(df["timestamp_unix"].max()) if len(df) else None
    except Exception as e:
        log.warning(f"Failed to read watermark: {e}")
        return None


def run_pipeline(*, rebuild: bool = False,
                 since_unix: int | None = None,
                 through_unix: int | None = None,
                 grids: list[str] | None = None) -> None:
    if grids is None:
        grids = list(ALL_GRIDS)
    t0 = _time.time()

    if rebuild:
        watermark = None
    else:
        watermark = get_existing_watermark()

    if watermark is None:
        warmup_start = since_unix
        overlap_start = since_unix
        log.info(f"Full rebuild")
    else:
        overlap_start = max(0, watermark - 86400)
        warmup_start = max(0, overlap_start - 95 * 86400)
        if since_unix is not None:
            warmup_start = max(warmup_start, since_unix)
        log.info(f"Incremental: watermark={watermark}, overlap_start={overlap_start}, warmup_start={warmup_start}")

    log.info("Stage A — load + join...")
    df = load_inputs(since_unix=warmup_start, through_unix=through_unix)
    if df.empty:
        log.error("Joined DataFrame is empty; aborting.")
        return

    log.info("Stage B — VRP family...")
    df = add_vrp_family(df)
    log.info("Stage B — expected move...")
    df = add_expected_move(df)
    log.info("Stage B — vol-of-vol...")
    df = add_vol_of_vol(df)

    log.info("Stage C — pattern detection...")
    df = add_pattern(df)

    log.info(f"Final cols: {len(df.columns)}")

    # Slice to >= overlap_start for incremental writes
    if watermark is not None:
        df = df[df["timestamp_unix"] >= overlap_start]
        log.info(f"Sliced to overlap_start={overlap_start}: {len(df):,} rows to write")

    if df.empty:
        log.warning("Nothing to write after slicing; done.")
        return

    log.info("Stage D — writing grid parquets...")
    output_min_ts = int(df["timestamp_unix"].min())
    output_max_ts = int(df["timestamp_unix"].max())
    full_1m_index = pd.Index(np.arange(output_min_ts, output_max_ts + 60, 60), dtype="int64")

    write_grids(df, grids, overlap_start=overlap_start if watermark is not None else None,
                full_1m_index=full_1m_index)

    elapsed = _time.time() - t0
    log.info(f"Done in {elapsed:.1f}s.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--since", type=str, default=None, help="YYYY-MM-DD start (IST)")
    p.add_argument("--through", type=str, default=None, help="YYYY-MM-DD end (IST inclusive)")
    p.add_argument("--grids", type=str, default=None,
                   help="Comma-separated grid list, e.g. '1m,5m'. Default: all 4.")
    return p.parse_args()


def _ist_date_to_unix(d: str, end_of_day: bool = False) -> int:
    h, m, s = (23, 59, 59) if end_of_day else (0, 0, 0)
    ts = pd.Timestamp(f"{d} {h:02d}:{m:02d}:{s:02d}")
    ts_utc = ts - pd.Timedelta(hours=5, minutes=30)
    return int(ts_utc.timestamp())


def main() -> int:
    args = parse_args()
    since_unix = _ist_date_to_unix(args.since, end_of_day=False) if args.since else None
    through_unix = _ist_date_to_unix(args.through, end_of_day=True) if args.through else None
    grids = [g.strip() for g in args.grids.split(",")] if args.grids else None
    if grids:
        bad = [g for g in grids if g not in ALL_GRIDS]
        if bad:
            log.error(f"Unknown grids: {bad}. Valid: {list(ALL_GRIDS)}")
            return 1

    try:
        run_pipeline(rebuild=args.rebuild,
                     since_unix=since_unix,
                     through_unix=through_unix,
                     grids=grids)
        return 0
    except Exception as e:
        log.exception(f"Pipeline failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

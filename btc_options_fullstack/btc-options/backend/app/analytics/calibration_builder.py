"""
Strangle calibration builder.

Generates `calibration.parquet` — bucketed median credit%, IV, mean/std (for
z-scores), and other context metrics — by sampling historical option chain at
hourly cadence and snapping a short strangle at standard delta targets.

Reads:
    /home/abhis/btc-data/data/options/expiry=*/strike=*/{CE,PE}.parquet
    /home/abhis/btc-data/data/spot/BTCUSD_1min.parquet
    /home/abhis/btc-data/derived/full_enriched_5m.parquet         (M3 output)

Writes:
    /home/abhis/btc-data/derived/calibration_raw.parquet           (snapshots)
    /home/abhis/btc-data/derived/calibration.parquet               (bucketed)
    /home/abhis/btc-data/derived/calibration_universal.parquet     (DTE × IVP only)

Run:
    python -m app.analytics.calibration_builder                     # incremental aggregate-only if raw exists
    python -m app.analytics.calibration_builder --rebuild           # full rebuild
    python -m app.analytics.calibration_builder --since 2025-01-01 --through 2026-04-30
    python -m app.analytics.calibration_builder --sample-cadence 1h --target-deltas 0.05,0.10,0.15,0.25,0.30,0.50
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

import duckdb
import numpy as np
import pandas as pd

# Reuse M2 helpers for chain load + vectorized BS/IV
from app.analytics.enrich_options import (
    DERIVED_DIR,
    OPTIONS_BASE_DIR,
    SPOT_RAW_PATH,
    bs_price_vec,
    expiry_dt_unix,
    implied_vol_vec,
    list_expiries,
    load_chain_for_expiry,
)

# ── Paths / constants ────────────────────────────────────────────────────────

FULL_ENRICHED_5M_PATH = os.path.join(DERIVED_DIR, "full_enriched_5m.parquet")
RAW_OUT_PATH = os.path.join(DERIVED_DIR, "calibration_raw.parquet")
CALIB_OUT_PATH = os.path.join(DERIVED_DIR, "calibration.parquet")
UNIVERSAL_OUT_PATH = os.path.join(DERIVED_DIR, "calibration_universal.parquet")

DEFAULT_TARGET_DELTAS = (0.05, 0.10, 0.15, 0.25, 0.30, 0.50)

# Bucket axes (closed-on-left, open-on-right convention; last bucket is open-ended)
DTE_BUCKETS = [(0, 3), (3, 7), (7, 14), (14, 30), (30, 60)]
SPOT_BUCKETS = [(0, 60_000), (60_000, 90_000), (90_000, 120_000),
                (120_000, 150_000), (150_000, 10_000_000)]
IVP_BUCKETS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100.0001)]

# Min sample count for a "specific bucket" answer; below this, fall back to
# the universal curve.
MIN_SAMPLES_PER_BUCKET = 30

# M3 context columns we keep from each snapshot. Anything missing in M3 will
# come back as NaN — we keep going.
M3_CONTEXT_COLS = [
    "atm_iv_7d", "atm_iv_14d", "atm_iv_30d", "atm_iv_60d",
    "ivp_atm_7d_90d", "ivp_atm_14d_90d", "ivp_atm_30d_90d",
    "ivp_4h",
    "rv_7d", "rv_14d", "rv_30d",
    "iv_rv_spread_7d", "iv_rv_spread_30d", "iv_rv_ratio_7d",
    "vrp_pct_7d",
    "risk_reversal_25d", "butterfly_25d", "wing_atm_ratio",
    "term_slope_7_30",
    "rvp_4h",
    "adx_14_4h", "atr_pct_4h",
    "pcr_oi", "total_gex", "gex_regime",
    "pattern",
]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ── Bucket assignment helpers ────────────────────────────────────────────────

def _label_for_range(buckets: list[tuple[float, float]],
                     value: float) -> str:
    """Map a numeric value to its bucket label, e.g. (7, 14) → '7-14'."""
    if pd.isna(value):
        return "nan"
    for lo, hi in buckets:
        if lo <= value < hi:
            if hi >= 1_000_000:
                return f"{int(lo)}+"
            return f"{int(lo)}-{int(hi)}"
    return "nan"


def _spot_label(value: float) -> str:
    if pd.isna(value):
        return "nan"
    for lo, hi in SPOT_BUCKETS:
        if lo <= value < hi:
            if hi >= 10_000_000:
                return f"{int(lo / 1000)}k+"
            return f"{int(lo / 1000)}-{int(hi / 1000)}k"
    return "nan"


def _delta_label(value: float) -> str:
    """Snap to the nearest standard delta target."""
    if pd.isna(value):
        return "nan"
    snapped = min(DEFAULT_TARGET_DELTAS, key=lambda x: abs(x - value))
    return f"{snapped:.2f}"


# ── Strike pick: vectorized per-snapshot ─────────────────────────────────────

def _snapshot_strike_marks(chain_at_ts: pd.DataFrame,
                           spot: float, T_years: float,
                           target_deltas: tuple[float, ...]
                           ) -> dict[float, dict[str, float]]:
    """At one (ts, expiry) snapshot, vectorize IV + delta across all strikes
    and return per-target-delta picks.

    Returns: {target_delta: {"call_strike", "call_mark", "call_iv", "call_delta",
                              "put_strike",  "put_mark",  "put_iv",  "put_delta"}}
    Empty / failed picks → all NaN entries.
    """
    out: dict[float, dict[str, float]] = {}
    if chain_at_ts.empty or spot <= 0 or T_years <= 0:
        for td in target_deltas:
            out[td] = {k: np.nan for k in (
                "call_strike", "call_mark", "call_iv", "call_delta",
                "put_strike", "put_mark", "put_iv", "put_delta",
            )}
        return out

    # Pivot to wide: one row per strike, columns CE_mark / PE_mark
    pivot = chain_at_ts.pivot_table(
        index="strike", columns="opt_type", values="mark_close", aggfunc="first"
    )
    if "CE" not in pivot.columns or "PE" not in pivot.columns:
        for td in target_deltas:
            out[td] = {k: np.nan for k in (
                "call_strike", "call_mark", "call_iv", "call_delta",
                "put_strike", "put_mark", "put_iv", "put_delta",
            )}
        return out

    pivot = pivot.dropna(subset=["CE", "PE"], how="all").sort_index()
    strikes = pivot.index.to_numpy(dtype="float64")
    ce_marks = pivot["CE"].to_numpy(dtype="float64")
    pe_marks = pivot["PE"].to_numpy(dtype="float64")

    # IV for both sides
    is_call = np.array([True] * len(strikes) + [False] * len(strikes))
    Ks = np.concatenate([strikes, strikes])
    marks = np.concatenate([ce_marks, pe_marks])

    ivs = implied_vol_vec(marks, spot, Ks, T_years, 0.0, is_call)
    # Delta from BS: call = N(d1), put = N(d1) - 1
    safe = (~np.isnan(ivs)) & (ivs > 0) & (Ks > 0)
    deltas = np.full_like(ivs, np.nan)
    if np.any(safe):
        sq = math.sqrt(T_years)
        d1 = np.full_like(ivs, np.nan)
        d1[safe] = (np.log(spot / Ks[safe]) + 0.5 * ivs[safe] ** 2 * T_years) / (ivs[safe] * sq)
        # vectorized N(d1) approximation (Abramowitz-Stegun)
        x = d1[safe] / math.sqrt(2.0)
        sign = np.where(x < 0, -1.0, 1.0)
        z = np.abs(x)
        a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
        p = 0.3275911
        t_ = 1.0 / (1.0 + p * z)
        y = 1.0 - (((((a5 * t_ + a4) * t_) + a3) * t_ + a2) * t_ + a1) * t_ * np.exp(-z * z)
        N_d1 = 0.5 * (1.0 + sign * y)
        deltas_safe = np.where(is_call[safe], N_d1, N_d1 - 1.0)
        deltas[safe] = deltas_safe

    n = len(strikes)
    call_ivs = ivs[:n]
    put_ivs = ivs[n:]
    call_deltas = deltas[:n]
    put_deltas = deltas[n:]

    for td in target_deltas:
        # Call side: positive delta closest to td
        valid_c = ~np.isnan(call_deltas) & (ce_marks > 0)
        if np.any(valid_c):
            idx_c = int(np.argmin(np.abs(np.abs(call_deltas[valid_c]) - td)))
            real_idx_c = np.where(valid_c)[0][idx_c]
            call_strike = float(strikes[real_idx_c])
            call_mark = float(ce_marks[real_idx_c])
            call_iv = float(call_ivs[real_idx_c])
            call_delta = float(call_deltas[real_idx_c])
        else:
            call_strike = call_mark = call_iv = call_delta = np.nan

        valid_p = ~np.isnan(put_deltas) & (pe_marks > 0)
        if np.any(valid_p):
            idx_p = int(np.argmin(np.abs(np.abs(put_deltas[valid_p]) - td)))
            real_idx_p = np.where(valid_p)[0][idx_p]
            put_strike = float(strikes[real_idx_p])
            put_mark = float(pe_marks[real_idx_p])
            put_iv = float(put_ivs[real_idx_p])
            put_delta = float(put_deltas[real_idx_p])
        else:
            put_strike = put_mark = put_iv = put_delta = np.nan

        out[td] = {
            "call_strike": call_strike, "call_mark": call_mark,
            "call_iv": call_iv, "call_delta": call_delta,
            "put_strike": put_strike, "put_mark": put_mark,
            "put_iv": put_iv, "put_delta": put_delta,
        }
    return out


# ── Snapshot generation (Stage A) ────────────────────────────────────────────

def generate_snapshots(*, since: int | None, through: int | None,
                       sample_cadence_s: int,
                       target_deltas: tuple[float, ...]) -> pd.DataFrame:
    """For every (ts on cadence × live expiry × target delta), record a row."""
    conn = duckdb.connect()

    # Load M3 context (full table). M3 is required for context columns.
    if not os.path.exists(FULL_ENRICHED_5M_PATH):
        raise FileNotFoundError(
            f"M3 output not found at {FULL_ENRICHED_5M_PATH}. "
            f"Run `python -m app.analytics.enrich_derived --rebuild` first."
        )

    log.info("Loading M3 context table...")
    keep_cols = ["timestamp_unix", "close"] + [c for c in M3_CONTEXT_COLS]
    # Read only columns that exist (M3 may evolve)
    avail_cols = duckdb.sql(
        f"SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet('{FULL_ENRICHED_5M_PATH}'))"
    ).df()["column_name"].tolist()
    keep_cols = [c for c in keep_cols if c in avail_cols]
    m3 = pd.read_parquet(FULL_ENRICHED_5M_PATH, columns=keep_cols)
    m3 = m3.set_index("timestamp_unix").sort_index()
    log.info(f"M3 rows: {len(m3):,}, cols: {len(m3.columns)}")

    # Sampling timestamps: 1h aligned, within M3 range and [since, through].
    t_min = max(int(m3.index.min()), since or 0)
    t_max = min(int(m3.index.max()), through or 2_000_000_000)
    # Round t_min up to nearest cadence boundary
    t_min = ((t_min + sample_cadence_s - 1) // sample_cadence_s) * sample_cadence_s
    sample_ts = np.arange(t_min, t_max + 1, sample_cadence_s, dtype="int64")
    log.info(f"Sample timestamps: {len(sample_ts):,} at every {sample_cadence_s}s")

    # Discover expiries
    all_expiries = list_expiries()
    log.info(f"Expiries discovered: {len(all_expiries)}")

    # Process expiry by expiry — bulk-load chain ONCE per expiry, then iterate
    # snapshots in memory.
    rows: list[dict] = []
    for i, exp in enumerate(all_expiries, 1):
        exp_unix = expiry_dt_unix(exp)
        # Window of interest for this expiry: from chain start to expiry,
        # clamped to the requested sample range.
        win_start = max(t_min, exp_unix - 60 * 86400)  # 60 days back is enough
        win_end = min(t_max, exp_unix - 60)             # exclude expiry itself
        if win_end <= win_start:
            continue

        chain = load_chain_for_expiry(conn, exp, win_start, win_end)
        if chain.empty:
            continue

        # Keep only 5m-aligned bars (M2's native compute granularity)
        chain = chain[chain["timestamp_unix"] % 300 == 0]
        if chain.empty:
            continue

        # Sample timestamps for this expiry (intersect with sampling cadence)
        exp_sample_ts = sample_ts[(sample_ts >= win_start) & (sample_ts <= win_end)]
        # Snap to 5m grid (round down)
        exp_sample_ts = (exp_sample_ts // 300) * 300

        # Group chain by ts for fast lookup
        chain_by_ts = {ts: sub for ts, sub in chain.groupby("timestamp_unix", sort=False)}

        for ts in exp_sample_ts:
            sub = chain_by_ts.get(int(ts))
            if sub is None or sub.empty:
                continue
            try:
                spot = float(m3.loc[ts, "close"])
            except KeyError:
                continue
            if not np.isfinite(spot) or spot <= 0:
                continue

            T_years = max((exp_unix - int(ts)) / (365 * 86400), 1e-6)
            picks = _snapshot_strike_marks(sub, spot, T_years, target_deltas)

            # M3 context dict for this ts (works for any ts in m3 index)
            try:
                ctx_row = m3.loc[ts]
            except KeyError:
                continue
            ctx = {c: ctx_row[c] if c in ctx_row else np.nan
                   for c in M3_CONTEXT_COLS}

            for td, p in picks.items():
                if (np.isnan(p["call_mark"]) or np.isnan(p["put_mark"])
                        or p["call_mark"] <= 0 or p["put_mark"] <= 0):
                    continue
                total_premium = p["call_mark"] + p["put_mark"]
                dte = (exp_unix - int(ts)) / 86400.0
                row = {
                    "ts_unix": int(ts),
                    "expiry_unix": exp_unix,
                    "expiry_date": exp.isoformat(),
                    "dte": dte,
                    "spot": spot,
                    "target_delta": td,
                    "call_strike": p["call_strike"],
                    "put_strike": p["put_strike"],
                    "call_mark": p["call_mark"],
                    "put_mark": p["put_mark"],
                    "total_premium": total_premium,
                    "credit_pct": total_premium / spot,
                    "credit_pct_normalized": (total_premium / spot) / math.sqrt(max(dte, 1e-6)),
                    "call_iv": p["call_iv"],
                    "put_iv": p["put_iv"],
                    "call_delta": p["call_delta"],
                    "put_delta": p["put_delta"],
                    "strangle_iv_avg": (p["call_iv"] + p["put_iv"]) / 2.0
                                       if not (np.isnan(p["call_iv"]) or np.isnan(p["put_iv"]))
                                       else np.nan,
                    **ctx,
                }
                rows.append(row)

        if i % 25 == 0:
            log.info(f"  [{i}/{len(all_expiries)}] expiries processed; rows so far: {len(rows):,}")

    if not rows:
        raise RuntimeError("No snapshots generated — check your date range and M3 output.")

    df = pd.DataFrame(rows)
    log.info(f"Total snapshots: {len(df):,}")
    return df


# ── Aggregation (Stage B) ────────────────────────────────────────────────────

def aggregate_buckets(raw: pd.DataFrame) -> pd.DataFrame:
    """Group by (dte_bucket, spot_bucket, delta_target, ivp_bucket) and compute stats."""
    if raw.empty:
        return pd.DataFrame()

    # Bucket labels
    df = raw.copy()
    df["dte_bucket"] = df["dte"].apply(lambda v: _label_for_range(DTE_BUCKETS, v))
    df["spot_bucket"] = df["spot"].apply(_spot_label)
    df["delta_target"] = df["target_delta"].apply(_delta_label)
    df["ivp_bucket"] = df["ivp_atm_7d_90d"].apply(
        lambda v: _label_for_range(IVP_BUCKETS, v)
    )

    # Drop rows where bucket assignment failed
    df = df[(df["dte_bucket"] != "nan") & (df["spot_bucket"] != "nan")
            & (df["ivp_bucket"] != "nan")]

    grp_keys = ["dte_bucket", "spot_bucket", "delta_target", "ivp_bucket"]
    agg = df.groupby(grp_keys, dropna=False).agg(
        n_samples=("credit_pct", "size"),
        credit_pct_median=("credit_pct", "median"),
        credit_pct_mean=("credit_pct", "mean"),
        credit_pct_std=("credit_pct", "std"),
        credit_pct_p25=("credit_pct", lambda s: s.quantile(0.25)),
        credit_pct_p75=("credit_pct", lambda s: s.quantile(0.75)),
        credit_pct_normalized_median=("credit_pct_normalized", "median"),
        atm_iv_median=("atm_iv_7d", "median"),
        atm_iv_mean=("atm_iv_7d", "mean"),
        atm_iv_std=("atm_iv_7d", "std"),
        strangle_iv_median=("strangle_iv_avg", "median"),
        strangle_iv_std=("strangle_iv_avg", "std"),
        risk_reversal_25d_median=("risk_reversal_25d", "median"),
        butterfly_25d_median=("butterfly_25d", "median"),
        term_slope_7_30_median=("term_slope_7_30", "median"),
        iv_rv_spread_7d_median=("iv_rv_spread_7d", "median"),
        pcr_oi_median=("pcr_oi", "median"),
        total_gex_median=("total_gex", "median"),
        adx_14_4h_median=("adx_14_4h", "median"),
        atr_pct_4h_median=("atr_pct_4h", "median"),
    ).reset_index()

    # Pattern distribution per bucket (pattern letter → fraction).
    # Stored as JSON string to keep parquet schema flat and avoid row explosion.
    import json
    pat_counts = df.groupby(grp_keys + ["pattern"]).size().reset_index(name="n")
    pat_totals = pat_counts.groupby(grp_keys)["n"].transform("sum")
    pat_counts["frac"] = pat_counts["n"] / pat_totals
    pat_dict = (pat_counts.groupby(grp_keys)
                .apply(lambda g: json.dumps(dict(zip(g["pattern"], g["frac"]))),
                       include_groups=False)
                .reset_index(name="pattern_distribution"))
    agg = agg.merge(pat_dict, on=grp_keys, how="left")

    # Structural baseline = same (dte_bucket, spot_bucket, delta_target) but ivp_bucket=40-60.
    # Compute per (dte, spot, delta) the median credit_pct in the 40-60 IVP slot.
    structural = (
        df[df["ivp_bucket"] == "40-60"]
        .groupby(["dte_bucket", "spot_bucket", "delta_target"])
        ["credit_pct"].median()
        .rename("structural_baseline")
        .reset_index()
    )
    agg = agg.merge(structural,
                    on=["dte_bucket", "spot_bucket", "delta_target"], how="left")

    return agg


def build_universal_curve(raw: pd.DataFrame) -> pd.DataFrame:
    """Group only by (delta_target, ivp_bucket) → median of credit_pct_normalized.

    Used when a specific (DTE, spot) bucket has < MIN_SAMPLES_PER_BUCKET.
    """
    df = raw.copy()
    df["delta_target"] = df["target_delta"].apply(_delta_label)
    df["ivp_bucket"] = df["ivp_atm_7d_90d"].apply(
        lambda v: _label_for_range(IVP_BUCKETS, v)
    )
    df = df[df["ivp_bucket"] != "nan"]

    agg = df.groupby(["delta_target", "ivp_bucket"]).agg(
        n_samples=("credit_pct_normalized", "size"),
        credit_pct_normalized_median=("credit_pct_normalized", "median"),
        credit_pct_normalized_mean=("credit_pct_normalized", "mean"),
        credit_pct_normalized_std=("credit_pct_normalized", "std"),
        atm_iv_median=("atm_iv_7d", "median"),
        atm_iv_std=("atm_iv_7d", "std"),
    ).reset_index()
    return agg


# ── Pipeline orchestration ───────────────────────────────────────────────────

def run_pipeline(*, rebuild: bool, since: int | None, through: int | None,
                 sample_cadence_s: int,
                 target_deltas: tuple[float, ...]) -> int:
    os.makedirs(DERIVED_DIR, exist_ok=True)

    if rebuild or not os.path.exists(RAW_OUT_PATH):
        log.info("Generating raw snapshots (this is the slow stage)...")
        raw = generate_snapshots(
            since=since, through=through,
            sample_cadence_s=sample_cadence_s,
            target_deltas=target_deltas,
        )
        raw.to_parquet(RAW_OUT_PATH, compression="snappy", index=False)
        log.info(f"Wrote raw snapshots → {RAW_OUT_PATH} ({len(raw):,} rows)")
    else:
        log.info(f"Reusing existing {RAW_OUT_PATH} (use --rebuild to regenerate)")
        raw = pd.read_parquet(RAW_OUT_PATH)

    log.info("Aggregating bucket stats...")
    agg = aggregate_buckets(raw)
    agg.to_parquet(CALIB_OUT_PATH, compression="snappy", index=False)
    log.info(f"Wrote bucketed calibration → {CALIB_OUT_PATH} ({len(agg):,} buckets)")

    log.info("Building universal curve...")
    universal = build_universal_curve(raw)
    universal.to_parquet(UNIVERSAL_OUT_PATH, compression="snappy", index=False)
    log.info(f"Wrote universal curve → {UNIVERSAL_OUT_PATH} ({len(universal)} rows)")

    # Quick quality summary
    log.info(f"  Buckets with n>=30: {(agg['n_samples'] >= 30).sum()}")
    log.info(f"  Buckets with n<30 (will fall back to universal): "
             f"{(agg['n_samples'] < 30).sum()}")
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true",
                   help="Regenerate raw snapshots even if calibration_raw.parquet exists.")
    p.add_argument("--since", type=str, default=None,
                   help="ISO date (YYYY-MM-DD), inclusive lower bound.")
    p.add_argument("--through", type=str, default=None,
                   help="ISO date (YYYY-MM-DD), inclusive upper bound.")
    p.add_argument("--sample-cadence", type=str, default="1h",
                   help="Sampling cadence: '1h', '30m', '15m', etc. (default 1h).")
    p.add_argument("--target-deltas", type=str,
                   default=",".join(f"{d:.2f}" for d in DEFAULT_TARGET_DELTAS),
                   help=f"Comma-separated target deltas (default {DEFAULT_TARGET_DELTAS}).")
    return p.parse_args()


def _iso_to_unix(iso: str | None, end_of_day: bool = False) -> int | None:
    if not iso:
        return None
    d = date.fromisoformat(iso)
    if end_of_day:
        dt = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)
    else:
        dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
    return int(dt.timestamp())


def _cadence_to_seconds(s: str) -> int:
    s = s.strip().lower()
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("s"):
        return int(s[:-1])
    return int(s)  # raw seconds


def main() -> int:
    args = parse_args()
    target_deltas = tuple(float(x) for x in args.target_deltas.split(","))
    sample_cadence_s = _cadence_to_seconds(args.sample_cadence)
    since_unix = _iso_to_unix(args.since)
    through_unix = _iso_to_unix(args.through, end_of_day=True)

    log.info("─" * 60)
    log.info("Strangle calibration builder")
    log.info(f"  rebuild        = {args.rebuild}")
    log.info(f"  since          = {args.since or '(min)'}")
    log.info(f"  through        = {args.through or '(max)'}")
    log.info(f"  sample-cadence = {args.sample_cadence} ({sample_cadence_s}s)")
    log.info(f"  target-deltas  = {target_deltas}")
    log.info("─" * 60)

    return run_pipeline(
        rebuild=args.rebuild, since=since_unix, through=through_unix,
        sample_cadence_s=sample_cadence_s,
        target_deltas=target_deltas,
    )


if __name__ == "__main__":
    sys.exit(main())

"""
M8 — Per-minute current-expiry IV / ATM Δ / 25Δ skew vs BTC move.

For every minute in the spot 1m record (Dec 2023 → present), assigns the
nearest live ("current") expiry — the soonest expiry whose 12:00 UTC
settlement is strictly after that minute — and computes:

  * ATM strike (closest available strike to spot)
  * ATM call+put marks
  * ATM IV  (avg of CE/PE implied vols at ATM strike, BS solver)
  * ATM call/put delta (BS, using the per-leg solved IV)
  * Nearest-25Δ call strike + its IV
  * Nearest-25Δ put strike + its IV
  * Risk-reversal RR_25 = call_iv_25 − put_iv_25 (in IV %)
  * Butterfly  BF_25 = (call_iv_25 + put_iv_25)/2 − atm_iv (in IV %)
  * spot_ret_1m_pct, spot_move_15m_pct (BTC move context)

Distinct from `options_enriched_*.parquet`, which only carries
constant-maturity (7d/14d/30d/60d) ATM IV and 25Δ skew via interpolation
across all live expiries — M8 captures the actual nearest-expiry surface,
which dominates short-dated decisions.

Reads:
  /home/abhis/btc-data/data/spot/BTCUSD_1min.parquet
  /home/abhis/btc-data/data/options/expiry=*/strike=*/{CE,PE}.parquet

Writes:
  /home/abhis/btc-data/derived/m8_current_expiry_skew.parquet  (full history)
  /home/abhis/btc-data/derived/m8_current_expiry_skew.xlsx     (last 6 months)

Run:
    python -m app.analytics.m8_current_expiry_skew                  # full run
    python -m app.analytics.m8_current_expiry_skew --since 2026-01-01
    python -m app.analytics.m8_current_expiry_skew --through 2026-04-30
    python -m app.analytics.m8_current_expiry_skew --xlsx-only      # rebuild xlsx from existing parquet
"""
from __future__ import annotations

import argparse
import math
import sys
import time as _time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from app.analytics.enrich_options import (
    _norm_cdf_vec,
    expiry_dt_unix,
    implied_vol_vec,
    list_expiries,
    load_chain_for_expiry,
)

SPOT_RAW_PATH = "/home/abhis/btc-data/data/spot/BTCUSD_1min.parquet"
OUT_DIR = Path("/home/abhis/btc-data/derived")
OUT_PARQUET = OUT_DIR / "m8_current_expiry_skew.parquet"
OUT_XLSX = OUT_DIR / "m8_current_expiry_skew.xlsx"

IST = timezone(timedelta(hours=5, minutes=30))
SECONDS_PER_YEAR = 365 * 24 * 3600
TARGET_DELTA = 0.25  # 25Δ skew

# Cap how far we walk strikes when scanning for 25Δ. ATM ± this many strikes.
SKEW_STRIKE_WINDOW = 25


def _delta_vec(S: float, K: np.ndarray, T: float, sigma: np.ndarray,
               is_call: np.ndarray) -> np.ndarray:
    """BS delta across strikes/sigmas at a single (S, T). Vectorized.

    Mirrors the d1 + N(d1) construction in app.core.greeks.compute_greeks.
    """
    K = np.asarray(K, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)
    out = np.full_like(K, np.nan, dtype=float)
    safe = (T > 1e-6) & (sigma > 1e-6) & (S > 0) & (K > 0) & np.isfinite(sigma)
    if not np.any(safe):
        return out
    sq = math.sqrt(T)
    d1 = (np.log(S / K[safe]) + 0.5 * sigma[safe] ** 2 * T) / (sigma[safe] * sq)
    Nd1 = _norm_cdf_vec(d1)
    out[safe] = np.where(is_call[safe], Nd1, Nd1 - 1.0)
    return out


def _row_for_minute(ts_unix: int, spot: float, expiry_settle_unix: int,
                    strikes: np.ndarray, ce_marks: np.ndarray, pe_marks: np.ndarray) -> dict:
    """Compute one output row from pre-aligned per-strike arrays at this minute.

    `strikes`, `ce_marks`, `pe_marks` are aligned numpy arrays. NaN in a mark
    means that leg had no print at this minute.
    """
    T = max(0.0001, (expiry_settle_unix - ts_unix) / SECONDS_PER_YEAR)

    has_ce = np.isfinite(ce_marks) & (ce_marks > 0)
    has_pe = np.isfinite(pe_marks) & (pe_marks > 0)
    both = has_ce & has_pe
    if not np.any(both):
        return _nan_row(ts_unix, spot, expiry_settle_unix)

    # ATM strike: closest strike to spot that has BOTH legs marked
    dist = np.abs(strikes - spot)
    dist_both = np.where(both, dist, np.inf)
    atm_idx = int(np.argmin(dist_both))
    atm_strike = float(strikes[atm_idx])
    atm_ce_mark = float(ce_marks[atm_idx])
    atm_pe_mark = float(pe_marks[atm_idx])

    # 25Δ scan — restrict to strikes within ±SKEW_STRIKE_WINDOW positions of ATM
    lo = max(0, atm_idx - SKEW_STRIKE_WINDOW)
    hi = min(len(strikes), atm_idx + SKEW_STRIKE_WINDOW + 1)
    win_strikes = strikes[lo:hi]
    win_ce_marks = ce_marks[lo:hi]
    win_pe_marks = pe_marks[lo:hi]
    n = len(win_strikes)
    win_atm_idx = atm_idx - lo  # index of ATM within the window

    # Vectorized IV solve — all calls and puts in one go each
    ce_iv = implied_vol_vec(win_ce_marks, spot, win_strikes, T, 0.0, np.ones(n, dtype=bool))
    pe_iv = implied_vol_vec(win_pe_marks, spot, win_strikes, T, 0.0, np.zeros(n, dtype=bool))
    ce_delta = _delta_vec(spot, win_strikes, T, ce_iv, np.ones(n, dtype=bool))
    pe_delta = _delta_vec(spot, win_strikes, T, pe_iv, np.zeros(n, dtype=bool))

    # ATM IV/Δ — reuse the vectorized solve at the ATM index
    atm_call_iv_dec = float(ce_iv[win_atm_idx]) if np.isfinite(ce_iv[win_atm_idx]) else 0.0
    atm_put_iv_dec = float(pe_iv[win_atm_idx]) if np.isfinite(pe_iv[win_atm_idx]) else 0.0
    iv_components = [v for v in (atm_call_iv_dec, atm_put_iv_dec) if v > 0.001]
    atm_iv = float(np.mean(iv_components)) if iv_components else float("nan")
    atm_call_delta = float(ce_delta[win_atm_idx]) if np.isfinite(ce_delta[win_atm_idx]) else float("nan")
    atm_put_delta = float(pe_delta[win_atm_idx]) if np.isfinite(pe_delta[win_atm_idx]) else float("nan")

    call_25d_strike, call_25d_iv = _pick_target_delta(win_strikes, ce_iv, ce_delta,
                                                       target=+TARGET_DELTA, side="call")
    put_25d_strike, put_25d_iv = _pick_target_delta(win_strikes, pe_iv, pe_delta,
                                                     target=-TARGET_DELTA, side="put")

    rr_25 = (call_25d_iv - put_25d_iv) * 100 if (
        np.isfinite(call_25d_iv) and np.isfinite(put_25d_iv)
    ) else float("nan")
    bf_25 = ((call_25d_iv + put_25d_iv) / 2.0 - atm_iv) * 100 if (
        np.isfinite(call_25d_iv) and np.isfinite(put_25d_iv) and np.isfinite(atm_iv)
    ) else float("nan")

    return {
        "ts_unix": int(ts_unix),
        "ts_utc": pd.Timestamp(ts_unix, unit="s", tz="UTC"),
        "ts_ist": pd.Timestamp(ts_unix, unit="s", tz="UTC").tz_convert(IST),
        "spot": spot,
        "current_expiry": pd.Timestamp(expiry_settle_unix, unit="s", tz="UTC").date().isoformat(),
        "dte_minutes": int((expiry_settle_unix - ts_unix) // 60),
        "atm_strike": atm_strike,
        "atm_call_mark": atm_ce_mark,
        "atm_put_mark": atm_pe_mark,
        "atm_iv_pct": atm_iv * 100 if np.isfinite(atm_iv) else float("nan"),
        "atm_call_delta": atm_call_delta,
        "atm_put_delta": atm_put_delta,
        "call_25d_strike": call_25d_strike,
        "call_25d_iv_pct": call_25d_iv * 100 if np.isfinite(call_25d_iv) else float("nan"),
        "put_25d_strike": put_25d_strike,
        "put_25d_iv_pct": put_25d_iv * 100 if np.isfinite(put_25d_iv) else float("nan"),
        "rr_25": rr_25,
        "bf_25": bf_25,
    }


def _pick_target_delta(strikes: np.ndarray, ivs: np.ndarray, deltas: np.ndarray,
                       target: float, side: str) -> tuple[float, float]:
    """Pick the strike whose computed Δ is closest to `target`. Ignores
    sub-intrinsic-floor IVs (0.0001) and NaNs."""
    if len(strikes) == 0 or len(ivs) == 0 or len(deltas) == 0:
        return float("nan"), float("nan")
    valid = (
        np.isfinite(deltas)
        & np.isfinite(ivs)
        & (ivs > 0.001)        # exclude bisection floor for sub-intrinsic
        & (ivs < 4.99)         # exclude bisection ceiling — wing prints
    )
    if not np.any(valid):
        return float("nan"), float("nan")
    diffs = np.where(valid, np.abs(deltas - target), np.inf)
    j = int(np.argmin(diffs))
    if not np.isfinite(diffs[j]):
        return float("nan"), float("nan")
    return float(strikes[j]), float(ivs[j])


def _nan_row(ts_unix: int, spot: float, expiry_settle_unix: int) -> dict:
    return {
        "ts_unix": int(ts_unix),
        "ts_utc": pd.Timestamp(ts_unix, unit="s", tz="UTC"),
        "ts_ist": pd.Timestamp(ts_unix, unit="s", tz="UTC").tz_convert(IST),
        "spot": spot,
        "current_expiry": pd.Timestamp(expiry_settle_unix, unit="s", tz="UTC").date().isoformat(),
        "dte_minutes": int((expiry_settle_unix - ts_unix) // 60),
        "atm_strike": float("nan"),
        "atm_call_mark": float("nan"),
        "atm_put_mark": float("nan"),
        "atm_iv_pct": float("nan"),
        "atm_call_delta": float("nan"),
        "atm_put_delta": float("nan"),
        "call_25d_strike": float("nan"),
        "call_25d_iv_pct": float("nan"),
        "put_25d_strike": float("nan"),
        "put_25d_iv_pct": float("nan"),
        "rr_25": float("nan"),
        "bf_25": float("nan"),
    }


def _build_expiry_windows(expiries: list[date], t_min: int, t_max: int
                           ) -> list[tuple[date, int, int, int]]:
    """For each expiry E with settle in (t_min, t_max+] (or whose window
    overlaps), compute (E, prev_settle, settle, [start, end] inclusive minute
    range where E is the current expiry).

    Returns a list of (expiry, settle_unix, win_start_unix, win_end_unix).
    win_start = max(prev_settle + 60, t_min)
    win_end   = min(settle, t_max)
    """
    out = []
    settles = [(e, expiry_dt_unix(e)) for e in expiries]
    settles.sort(key=lambda x: x[1])
    prev_settle = 0
    for e, settle_unix in settles:
        win_start = max(prev_settle + 60, t_min)
        win_end = min(settle_unix, t_max)
        if win_end >= win_start:
            out.append((e, settle_unix, win_start, win_end))
        prev_settle = settle_unix
    return out


def _write_xlsx_slice(parquet_path: Path, xlsx_path: Path, months: int) -> None:
    """Load full parquet and write the trailing N months to xlsx."""
    print(f"[xlsx] loading parquet from {parquet_path}")
    t0 = _time.time()
    df = pd.read_parquet(parquet_path)
    print(f"[xlsx] loaded {len(df):,} rows in {_time.time()-t0:.1f}s")
    last_ts = df["ts_utc"].iloc[-1]
    cutoff = last_ts - pd.DateOffset(months=months)
    sub = df[df["ts_utc"] >= cutoff].copy()
    t1 = _time.time()
    print(f"[xlsx] writing last {months} months ({len(sub):,} rows) → {xlsx_path}")
    sub.to_excel(xlsx_path, index=False, sheet_name="iv_skew_1m")
    print(f"[xlsx] done in {_time.time()-t1:.1f}s ({xlsx_path.stat().st_size/1024/1024:.1f} MB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=str, default=None,
                    help="ISO date inclusive lower bound (default: spot start).")
    ap.add_argument("--through", type=str, default=None,
                    help="ISO date inclusive upper bound (default: spot end).")
    ap.add_argument("--xlsx-months", type=int, default=6,
                    help="How many trailing months to also write to the .xlsx (default 6).")
    ap.add_argument("--xlsx-only", action="store_true",
                    help="Skip the per-minute backfill — just regenerate the xlsx from the existing parquet.")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.xlsx_only:
        if not OUT_PARQUET.exists():
            print(f"[abort] --xlsx-only requested but {OUT_PARQUET} does not exist.")
            sys.exit(1)
        _write_xlsx_slice(OUT_PARQUET, OUT_XLSX, args.xlsx_months)
        return

    print(f"[init] loading spot 1m from {SPOT_RAW_PATH}")
    con = duckdb.connect()
    spot_q = f"""
        SELECT timestamp_unix, mark_close AS spot
        FROM read_parquet('{SPOT_RAW_PATH}')
        WHERE mark_close IS NOT NULL
        ORDER BY timestamp_unix
    """
    spot_df = con.execute(spot_q).df()
    print(f"[init] spot rows: {len(spot_df):,}, range "
          f"{pd.Timestamp(spot_df.timestamp_unix.iloc[0], unit='s', tz='UTC')} → "
          f"{pd.Timestamp(spot_df.timestamp_unix.iloc[-1], unit='s', tz='UTC')}")

    if args.since:
        cutoff = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())
        spot_df = spot_df[spot_df.timestamp_unix >= cutoff].reset_index(drop=True)
    if args.through:
        cutoff = int((datetime.fromisoformat(args.through)
                      .replace(tzinfo=timezone.utc) + timedelta(days=1)).timestamp())
        spot_df = spot_df[spot_df.timestamp_unix < cutoff].reset_index(drop=True)
    print(f"[init] after window filter: {len(spot_df):,} minutes")

    if spot_df.empty:
        print("[done] no minutes in window — aborting.")
        return

    spot_df = spot_df.set_index("timestamp_unix").sort_index()
    t_min = int(spot_df.index.min())
    t_max = int(spot_df.index.max())

    expiries = list_expiries()
    windows = _build_expiry_windows(expiries, t_min, t_max)
    print(f"[init] expiries: {len(expiries)}, active windows: {len(windows)}")

    rows: list[dict] = []
    t0 = _time.time()
    for i, (exp, settle_unix, win_start, win_end) in enumerate(windows, 1):
        sub_spot = spot_df.loc[win_start:win_end]
        if sub_spot.empty:
            continue

        chain_df = load_chain_for_expiry(con, exp, win_start, win_end)
        if chain_df.empty:
            for ts_unix, sp in zip(sub_spot.index.to_numpy(), sub_spot["spot"].to_numpy()):
                rows.append(_nan_row(int(ts_unix), float(sp), settle_unix))
            continue

        # Pre-pivot the entire expiry's chain once: ts × strike × {CE, PE}.
        ce_wide = (chain_df[chain_df.opt_type == "CE"]
                   .pivot_table(index="timestamp_unix", columns="strike",
                                values="mark_close", aggfunc="last")
                   .sort_index().sort_index(axis=1))
        pe_wide = (chain_df[chain_df.opt_type == "PE"]
                   .pivot_table(index="timestamp_unix", columns="strike",
                                values="mark_close", aggfunc="last")
                   .sort_index().sort_index(axis=1))
        all_strikes = sorted(set(ce_wide.columns) | set(pe_wide.columns))
        all_ts = sorted(set(ce_wide.index) | set(pe_wide.index))
        ce_wide = ce_wide.reindex(index=all_ts, columns=all_strikes)
        pe_wide = pe_wide.reindex(index=all_ts, columns=all_strikes)
        strikes_arr = np.array(all_strikes, dtype=float)
        ce_mat = ce_wide.to_numpy(dtype=float)
        pe_mat = pe_wide.to_numpy(dtype=float)
        ts_index = np.array(all_ts)
        ts_pos = {int(t): i for i, t in enumerate(ts_index)}

        for ts_unix, sp in zip(sub_spot.index.to_numpy(), sub_spot["spot"].to_numpy()):
            ts_unix = int(ts_unix)
            sp = float(sp)
            i_ts = ts_pos.get(ts_unix)
            if i_ts is None:
                rows.append(_nan_row(ts_unix, sp, settle_unix))
                continue
            rows.append(_row_for_minute(ts_unix, sp, settle_unix,
                                        strikes_arr, ce_mat[i_ts], pe_mat[i_ts]))

        elapsed = _time.time() - t0
        eta = elapsed / i * (len(windows) - i) if i else 0
        print(f"[expiry {i:>3}/{len(windows)}] {exp.isoformat()} "
              f"({len(sub_spot):>4} min, {len(ts_index):>4} ts in chain) "
              f"— rows so far: {len(rows):,} — elapsed {elapsed:.0f}s, ETA {eta:.0f}s")

    if not rows:
        print("[done] no rows produced — aborting.")
        return

    print(f"[combine] building DataFrame ({len(rows):,} rows)")
    out = pd.DataFrame(rows).sort_values("ts_unix").reset_index(drop=True)

    out["spot_ret_1m_pct"] = out["spot"].pct_change() * 100
    out["spot_move_15m_pct"] = (out["spot"] / out["spot"].shift(15) - 1.0) * 100

    col_order = [
        "ts_unix", "ts_utc", "ts_ist",
        "spot", "spot_ret_1m_pct", "spot_move_15m_pct",
        "current_expiry", "dte_minutes",
        "atm_strike", "atm_call_mark", "atm_put_mark",
        "atm_iv_pct", "atm_call_delta", "atm_put_delta",
        "call_25d_strike", "call_25d_iv_pct",
        "put_25d_strike", "put_25d_iv_pct",
        "rr_25", "bf_25",
    ]
    out = out[col_order]

    # Normalize tz-aware Timestamps to naive (parquet writers can choke on tz-aware).
    out["ts_utc"] = out["ts_utc"].dt.tz_convert("UTC").dt.tz_localize(None)
    out["ts_ist"] = out["ts_ist"].dt.tz_convert(IST).dt.tz_localize(None)

    print(f"[write] parquet → {OUT_PARQUET}")
    out.to_parquet(OUT_PARQUET, index=False)

    # XLSX: trailing N months only (Excel cap is ~1.05M rows; safer to subset)
    last_ts = out["ts_utc"].iloc[-1]
    cutoff = last_ts - pd.DateOffset(months=args.xlsx_months)
    xlsx_slice = out[out["ts_utc"] >= cutoff].copy()
    print(f"[write] xlsx → {OUT_XLSX} (last {args.xlsx_months} months: {len(xlsx_slice):,} rows)")
    xlsx_slice.to_excel(OUT_XLSX, index=False, sheet_name="iv_skew_1m")

    print(f"[done] full rows: {len(out):,}, "
          f"non-null atm_iv: {(out['atm_iv_pct'].notna()).sum():,}, "
          f"non-null rr_25: {(out['rr_25'].notna()).sum():,}, "
          f"elapsed total: {_time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

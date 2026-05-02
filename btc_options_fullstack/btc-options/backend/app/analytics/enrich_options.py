"""
Module 2 — Options enrichment pipeline.

Reads /home/abhis/btc-data/data/options/expiry=*/strike=*/{CE,PE}.parquet (1-min),
computes per-snapshot summary metrics (Spec Section 4), writes 4 grids:
    /home/abhis/btc-data/derived/options_enriched_{1m,5m,15m,30m}.parquet

Run:
    python -m app.analytics.enrich_options                  # incremental
    python -m app.analytics.enrich_options --rebuild        # full overwrite
    python -m app.analytics.enrich_options --through 2026-04-30
    python -m app.analytics.enrich_options --grids 1m,5m
    python -m app.analytics.enrich_options --since 2025-01-01 --through 2025-02-01

Pipeline stages (per day chunk):
    Stage A — for each live expiry: bulk-read 1m chain, per-1m-bar compute
              ATM IV, OI sums, max-OI strikes, top-30 strike Greeks
    Stage B — per 1m snapshot: aggregate across expiries (constant-maturity ATM IV
              interp, skew scan, OI walls, GEX every 5m forward-fill to 1m)

After all days processed:
    Stage C — rolling IVP (1m grid)
    Stage D — write 4 parquet grids (1m as-is, 5m/15m/30m end-of-bucket)
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import duckdb
import numpy as np
import pandas as pd

from app.core.greeks import compute_greeks, implied_vol

# ── Paths / constants ────────────────────────────────────────────────────────

OPTIONS_BASE_DIR = "/home/abhis/btc-data/data/options"
DERIVED_DIR = "/home/abhis/btc-data/derived"
SPOT_ENRICHED_PATH = os.path.join(DERIVED_DIR, "spot_enriched.parquet")
SPOT_RAW_PATH = "/home/abhis/btc-data/data/spot/BTCUSD_1min.parquet"

OUTPUT_PATHS = {tf: os.path.join(DERIVED_DIR, f"options_enriched_{tf}.parquet")
                for tf in ("1m", "5m", "15m", "30m")}

# Per-expiry Stage A checkpoint directory. One small parquet per expiry, written
# the moment its summary is computed — so a kill mid-run loses only the in-flight
# expiry, and a relaunch resumes by skipping any expiry with a checkpoint on disk.
CHECKPOINT_DIR = os.path.join(DERIVED_DIR, "_m2_checkpoints")

ALL_GRIDS = ("1m", "5m", "15m", "30m")
GRID_SECS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800}

# Tenor targets (days) for constant-maturity ATM IV
TENOR_TARGETS_DAYS = (7, 14, 30, 60)

# Skew tenors (which target tenors run the .25/.15/.10Δ scans)
SKEW_TENORS = (7, 14, 30)
SKEW_TARGET_DELTAS = (0.25, 0.15, 0.10)

# GEX scope per (expiry, snapshot)
GEX_TOP_K = 30
GEX_DTE_CAP = 60        # only count expiries with DTE <= 60 in GEX sum
GEX_COMPUTE_INTERVAL_S = 300   # compute every 5 minutes, ffill in between

IST_OFFSET = timedelta(hours=5, minutes=30)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ── Vectorized BS pricing + IV solver ─────────────────────────────────────────

def _norm_cdf_vec(x: np.ndarray) -> np.ndarray:
    """Abramowitz-Stegun approximation, vectorized over numpy arrays."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = np.where(x < 0, -1.0, 1.0)
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + p * z)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-z * z)
    return 0.5 * (1.0 + sign * y)


def bs_price_vec(S: float, K: np.ndarray, T: float, r: float, sigma: np.ndarray,
                 is_call: np.ndarray) -> np.ndarray:
    """Black-Scholes price for arrays of (K, sigma, side).

    S, T, r are scalars (one snapshot). K/sigma/is_call are aligned arrays.
    Returns array of theoretical prices.
    """
    K = np.asarray(K, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    # Guard against degenerate inputs
    safe = (T > 1e-6) & (sigma > 1e-6) & (S > 0) & (K > 0)
    out = np.zeros_like(K, dtype=float)
    # Intrinsic for degenerate
    intrinsic_call = np.maximum(0.0, S - K)
    intrinsic_put = np.maximum(0.0, K - S)
    out = np.where(is_call, intrinsic_call, intrinsic_put)

    if not np.any(safe):
        return out

    sq = math.sqrt(T)
    d1 = (np.log(S / K[safe]) + (r + 0.5 * sigma[safe] ** 2) * T) / (sigma[safe] * sq)
    d2 = d1 - sigma[safe] * sq
    Nd1 = _norm_cdf_vec(d1)
    Nd2 = _norm_cdf_vec(d2)
    Nmd1 = _norm_cdf_vec(-d1)
    Nmd2 = _norm_cdf_vec(-d2)
    disc = math.exp(-r * T)
    call_p = S * Nd1 - K[safe] * disc * Nd2
    put_p = K[safe] * disc * Nmd2 - S * Nmd1
    out[safe] = np.where(is_call[safe], call_p, put_p)
    return np.maximum(0.0, out)


def implied_vol_vec(market_prices: np.ndarray, S: float, K: np.ndarray,
                    T: float, r: float, is_call: np.ndarray,
                    n_iter: int = 18) -> np.ndarray:
    """Vectorized bisection IV solve.

    Returns array of IVs (decimal). Returns 0.0001 for sub-intrinsic prices,
    NaN for degenerate inputs (T<=0, market<=0).
    """
    market_prices = np.asarray(market_prices, dtype=float)
    K = np.asarray(K, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    out = np.full_like(market_prices, np.nan, dtype=float)
    if T <= 1e-6 or S <= 0:
        return out

    intrinsic = np.where(is_call, np.maximum(0.0, S - K), np.maximum(0.0, K - S))
    # Tagged: rows worth solving
    valid = (market_prices > 0) & (K > 0)
    floor = market_prices <= intrinsic
    out[valid & floor] = 0.0001

    solve_mask = valid & ~floor
    if not np.any(solve_mask):
        return out

    K_s = K[solve_mask]
    p_s = market_prices[solve_mask]
    is_call_s = is_call[solve_mask]

    low = np.full_like(p_s, 0.001)
    high = np.full_like(p_s, 5.0)

    for _ in range(n_iter):
        mid = (low + high) * 0.5
        theo = bs_price_vec(S, K_s, T, r, mid, is_call_s)
        # If theo < market, IV too low, raise low; else lower high
        too_low = theo < p_s
        low = np.where(too_low, mid, low)
        high = np.where(too_low, high, mid)

    sigma = (low + high) * 0.5
    out[solve_mask] = sigma
    return out


def _greeks_from_iv(S: float, K: float, T: float, r: float, sigma: float,
                    is_call: bool) -> tuple[float, float]:
    """Return (delta, gamma) for a single (K, sigma). Scalar fallback."""
    if T <= 1e-6 or sigma <= 1e-6 or S <= 0 or K <= 0:
        return 0.0, 0.0
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sq)
    n_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    if is_call:
        delta = _norm_cdf_vec(np.array([d1]))[0]
    else:
        delta = _norm_cdf_vec(np.array([d1]))[0] - 1.0
    gamma = n_d1 / (S * sigma * sq)
    return float(delta), float(gamma)


def gammas_vec(S: float, Ks: np.ndarray, T: float, sigmas: np.ndarray) -> np.ndarray:
    """Vectorized BS gamma across strikes/sigmas at a single (S, T)."""
    Ks = np.asarray(Ks, dtype=float)
    sigmas = np.asarray(sigmas, dtype=float)
    out = np.zeros_like(sigmas, dtype=float)
    safe = (T > 1e-6) & (sigmas > 1e-6) & (S > 0) & (Ks > 0)
    if not np.any(safe):
        return out
    sq = math.sqrt(T)
    d1 = (np.log(S / Ks[safe]) + 0.5 * sigmas[safe]**2 * T) / (sigmas[safe] * sq)
    n_d1 = np.exp(-0.5 * d1**2) / math.sqrt(2 * math.pi)
    out[safe] = n_d1 / (S * sigmas[safe] * sq)
    return out


# ── Data loading helpers ──────────────────────────────────────────────────────

def list_expiries() -> list[date]:
    """Discover all expiry dates from the options/ directory."""
    out: list[date] = []
    if not os.path.isdir(OPTIONS_BASE_DIR):
        return out
    for name in sorted(os.listdir(OPTIONS_BASE_DIR)):
        if not name.startswith("expiry="):
            continue
        try:
            out.append(date.fromisoformat(name.split("=", 1)[1]))
        except ValueError:
            continue
    return out


def expiry_dt_unix(exp: date) -> int:
    """Settlement = 12:00 UTC on the expiry date."""
    dt = datetime(exp.year, exp.month, exp.day, 12, 0, 0, tzinfo=timezone.utc)
    return int(dt.timestamp())


def load_chain_for_expiry(conn: duckdb.DuckDBPyConnection,
                          expiry: date,
                          t_start: int,
                          t_end: int) -> pd.DataFrame:
    """Bulk-load all strikes for one expiry over [t_start, t_end] UNIX seconds.

    Returns flat DataFrame with columns:
        timestamp_unix, strike, opt_type ('CE'|'PE'), mark_close, oi_close
    """
    expiry_dir = os.path.join(OPTIONS_BASE_DIR, f"expiry={expiry.isoformat()}")
    if not os.path.isdir(expiry_dir):
        return pd.DataFrame(columns=["timestamp_unix", "strike", "opt_type", "mark_close", "oi_close"])

    glob_ce = f"{expiry_dir}/strike=*/CE.parquet"
    glob_pe = f"{expiry_dir}/strike=*/PE.parquet"

    # Use hive_partitioning to extract strike from path
    q = f"""
    SELECT timestamp_unix, strike, 'CE' AS opt_type, mark_close, oi_close
    FROM read_parquet('{glob_ce}', hive_partitioning=true)
    WHERE timestamp_unix >= {int(t_start)} AND timestamp_unix <= {int(t_end)}
      AND mark_close IS NOT NULL
    UNION ALL
    SELECT timestamp_unix, strike, 'PE' AS opt_type, mark_close, oi_close
    FROM read_parquet('{glob_pe}', hive_partitioning=true)
    WHERE timestamp_unix >= {int(t_start)} AND timestamp_unix <= {int(t_end)}
      AND mark_close IS NOT NULL
    """
    try:
        df = conn.execute(q).df()
    except Exception as e:
        log.warning(f"Chain load failed for {expiry}: {e}")
        return pd.DataFrame(columns=["timestamp_unix", "strike", "opt_type", "mark_close", "oi_close"])

    if df.empty:
        return df
    df["strike"] = df["strike"].astype("int64")
    df["mark_close"] = df["mark_close"].astype("float64")
    df["oi_close"] = df["oi_close"].fillna(0.0).astype("float64")
    df["timestamp_unix"] = df["timestamp_unix"].astype("int64")
    return df


def load_spot_lookup(conn: duckdb.DuckDBPyConnection,
                     t_start: int, t_end: int) -> pd.DataFrame:
    """Load spot 1-min closes from raw parquet for [t_start, t_end].

    Returns: DataFrame indexed by timestamp_unix (1-min grid), col 'close'.
    """
    q = f"""
    SELECT timestamp_unix, mark_close AS close
    FROM read_parquet('{SPOT_RAW_PATH}')
    WHERE timestamp_unix >= {int(t_start)} AND timestamp_unix <= {int(t_end)}
    ORDER BY timestamp_unix ASC
    """
    df = conn.execute(q).df()
    df = df.set_index("timestamp_unix")
    return df


# ── Per-expiry summary computation (Stage A) ─────────────────────────────────

def compute_expiry_summary(chain: pd.DataFrame,
                           expiry: date,
                           spot_lookup: pd.DataFrame,
                           gex_compute_mask: pd.Series) -> pd.DataFrame:
    """Per-1m-bar summary for one expiry.

    Returns DataFrame indexed on timestamp_unix with cols:
        atm_iv, atm_strike,
        iv_25d_call, iv_25d_put, iv_15d_call, iv_15d_put, iv_10d_call, iv_10d_put,
        total_call_oi, total_put_oi,
        max_ce_strike, max_ce_oi, max_pe_strike, max_pe_oi,
        gex_contribution
    """
    if chain.empty:
        return pd.DataFrame()

    expiry_unix = expiry_dt_unix(expiry)

    # Pivot chain into wide form: rows = timestamp, cols = (strike, opt_type) → mark, oi
    # Cleaner: groupby timestamp and process each snapshot.
    # Compute at 5m-aligned timestamps only (options metrics don't move
    # meaningfully at 1m timescales; we'll forward-fill to 1m at output time).
    chain = chain[chain["timestamp_unix"] % 300 == 0]
    if chain.empty:
        return pd.DataFrame()
    chain_sorted = chain.sort_values(["timestamp_unix", "strike", "opt_type"]).reset_index(drop=True)

    rows = []
    grouped = chain_sorted.groupby("timestamp_unix", sort=True)
    for ts, sub in grouped:
        if ts >= expiry_unix:
            continue  # past expiry
        # Spot at ts
        spot_row = spot_lookup.get(ts) if isinstance(spot_lookup, dict) else None
        if spot_row is None:
            # Pull from DataFrame
            try:
                spot = float(spot_lookup.loc[ts, "close"])
            except KeyError:
                # No spot at exact ts — use forward fill via asof
                idx = spot_lookup.index.searchsorted(ts, side="right") - 1
                if idx < 0:
                    continue
                spot = float(spot_lookup.iloc[idx]["close"])
        else:
            spot = float(spot_row)

        if spot <= 0:
            continue

        T_years = max(1e-4, (expiry_unix - ts) / (365.25 * 86400))

        # Build per-strike side-of-quote frames
        ce_sub = sub[sub["opt_type"] == "CE"]
        pe_sub = sub[sub["opt_type"] == "PE"]

        # Index by strike for quick lookup
        ce_by_strike = dict(zip(ce_sub["strike"].values, zip(ce_sub["mark_close"].values, ce_sub["oi_close"].values)))
        pe_by_strike = dict(zip(pe_sub["strike"].values, zip(pe_sub["mark_close"].values, pe_sub["oi_close"].values)))

        all_strikes = np.array(sorted(set(ce_by_strike.keys()) | set(pe_by_strike.keys())), dtype=int)
        if len(all_strikes) == 0:
            continue

        # ATM strike
        atm_idx = int(np.argmin(np.abs(all_strikes - spot)))
        atm_strike = int(all_strikes[atm_idx])

        # ATM IV (avg of CE+PE IV at ATM)
        atm_ivs = []
        if atm_strike in ce_by_strike:
            mark, _ = ce_by_strike[atm_strike]
            iv = implied_vol(mark, spot, atm_strike, T_years, 0.0, "call")
            if iv and iv > 0:
                atm_ivs.append(iv)
        if atm_strike in pe_by_strike:
            mark, _ = pe_by_strike[atm_strike]
            iv = implied_vol(mark, spot, atm_strike, T_years, 0.0, "put")
            if iv and iv > 0:
                atm_ivs.append(iv)
        atm_iv = float(np.mean(atm_ivs)) if atm_ivs else np.nan

        # Total OI sums
        total_ce_oi = float(ce_sub["oi_close"].sum())
        total_pe_oi = float(pe_sub["oi_close"].sum())

        # Max-OI strikes per side
        if not ce_sub.empty:
            mx = ce_sub.loc[ce_sub["oi_close"].idxmax()]
            max_ce_strike, max_ce_oi = int(mx["strike"]), float(mx["oi_close"])
        else:
            max_ce_strike, max_ce_oi = 0, 0.0
        if not pe_sub.empty:
            mx = pe_sub.loc[pe_sub["oi_close"].idxmax()]
            max_pe_strike, max_pe_oi = int(mx["strike"]), float(mx["oi_close"])
        else:
            max_pe_strike, max_pe_oi = 0, 0.0

        # Skew scan: walk strikes for target deltas
        # Only do for tenors {7,14,30} where this expiry is the proxy (caller decides)
        # For now: compute IV at all candidate strikes within ±15 of ATM, then pick by delta
        candidate_offsets = list(range(-15, 16))
        candidate_strikes = []
        for off in candidate_offsets:
            ci = atm_idx + off
            if 0 <= ci < len(all_strikes):
                candidate_strikes.append(int(all_strikes[ci]))

        # Vectorize IV solve for candidates (CE + PE)
        ce_marks = []
        ce_strike_arr = []
        for ks in candidate_strikes:
            if ks in ce_by_strike:
                ce_marks.append(ce_by_strike[ks][0])
                ce_strike_arr.append(ks)
        pe_marks = []
        pe_strike_arr = []
        for ks in candidate_strikes:
            if ks in pe_by_strike:
                pe_marks.append(pe_by_strike[ks][0])
                pe_strike_arr.append(ks)

        ce_strike_arr = np.asarray(ce_strike_arr, dtype=float)
        pe_strike_arr = np.asarray(pe_strike_arr, dtype=float)
        ce_marks = np.asarray(ce_marks, dtype=float)
        pe_marks = np.asarray(pe_marks, dtype=float)

        ce_ivs = implied_vol_vec(ce_marks, spot, ce_strike_arr, T_years, 0.0,
                                 np.ones(len(ce_marks), dtype=bool)) if len(ce_marks) else np.array([])
        pe_ivs = implied_vol_vec(pe_marks, spot, pe_strike_arr, T_years, 0.0,
                                 np.zeros(len(pe_marks), dtype=bool)) if len(pe_marks) else np.array([])

        # Compute deltas for the candidates
        def _delta(K, sigma, is_call):
            if T_years <= 1e-6 or sigma <= 1e-6:
                return 0.0
            d1 = (math.log(spot / K) + 0.5 * sigma * sigma * T_years) / (sigma * math.sqrt(T_years))
            cdf = _norm_cdf_vec(np.array([d1]))[0]
            return cdf if is_call else (cdf - 1.0)

        ce_deltas = np.array([_delta(K, s, True) for K, s in zip(ce_strike_arr, ce_ivs)]) if len(ce_ivs) else np.array([])
        pe_deltas = np.array([_delta(K, s, False) for K, s in zip(pe_strike_arr, pe_ivs)]) if len(pe_ivs) else np.array([])

        # For each target delta, pick the strike closest
        def _pick_iv(deltas, ivs, target):
            if len(deltas) == 0:
                return np.nan
            mask = np.isfinite(deltas) & np.isfinite(ivs) & (ivs > 0)
            if not np.any(mask):
                return np.nan
            ds = deltas[mask]
            ivs_v = ivs[mask]
            idx = int(np.argmin(np.abs(ds - target)))
            return float(ivs_v[idx])

        iv_25d_call = _pick_iv(ce_deltas, ce_ivs, 0.25)
        iv_15d_call = _pick_iv(ce_deltas, ce_ivs, 0.15)
        iv_10d_call = _pick_iv(ce_deltas, ce_ivs, 0.10)
        iv_25d_put = _pick_iv(pe_deltas, pe_ivs, -0.25)
        iv_15d_put = _pick_iv(pe_deltas, pe_ivs, -0.15)
        iv_10d_put = _pick_iv(pe_deltas, pe_ivs, -0.10)

        # GEX contribution at this snapshot. Since we already sample at 5m
        # granularity, every snapshot is a GEX-eligible bar. We just need to
        # cap to expiries with DTE <= GEX_DTE_CAP.
        gex_contribution = 0.0
        do_gex = True
        dte = (expiry_unix - ts) / 86400
        if do_gex and dte <= GEX_DTE_CAP:
            # Top-K strikes by combined OI (vectorized GEX compute)
            combined_oi = {}
            for ks in (set(ce_by_strike.keys()) | set(pe_by_strike.keys())):
                combined_oi[ks] = (ce_by_strike.get(ks, (0, 0))[1]) + (pe_by_strike.get(ks, (0, 0))[1])
            top_k = sorted(combined_oi.keys(), key=lambda k: -combined_oi[k])[:GEX_TOP_K]
            if top_k:
                ks_arr = np.asarray(top_k, dtype=float)
                ce_marks = np.asarray([ce_by_strike.get(k, (0, 0))[0] for k in top_k], dtype=float)
                pe_marks = np.asarray([pe_by_strike.get(k, (0, 0))[0] for k in top_k], dtype=float)
                ce_oi_arr = np.asarray([ce_by_strike.get(k, (0, 0))[1] for k in top_k], dtype=float)
                pe_oi_arr = np.asarray([pe_by_strike.get(k, (0, 0))[1] for k in top_k], dtype=float)
                ce_ivs_arr = implied_vol_vec(ce_marks, spot, ks_arr, T_years, 0.0,
                                             np.ones_like(ks_arr, dtype=bool))
                pe_ivs_arr = implied_vol_vec(pe_marks, spot, ks_arr, T_years, 0.0,
                                             np.zeros_like(ks_arr, dtype=bool))
                ce_gammas = gammas_vec(spot, ks_arr, T_years, np.nan_to_num(ce_ivs_arr, nan=0.0))
                pe_gammas = gammas_vec(spot, ks_arr, T_years, np.nan_to_num(pe_ivs_arr, nan=0.0))
                gex_contribution = float(
                    -np.sum(ce_oi_arr * ce_gammas + pe_oi_arr * pe_gammas) * spot * spot * 0.01
                )

        rows.append({
            "timestamp_unix": int(ts),
            "atm_iv": atm_iv,
            "atm_strike": atm_strike,
            "iv_25d_call": iv_25d_call,
            "iv_25d_put":  iv_25d_put,
            "iv_15d_call": iv_15d_call,
            "iv_15d_put":  iv_15d_put,
            "iv_10d_call": iv_10d_call,
            "iv_10d_put":  iv_10d_put,
            "total_call_oi": total_ce_oi,
            "total_put_oi":  total_pe_oi,
            "max_ce_strike": max_ce_strike,
            "max_ce_oi":     max_ce_oi,
            "max_pe_strike": max_pe_strike,
            "max_pe_oi":     max_pe_oi,
            "gex_contribution": gex_contribution if do_gex else np.nan,
            "dte_at_ts": dte,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("timestamp_unix")
    return df


# ── Per-snapshot aggregation across expiries (Stage B) ────────────────────────

def _interp_atm_iv(target_dte: float, dtes: list[float], ivs: list[float]) -> float:
    """Linear interp ATM IV to target_dte. NaN if outside [min, max] of dtes."""
    if not dtes or not ivs:
        return np.nan
    pairs = sorted([(d, i) for d, i in zip(dtes, ivs) if not (math.isnan(d) or math.isnan(i)) and i > 0])
    if not pairs:
        return np.nan
    if target_dte < pairs[0][0] or target_dte > pairs[-1][0]:
        return np.nan
    # Find bracketing
    for k in range(len(pairs) - 1):
        if pairs[k][0] <= target_dte <= pairs[k+1][0]:
            d_lo, iv_lo = pairs[k]
            d_hi, iv_hi = pairs[k+1]
            if d_hi == d_lo:
                return iv_lo
            return iv_lo + (iv_hi - iv_lo) * (target_dte - d_lo) / (d_hi - d_lo)
    return np.nan


GEX_NEAR_FLIP_THRESHOLD_USD = 5_000_000  # $5M absolute threshold for NEAR_FLIP


def _gex_regime(total_gex: float, dist_to_flip_pct: float) -> str:
    """Classify regime by sign + magnitude of total_gex.

    NEAR_FLIP if magnitude is below threshold; else POSITIVE / NEGATIVE.
    `dist_to_flip_pct` accepted for future use (currently NaN — proper
    flip-level computation deferred to v2).
    """
    if total_gex is None or (isinstance(total_gex, float) and math.isnan(total_gex)):
        return ""
    if abs(total_gex) < GEX_NEAR_FLIP_THRESHOLD_USD:
        return "NEAR_FLIP"
    return "POSITIVE" if total_gex > 0 else "NEGATIVE"


def aggregate_snapshots(per_expiry: dict[date, pd.DataFrame],
                        spot_lookup: pd.DataFrame) -> pd.DataFrame:
    """Cross-expiry aggregation per 1m timestamp.

    per_expiry: {expiry_date: per-expiry summary DataFrame}
    spot_lookup: DataFrame indexed on timestamp_unix with col 'close'.

    Returns DataFrame indexed by timestamp_unix with all output cols (pre-IVP).
    """
    # Collect all timestamps that appear in any expiry's summary
    all_ts = sorted(set().union(*[df.index for df in per_expiry.values() if not df.empty]))
    if not all_ts:
        return pd.DataFrame()

    rows = []
    expiry_list = sorted(per_expiry.keys())
    expiry_unix_arr = np.array([expiry_dt_unix(e) for e in expiry_list], dtype=float)

    for ts in all_ts:
        # Spot
        try:
            spot = float(spot_lookup.loc[ts, "close"])
        except KeyError:
            idx = spot_lookup.index.searchsorted(ts, side="right") - 1
            if idx < 0:
                continue
            spot = float(spot_lookup.iloc[idx]["close"])
        if spot <= 0:
            continue

        # Live expiries (DTE > 0)
        live_dtes = (expiry_unix_arr - ts) / 86400
        live_mask = live_dtes > 0
        live_idx = np.where(live_mask)[0]
        if len(live_idx) == 0:
            continue

        # Pull per-expiry rows at this ts (if present)
        live_rows = []
        for i in live_idx:
            E = expiry_list[i]
            if ts in per_expiry[E].index:
                r = per_expiry[E].loc[ts]
                live_rows.append((live_dtes[i], r, E))

        if not live_rows:
            continue

        # Constant-maturity ATM IVs
        dtes = [r[0] for r in live_rows]
        atm_ivs = [r[1]["atm_iv"] for r in live_rows]
        atm_iv_7d = _interp_atm_iv(7.0, dtes, atm_ivs)
        atm_iv_14d = _interp_atm_iv(14.0, dtes, atm_ivs)
        atm_iv_30d = _interp_atm_iv(30.0, dtes, atm_ivs)
        atm_iv_60d = _interp_atm_iv(60.0, dtes, atm_ivs)

        # Term
        term_slope_7_30 = atm_iv_30d - atm_iv_7d if not math.isnan(atm_iv_7d) and not math.isnan(atm_iv_30d) else np.nan
        term_ratio_7_30 = atm_iv_7d / atm_iv_30d if (not math.isnan(atm_iv_7d) and not math.isnan(atm_iv_30d) and atm_iv_30d != 0) else np.nan
        term_slope_14_30 = atm_iv_30d - atm_iv_14d if not math.isnan(atm_iv_14d) and not math.isnan(atm_iv_30d) else np.nan
        term_slope_30_60 = atm_iv_60d - atm_iv_30d if not math.isnan(atm_iv_30d) and not math.isnan(atm_iv_60d) else np.nan

        # OI sums + walls
        total_call_oi = sum(r[1]["total_call_oi"] for r in live_rows)
        total_put_oi = sum(r[1]["total_put_oi"] for r in live_rows)
        pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else np.nan

        # Walls — aggregate by strike across live expiries (use max-OI strikes per expiry as a proxy
        # for top-OI candidates; sum across expiries by strike)
        ce_oi_by_strike: dict[int, float] = {}
        pe_oi_by_strike: dict[int, float] = {}
        for _, r, _ in live_rows:
            ks = int(r["max_ce_strike"])
            ce_oi_by_strike[ks] = ce_oi_by_strike.get(ks, 0.0) + float(r["max_ce_oi"])
            ks = int(r["max_pe_strike"])
            pe_oi_by_strike[ks] = pe_oi_by_strike.get(ks, 0.0) + float(r["max_pe_oi"])

        if ce_oi_by_strike:
            max_oi_call_strike = max(ce_oi_by_strike.keys(), key=lambda k: ce_oi_by_strike[k])
            max_oi_call = ce_oi_by_strike[max_oi_call_strike]
        else:
            max_oi_call_strike, max_oi_call = 0, 0.0

        if pe_oi_by_strike:
            max_oi_put_strike = max(pe_oi_by_strike.keys(), key=lambda k: pe_oi_by_strike[k])
            max_oi_put = pe_oi_by_strike[max_oi_put_strike]
        else:
            max_oi_put_strike, max_oi_put = 0, 0.0

        max_oi_call_pct_of_total = (max_oi_call / total_call_oi * 100) if total_call_oi > 0 else np.nan
        max_oi_put_pct_of_total = (max_oi_put / total_put_oi * 100) if total_put_oi > 0 else np.nan
        dist_to_call_wall_pct = (max_oi_call_strike - spot) / spot * 100 if max_oi_call_strike > 0 else np.nan
        dist_to_put_wall_pct = (spot - max_oi_put_strike) / spot * 100 if max_oi_put_strike > 0 else np.nan

        # Skew picks per tenor — find expiry closest to each target tenor
        def _proxy_idx(target_dte: float):
            if not live_rows:
                return None
            arr = np.array([r[0] for r in live_rows])
            return int(np.argmin(np.abs(arr - target_dte)))

        iv_per_tenor = {}
        for tau in SKEW_TENORS:
            pi = _proxy_idx(tau)
            if pi is None:
                iv_per_tenor[tau] = (np.nan, np.nan, np.nan, np.nan, np.nan, np.nan)
                continue
            r = live_rows[pi][1]
            iv_per_tenor[tau] = (r["iv_25d_call"], r["iv_25d_put"],
                                 r["iv_15d_call"], r["iv_15d_put"],
                                 r["iv_10d_call"], r["iv_10d_put"])

        # Headline skew uses 30d tenor
        h25c, h25p, h15c, h15p, h10c, h10p = iv_per_tenor.get(30, (np.nan,)*6)
        risk_reversal_25d = h25c - h25p if not math.isnan(h25c) and not math.isnan(h25p) else np.nan
        if not math.isnan(h25c) and not math.isnan(h25p) and not math.isnan(atm_iv_30d):
            butterfly_25d = (h25c + h25p) / 2 - atm_iv_30d
        else:
            butterfly_25d = np.nan
        if not math.isnan(h10c) and not math.isnan(h10p) and not math.isnan(atm_iv_30d) and atm_iv_30d != 0:
            wing_atm_ratio = ((h10c + h10p) / 2) / atm_iv_30d
        else:
            wing_atm_ratio = np.nan

        # Strangle synthetic IV per tenor
        strangle_iv_avg_7d = np.nan
        strangle_iv_avg_14d = np.nan
        strangle_iv_avg_30d = np.nan
        if 7 in iv_per_tenor and not math.isnan(iv_per_tenor[7][4]) and not math.isnan(iv_per_tenor[7][5]):
            strangle_iv_avg_7d = (iv_per_tenor[7][4] + iv_per_tenor[7][5]) / 2
        if 14 in iv_per_tenor and not math.isnan(iv_per_tenor[14][4]) and not math.isnan(iv_per_tenor[14][5]):
            strangle_iv_avg_14d = (iv_per_tenor[14][4] + iv_per_tenor[14][5]) / 2
        if 30 in iv_per_tenor and not math.isnan(iv_per_tenor[30][4]) and not math.isnan(iv_per_tenor[30][5]):
            strangle_iv_avg_30d = (iv_per_tenor[30][4] + iv_per_tenor[30][5]) / 2

        # GEX (only computed at GEX compute bars; sum contributions)
        total_gex = np.nan
        gex_flip_level = np.nan
        gex_regime = ""
        dist_to_flip_pct = np.nan
        gex_contribs = [r[1]["gex_contribution"] for r in live_rows
                        if not math.isnan(r[1]["gex_contribution"])]
        if gex_contribs:
            total_gex = sum(gex_contribs)
            # Proper flip level computation deferred to v2 (would require
            # recomputing GEX over a 21-point spot grid). For v1 we leave
            # gex_flip_level / dist_to_flip_pct as NaN and classify regime
            # by absolute magnitude of total_gex.
            gex_regime = _gex_regime(total_gex, np.nan)

        rows.append({
            "timestamp_unix": int(ts),
            "spot": spot,
            "atm_iv_7d":  atm_iv_7d,
            "atm_iv_14d": atm_iv_14d,
            "atm_iv_30d": atm_iv_30d,
            "atm_iv_60d": atm_iv_60d,
            "term_slope_7_30":  term_slope_7_30,
            "term_ratio_7_30":  term_ratio_7_30,
            "term_slope_14_30": term_slope_14_30,
            "term_slope_30_60": term_slope_30_60,
            "iv_25d_call": h25c,
            "iv_25d_put":  h25p,
            "iv_15d_call": h15c,
            "iv_15d_put":  h15p,
            "iv_10d_call": h10c,
            "iv_10d_put":  h10p,
            "risk_reversal_25d": risk_reversal_25d,
            "butterfly_25d":     butterfly_25d,
            "wing_atm_ratio":    wing_atm_ratio,
            "total_call_oi": total_call_oi,
            "total_put_oi":  total_put_oi,
            "pcr_oi":        pcr_oi,
            "pcr_volume":    np.nan,
            "max_oi_call_strike": max_oi_call_strike,
            "max_oi_put_strike":  max_oi_put_strike,
            "max_oi_call_pct_of_total": max_oi_call_pct_of_total,
            "max_oi_put_pct_of_total":  max_oi_put_pct_of_total,
            "dist_to_call_wall_pct": dist_to_call_wall_pct,
            "dist_to_put_wall_pct":  dist_to_put_wall_pct,
            "total_gex":       total_gex,
            "gex_flip_level":  gex_flip_level,
            "gex_regime":      gex_regime,
            "dist_to_flip_pct": dist_to_flip_pct,
            "strangle_iv_avg_7d":  strangle_iv_avg_7d,
            "strangle_iv_avg_14d": strangle_iv_avg_14d,
            "strangle_iv_avg_30d": strangle_iv_avg_30d,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("timestamp_unix")
    df = df.sort_index()
    return df


# ── Stage C: rolling IVP ──────────────────────────────────────────────────────

def add_rolling_ivp(df: pd.DataFrame) -> pd.DataFrame:
    """Compute IVP-per-tenor (90d) and multi-TF IVP windows.

    Operates on a 5-minute-bar DataFrame. Window sizes given in 5m bars.
    """
    bars_per_day_5m = 288
    window_90d = 90 * bars_per_day_5m

    for tenor in ("7d", "14d", "30d"):
        col = f"atm_iv_{tenor}"
        if col in df.columns:
            df[f"ivp_atm_{tenor}_90d"] = (
                df[col].rolling(window_90d, min_periods=window_90d).rank(pct=True) * 100
            )

    # Multi-TF IVP — windows of atm_iv_30d (sizes in 5m bars).
    # 1m label is degenerate at 5m base (window=1 always 100); kept for schema symmetry.
    for label, n_bars in [("1m", 1), ("5m", 1), ("15m", 3), ("30m", 6),
                          ("1h", 12), ("4h", 48), ("1d", 288)]:
        df[f"ivp_{label}"] = (
            df["atm_iv_30d"].rolling(n_bars, min_periods=n_bars).rank(pct=True) * 100
        )

    # Strangle IVP per tenor (90d window)
    for tenor in ("7d", "14d", "30d"):
        col = f"strangle_iv_avg_{tenor}"
        if col in df.columns:
            df[f"strangle_ivp_{tenor}"] = (
                df[col].rolling(window_90d, min_periods=window_90d).rank(pct=True) * 100
            )

    return df


# ── Stage D: write 4 grids ────────────────────────────────────────────────────

def _ts_to_ist(ts: pd.Series) -> pd.Series:
    """UNIX seconds → IST naive datetime."""
    return pd.to_datetime(ts.values, unit="s", utc=True).tz_convert("Asia/Kolkata").tz_localize(None)


def write_grids(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection,
                grids: list[str], overlap_start: int | None,
                full_1m_index: pd.Index | None = None) -> None:
    """Write 4 grid parquets.

    df is at 5m granularity (computed natively at 5m). Output:
    - 1m grid: forward-fill 5m → 1m using `full_1m_index` as target index.
    - 5m grid: as-is.
    - 15m grid: every 3rd 5m row (end-of-bucket).
    - 30m grid: every 6th 5m row (end-of-bucket).
    """
    os.makedirs(DERIVED_DIR, exist_ok=True)
    df = df.copy()
    df.index.name = "timestamp_unix"
    df_5m = df.reset_index()
    df_5m["timestamp_ist"] = _ts_to_ist(df_5m["timestamp_unix"])
    cols = ["timestamp_ist", "timestamp_unix"] + [c for c in df_5m.columns if c not in ("timestamp_ist", "timestamp_unix")]
    df_5m = df_5m[cols]
    if "gex_regime" in df_5m.columns:
        df_5m["gex_regime"] = df_5m["gex_regime"].astype("string")

    for tf in grids:
        secs = GRID_SECS[tf]
        if tf == "1m":
            # Upsample 5m → 1m via forward-fill.
            if full_1m_index is None or len(full_1m_index) == 0:
                log.warning("  1m grid: no full_1m_index; skipping")
                continue
            sub_idx = pd.DataFrame({"timestamp_unix": full_1m_index})
            merged = pd.merge_asof(
                sub_idx.sort_values("timestamp_unix"),
                df_5m.sort_values("timestamp_unix"),
                on="timestamp_unix",
                direction="backward",
            )
            merged["timestamp_ist"] = _ts_to_ist(merged["timestamp_unix"])
            sub = merged
        elif tf == "5m":
            sub = df_5m
        elif tf == "15m":
            # 15m bucket end = ts where ts % 900 == 600 (i.e. last 5m of 15m bucket: 0→600, 5m→300, 10m→0... wait)
            # 15m buckets: [0,900), [900,1800), ... End-of-bucket 5m bar = ts where (ts+300) % 900 == 0
            # i.e. ts % 900 == 600
            sub = df_5m[df_5m["timestamp_unix"] % 900 == 600].reset_index(drop=True)
        elif tf == "30m":
            # End-of-30m-bucket 5m bar: ts % 1800 == 1500
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


# ── Main pipeline ─────────────────────────────────────────────────────────────

def get_existing_watermark(conn: duckdb.DuckDBPyConnection) -> int | None:
    if not os.path.exists(OUTPUT_PATHS["1m"]):
        return None
    try:
        res = conn.execute(
            f"SELECT max(timestamp_unix) FROM read_parquet('{OUTPUT_PATHS['1m']}')"
        ).fetchone()
        return int(res[0]) if res and res[0] is not None else None
    except Exception as e:
        log.warning(f"Failed to read watermark: {e}")
        return None


def get_source_range(conn: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    res = conn.execute(
        f"SELECT min(timestamp_unix), max(timestamp_unix) FROM read_parquet('{SPOT_RAW_PATH}')"
    ).fetchone()
    return int(res[0]), int(res[1])


def run_pipeline(*, rebuild: bool = False,
                 since_unix: int | None = None,
                 through_unix: int | None = None,
                 grids: list[str] | None = None) -> None:
    if grids is None:
        grids = list(ALL_GRIDS)
    t0 = _time.time()

    if not os.path.exists(SPOT_ENRICHED_PATH):
        log.error(f"Pre-flight failed: {SPOT_ENRICHED_PATH} does not exist. Run Module 1 first.")
        sys.exit(2)

    conn = duckdb.connect(":memory:")
    src_min, src_max = get_source_range(conn)
    if through_unix is not None:
        src_max = min(src_max, int(through_unix))
    if since_unix is not None:
        src_min = max(src_min, int(since_unix))

    if rebuild:
        watermark = None
    else:
        watermark = get_existing_watermark(conn)

    if watermark is None:
        warmup_start = src_min
        overlap_start = src_min
        log.info(f"Full rebuild: range {src_min} → {src_max}")
    else:
        overlap_start = max(src_min, watermark - 86400)
        warmup_start = max(src_min, overlap_start - 95 * 86400)
        log.info(f"Incremental: watermark={watermark}, overlap_start={overlap_start}, warmup_start={warmup_start}")

    if since_unix is not None and rebuild:
        warmup_start = max(warmup_start, int(since_unix))
        overlap_start = max(overlap_start, int(since_unix))

    log.info("Loading spot lookup...")
    spot_lookup = load_spot_lookup(conn, warmup_start, src_max)
    log.info(f"Spot bars: {len(spot_lookup):,}")
    if spot_lookup.empty:
        log.error("No spot data; aborting.")
        return

    log.info("Discovering expiries...")
    all_expiries = list_expiries()
    log.info(f"Total expiries: {len(all_expiries)}")

    # Filter to expiries that have any live window in our backfill range
    relevant_expiries = []
    for e in all_expiries:
        e_unix = expiry_dt_unix(e)
        # Live during warmup_start..src_max if expiry > warmup_start
        if e_unix > warmup_start and e_unix < src_max + 70 * 86400:
            relevant_expiries.append(e)
    log.info(f"Relevant expiries: {len(relevant_expiries)}")

    # Process per expiry; collect summaries.
    # Per-expiry checkpoints survive process kills: any expiry whose parquet
    # already exists in CHECKPOINT_DIR is loaded from disk instead of recomputed.
    log.info("Stage A — per-expiry summaries...")
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    per_expiry: dict[date, pd.DataFrame] = {}
    gex_compute_mask = pd.Series(dtype=bool)  # placeholder; compute_expiry_summary uses ts % 300 == 0
    n_resumed = 0
    n_computed = 0

    for i, exp in enumerate(relevant_expiries, 1):
        ckpt_path = os.path.join(CHECKPOINT_DIR, f"{exp.isoformat()}.parquet")
        if os.path.exists(ckpt_path):
            try:
                cached = pd.read_parquet(ckpt_path)
                if not cached.empty:
                    if "timestamp_unix" in cached.columns:
                        cached = cached.set_index("timestamp_unix")
                    per_expiry[exp] = cached
                n_resumed += 1
                if i % 25 == 0 or i == len(relevant_expiries):
                    log.info(f"  [{i}/{len(relevant_expiries)}] resumed={n_resumed} computed={n_computed} non-empty={len(per_expiry)}")
                continue
            except Exception as ce:
                log.warning(f"  checkpoint {ckpt_path} unreadable ({ce}); recomputing")

        e_unix = expiry_dt_unix(exp)
        # Live window for this expiry: [warmup_start, min(src_max, e_unix-60)]
        e_load_start = max(warmup_start, e_unix - 70 * 86400)  # max 70 days back from expiry
        e_load_end = min(src_max, e_unix - 60)
        if e_load_start >= e_load_end:
            # Touch an empty checkpoint so we don't reattempt next run
            pd.DataFrame().to_parquet(ckpt_path, compression="snappy", index=False)
            continue
        chain = load_chain_for_expiry(conn, exp, e_load_start, e_load_end)
        if chain.empty:
            pd.DataFrame().to_parquet(ckpt_path, compression="snappy", index=False)
            continue
        summary = compute_expiry_summary(chain, exp, spot_lookup, gex_compute_mask)
        # Persist atomically: write tmp + rename so a kill mid-write doesn't
        # leave a corrupt parquet that the next run would happily skip.
        tmp_path = ckpt_path + ".tmp"
        if summary.empty:
            pd.DataFrame().to_parquet(tmp_path, compression="snappy", index=False)
        else:
            out = summary.reset_index().rename(columns={"index": "timestamp_unix"})
            out.to_parquet(tmp_path, compression="snappy", index=False)
            per_expiry[exp] = summary
        os.replace(tmp_path, ckpt_path)
        n_computed += 1
        if i % 25 == 0 or i == len(relevant_expiries):
            log.info(f"  [{i}/{len(relevant_expiries)}] resumed={n_resumed} computed={n_computed} non-empty={len(per_expiry)}")

    if not per_expiry:
        log.error("No per-expiry summaries; aborting.")
        return

    log.info("Stage B — cross-expiry aggregation per snapshot...")
    df = aggregate_snapshots(per_expiry, spot_lookup)
    log.info(f"Aggregated rows: {len(df):,}")

    if df.empty:
        log.error("No aggregated rows; aborting.")
        return

    log.info("Stage C — rolling IVP...")
    df = add_rolling_ivp(df)

    # Slice to rows >= overlap_start (drop warm-up tail)
    if watermark is not None:
        df = df[df.index >= overlap_start]
        log.info(f"Sliced to overlap_start={overlap_start}: {len(df):,} rows to write")

    log.info("Stage D — writing grid parquets...")
    # Build 1m index covering the output window so we can ffill 5m → 1m
    # for the 1m grid output.
    output_min_ts = int(df.index.min()) if not df.empty else 0
    output_max_ts = int(df.index.max()) if not df.empty else 0
    if output_max_ts > output_min_ts:
        full_1m_index = pd.Index(np.arange(output_min_ts, output_max_ts + 60, 60), dtype="int64")
    else:
        full_1m_index = pd.Index([], dtype="int64")
    write_grids(df, conn, grids,
                overlap_start=overlap_start if watermark is not None else None,
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
    p.add_argument("--clear-checkpoint", action="store_true",
                   help="Wipe Stage A per-expiry checkpoint dir before running")
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

    if args.clear_checkpoint and os.path.isdir(CHECKPOINT_DIR):
        import shutil
        shutil.rmtree(CHECKPOINT_DIR)
        log.info(f"Cleared checkpoint dir: {CHECKPOINT_DIR}")

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

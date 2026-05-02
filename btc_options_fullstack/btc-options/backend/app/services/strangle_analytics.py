"""Server-side analytics helper — Python equivalent of frontend's
`utils/strangleAnalytics.ts`. Used by the backtest service to bake per-trade
credit_pct / ratios / quality_score into each trade row, so the trade log can
sort/filter on them without N round-trips to the API.

Math intentionally mirrors the TS utility — keep the two in sync if either
side changes.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Optional

import duckdb
import pandas as pd

CONTRACT_SIZE_BTC = 0.001

CALIBRATION_PATH = "/home/abhis/btc-data/derived/calibration.parquet"
CALIBRATION_UNIVERSAL_PATH = "/home/abhis/btc-data/derived/calibration_universal.parquet"
FULL_ENRICHED_5M_PATH = "/home/abhis/btc-data/derived/full_enriched_5m.parquet"

_DTE_BUCKETS = [(0, 3), (3, 7), (7, 14), (14, 30), (30, 60)]
_SPOT_BUCKETS = [(0, 60_000), (60_000, 90_000), (90_000, 120_000),
                 (120_000, 150_000), (150_000, 10_000_000)]
_IVP_BUCKETS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100.0001)]
_STD_DELTAS = (0.05, 0.10, 0.15, 0.25)

# Loaded lazily and cached for the lifetime of the process.
_calibration_df: Optional[pd.DataFrame] = None
_universal_df: Optional[pd.DataFrame] = None
_calibration_load_attempted = False


def _norm_cdf(x: float) -> float:
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = -1 if x < 0 else 1
    z = abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + p * z)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-z * z)
    return 0.5 * (1.0 + sign * y)


def _z_to_pct(z: float) -> float:
    if not math.isfinite(z):
        return 50.0
    return max(0.0, min(100.0, _norm_cdf(z) * 100.0))


def _label_dte(v: float) -> str:
    for lo, hi in _DTE_BUCKETS:
        if lo <= v < hi:
            return f"{int(lo)}-{int(hi)}"
    return "nan"


def _label_spot(v: float) -> str:
    for lo, hi in _SPOT_BUCKETS:
        if lo <= v < hi:
            if hi >= 10_000_000:
                return f"{int(lo / 1000)}k+"
            return f"{int(lo / 1000)}-{int(hi / 1000)}k"
    return "nan"


def _label_ivp(v: float) -> str:
    for lo, hi in _IVP_BUCKETS:
        if lo <= v < hi:
            return f"{int(lo)}-{int(hi)}"
    return "nan"


def _label_delta(v: float) -> str:
    snapped = min(_STD_DELTAS, key=lambda x: abs(x - v))
    return f"{snapped:.2f}"


def _load_calibration():
    """Load and cache the calibration parquets. Idempotent."""
    global _calibration_df, _universal_df, _calibration_load_attempted
    if _calibration_load_attempted:
        return
    _calibration_load_attempted = True
    if os.path.exists(CALIBRATION_PATH):
        try:
            _calibration_df = pd.read_parquet(CALIBRATION_PATH)
        except Exception:
            _calibration_df = None
    if os.path.exists(CALIBRATION_UNIVERSAL_PATH):
        try:
            _universal_df = pd.read_parquet(CALIBRATION_UNIVERSAL_PATH)
        except Exception:
            _universal_df = None


def lookup_calibration(dte: float, spot: float, target_delta: float, ivp: float) -> Optional[dict]:
    """Return bucket dict for these inputs, or None if no calibration."""
    _load_calibration()
    if _calibration_df is None:
        return None
    dte_b = _label_dte(dte)
    spot_b = _label_spot(spot)
    delta_b = _label_delta(target_delta)
    ivp_b = _label_ivp(ivp)

    hit = _calibration_df[(_calibration_df["dte_bucket"] == dte_b)
                          & (_calibration_df["spot_bucket"] == spot_b)
                          & (_calibration_df["delta_target"] == delta_b)
                          & (_calibration_df["ivp_bucket"] == ivp_b)]
    if not hit.empty and int(hit.iloc[0]["n_samples"]) >= 30:
        row = hit.iloc[0].to_dict()
        return {
            "source": "specific_bucket",
            "credit_pct_median": float(row["credit_pct_median"]),
            "credit_pct_mean": float(row["credit_pct_mean"]),
            "credit_pct_std": float(row["credit_pct_std"]) if pd.notna(row["credit_pct_std"]) else 0.0,
            "atm_iv_median": float(row["atm_iv_median"]) if pd.notna(row["atm_iv_median"]) else None,
            "atm_iv_std": float(row["atm_iv_std"]) if pd.notna(row["atm_iv_std"]) else None,
            "structural_baseline": float(row["structural_baseline"])
                if pd.notna(row["structural_baseline"]) else None,
        }

    # Universal fallback
    if _universal_df is None:
        return None
    u_hit = _universal_df[(_universal_df["delta_target"] == delta_b)
                          & (_universal_df["ivp_bucket"] == ivp_b)]
    if u_hit.empty:
        return None
    u_row = u_hit.iloc[0]
    sqrt_dte = math.sqrt(max(dte, 1e-6))
    scaled_credit = float(u_row["credit_pct_normalized_median"]) * sqrt_dte
    scaled_std = float(u_row["credit_pct_normalized_std"]) * sqrt_dte if pd.notna(u_row["credit_pct_normalized_std"]) else 0.0
    struct_hit = _universal_df[(_universal_df["delta_target"] == delta_b)
                               & (_universal_df["ivp_bucket"] == "40-60")]
    struct = (float(struct_hit.iloc[0]["credit_pct_normalized_median"]) * sqrt_dte
              if not struct_hit.empty else scaled_credit * 0.7)
    return {
        "source": "universal_fallback",
        "credit_pct_median": scaled_credit,
        "credit_pct_mean": scaled_credit,
        "credit_pct_std": scaled_std,
        "atm_iv_median": float(u_row["atm_iv_median"]) if pd.notna(u_row["atm_iv_median"]) else None,
        "atm_iv_std": float(u_row["atm_iv_std"]) if pd.notna(u_row["atm_iv_std"]) else None,
        "structural_baseline": struct,
    }


# In-process M3 snapshot cache. Keyed by 5m-aligned ts.
_m3_cache: dict[int, dict] = {}
_m3_avail_cols: Optional[list[str]] = None


def _m3_columns_available() -> list[str]:
    global _m3_avail_cols
    if _m3_avail_cols is not None:
        return _m3_avail_cols
    if not os.path.exists(FULL_ENRICHED_5M_PATH):
        _m3_avail_cols = []
        return _m3_avail_cols
    try:
        df = duckdb.sql(
            f"SELECT column_name FROM (DESCRIBE SELECT * "
            f"FROM read_parquet('{FULL_ENRICHED_5M_PATH}'))"
        ).df()
        _m3_avail_cols = df["column_name"].tolist()
    except Exception:
        _m3_avail_cols = []
    return _m3_avail_cols


_M3_WANT_COLS = [
    "atm_iv_7d", "atm_iv_14d", "atm_iv_30d",
    "ivp_atm_7d_90d", "ivp_4h",
    "rv_7d", "rv_14d",
    "iv_rv_spread_7d", "iv_rv_ratio_7d",
    "vrp_pct_7d",
    "risk_reversal_25d", "butterfly_25d", "wing_atm_ratio",
    "term_slope_7_30",
    "rvp_4h",
    "adx_14_4h", "atr_pct_4h",
    "pcr_oi", "total_gex", "gex_regime",
    "expected_move_1sigma_7d",
    "pattern",
]


def get_snapshot(ts: int) -> dict:
    """Fetch the M3 row at-or-before `ts`. Cached. Returns {} if M3 missing."""
    if not os.path.exists(FULL_ENRICHED_5M_PATH):
        return {}
    bucket = (int(ts) // 300) * 300
    if bucket in _m3_cache:
        return _m3_cache[bucket]
    avail = _m3_columns_available()
    cols = [c for c in _M3_WANT_COLS if c in avail]
    if not cols:
        _m3_cache[bucket] = {}
        return {}
    cols_sql = ", ".join(cols)
    try:
        res = duckdb.sql(
            f"SELECT {cols_sql} FROM read_parquet('{FULL_ENRICHED_5M_PATH}') "
            f"WHERE timestamp_unix <= {int(ts)} "
            f"ORDER BY timestamp_unix DESC LIMIT 1"
        ).df()
    except Exception:
        _m3_cache[bucket] = {}
        return {}
    if res.empty:
        _m3_cache[bucket] = {}
        return {}
    row = res.iloc[0].to_dict()
    # Convert NaN → None for clean JSON
    for k, v in list(row.items()):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            row[k] = None
    _m3_cache[bucket] = row
    return row


# ── Per-trade analytics ───────────────────────────────────────────────────────

@dataclass
class TradeLeg:
    type: str           # "CE" or "PE"
    side: str           # "BUY" or "SELL"
    strike: float
    qty: int
    mark: float         # USD per contract
    iv: float           # decimal
    delta: float
    gamma: float
    vega: float
    theta: float


def compute_trade_analytics(
    legs: list[TradeLeg], spot: float, dte: float, entry_ts: int,
    expiry_ts: int,
) -> dict:
    """Compute per-trade analytics. Returns a dict of fields to be merged
    into the BacktestTrade row.
    """
    if spot <= 0 or dte <= 0 or len(legs) == 0:
        return {}

    sign = lambda l: -1 if l.side == "SELL" else 1
    total_premium = sum(-sign(l) * l.mark * l.qty * CONTRACT_SIZE_BTC for l in legs)
    credit_pct = total_premium / spot
    pos_delta = sum(l.delta * sign(l) * l.qty for l in legs)
    pos_gamma = sum(l.gamma * sign(l) * l.qty for l in legs)
    pos_vega = sum(l.vega * sign(l) * l.qty for l in legs)
    pos_theta = sum(l.theta * sign(l) * l.qty for l in legs)

    snap = get_snapshot(entry_ts)
    pattern = snap.get("pattern") if isinstance(snap.get("pattern"), str) else "Other"
    ivp = snap.get("ivp_atm_7d_90d")
    if ivp is None or (isinstance(ivp, float) and math.isnan(ivp)):
        ivp = 50.0

    # Calibration lookup for this trade
    target_delta = (sum(abs(l.delta) for l in legs) / max(len(legs), 1))
    calib = lookup_calibration(dte, spot, target_delta, ivp)

    # Decomposition / z-scores / quality (NaN if no calibration)
    fair_pct = calib["credit_pct_median"] if calib else None
    structural_pct = calib["structural_baseline"] if (calib and calib.get("structural_baseline") is not None) else None
    iv_regime_pct = ((fair_pct - structural_pct) if (fair_pct is not None and structural_pct is not None) else None)
    excess_pct = ((credit_pct - fair_pct) if fair_pct is not None else None)

    z_credit = None; z_iv = None; z_credit_pct_pct = None
    if calib and calib["credit_pct_std"] and calib["credit_pct_std"] > 0:
        z_credit = (credit_pct - calib["credit_pct_mean"]) / calib["credit_pct_std"]
        z_credit_pct_pct = _z_to_pct(z_credit)
    atm_iv_7d = snap.get("atm_iv_7d")
    if calib and calib.get("atm_iv_std") and calib.get("atm_iv_median") is not None and calib["atm_iv_std"] > 0 \
            and atm_iv_7d is not None and not (isinstance(atm_iv_7d, float) and math.isnan(atm_iv_7d)):
        z_iv = (atm_iv_7d - calib["atm_iv_median"]) / calib["atm_iv_std"]

    quality = None
    size_band = None
    if z_credit_pct_pct is not None:
        quality = 0.40 * z_credit_pct_pct + 0.60 * float(ivp)
        if quality >= 75: size_band = "strong"
        elif quality >= 60: size_band = "standard"
        elif quality >= 45: size_band = "marginal"
        else:               size_band = "skip"

    out = {
        "credit_pct": round(credit_pct, 6) if math.isfinite(credit_pct) else None,
        "credit_pct_normalized": round(credit_pct / math.sqrt(max(dte, 1e-6)), 6),
        "credit_per_day": round(credit_pct / max(dte, 1e-6), 6),
        "annualized_credit_pct": round(credit_pct * (365 / max(dte, 1e-6)), 6),
        "fair_credit_at_ivp": round(fair_pct, 6) if fair_pct is not None else None,
        "structural_credit_pct": round(structural_pct, 6) if structural_pct is not None else None,
        "iv_regime_premium_pct": round(iv_regime_pct, 6) if iv_regime_pct is not None else None,
        "excess_over_fair_pct": round(excess_pct, 6) if excess_pct is not None else None,
        "z_credit_pct": round(z_credit, 4) if z_credit is not None else None,
        "z_atm_iv": round(z_iv, 4) if z_iv is not None else None,
        "quality_score": round(quality, 2) if quality is not None else None,
        "size_band": size_band,
        "pattern": pattern,
        "ivp_atm_7d_90d_at_entry": float(ivp) if ivp is not None else None,
        "atm_iv_7d_at_entry": atm_iv_7d if atm_iv_7d is not None else None,
        "theta_vega_ratio": round(abs(pos_theta) / abs(pos_vega), 4) if pos_vega != 0 else None,
        "gamma_theta_dollar": round((pos_gamma * spot * spot / 100) / abs(pos_theta), 4)
            if pos_theta != 0 else None,
        "position_delta": round(pos_delta, 4),
        "position_gamma": round(pos_gamma, 6),
        "position_vega": round(pos_vega, 4),
        "position_theta": round(pos_theta, 4),
        "calibration_source": calib.get("source") if calib else None,
    }
    return out

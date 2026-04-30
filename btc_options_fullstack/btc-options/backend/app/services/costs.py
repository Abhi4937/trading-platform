"""
Backtest cost layer — slippage + brokerage.

Python ports of the live UI cost models:
  - frontend/src/utils/slippage.ts  (the wired v1 — NOT slippage_v2 experimental)
  - frontend/src/utils/brokerage.ts

Constants are duplicated here. Keep in sync if the TS files are recalibrated;
verify with a single-day backtest matching `StrategyPanel.tsx` Final P&L.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

CONTRACT_VALUE = 0.001  # BTC per contract on Delta India BTC options


# ── Slippage ──────────────────────────────────────────────────────────────────

# Mirrors slippage.ts as currently shipped. Last recalibration 2026-04-28
# (sub-linear qty scaling + softened hour/weekend mults).
MIN_SPREAD_USD  = 0.10
PER_BTC_BASE    = 3.85
BASE_PCT        = 0.012
FLOOR_USD       = 0.05
CAP_FRAC        = 0.30
CAP_USD         = 50.0


def _moneyness_mult(moneyness: float) -> float:
    # REMOVED 2026-04-30 per user calibration. Wings don't get extra slip
    # in observed fills (deep-OTM CE 78.5k @ 700 lots: $2 actual vs old-model
    # $5.59 with mult=2.0). The mark-proportional BASE_PCT term already
    # differentiates expensive ATM legs from cheap wings naturally.
    # Kept as a stub so the call sites still work — no-op multiplier of 1.0.
    return 1.0


def _hour_mult(hour_ist: int) -> float:
    if 0 <= hour_ist < 6: return 1.1
    if 6 <= hour_ist < 9: return 1.05
    return 1.0


def _ist_hour(unix_sec: int) -> int:
    """Hour-of-day in IST (UTC+5:30) for a unix timestamp."""
    dt = datetime.fromtimestamp(int(unix_sec) + int(5.5 * 3600), tz=timezone.utc)
    return dt.hour


def _is_weekend_ist(unix_sec: int) -> bool:
    dt = datetime.fromtimestamp(int(unix_sec) + int(5.5 * 3600), tz=timezone.utc)
    dow = dt.weekday()  # Mon=0, Sun=6
    return dow >= 5


def estimate_slippage_per_btc(
    spot: float,
    mark: float,
    strike: float,
    is_call: bool,
    qty: int,
    timestamp: int,
) -> float:
    """Per-side slippage in USDT/BTC equivalent. Multiply by qty * 0.001 for $."""
    if mark <= 0:
        return 0.0
    moneyness = abs(strike - spot) / spot if spot > 0 else 0.0
    mn = _moneyness_mult(moneyness)
    hr = _hour_mult(_ist_hour(timestamp))
    wk = 1.05 if _is_weekend_ist(timestamp) else 1.0

    qty_btc = max(0.001, max(qty, 1) * CONTRACT_VALUE)
    additive = MIN_SPREAD_USD / qty_btc + PER_BTC_BASE
    mark_pct = BASE_PCT * mark
    base = max(additive, mark_pct)
    return base * mn * hr * wk


def compute_slippage_per_side(
    enabled: bool,
    mode: Literal["flat", "smart"],
    flat_value: float,
    multiplier: float,
    spot: float,
    mark: float,
    strike: float,
    is_call: bool,
    qty: int,
    timestamp: int,
) -> float:
    """Per-side slippage in USDT/BTC. UI-equivalent of computeSlippagePerSide().

    Returns 0 if disabled. For 'flat' mode, returns flat_value * multiplier.
    For 'smart' mode, applies multipliers + floor/cap bounds.
    """
    if not enabled:
        return 0.0
    if mode == "flat":
        return max(0.0, flat_value * multiplier)
    if mark <= 0:
        return 0.0

    raw = estimate_slippage_per_btc(spot, mark, strike, is_call, qty, timestamp)
    scaled = raw * multiplier
    floor = FLOOR_USD * multiplier
    cap = max(CAP_USD, CAP_FRAC * mark) * multiplier
    return max(floor, min(cap, scaled))


def slippage_dollars_per_side(
    enabled: bool,
    mode: Literal["flat", "smart"],
    flat_value: float,
    multiplier: float,
    spot: float,
    mark: float,
    strike: float,
    is_call: bool,
    qty: int,
    timestamp: int,
) -> float:
    """Total $ slippage for one side of one leg (per_btc × qty × 0.001)."""
    per_btc = compute_slippage_per_side(
        enabled, mode, flat_value, multiplier,
        spot, mark, strike, is_call, qty, timestamp,
    )
    return per_btc * qty * CONTRACT_VALUE


# ── Brokerage ─────────────────────────────────────────────────────────────────

# Mirrors brokerage.ts. Two tiers + GST + optional 10% referral discount.
_BROKERAGE_RATES = {
    "standard": (0.0003, 0.10),   # 0.03% of notional, cap 10% of premium
    "offer":    (0.0001, 0.035),  # 0.010% of notional, cap 3.5% of premium
}


def compute_brokerage_one_side(
    spot: float,
    mark: float,
    qty: int,
    rate: Literal["standard", "offer"],
    referral: bool,
) -> float:
    """Total brokerage cost in USD for one side of a trade. 18% GST applied."""
    if mark <= 0 or spot <= 0 or qty <= 0:
        return 0.0
    pct, cap = _BROKERAGE_RATES.get(rate, _BROKERAGE_RATES["standard"])
    raw = min(pct * spot, cap * mark) * qty * CONTRACT_VALUE
    return raw * 1.18 * (0.90 if referral else 1.0)

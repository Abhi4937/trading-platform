"""
Delta Exchange India — Portfolio Margin Engine (Python)

Single source of truth, mirrors frontend/src/utils/marginEngine.ts 1:1.
Used by both margin_check.py (live API validation) and historical_margin.py
(parquet backtest).

Reference: https://guides.delta.exchange/delta-exchange-india-user-guide/
           trading-guide/margin-explainer/portfolio-margin

Formulas (verbatim from docs):
    Margin              = max(Risk Margin, Margin Floor)
    Initial Margin      = max(Risk Margin, Margin Floor) − UCF
    Maintenance Margin  = 0.80 × (Initial Margin + UCF) − UCF
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

CONTRACT_VALUE = 0.001   # BTC per BTC-options contract on Delta India

# ── Black-Scholes ────────────────────────────────────────────────────────────

def norm_cdf(x: float) -> float:
    """Cumulative standard normal (Abramowitz & Stegun, error < 7.5e-8)."""
    a1, a2, a3, a4, a5, p = (
        0.254829592, -0.284496736, 1.421413741,
        -1.453152027, 1.061405429, 0.3275911,
    )
    sign = -1 if x < 0 else 1
    ax = abs(x)
    t = 1.0 / (1.0 + p * ax)
    y = 1.0 - ((((a5*t+a4)*t+a3)*t+a2)*t+a1) * t * math.exp(-ax * ax)
    return 0.5 * (1.0 + sign * y)


def bs_price(S: float, K: float, T: float, sigma: float, is_call: bool,
             r: float = 0.0) -> float:
    """Black-Scholes price in USDT per BTC. Multiply by CONTRACT_VALUE for per-contract."""
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sq)
    d2 = d1 - sigma * sq
    df = math.exp(-r * T)
    return (S * norm_cdf(d1) - K * df * norm_cdf(d2)
            if is_call else K * df * norm_cdf(-d2) - S * norm_cdf(-d1))


def bs_delta(S: float, K: float, T: float, sigma: float, is_call: bool,
             r: float = 0.0) -> float:
    if T <= 0 or sigma <= 0:
        if is_call: return 1.0 if S >= K else 0.0
        return 0.0 if S >= K else -1.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1) if is_call else norm_cdf(d1) - 1.0


# ── Delta India shock-span parameters (from docs) ────────────────────────────

NOTIONAL_FLOOR        = 100_000          # USD — ramp starts above this

PRICE_SHOCK_MIN       = 0.01             # 1%
PRICE_SHOCK_MAX       = 0.10             # 10%
PRICE_SHOCK_CAP_NOT   = 2_350_000        # USD
PRICE_SHOCK_SLOPE     = 4e-8

VOL_DOWN_MIN          = 0.06             # 6%
VOL_DOWN_MAX          = 0.30             # 30%
VOL_CAP_NOT           = 2_100_000        # USD
VOL_DOWN_SLOPE        = 1.2e-7

VOL_UP_MIN            = 0.09             # 9%
VOL_UP_MAX            = 0.45             # 45%
VOL_UP_SLOPE          = 1.8e-7

# DTE adjustment for IV shocks: shock × min(DTE_MULT_CAP, (30/DTE)^0.30)
# Cap prevents vol shock blowing up at very-near-expiry. Empirically Delta's
# basket margin doesn't track the docs' (30/DTE)^0.30 unbounded growth.
DTE_REF               = 30
DTE_EXPONENT          = 0.30
DTE_MULT_CAP          = 2.5              # max IV-shock amplification
DTE_FLOOR_DAYS        = 1 / 24           # clamp to 1 hour

# 9-point price grid (fractions of priceShock span)
PRICE_STEPS           = (-1, -2/3, -0.5, -1/3, 0, 1/3, 0.5, 2/3, 1)
EXTREME_PRICE_MULT    = 3.0
EXTREME_WEIGHT        = 1/3

# OM% — used for the per-leg margin floor
OM_PCT_FLOOR_NOT      = 200_000          # USD (BTC)
OM_PCT_MIN            = 0.0055           # 0.55% — empirical (docs say 0.5%, real basket charges ~0.55%)
OM_PCT_MAX            = 0.02             # 2%
OM_PCT_SLOPE          = 5e-9

# Floor coefficient on premium.
# Docs say 5%, but empirical calibration vs Delta's basket-margin endpoint
# shows ~65% of premium for ATM/near-ATM positions where premium dominates.
# (Earlier 0.90 was fit against /v2/orders/estimate_margin per-leg sum which
# over-states basket margin because it ignores cross-leg netting.)
FLOOR_PREMIUM_COEF    = 0.65

# Empirical DTE scaling on portfolio_margin.
# Our BS scenario engine systematically over-estimates Delta's actual margin as
# DTE grows (their engine isn't pure BS — likely smile-aware + path-dependent).
# Calibrated against the TRUE basket margin endpoint
# /v2/orders/estimate_margin/basket across DTE × moneyness × qty grid
# (see scripts/calibrate_margin.py). Re-fit via fit_margin_scale.py.
#
#   scale(DTE) = min(1.0, (DTE_SCALE_T0 / DTE_days)^DTE_SCALE_EXP)
#
# Median residual after applying: ±7% across DTE 0.4d-60d, all moneyness.
DTE_SCALE_T0          = 0.49            # days at which scale = 1.0
DTE_SCALE_EXP         = 0.234           # decay exponent

# Safety buffer applied to final portfolio_margin.
# Hard rule: model output must always be at-or-above Delta's actual ARM
# (the "Order Margin" charge shown in UI). Slight over-estimation is fine,
# under-estimation breaks orders at placement.
# Verified against UI on 2026-04-30 (8-May expiry δ=0.10 strangle):
# 20% covers all lot sizes within ±3% of Delta's UI charge.
SAFETY_BUFFER_PCT     = 0.20


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _ramp(notional: float, lo: float, hi: float,
          floor_n: float, cap_n: float, slope: float) -> float:
    if notional <= floor_n: return lo
    if notional >= cap_n:   return hi
    return lo + slope * (notional - floor_n)


def get_shock_params(total_notional: float) -> tuple[float, float, float]:
    """Returns (price_shock, vol_up, vol_down) — all decimals."""
    return (
        _ramp(total_notional, PRICE_SHOCK_MIN, PRICE_SHOCK_MAX,
              NOTIONAL_FLOOR, PRICE_SHOCK_CAP_NOT, PRICE_SHOCK_SLOPE),
        _ramp(total_notional, VOL_UP_MIN, VOL_UP_MAX,
              NOTIONAL_FLOOR, VOL_CAP_NOT, VOL_UP_SLOPE),
        _ramp(total_notional, VOL_DOWN_MIN, VOL_DOWN_MAX,
              NOTIONAL_FLOOR, VOL_CAP_NOT, VOL_DOWN_SLOPE),
    )


def get_om_pct(short_notional: float) -> float:
    return _clamp(
        OM_PCT_MIN + OM_PCT_SLOPE * max(0.0, short_notional - OM_PCT_FLOOR_NOT),
        OM_PCT_MIN, OM_PCT_MAX,
    )


def dte_adjust(span: float, dte_days: float) -> float:
    mult = (DTE_REF / max(DTE_FLOOR_DAYS, dte_days)) ** DTE_EXPONENT
    return span * min(DTE_MULT_CAP, mult)


# ── Public types ──────────────────────────────────────────────────────────────

@dataclass
class MarginLeg:
    strike: float
    is_call: bool
    is_buy: bool
    qty: int
    current_price: float    # USDT per CONTRACT (mark_price × CONTRACT_VALUE)
    iv: float               # decimal, 0.60 for 60%
    T: float                # years to expiry
    forward: float = 0.0    # implied futures price (USDT/BTC) for this expiry.
                            # If 0, BS scenarios fall back to spot.
                            # Derive via put-call parity: F = C − P + K.


@dataclass
class ScenarioInfo:
    price_shock_pct: float = 0.0    # spot move %, e.g. -4.0 = -4%
    vol_shock_pts:   float = 0.0    # IV move in percentage points
    pnl:             float = 0.0    # USDT


@dataclass
class MarginResult:
    portfolio_margin:        float = 0.0    # max(Risk, Floor) × dte_scale
    initial_margin:          float = 0.0    # = portfolio_margin (order-margin context)
    maintenance_margin:      float = 0.0    # 0.80 × initial_margin
    risk_margin:             float = 0.0    # raw, pre-scale
    margin_floor:            float = 0.0    # raw, pre-scale
    pre_scale_margin:        float = 0.0    # max(risk, floor) before DTE scale
    dte_scale_applied:       float = 1.0    # min(1, (T0/DTE)^p)
    ucf:                     float = 0.0    # signed: long bias positive
    binding_constraint:      str   = "risk_margin"   # or "margin_floor"
    effective_leverage:      float = 0.0
    total_notional:          float = 0.0
    short_options_notional:  float = 0.0
    om_pct_applied:          float = 0.005
    total_premium_collected: float = 0.0
    net_delta_btc:           float = 0.0
    margin_per_lot:          float = 0.0
    worst_scenario:          ScenarioInfo = field(default_factory=ScenarioInfo)
    best_scenario:           ScenarioInfo = field(default_factory=ScenarioInfo)
    price_shock_applied:     float = 0.0
    vol_up_applied:          float = 0.0
    vol_down_applied:        float = 0.0
    min_dte_days_applied:    float = 0.0
    skipped_legs:            int   = 0


# ── Main function ─────────────────────────────────────────────────────────────

def compute_portfolio_margin(legs: list[MarginLeg],
                             spot: float,
                             contract_value: float = CONTRACT_VALUE,
                             skipped_legs: int = 0) -> MarginResult | None:
    """
    Implements Delta India portfolio-margin methodology.
    `spot` drives notional + scenario shocks (use BTC perp mark price).
    """
    if not legs or spot <= 0:
        return None

    total_notional = sum(l.qty * contract_value * spot for l in legs)
    short_options_notional = sum(
        l.qty * contract_value * spot for l in legs if not l.is_buy
    )

    price_shock, vol_up, vol_down = get_shock_params(total_notional)

    min_dte_days = max(DTE_FLOOR_DAYS, min(l.T * 365 for l in legs))
    iv_up   = dte_adjust(vol_up,   min_dte_days)
    iv_down = dte_adjust(vol_down, min_dte_days)

    # Build 29 scenarios
    scenarios: list[tuple[float, float, float]] = []   # (price_step, vol_delta, weight)
    for ps in PRICE_STEPS:
        for vd in (-iv_down, 0.0, iv_up):
            scenarios.append((ps, vd, 1.0))
    scenarios.append((-EXTREME_PRICE_MULT, iv_up, EXTREME_WEIGHT))
    scenarios.append(( EXTREME_PRICE_MULT, iv_up, EXTREME_WEIGHT))

    worst_loss = 0.0
    best_pnl   = -1e18
    worst_sc   = ScenarioInfo()
    best_sc    = ScenarioInfo()

    for price_step, vol_delta, weight in scenarios:
        scenario_pnl = 0.0
        for leg in legs:
            # Use per-leg forward (PCP-derived futures) if provided; else spot.
            # Apply same percentage shock to whichever underlying we're using
            # (spots and forwards are highly correlated for near-term expiries).
            base_underlying = leg.forward if leg.forward > 0 else spot
            s_under = base_underlying * (1 + price_step * price_shock)
            iv_sc = max(0.01, leg.iv + vol_delta)
            sc_price = bs_price(s_under, leg.strike, leg.T, iv_sc, leg.is_call) * contract_value
            direction = 1 if leg.is_buy else -1
            scenario_pnl += direction * leg.qty * (sc_price - leg.current_price)

        weighted_loss = -scenario_pnl * weight

        if weighted_loss > worst_loss:
            worst_loss = weighted_loss
            worst_sc = ScenarioInfo(
                price_shock_pct=price_step * price_shock * 100,
                vol_shock_pts=vol_delta * 100,
                pnl=scenario_pnl,
            )
        if scenario_pnl > best_pnl:
            best_pnl = scenario_pnl
            best_sc = ScenarioInfo(
                price_shock_pct=price_step * price_shock * 100,
                vol_shock_pts=vol_delta * 100,
                pnl=scenario_pnl,
            )

    risk_margin = max(0.0, worst_loss)

    # Margin floor — short uncapped, long capped at premium paid
    om_pct = get_om_pct(short_options_notional)
    margin_floor = 0.0
    total_premium_collected = 0.0
    for leg in legs:
        leg_notional = leg.qty * contract_value * spot
        leg_premium  = leg.qty * leg.current_price
        base_floor   = max(FLOOR_PREMIUM_COEF * leg_premium, om_pct * leg_notional)
        if leg.is_buy:
            margin_floor += min(leg_premium, base_floor)
        else:
            margin_floor += base_floor
            total_premium_collected += leg_premium

    pre_scale_pm = max(risk_margin, margin_floor)

    # Empirical DTE scale — corrects long-DTE over-estimate (see constants block).
    # The scale was calibrated against short strangles (2 short legs, no longs).
    # Apply less aggressively for other strategies:
    #   • Pure short multi-leg (strangle, straddle): full scale (calibrated)
    #   • Single short leg: 0.8× factor (no cross-leg netting → less reduction)
    #   • Protected (any long leg present): 0.5× factor (longs already cut risk)
    n_short = sum(1 for l in legs if not l.is_buy)
    n_long  = sum(1 for l in legs if l.is_buy)
    if n_long > 0:
        strategy_factor = 0.7      # protected (iron condor, spread)
    elif n_short == 1:
        strategy_factor = 0.9      # single leg
    else:
        strategy_factor = 1.0      # pure-short multi-leg (calibrated)
    raw_dte_scale  = min(1.0, (DTE_SCALE_T0 / max(min_dte_days, DTE_FLOOR_DAYS)) ** DTE_SCALE_EXP)
    dte_scale      = 1.0 + (raw_dte_scale - 1.0) * strategy_factor
    portfolio_margin = pre_scale_pm * dte_scale * (1.0 + SAFETY_BUFFER_PCT)

    # UCF (Unsettled Cashflows) is computed for transparency / account-level use.
    # NOTE: for ORDER margin (what Delta charges at trade placement) UCF = 0.
    # The doc's `IM = max(Risk,Floor) − UCF` formula applies to ONGOING portfolio
    # margin where UCF = running unrealised P&L. Empirical validation shows
    # Delta's order margin matches max(Risk, Floor) × dte_scale within ~10%.
    ucf = 0.0
    for leg in legs:
        sign = 1 if leg.is_buy else -1
        ucf += sign * leg.qty * leg.current_price

    initial_margin     = portfolio_margin             # order-margin context
    maintenance_margin = 0.80 * initial_margin

    binding = "risk_margin" if risk_margin >= margin_floor else "margin_floor"
    eff_lev = (total_notional / portfolio_margin) if portfolio_margin > 0 else 0.0

    # Net portfolio delta in BTC (use per-leg forward when available)
    net_delta_btc = 0.0
    for leg in legs:
        u = leg.forward if leg.forward > 0 else spot
        d = bs_delta(u, leg.strike, leg.T, leg.iv if leg.iv > 0 else 0.5, leg.is_call)
        net_delta_btc += (1 if leg.is_buy else -1) * leg.qty * contract_value * d

    total_lots = sum(l.qty for l in legs)
    margin_per_lot = (portfolio_margin / total_lots) if total_lots > 0 else 0.0

    return MarginResult(
        portfolio_margin=portfolio_margin,
        initial_margin=initial_margin,
        maintenance_margin=maintenance_margin,
        risk_margin=risk_margin,
        margin_floor=margin_floor,
        pre_scale_margin=pre_scale_pm,
        dte_scale_applied=dte_scale,
        ucf=ucf,
        binding_constraint=binding,
        effective_leverage=eff_lev,
        total_notional=total_notional,
        short_options_notional=short_options_notional,
        om_pct_applied=om_pct,
        total_premium_collected=total_premium_collected,
        net_delta_btc=net_delta_btc,
        margin_per_lot=margin_per_lot,
        worst_scenario=worst_sc,
        best_scenario=best_sc,
        price_shock_applied=price_shock,
        vol_up_applied=iv_up,
        vol_down_applied=iv_down,
        min_dte_days_applied=min_dte_days,
        skipped_legs=skipped_legs,
    )


# ── Implied forward (put-call parity) ────────────────────────────────────────

def implied_forward_pcp(call_mark_per_btc: float, put_mark_per_btc: float,
                        strike: float) -> float:
    """
    F = C − P + K  (futures-style options, r=0)
    All inputs and output in USDT/BTC.
    Returns 0 if either leg's mark is missing.
    """
    if call_mark_per_btc is None or put_mark_per_btc is None:
        return 0.0
    if call_mark_per_btc <= 0 or put_mark_per_btc <= 0:
        return 0.0
    return float(call_mark_per_btc) - float(put_mark_per_btc) + float(strike)


def best_forward_for_expiry(chain_rows: list[dict], spot: float) -> float:
    """
    Walk a list of strike rows looking for the strike closest to spot where
    BOTH call and put have positive marks. Compute F via PCP.

    `chain_rows` shape (per row): {strike, call_mark_per_btc, put_mark_per_btc}
    Returns 0 if no usable strike found.
    """
    candidates = [r for r in chain_rows
                  if (r.get("call_mark_per_btc") or 0) > 0
                  and (r.get("put_mark_per_btc")  or 0) > 0]
    if not candidates:
        return 0.0
    best = min(candidates, key=lambda r: abs(r["strike"] - spot))
    return implied_forward_pcp(
        best["call_mark_per_btc"], best["put_mark_per_btc"], best["strike"],
    )


# ── Convenience builder for dict-based callers (margin_check.py) ──────────────

def build_legs_from_dicts(legs_dict: list[dict]) -> list[MarginLeg]:
    """Adapt the older dict-style leg representation used by margin_check.py."""
    out = []
    for d in legs_dict:
        # mark_usdt_per_btc * CV gives USDT per contract
        cp = d.get("current_price")
        if cp is None and "mark_usdt_per_btc" in d:
            cp = d["mark_usdt_per_btc"] * CONTRACT_VALUE
        out.append(MarginLeg(
            strike=d["strike"],
            is_call=d["is_call"],
            is_buy=d.get("is_buy", False),
            qty=int(d["qty"]),
            current_price=cp,
            iv=d["iv"],
            T=d["T"],
        ))
    return out

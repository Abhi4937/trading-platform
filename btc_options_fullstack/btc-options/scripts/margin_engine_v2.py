"""
Delta Exchange India — Portfolio Margin Engine v2 (Python)

Standalone, separate from scripts/margin_engine.py (v1) and
frontend/src/utils/marginEngine.ts. Same Delta India SPAN-style structure,
but with all tunable constants externalized to margin_engine_v2_constants.json
so calibration can update them without touching this file.

Designed for:
- Capital-budgeted backtest sizing ("max lots at $50K")
- A/B comparison against v1 + Delta's /v2/orders/estimate_margin/basket
- Refit via scripts/fit_v2.py against the calibration sweep CSV

Doc reference:
    https://guides.delta.exchange/delta-exchange-india-user-guide/
    trading-guide/margin-explainer/portfolio-margin

Formulas (verbatim from docs):
    Margin              = max(Risk Margin, Margin Floor)
    Initial Margin      = max(Risk Margin, Margin Floor) − UCF
    Maintenance Margin  = 0.80 × (Initial Margin + UCF) − UCF
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any

CONTRACT_VALUE = 0.001  # BTC per BTC-options contract on Delta India

# ── Constants loader ─────────────────────────────────────────────────────────
# Loaded once at import. Reload via reload_constants() after fit.

_CONSTANTS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "margin_engine_v2_constants.json",
)


def _load_constants(path: str = _CONSTANTS_PATH) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


C: dict[str, Any] = _load_constants()


def reload_constants(path: str = _CONSTANTS_PATH) -> dict[str, Any]:
    """Re-read JSON config (call after fit_v2.py writes new values)."""
    global C
    C = _load_constants(path)
    return C


# ── Black-Scholes (identical to v1) ──────────────────────────────────────────

def norm_cdf(x: float) -> float:
    a1, a2, a3, a4, a5, p = (
        0.254829592, -0.284496736, 1.421413741,
        -1.453152027, 1.061405429, 0.3275911,
    )
    sign = -1 if x < 0 else 1
    ax = abs(x)
    t = 1.0 / (1.0 + p * ax)
    y = 1.0 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * math.exp(-ax * ax)
    return 0.5 * (1.0 + sign * y)


def bs_price(S: float, K: float, T: float, sigma: float, is_call: bool,
             r: float = 0.0) -> float:
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
        if is_call:
            return 1.0 if S >= K else 0.0
        return 0.0 if S >= K else -1.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1) if is_call else norm_cdf(d1) - 1.0


# ── Public types (match v1 names so comparison code can swap engines easily) ─

@dataclass
class MarginLeg:
    strike: float
    is_call: bool
    is_buy: bool
    qty: int
    current_price: float    # USDT per CONTRACT (mark × CONTRACT_VALUE)
    iv: float               # decimal
    T: float                # years to expiry
    forward: float = 0.0    # PCP-derived futures price; 0 → use spot


@dataclass
class ScenarioInfo:
    price_shock_pct: float = 0.0
    vol_shock_pts:   float = 0.0
    pnl:             float = 0.0


@dataclass
class MarginResult:
    portfolio_margin:        float = 0.0
    initial_margin:          float = 0.0
    maintenance_margin:      float = 0.0
    risk_margin:             float = 0.0
    margin_floor:            float = 0.0
    pre_scale_margin:        float = 0.0
    dte_scale_applied:       float = 1.0
    ucf:                     float = 0.0
    binding_constraint:      str   = "risk_margin"
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _ramp(notional: float, lo: float, hi: float,
          floor_n: float, cap_n: float, slope: float) -> float:
    if notional <= floor_n:
        return lo
    if notional >= cap_n:
        return hi
    return lo + slope * (notional - floor_n)


def get_shock_params(total_notional: float) -> tuple[float, float, float]:
    return (
        _ramp(total_notional, C["PRICE_SHOCK_MIN"], C["PRICE_SHOCK_MAX"],
              C["NOTIONAL_FLOOR"], C["PRICE_SHOCK_CAP_NOT"], C["PRICE_SHOCK_SLOPE"]),
        _ramp(total_notional, C["VOL_UP_MIN"], C["VOL_UP_MAX"],
              C["NOTIONAL_FLOOR"], C["VOL_CAP_NOT"], C["VOL_UP_SLOPE"]),
        _ramp(total_notional, C["VOL_DOWN_MIN"], C["VOL_DOWN_MAX"],
              C["NOTIONAL_FLOOR"], C["VOL_CAP_NOT"], C["VOL_DOWN_SLOPE"]),
    )


def get_om_pct(short_notional: float) -> float:
    return _clamp(
        C["OM_PCT_MIN"] + C["OM_PCT_SLOPE"] * max(0.0, short_notional - C["OM_PCT_FLOOR_NOT"]),
        C["OM_PCT_MIN"], C["OM_PCT_MAX"],
    )


def dte_adjust(span: float, dte_days: float) -> float:
    floor = max(C["DTE_FLOOR_DAYS"], dte_days)
    mult = (C["DTE_REF"] / floor) ** C["DTE_EXPONENT"]
    return span * min(C["DTE_MULT_CAP"], mult)


def _strategy_factor(legs: list[MarginLeg]) -> float:
    n_long  = sum(1 for l in legs if l.is_buy)
    n_short = sum(1 for l in legs if not l.is_buy)
    if n_long > 0:
        return C["STRATEGY_FACTOR_PROTECTED"]
    if n_short == 1:
        return C["STRATEGY_FACTOR_SINGLE_SHORT"]
    return C["STRATEGY_FACTOR_PURE_SHORT"]


# ── Main ──────────────────────────────────────────────────────────────────────

def compute_portfolio_margin(legs: list[MarginLeg],
                             spot: float,
                             contract_value: float = CONTRACT_VALUE,
                             skipped_legs: int = 0) -> MarginResult | None:
    if not legs or spot <= 0:
        return None

    total_notional = sum(l.qty * contract_value * spot for l in legs)
    short_options_notional = sum(
        l.qty * contract_value * spot for l in legs if not l.is_buy
    )

    price_shock, vol_up, vol_down = get_shock_params(total_notional)

    min_dte_days = max(C["DTE_FLOOR_DAYS"], min(l.T * 365 for l in legs))
    iv_up   = dte_adjust(vol_up,   min_dte_days)
    iv_down = dte_adjust(vol_down, min_dte_days)

    PRICE_STEPS = (-1, -2/3, -0.5, -1/3, 0, 1/3, 0.5, 2/3, 1)
    scenarios: list[tuple[float, float, float]] = []
    for ps in PRICE_STEPS:
        for vd in (-iv_down, 0.0, iv_up):
            scenarios.append((ps, vd, 1.0))
    scenarios.append((-C["EXTREME_PRICE_MULT"], iv_up, C["EXTREME_WEIGHT"]))
    scenarios.append(( C["EXTREME_PRICE_MULT"], iv_up, C["EXTREME_WEIGHT"]))

    worst_loss = 0.0
    best_pnl   = -1e18
    worst_sc   = ScenarioInfo()
    best_sc    = ScenarioInfo()

    for price_step, vol_delta, weight in scenarios:
        scenario_pnl = 0.0
        for leg in legs:
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

    om_pct = get_om_pct(short_options_notional)
    margin_floor = 0.0
    total_premium_collected = 0.0
    for leg in legs:
        leg_notional = leg.qty * contract_value * spot
        leg_premium  = leg.qty * leg.current_price
        base_floor   = max(C["FLOOR_PREMIUM_COEF"] * leg_premium, om_pct * leg_notional)
        if leg.is_buy:
            margin_floor += min(leg_premium, base_floor)
        else:
            margin_floor += base_floor
            total_premium_collected += leg_premium

    pre_scale_pm = max(risk_margin, margin_floor)

    raw_dte_scale  = min(1.0, (C["DTE_SCALE_T0"] / max(min_dte_days, C["DTE_FLOOR_DAYS"])) ** C["DTE_SCALE_EXP"])
    strat_factor   = _strategy_factor(legs)
    dte_scale      = 1.0 + (raw_dte_scale - 1.0) * strat_factor
    portfolio_margin = pre_scale_pm * dte_scale

    ucf = 0.0
    for leg in legs:
        sign = 1 if leg.is_buy else -1
        ucf += sign * leg.qty * leg.current_price

    initial_margin     = portfolio_margin
    maintenance_margin = 0.80 * initial_margin

    binding = "risk_margin" if risk_margin >= margin_floor else "margin_floor"
    eff_lev = (total_notional / portfolio_margin) if portfolio_margin > 0 else 0.0

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


# ── Implied forward (PCP) ────────────────────────────────────────────────────

def implied_forward_pcp(call_mark_per_btc: float, put_mark_per_btc: float,
                        strike: float) -> float:
    if call_mark_per_btc is None or put_mark_per_btc is None:
        return 0.0
    if call_mark_per_btc <= 0 or put_mark_per_btc <= 0:
        return 0.0
    return float(call_mark_per_btc) - float(put_mark_per_btc) + float(strike)


def best_forward_for_expiry(chain_rows: list[dict], spot: float) -> float:
    candidates = [r for r in chain_rows
                  if (r.get("call_mark_per_btc") or 0) > 0
                  and (r.get("put_mark_per_btc")  or 0) > 0]
    if not candidates:
        return 0.0
    best = min(candidates, key=lambda r: abs(r["strike"] - spot))
    return implied_forward_pcp(
        best["call_mark_per_btc"], best["put_mark_per_btc"], best["strike"],
    )

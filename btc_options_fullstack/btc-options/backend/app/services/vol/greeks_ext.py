"""
Gamma-vs-Theta competition.

Adapted from rv_engine/options_math.py::gamma_theta_ratio, but instead of
re-deriving greeks here it takes gamma and theta computed by the platform's own
`app.core.greeks.compute_greeks` — keeping a single greeks code path and
guaranteeing the conventions match the option chain:

  * gamma          — standard per-$ gamma (compute_greeks: n1/(S*sigma*sqrt(T)))
  * theta_per_day  — per calendar day, negative for long premium
                     (compute_greeks divides the annual theta by 365)

Logic:
  expected 1σ daily move ≈ sigma_realized * S / sqrt(365)
  gamma PnL per day (1σ)  = 0.5 * gamma * move²
  ratio                   = gamma_pnl_per_day / |theta_per_day|
    ratio > 1.0  → GAMMA WINS (long premium pays — RV out-delivering decay)
    ratio < 1.0  → THETA WINS (short premium pays — decay out-running RV)
  break-even daily move    = sqrt(2 * |theta| / gamma)
"""

import math

from .constants import DAYS_PER_YEAR


def gamma_theta_ratio(
    spot: float,
    gamma: float,
    theta_per_day: float,
    sigma_realized: float,
) -> dict:
    """
    Compare gamma PnL vs theta decay for the ATM option, given pre-computed
    greeks and a realized-vol estimate (decimal, annualized).
    """
    daily_move_1sd = sigma_realized * spot / math.sqrt(DAYS_PER_YEAR)
    gamma_pnl_per_day = 0.5 * gamma * (daily_move_1sd ** 2)

    theta_abs = abs(theta_per_day)
    if theta_abs < 1e-9:
        ratio = float("inf") if gamma_pnl_per_day > 0 else 0.0
    else:
        ratio = gamma_pnl_per_day / theta_abs

    breakeven = math.sqrt(2 * theta_abs / gamma) if gamma > 0 else float("inf")

    return {
        "gamma": gamma,
        "theta_per_day": theta_per_day,
        "expected_daily_move_1sd": daily_move_1sd,
        "gamma_pnl_per_day_1sd": gamma_pnl_per_day,
        "theta_pnl_per_day": -theta_abs,
        "gamma_theta_ratio": ratio,
        "verdict": "GAMMA WINS (long premium)" if ratio > 1.0 else "THETA WINS (short premium)",
        "breakeven_move_per_day": breakeven,
    }

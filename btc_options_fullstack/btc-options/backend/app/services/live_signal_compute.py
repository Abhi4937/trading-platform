"""
LiveSignal compute layer.

Scans every live BTC option expiry × all 6 calibrated target deltas, builds
a short strangle for each (CE SELL + PE SELL at the closest-delta strikes),
runs `strangle_analytics.compute_trade_analytics` on it, and returns a
ranked list of candidates with full quality_score (calibrated_v2 when
available) + decomposition + hard-filter flags.

The frontend `<LiveSignalDashboard />` polls this every ~7s to surface the
"best (Δ, expiry) right now" recommendation.

Hot path:
- Reuses existing `option_chain_service.get_option_chain_from_store(expiry)`
  which already pulls from `ticker_store` and computes per-leg IV/Greeks
  server-side via `app.core.greeks` — no duplication.
- Reuses `strangle_analytics.compute_trade_analytics` (the same code that
  bakes analytics into per-job backtest trade rows + M4 trades).
- Reuses `strangle_analytics.get_market_context(now)` for the M3 row at the
  most recent 5m-aligned timestamp.

Performance: ~5–8 live expiries × 6 deltas = 30–50 candidates per scan.
Each candidate: chain fetch (cached on warm calls), 1 strike-resolution
loop, 1 calibration lookup, 1 analytics compute = ~30ms. Total scan
typically <1s; 5s response cache prevents repeat work for multi-client
polling.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from app.services import ticker_store
from app.services.option_chain_service import (
    get_expiries,
    get_option_chain_from_store,
)
from app.services.strangle_analytics import (
    TradeLeg,
    compute_trade_analytics,
    get_market_context,
    lookup_calibration,
)

log = logging.getLogger(__name__)

TARGET_DELTAS: tuple[float, ...] = (0.05, 0.10, 0.15, 0.25, 0.30, 0.50)
QTY_LOTS: int = 100  # matches M4 default


# ── Public dataclass ─────────────────────────────────────────────────────────

@dataclass
class LiveSignalCandidate:
    ts: int
    expiry: str          # YYYY-MM-DD
    expiry_ts: int       # unix UTC at 12:00 (Delta settlement)
    dte_days: float
    target_delta: float
    call_strike: int
    put_strike: int
    call_mark: float
    put_mark: float
    call_iv: float       # decimal
    put_iv: float
    call_delta: float
    put_delta: float
    spot: float
    total_credit: float  # call_mark + put_mark
    credit_usd: float    # total_credit × QTY × CONTRACT_VALUE (0.001)
    credit_pct: float    # total_credit / spot
    margin_used_usd: Optional[float]
    # Analytics output (flattened from compute_trade_analytics)
    quality_score: Optional[float]
    quality_source: Optional[str]
    size_band: Optional[str]
    pattern: Optional[str]
    fair_credit_at_ivp: Optional[float]
    structural_credit_pct: Optional[float]
    iv_regime_premium_pct: Optional[float]
    excess_over_fair_pct: Optional[float]
    z_credit_pct: Optional[float]
    theta_vega_ratio: Optional[float]
    gamma_theta_dollar: Optional[float]
    # v2 fields (None when bucket has no M4 data)
    overall_winrate: Optional[float]
    pattern_winrate: Optional[float]
    n_trades_in_bucket: Optional[int]
    # Hard-filter flags
    flt_ivp_gt50: bool
    flt_iv_rv_spread_pos: bool
    flt_adx_lt30: bool
    flt_dte_5_14: bool
    flt_gex_ok: bool
    flt_all_pass: bool


# ── Helpers ──────────────────────────────────────────────────────────────────

def _expiry_unix(expiry: str | date) -> int:
    if isinstance(expiry, str):
        dt = datetime.strptime(expiry, "%Y-%m-%d")
    else:
        dt = datetime(expiry.year, expiry.month, expiry.day)
    return int(dt.replace(tzinfo=timezone.utc, hour=12).timestamp())


def _pick_closest_delta(
    chain_rows: list[Any], target_delta: float, side: str,
) -> Optional[Any]:
    """From the chain (list of ChainRow with .call / .put OptionLeg), pick
    the leg whose |abs(delta) - target_delta| is minimum. Returns the leg
    (OptionLeg) or None if no valid option found.
    """
    best = None
    best_diff = float("inf")
    for row in chain_rows:
        leg = row.call if side == "CE" else row.put
        if leg is None:
            continue
        if leg.last_price <= 0:
            continue
        if leg.delta is None:
            continue
        d = abs(leg.delta)
        if d <= 0 or d >= 1.0:
            continue
        diff = abs(d - target_delta)
        if diff < best_diff:
            best_diff = diff
            best = leg
    return best


def _hard_filters(ctx: dict, dte_days: float) -> dict:
    """Hard-filter checklist. Each flag is True when the criterion passes."""
    ivp = ctx.get("ivp_atm_7d_90d")
    ivp_4h = ctx.get("ivp_4h")
    rv7 = ctx.get("rv_7d")
    atm7 = ctx.get("atm_iv_7d")
    adx = ctx.get("adx_14_4h")
    gex_regime = ctx.get("gex_regime") or ""
    flt = {
        "flt_ivp_gt50": bool(ivp is not None and not _isnan(ivp) and ivp > 50),
        "flt_iv_rv_spread_pos": bool(
            atm7 is not None and rv7 is not None
            and not _isnan(atm7) and not _isnan(rv7)
            and rv7 > 0 and atm7 > rv7 / 100.0
        ),
        "flt_adx_lt30": bool(adx is not None and not _isnan(adx) and adx < 30),
        "flt_dte_5_14": bool(5.0 <= dte_days <= 14.0),
        "flt_gex_ok": gex_regime != "NEAR_FLIP",
    }
    flt["flt_all_pass"] = all(flt.values())
    return flt


def _isnan(x) -> bool:
    try:
        return x != x  # NaN != NaN
    except Exception:
        return False


def _parse_pattern_winrate(raw: Any, pattern: Optional[str]) -> Optional[float]:
    """Pull the winrate for THIS trade's pattern out of the bucket's
    JSON-encoded `pattern_winrate` map. Returns None if absent.
    """
    if raw is None or not pattern:
        return None
    if isinstance(raw, str):
        try:
            import json
            d = json.loads(raw)
        except Exception:
            return None
    elif isinstance(raw, dict):
        d = raw
    else:
        return None
    val = d.get(str(pattern))
    if val is None:
        # Fall back to "Other" pattern winrate if available
        val = d.get("Other")
    return float(val) if val is not None else None


# ── Core scan ────────────────────────────────────────────────────────────────

async def scan_live_candidates(
    *, max_expiries: Optional[int] = None,
    target_deltas: tuple[float, ...] = TARGET_DELTAS,
) -> list[LiveSignalCandidate]:
    """Build the full grid of (expiry × delta) candidates from live ws data.

    Returns a list sorted by quality_score desc (None scores last).
    """
    if not ticker_store.has_data():
        log.info("scan_live_candidates: ticker_store empty, returning [].")
        return []

    spot = ticker_store.get_spot()
    if spot <= 0:
        log.warning("scan_live_candidates: spot <= 0, returning [].")
        return []

    now_ts = int(time.time())

    # 1) Enumerate live expiries (cached, fast)
    try:
        exp_resp = await get_expiries()
    except Exception as e:
        log.exception("scan_live_candidates: get_expiries failed: %s", e)
        return []
    expiries = [e.date for e in exp_resp.expiries]
    if max_expiries:
        expiries = expiries[:max_expiries]
    if not expiries:
        return []

    # 2) Pull each chain in parallel from the in-memory ticker_store
    chain_tasks = [get_option_chain_from_store(e) for e in expiries]
    chains = await asyncio.gather(*chain_tasks, return_exceptions=True)

    # 3) M3 context once for this scan (slow-moving; same ts feeds all candidates)
    try:
        ctx = get_market_context(now_ts)
    except Exception as e:
        log.warning("scan_live_candidates: get_market_context failed: %s", e)
        ctx = {}

    candidates: list[LiveSignalCandidate] = []
    for exp_date, chain_resp in zip(expiries, chains):
        if isinstance(chain_resp, Exception):
            log.warning("chain fetch failed for %s: %s", exp_date, chain_resp)
            continue
        if chain_resp is None or not chain_resp.chain:
            continue

        exp_iso = exp_date.isoformat() if hasattr(exp_date, "isoformat") else str(exp_date)
        exp_ts = _expiry_unix(exp_date)
        dte_days = max(0.0, (exp_ts - now_ts) / 86400.0)
        if dte_days < 0.1:
            continue  # too close to settlement

        for td in target_deltas:
            call_leg = _pick_closest_delta(chain_resp.chain, td, "CE")
            put_leg  = _pick_closest_delta(chain_resp.chain, td, "PE")
            if call_leg is None or put_leg is None:
                continue
            if call_leg.last_price <= 0 or put_leg.last_price <= 0:
                continue

            # Build SELL CE + SELL PE legs (qty=100 each)
            ta_legs: list[TradeLeg] = [
                TradeLeg(
                    type="CE", side="SELL",
                    strike=float(call_leg.strike), qty=QTY_LOTS,
                    mark=float(call_leg.last_price),
                    iv=float(call_leg.iv or 0.0),
                    delta=float(call_leg.delta or 0.0),
                    gamma=float(call_leg.gamma or 0.0),
                    vega=float(call_leg.vega or 0.0),
                    theta=float(call_leg.theta or 0.0),
                ),
                TradeLeg(
                    type="PE", side="SELL",
                    strike=float(put_leg.strike), qty=QTY_LOTS,
                    mark=float(put_leg.last_price),
                    iv=float(put_leg.iv or 0.0),
                    delta=float(put_leg.delta or 0.0),
                    gamma=float(put_leg.gamma or 0.0),
                    vega=float(put_leg.vega or 0.0),
                    theta=float(put_leg.theta or 0.0),
                ),
            ]

            try:
                analytics = compute_trade_analytics(
                    ta_legs, spot=spot, dte=dte_days,
                    entry_ts=now_ts, expiry_ts=exp_ts,
                )
            except Exception as e:
                log.warning("compute_trade_analytics failed for %s td=%.2f: %s",
                            exp_iso, td, e)
                continue

            total_credit = float(call_leg.last_price + put_leg.last_price)
            credit_usd = total_credit * QTY_LOTS * 0.001
            credit_pct = total_credit / spot if spot > 0 else 0.0

            # Pull bucket info to get pattern_winrate / overall_winrate
            bucket = lookup_calibration(dte_days, spot, td, analytics.get("ivp_atm_7d_90d_at_entry") or 50.0)
            overall_wr = bucket.get("overall_winrate") if bucket else None
            n_trades = bucket.get("n_trades") if bucket else None
            pattern_wr = _parse_pattern_winrate(
                bucket.get("pattern_winrate") if bucket else None,
                analytics.get("pattern"),
            )

            flt = _hard_filters(ctx, dte_days)

            candidates.append(LiveSignalCandidate(
                ts=now_ts,
                expiry=exp_iso, expiry_ts=exp_ts, dte_days=round(dte_days, 4),
                target_delta=td,
                call_strike=int(call_leg.strike),
                put_strike=int(put_leg.strike),
                call_mark=round(float(call_leg.last_price), 4),
                put_mark=round(float(put_leg.last_price), 4),
                call_iv=round(float(call_leg.iv or 0.0), 6),
                put_iv=round(float(put_leg.iv or 0.0), 6),
                call_delta=round(float(call_leg.delta or 0.0), 4),
                put_delta=round(float(put_leg.delta or 0.0), 4),
                spot=round(spot, 2),
                total_credit=round(total_credit, 4),
                credit_usd=round(credit_usd, 4),
                credit_pct=round(credit_pct, 6),
                margin_used_usd=None,  # margin compute is expensive; skip on hot path
                quality_score=analytics.get("quality_score"),
                quality_source=analytics.get("quality_source"),
                size_band=analytics.get("size_band"),
                pattern=analytics.get("pattern"),
                fair_credit_at_ivp=analytics.get("fair_credit_at_ivp"),
                structural_credit_pct=analytics.get("structural_credit_pct"),
                iv_regime_premium_pct=analytics.get("iv_regime_premium_pct"),
                excess_over_fair_pct=analytics.get("excess_over_fair_pct"),
                z_credit_pct=analytics.get("z_credit_pct"),
                theta_vega_ratio=analytics.get("theta_vega_ratio"),
                gamma_theta_dollar=analytics.get("gamma_theta_dollar"),
                overall_winrate=overall_wr,
                pattern_winrate=pattern_wr,
                n_trades_in_bucket=n_trades,
                **flt,
            ))

    # Sort by quality_score desc; None scores sink to the bottom.
    candidates.sort(
        key=lambda c: (c.quality_score is None, -(c.quality_score or 0.0))
    )
    return candidates


# ── Convenience for test/debug ───────────────────────────────────────────────

def candidate_to_dict(c: LiveSignalCandidate) -> dict:
    """For JSON serialization in the API layer."""
    return {
        k: getattr(c, k)
        for k in c.__dataclass_fields__.keys()
    }

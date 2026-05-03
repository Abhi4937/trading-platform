"""
Single-trade simulator — extracted from `_simulate_day` so M4's batch
backtester can reuse the bar-walk + SL + cost + margin logic without
duplicating it. Public per-job behavior in `backtest._simulate_day` will
delegate here once the equivalence test passes.

The function is **pure logic** — it takes already-resolved legs (strike +
expiry fixed), an entry timestamp, a forced-exit timestamp, optional
per-leg SL configs, cost config, and returns a `TradeResult` dataclass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.core.greeks import compute_greeks, implied_vol
from .costs import (
    CONTRACT_VALUE,
    compute_brokerage_one_side,
    slippage_dollars_per_side,
)
from .margin_v2 import MarginLeg, compute_portfolio_margin
from .option_data import (
    get_mark_at_or_before,
    get_spot_at_or_before,
    load_leg_series,
)


# ── Public dataclasses ───────────────────────────────────────────────────────

@dataclass
class TradeLegSpec:
    """Already-resolved leg: strike + expiry are fixed.

    M4 callers build these via `strike_for_closest_delta(...)`; the per-job
    backtester builds them via its existing strike-criteria machinery.
    """
    expiry: str        # "YYYY-MM-DD" (UTC settlement at 12:00)
    strike: int
    opt_type: str      # "CE" | "PE"
    action: str        # "BUY" | "SELL"
    qty: int


@dataclass
class LegSlSpec:
    """Per-leg stop-loss config — mirrors the per-job backtester's leg_sl shape.

    type='pct'    → exit if mark crosses entry_mark by `value` percent
                    (for SELL: mark >= entry × (1 + value/100); BUY: mark <= entry × (1 - value/100))
    type='points' → exit if mark crosses entry_mark by `value` dollars
    type='delta'  → exit if abs(current_delta) >= value
    """
    type: str          # "pct" | "points" | "delta"
    value: float


@dataclass
class PathSnapshot:
    ts: int
    spot: float
    leg_marks: list[float]
    leg_deltas: list[float]
    leg_ivs: list[float]
    pnl_gross_usd: float
    pnl_net_usd: float        # gross minus baked-in entry slippage


@dataclass
class TradeResult:
    # Entry
    entry_ts: int
    spot_at_entry: float
    entry_marks: list[float]
    entry_deltas: list[float]
    entry_ivs: list[float]            # decimal (not %)
    # Exit (actual)
    exit_ts: int
    exit_reason: str                  # "TimeStop" | "LegSL" | "Settlement"
    exit_marks: list[float]
    spot_at_exit: float
    breaching_leg_idx: Optional[int]  # which leg tripped SL, None otherwise
    # P&L
    gross_pnl: float
    entry_slip: float
    exit_slip: float
    entry_brk: float
    exit_brk: float
    net_pnl: float
    # Path-derived (with entry slip baked into max/min — matches MTM convention)
    max_mtm: float
    max_mtm_ts: int
    max_marks: list[float]
    max_leg_deltas: list[float]
    min_mtm: float
    min_mtm_ts: int
    min_marks: list[float]
    min_leg_deltas: list[float]
    # Peak / trough exit costs (recomputed at peak-MTM marks/spot/hour)
    peak_exit_slip: float
    peak_exit_brk: float
    trough_exit_slip: float
    trough_exit_brk: float
    max_pnl_net: float
    min_pnl_net: float
    # Margin
    margin_used: Optional[float]
    # Optional
    snapshots: Optional[list[PathSnapshot]] = None


# ── Internals ────────────────────────────────────────────────────────────────

def _expiry_unix(expiry_iso: str) -> int:
    """YYYY-MM-DD → unix UTC at 12:00 (settlement)."""
    return int(
        datetime.strptime(expiry_iso, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc, hour=12)
        .timestamp()
    )


def _delta_iv_at(
    mark: float, spot: float, strike: int, T_years: float, opt_type: str,
) -> tuple[float, float]:
    """Returns (delta, iv_decimal) using BS; (0, 0) on failure."""
    flag = "call" if opt_type == "CE" else "put"
    try:
        iv = implied_vol(mark, spot, strike, T_years, 0.0, flag) or 0.0
        if iv <= 0:
            return 0.0, 0.0
        g = compute_greeks(spot, strike, T_years, 0.0, iv, flag)
        return float(g.delta), float(iv)
    except Exception:
        return 0.0, 0.0


def _sl_trigger_price(
    leg: TradeLegSpec, entry_mark: float, sl_cfg: Optional[LegSlSpec],
) -> tuple[Optional[float], Optional[float]]:
    """Translate a (pct|points|delta) SL into either a static price trigger
    or an abs-delta threshold. Returns (price_trigger, delta_threshold);
    exactly one is non-None when SL is configured.
    """
    if sl_cfg is None or sl_cfg.value <= 0:
        return None, None
    if sl_cfg.type == "delta":
        return None, abs(sl_cfg.value)
    if sl_cfg.type == "pct":
        if leg.action == "SELL":
            return entry_mark * (1.0 + sl_cfg.value / 100.0), None
        return max(0.0, entry_mark * (1.0 - sl_cfg.value / 100.0)), None
    if sl_cfg.type == "points":
        if leg.action == "SELL":
            return entry_mark + sl_cfg.value, None
        return max(0.0, entry_mark - sl_cfg.value), None
    return None, None


# ── Public entry point ───────────────────────────────────────────────────────

def simulate_trade_path(
    legs: list[TradeLegSpec],
    entry_ts: int,
    forced_exit_ts: int,
    *,
    leg_sl_configs: Optional[list[Optional[LegSlSpec]]] = None,
    cost_cfg: Optional[dict] = None,
    record_snapshots: bool = False,
    snapshot_cadence_s: int = 3600,
    margin_compute: bool = True,
) -> Optional[TradeResult]:
    """Simulate one trade. Returns None if entry/exit data missing.

    `cost_cfg` shape mirrors the per-job backtester's `params`:
        {
          "slippage":  {"enabled", "mode", "flat_value", "mult"},
          "brokerage": {"enabled", "rate", "referral"},
        }

    For M4 Friday-overnight: pass `leg_sl_configs=[LegSlSpec("pct", 100.0)] * len(legs)`,
    `forced_exit_ts = Saturday 10:00 IST`, `record_snapshots=True`.
    """
    if not legs:
        return None
    cost_cfg = cost_cfg or {}
    sl_cfg = (cost_cfg.get("slippage") or {}) if cost_cfg else {}
    br_cfg = (cost_cfg.get("brokerage") or {}) if cost_cfg else {}

    spot_at_entry = get_spot_at_or_before(entry_ts)
    if spot_at_entry <= 0:
        return None

    # Cap exit at the earliest leg expiry (12:00 UTC on expiry date).
    settlement_ts = min(_expiry_unix(leg.expiry) for leg in legs)
    settlement_capped = forced_exit_ts > settlement_ts
    exit_ts = settlement_ts if settlement_capped else forced_exit_ts

    # Fetch entry + exit marks per leg.
    entry_marks: list[float] = []
    exit_marks: list[float] = []
    actual_exit_ts = exit_ts
    for leg in legs:
        em, _ = get_mark_at_or_before(leg.expiry, leg.strike, leg.opt_type, entry_ts)
        if em <= 0:
            return None
        xm, xm_ts = get_mark_at_or_before(leg.expiry, leg.strike, leg.opt_type, exit_ts)
        if xm <= 0:
            return None
        entry_marks.append(em)
        exit_marks.append(xm)
        actual_exit_ts = max(actual_exit_ts, xm_ts)

    spot_at_exit = get_spot_at_or_before(exit_ts) or spot_at_entry

    # Per-leg entry delta + IV.
    leg_expiry_ts = [_expiry_unix(leg.expiry) for leg in legs]
    entry_deltas: list[float] = []
    entry_ivs: list[float] = []
    for leg, em, e_ts in zip(legs, entry_marks, leg_expiry_ts):
        T_e = max(0.0001, (e_ts - entry_ts) / (365.0 * 86400.0))
        d, iv = _delta_iv_at(em, spot_at_entry, leg.strike, T_e, leg.opt_type)
        entry_deltas.append(d)
        entry_ivs.append(iv)

    # Translate SL configs → (price, delta) thresholds per leg.
    sl_specs = leg_sl_configs or [None] * len(legs)
    leg_sl_prices: list[Optional[float]] = []
    leg_sl_deltas: list[Optional[float]] = []
    for leg, em, sl in zip(legs, entry_marks, sl_specs):
        p, d = _sl_trigger_price(leg, em, sl)
        leg_sl_prices.append(p)
        leg_sl_deltas.append(d)
    any_sl_price = any(p is not None for p in leg_sl_prices)
    any_sl_delta = any(d is not None for d in leg_sl_deltas)
    any_sl = any_sl_price or any_sl_delta

    # Per-bar delta computation (lazy, cached).
    def deltas_at(t: int, marks: list[float]) -> list[float]:
        sp = get_spot_at_or_before(t) or spot_at_entry
        out: list[float] = []
        for i, mk in enumerate(marks):
            T_t = max(0.0001, (leg_expiry_ts[i] - t) / (365.0 * 86400.0))
            d, _ = _delta_iv_at(mk, sp, legs[i].strike, T_t, legs[i].opt_type)
            out.append(d)
        return out

    # ── Bar walk: track max/min MTM, check SL, optionally record snapshots ─
    max_mtm = -math.inf; min_mtm = math.inf
    max_mtm_ts = actual_exit_ts; min_mtm_ts = actual_exit_ts
    max_marks: list[float] = list(exit_marks)
    min_marks: list[float] = list(exit_marks)
    max_leg_deltas: list[float] = list(entry_deltas)
    min_leg_deltas: list[float] = list(entry_deltas)
    sl_triggered = False
    sl_exit_marks: list[float] = list(exit_marks)
    sl_exit_ts: int = actual_exit_ts
    breaching_leg_idx: Optional[int] = None
    snapshots: list[PathSnapshot] = []
    next_snapshot_ts = entry_ts + snapshot_cadence_s if record_snapshots else None

    try:
        leg_series = [
            load_leg_series(leg.expiry, leg.strike, leg.opt_type,
                            entry_ts, actual_exit_ts, "1m")
            for leg in legs
        ]
        ts_union = sorted({
            int(bar["time"]) for series in leg_series for bar in series
            if entry_ts <= int(bar["time"]) <= actual_exit_ts
        })
        if ts_union:
            idx = [0] * len(legs)
            last_mark = list(entry_marks)
            for t in ts_union:
                for i, series in enumerate(leg_series):
                    while idx[i] < len(series) and int(series[idx[i]]["time"]) <= t:
                        last_mark[i] = float(series[idx[i]]["close"])
                        idx[i] += 1
                pnl_t = 0.0
                for leg, em, mk in zip(legs, entry_marks, last_mark):
                    direction = 1 if leg.action == "BUY" else -1
                    pnl_t += (mk - em) * leg.qty * CONTRACT_VALUE * direction

                cur_deltas: Optional[list[float]] = None

                if pnl_t > max_mtm:
                    max_mtm = pnl_t; max_mtm_ts = t; max_marks = list(last_mark)
                    if cur_deltas is None:
                        cur_deltas = deltas_at(t, last_mark)
                    max_leg_deltas = list(cur_deltas)
                if pnl_t < min_mtm:
                    min_mtm = pnl_t; min_mtm_ts = t; min_marks = list(last_mark)
                    if cur_deltas is None:
                        cur_deltas = deltas_at(t, last_mark)
                    min_leg_deltas = list(cur_deltas)

                # Snapshot recording at hourly cadence (or whatever requested).
                if next_snapshot_ts is not None and t >= next_snapshot_ts:
                    if cur_deltas is None:
                        cur_deltas = deltas_at(t, last_mark)
                    sp_t = get_spot_at_or_before(t) or spot_at_entry
                    iv_at_t: list[float] = []
                    for i, mk in enumerate(last_mark):
                        T_t = max(0.0001, (leg_expiry_ts[i] - t) / (365.0 * 86400.0))
                        _, iv_t = _delta_iv_at(mk, sp_t, legs[i].strike, T_t, legs[i].opt_type)
                        iv_at_t.append(iv_t)
                    snapshots.append(PathSnapshot(
                        ts=t, spot=sp_t,
                        leg_marks=list(last_mark),
                        leg_deltas=list(cur_deltas),
                        leg_ivs=iv_at_t,
                        pnl_gross_usd=pnl_t,
                        pnl_net_usd=pnl_t,   # entry slip baked in below
                    ))
                    next_snapshot_ts = t + snapshot_cadence_s

                if any_sl:
                    if any_sl_price:
                        for i, (leg, sl_price) in enumerate(zip(legs, leg_sl_prices)):
                            if sl_price is None:
                                continue
                            hit = (
                                (leg.action == "SELL" and last_mark[i] >= sl_price) or
                                (leg.action == "BUY"  and last_mark[i] <= sl_price)
                            )
                            if hit:
                                sl_triggered = True
                                sl_exit_marks = list(last_mark)
                                sl_exit_ts = t
                                breaching_leg_idx = i
                                break
                    if not sl_triggered and any_sl_delta:
                        if cur_deltas is None:
                            cur_deltas = deltas_at(t, last_mark)
                        for i, sl_d in enumerate(leg_sl_deltas):
                            if sl_d is None:
                                continue
                            if abs(cur_deltas[i]) >= sl_d:
                                sl_triggered = True
                                sl_exit_marks = list(last_mark)
                                sl_exit_ts = t
                                breaching_leg_idx = i
                                break
                    if sl_triggered:
                        break
    except Exception:
        pass

    # Finalize: include the actual exit point as a max/min candidate, then
    # override with the SL trigger if it fired.
    fin_marks = sl_exit_marks if sl_triggered else exit_marks
    fin_ts    = sl_exit_ts    if sl_triggered else actual_exit_ts
    fin_pnl   = sum(
        (mk - em) * leg.qty * CONTRACT_VALUE * (1 if leg.action == "BUY" else -1)
        for leg, em, mk in zip(legs, entry_marks, fin_marks)
    )
    if fin_pnl > max_mtm or max_mtm == -math.inf:
        max_mtm = fin_pnl; max_mtm_ts = fin_ts; max_marks = list(fin_marks)
        max_leg_deltas = deltas_at(fin_ts, fin_marks)
    if fin_pnl < min_mtm or min_mtm == math.inf:
        min_mtm = fin_pnl; min_mtm_ts = fin_ts; min_marks = list(fin_marks)
        min_leg_deltas = deltas_at(fin_ts, fin_marks)

    if sl_triggered:
        exit_marks = sl_exit_marks
        actual_exit_ts = sl_exit_ts
        spot_at_exit = get_spot_at_or_before(sl_exit_ts) or spot_at_entry

    gross = sum(
        (xm - em) * leg.qty * CONTRACT_VALUE * (1 if leg.action == "BUY" else -1)
        for leg, em, xm in zip(legs, entry_marks, exit_marks)
    )

    # Costs.
    sl_enabled = sl_cfg.get("enabled", False)
    sl_mode    = sl_cfg.get("mode", "smart")
    sl_flat    = sl_cfg.get("flat_value", 5.0)
    sl_mult    = sl_cfg.get("mult", 1.0)
    br_rate = br_cfg.get("rate", "offer")
    br_ref  = br_cfg.get("referral", False)
    br_enabled = br_cfg.get("enabled", False)

    entry_slip_total = 0.0; exit_slip_total = 0.0
    entry_brk_total = 0.0;  exit_brk_total = 0.0
    for leg, em, xm in zip(legs, entry_marks, exit_marks):
        entry_slip_total += slippage_dollars_per_side(
            sl_enabled, sl_mode, sl_flat, sl_mult,
            spot_at_entry, em, leg.strike, leg.opt_type == "CE",
            leg.qty, entry_ts,
        )
        exit_slip_total += slippage_dollars_per_side(
            sl_enabled, sl_mode, sl_flat, sl_mult,
            spot_at_exit, xm, leg.strike, leg.opt_type == "CE",
            leg.qty, actual_exit_ts,
        )
        if br_enabled:
            entry_brk_total += compute_brokerage_one_side(
                spot_at_entry, em, leg.qty, br_rate, br_ref,
            )
            exit_brk_total += compute_brokerage_one_side(
                spot_at_exit, xm, leg.qty, br_rate, br_ref,
            )
    net_pnl = gross - entry_slip_total - exit_slip_total - entry_brk_total - exit_brk_total

    # Peak / trough exit cost recomputation.
    def exit_slip_at(ts: int, marks: list[float]) -> float:
        if not sl_enabled:
            return 0.0
        sp = get_spot_at_or_before(ts) or spot_at_exit
        return sum(
            slippage_dollars_per_side(
                sl_enabled, sl_mode, sl_flat, sl_mult,
                sp, mk, leg.strike, leg.opt_type == "CE", leg.qty, ts,
            )
            for leg, mk in zip(legs, marks)
        )

    def exit_brk_at(ts: int, marks: list[float]) -> float:
        if not br_enabled:
            return 0.0
        sp = get_spot_at_or_before(ts) or spot_at_exit
        return sum(
            compute_brokerage_one_side(sp, mk, leg.qty, br_rate, br_ref)
            for leg, mk in zip(legs, marks)
        )

    peak_exit_slip   = exit_slip_at(max_mtm_ts, max_marks)
    peak_exit_brk    = exit_brk_at(max_mtm_ts, max_marks)
    trough_exit_slip = exit_slip_at(min_mtm_ts, min_marks)
    trough_exit_brk  = exit_brk_at(min_mtm_ts, min_marks)

    # Bake entry slip into max/min MTM (matches per-job backtester convention).
    max_mtm -= entry_slip_total
    min_mtm -= entry_slip_total
    max_pnl_net = max_mtm - peak_exit_slip - entry_brk_total - peak_exit_brk
    min_pnl_net = min_mtm - trough_exit_slip - entry_brk_total - trough_exit_brk

    # Apply entry-slip baking to recorded snapshots' net P&L.
    if record_snapshots:
        for snap in snapshots:
            snap.pnl_net_usd = snap.pnl_gross_usd - entry_slip_total

    # Margin (29-scenario portfolio stress at entry).
    margin_used: Optional[float] = None
    if margin_compute:
        try:
            m_legs: list[MarginLeg] = []
            for leg, em in zip(legs, entry_marks):
                e_ts = _expiry_unix(leg.expiry)
                T = max(0.0001, (e_ts - entry_ts) / (365.0 * 86400.0))
                flag = "call" if leg.opt_type == "CE" else "put"
                iv = implied_vol(em, spot_at_entry, leg.strike, T, 0.0, flag) or 0.0
                if iv <= 0:
                    continue
                m_legs.append(MarginLeg(
                    strike=float(leg.strike),
                    is_call=(leg.opt_type == "CE"),
                    is_buy=(leg.action == "BUY"),
                    qty=int(leg.qty),
                    current_price=float(em) * CONTRACT_VALUE,
                    iv=float(iv),
                    T=float(T),
                    forward=0.0,
                ))
            if m_legs:
                mr = compute_portfolio_margin(m_legs, spot_at_entry)
                if mr is not None:
                    margin_used = round(mr.portfolio_margin, 2)
        except Exception:
            margin_used = None

    exit_reason = (
        "LegSL"      if sl_triggered      else
        "Settlement" if settlement_capped else
        "TimeStop"
    )

    return TradeResult(
        entry_ts=entry_ts,
        spot_at_entry=spot_at_entry,
        entry_marks=entry_marks,
        entry_deltas=entry_deltas,
        entry_ivs=entry_ivs,
        exit_ts=actual_exit_ts,
        exit_reason=exit_reason,
        exit_marks=exit_marks,
        spot_at_exit=spot_at_exit,
        breaching_leg_idx=breaching_leg_idx,
        gross_pnl=gross,
        entry_slip=entry_slip_total,
        exit_slip=exit_slip_total,
        entry_brk=entry_brk_total,
        exit_brk=exit_brk_total,
        net_pnl=net_pnl,
        max_mtm=max_mtm, max_mtm_ts=max_mtm_ts,
        max_marks=max_marks, max_leg_deltas=max_leg_deltas,
        min_mtm=min_mtm, min_mtm_ts=min_mtm_ts,
        min_marks=min_marks, min_leg_deltas=min_leg_deltas,
        peak_exit_slip=peak_exit_slip, peak_exit_brk=peak_exit_brk,
        trough_exit_slip=trough_exit_slip, trough_exit_brk=trough_exit_brk,
        max_pnl_net=max_pnl_net, min_pnl_net=min_pnl_net,
        margin_used=margin_used,
        snapshots=snapshots if record_snapshots else None,
    )

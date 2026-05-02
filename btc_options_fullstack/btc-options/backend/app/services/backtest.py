"""
Multi-day options strategy backtester — pure logic, no FastAPI deps.

Phase 1: weekday filter + entry-time-of-day + EOD forced close. No SL/TG/
trail/per-leg/re-entry yet (those land in Phase 3).

Day loop:
  for each date d in [start_date, end_date]:
      if weekday filtered out: skip
      resolve expiry per leg's expiry_selector at d
      resolve strike per leg's strike_offset (ATM-relative at entry)
      load each leg's intraday bar series [entry_ts, eod_ts]
      enter at entry_ts marks
      walk bar-by-bar updating MTM
      exit at forced_exit_ts (EOD)
      compute slippage + brokerage on entry+exit
      record trade

Aggregate: equity curve, daily P&L bars, win rate, Sharpe, max DD, expectancy.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Optional

from . import backtest_jobs
from .costs import (
    CONTRACT_VALUE,
    compute_brokerage_one_side,
    slippage_dollars_per_side,
)
from .option_data import (
    atm_iv_at,
    get_mark_at_or_before,
    get_spot_at_or_before,
    list_expiries_for,
    load_leg_series,
    resolve_expiry,
    strike_at_offset,
    strike_for_strike_type,
    strike_for_closest_premium,
    strike_for_closest_delta,
    strike_for_closest_delta_below,
    strikes_pool_for_delta_below,
    strike_for_highest_oi,
    atm_iv_at,
    hv_at,
)
from .margin_v2 import MarginLeg, compute_portfolio_margin
from app.core.greeks import implied_vol, compute_greeks


IST_OFFSET_SEC = int(5.5 * 3600)


# ── IST date/time helpers ─────────────────────────────────────────────────────

def _ist_date_to_unix(d: date, time_str: str) -> int:
    """date + 'HH:MM' IST → unix UTC seconds."""
    h, m = (int(x) for x in time_str.split(":"))
    dt_ist = datetime(d.year, d.month, d.day, h, m, tzinfo=timezone.utc) - timedelta(
        hours=5, minutes=30,
    )
    # Above gives the UTC equivalent of (d, h:m) IST.
    return int(dt_ist.timestamp())


def _iter_dates(start_iso: str, end_iso: str):
    s = date.fromisoformat(start_iso)
    e = date.fromisoformat(end_iso)
    cur = s
    while cur <= e:
        yield cur
        cur += timedelta(days=1)


# ── Stats helpers ─────────────────────────────────────────────────────────────

def _compute_summary(trades: list[dict]) -> dict:
    real_trades = [t for t in trades if not t.get("skipped")]
    if not real_trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "breakevens": 0,
            "win_rate_pct": 0.0,
            "gross_pnl": 0.0, "total_costs": 0.0, "net_pnl": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0,
            "total_wins_pnl": 0.0, "total_losses_pnl": 0.0,
            "expectancy": 0.0,
            "sharpe_daily": 0.0,
            "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
            "max_dd_peak_date": None, "max_dd_trough_date": None,
            "avg_margin_used": 0.0, "return_on_margin_pct": 0.0,
        }

    losses = [t for t in real_trades if t.get("exit_reason") == "LegSL" and t["net_pnl"] < -0.01]
    wins   = [t for t in real_trades if not (t.get("exit_reason") == "LegSL" and t["net_pnl"] < -0.01)]
    breakevens = 0

    gross  = sum(t["gross_pnl"] for t in real_trades)
    costs  = sum(t["slippage_cost"] + t["brokerage_cost"] for t in real_trades)
    net    = sum(t["net_pnl"] for t in real_trades)
    total_wins_pnl  = sum(t["net_pnl"] for t in wins)
    total_losses_pnl = sum(t["net_pnl"] for t in losses)
    avg_win  = total_wins_pnl  / len(wins)   if wins   else 0.0
    avg_loss = total_losses_pnl / len(losses) if losses else 0.0
    expectancy = net / len(real_trades)

    # Daily P&L for Sharpe/DD
    by_day: dict[str, float] = {}
    for t in real_trades:
        by_day[t["date"]] = by_day.get(t["date"], 0.0) + t["net_pnl"]
    daily_pnls = list(by_day.values())
    if len(daily_pnls) >= 2:
        mean = sum(daily_pnls) / len(daily_pnls)
        var = sum((p - mean) ** 2 for p in daily_pnls) / (len(daily_pnls) - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd) * math.sqrt(252) if sd > 1e-9 else 0.0
    else:
        sharpe = 0.0

    # Cumulative + max drawdown
    sorted_dates = sorted(by_day.keys())
    cum = 0.0
    peak = 0.0
    peak_date = sorted_dates[0]
    max_dd = 0.0
    max_dd_peak = sorted_dates[0]
    max_dd_trough = sorted_dates[0]
    for d in sorted_dates:
        cum += by_day[d]
        if cum > peak:
            peak = cum
            peak_date = d
        dd = peak - cum  # in dollars (positive = drawdown)
        if dd > max_dd:
            max_dd = dd
            max_dd_peak = peak_date
            max_dd_trough = d
    max_dd_pct = (max_dd / peak * 100) if peak > 0 else 0.0

    margins = [t["margin_used"] for t in real_trades if t.get("margin_used") is not None]
    avg_margin = sum(margins) / len(margins) if margins else 0.0
    rom = (net / avg_margin * 100) if avg_margin > 0 else 0.0

    # Per-trade Max/Min MTM aggregates — average + most-extreme across all trades.
    # max_mtm/min_mtm have entry slip baked in; max_pnl_net/min_pnl_net additionally
    # subtract entry brk + peak-time exit slip + peak-time exit brk.
    n = len(real_trades)
    avg_max_mtm     = sum(t["max_mtm"]      for t in real_trades) / n
    avg_max_pnl_net = sum(t["max_pnl_net"]  for t in real_trades) / n
    avg_min_mtm     = sum(t["min_mtm"]      for t in real_trades) / n
    avg_min_pnl_net = sum(t["min_pnl_net"]  for t in real_trades) / n
    best_trade   = max(real_trades, key=lambda t: t["max_mtm"])
    worst_trade  = min(real_trades, key=lambda t: t["min_mtm"])
    best_net_tr  = max(real_trades, key=lambda t: t["max_pnl_net"])
    worst_net_tr = min(real_trades, key=lambda t: t["min_pnl_net"])

    return {
        "total_trades": len(real_trades),
        "wins": len(wins), "losses": len(losses), "breakevens": breakevens,
        "win_rate_pct": round(len(wins) / len(real_trades) * 100, 2),
        "gross_pnl": round(gross, 2),
        "total_costs": round(costs, 2),
        "net_pnl": round(net, 2),
        "avg_win":  round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "total_wins_pnl":   round(total_wins_pnl, 2),
        "total_losses_pnl": round(total_losses_pnl, 2),
        "expectancy": round(expectancy, 2),
        "sharpe_daily": round(sharpe, 3),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "max_dd_peak_date":   max_dd_peak,
        "max_dd_trough_date": max_dd_trough,
        "avg_margin_used": round(avg_margin, 2),
        "return_on_margin_pct": round(rom, 2),
        "avg_max_mtm":     round(avg_max_mtm, 2),
        "avg_max_pnl_net": round(avg_max_pnl_net, 2),
        "avg_min_mtm":     round(avg_min_mtm, 2),
        "avg_min_pnl_net": round(avg_min_pnl_net, 2),
        "best_max_mtm":     round(best_trade["max_mtm"], 2),
        "best_max_mtm_date": best_trade["date"],
        "best_max_pnl_net":  round(best_net_tr["max_pnl_net"], 2),
        "best_max_pnl_net_date": best_net_tr["date"],
        "worst_min_mtm":     round(worst_trade["min_mtm"], 2),
        "worst_min_mtm_date": worst_trade["date"],
        "worst_min_pnl_net":  round(worst_net_tr["min_pnl_net"], 2),
        "worst_min_pnl_net_date": worst_net_tr["date"],
    }


def _compute_equity_curve(trades: list[dict]) -> list[dict]:
    by_day: dict[str, float] = {}
    for t in trades:
        if t.get("skipped"):
            continue
        by_day[t["date"]] = by_day.get(t["date"], 0.0) + t["net_pnl"]
    out = []
    cum = 0.0
    for d in sorted(by_day.keys()):
        cum += by_day[d]
        out.append({"date": d, "cum_pnl": round(cum, 2),
                    "daily_pnl": round(by_day[d], 2)})
    return out


# ── Day-level trade simulation ────────────────────────────────────────────────

def _simulate_day(
    d: date,
    params: dict,
) -> dict:
    """Run one trading day. Returns a trade dict (possibly skipped)."""
    weekday_mask = params["weekday_mask"]
    if d.weekday() not in weekday_mask:
        return _skipped(d, "weekday_filter")

    entry_time_ist  = params["entry_time_ist"]
    forced_exit_ist = params["forced_exit_time_ist"]
    timeframe       = params.get("timeframe", "5m")
    exit_day_offset = int(params.get("exit_day_offset", 0))

    entry_ts = _ist_date_to_unix(d, entry_time_ist)
    exit_d   = d + timedelta(days=max(0, exit_day_offset))
    exit_ts  = _ist_date_to_unix(exit_d, forced_exit_ist)

    spot_at_entry = get_spot_at_or_before(entry_ts)
    if spot_at_entry <= 0:
        return _skipped(d, "no_spot_data", entry_ts=entry_ts)

    # Resolve legs: expiry per selector, then strike via the criteria mode
    resolved_legs: list[dict] = []
    # Pools for Delta ≤ Match legs — filled during resolution, used for cross-leg
    # premium alignment after entry marks are known.
    prem_match_pools: dict[int, list[tuple[int, float, float]]] = {}
    # Data for Delta ≤ Align legs — initial delta per leg, used to re-match the
    # lower-delta leg to the higher-delta leg's resolved value.
    delta_align_data: dict[int, dict] = {}
    for tmpl in params["legs"]:
        expiry = resolve_expiry(
            d.isoformat(), entry_time_ist,
            tmpl["expiry_selector"], tmpl.get("expiry_offset", 0),
        )
        if not expiry:
            return _skipped(d, "no_expiry_for_selector", entry_ts=entry_ts)

        criteria = tmpl.get("strike_criteria")
        opt_type = tmpl["type"]
        if criteria == "strike_type":
            level = tmpl.get("strike_level") or "ATM"
            strike = strike_for_strike_type(entry_ts, expiry, opt_type, level)
            skip_reason = f"no_strike_for_{level}"
        elif criteria == "closest_premium":
            target = float(tmpl.get("strike_value") or 0)
            strike = strike_for_closest_premium(entry_ts, expiry, opt_type, target)
            skip_reason = f"no_strike_for_premium_{target}"
        elif criteria == "closest_delta":
            target = float(tmpl.get("strike_value") or 0)
            strike = strike_for_closest_delta(entry_ts, expiry, opt_type, target)
            skip_reason = f"no_strike_for_delta_{target}"
        elif criteria == "closest_delta_below":
            target = float(tmpl.get("strike_value") or 0)
            strike = strike_for_closest_delta_below(entry_ts, expiry, opt_type, target)
            skip_reason = f"no_strike_for_delta_below_{target}"
        elif criteria == "closest_delta_prem_match":
            target = float(tmpl.get("strike_value") or 0)
            pool = strikes_pool_for_delta_below(entry_ts, expiry, opt_type, target)
            strike = pool[0][0] if pool else 0
            if pool:
                prem_match_pools[len(resolved_legs)] = pool
            skip_reason = f"no_strike_for_delta_prem_match_{target}"
        elif criteria == "closest_delta_align":
            target = float(tmpl.get("strike_value") or 0)
            pool = strikes_pool_for_delta_below(entry_ts, expiry, opt_type, target)
            strike = pool[0][0] if pool else 0
            if pool:
                delta_align_data[len(resolved_legs)] = {
                    "initial_delta": pool[0][1],
                    "expiry": expiry,
                    "opt_type": opt_type,
                }
            skip_reason = f"no_strike_for_delta_align_{target}"
        elif criteria == "highest_oi":
            target = float(tmpl.get("strike_value") or 0)
            strike = strike_for_highest_oi(entry_ts, expiry, opt_type, target)
            skip_reason = f"no_otm_strike_for_oi_delta_{target}"
        else:
            strike = strike_at_offset(entry_ts, expiry, int(tmpl.get("strike_offset", 0)))
            skip_reason = "no_strike_for_offset"

        if strike <= 0:
            return _skipped(d, skip_reason, entry_ts=entry_ts)
        resolved_legs.append({
            "strike": strike, "type": opt_type, "action": tmpl["action"],
            "qty": int(tmpl["qty"]), "expiry": expiry,
        })

    # Cap exit_ts at the earliest expiry settlement among the legs (12:00 UTC).
    # Multi-day positional held past expiry would have no market data.
    settlement_ts = min(
        int(datetime.strptime(leg["expiry"], "%Y-%m-%d")
            .replace(tzinfo=timezone.utc, hour=12).timestamp())
        for leg in resolved_legs
    )
    settlement_capped = exit_ts > settlement_ts
    if settlement_capped:
        exit_ts = settlement_ts

    # Use the EXACT mark_close at entry_ts (matches what /option-chain returns
    # for the same timestamp). Falls back to the latest mark before entry_ts
    # when an exact-second row is missing.
    entry_marks: list[float] = []
    exit_marks: list[float] = []
    actual_exit_ts = exit_ts
    for leg in resolved_legs:
        em, em_ts = get_mark_at_or_before(
            leg["expiry"], leg["strike"], leg["type"], entry_ts,
        )
        if em <= 0:
            return _skipped(d, f"no_data_leg_{leg['type']}_{leg['strike']}",
                            entry_ts=entry_ts)
        xm, xm_ts = get_mark_at_or_before(
            leg["expiry"], leg["strike"], leg["type"], exit_ts,
        )
        if xm <= 0:
            return _skipped(d, f"no_exit_for_leg_{leg['type']}_{leg['strike']}",
                            entry_ts=entry_ts)
        entry_marks.append(em)
        exit_marks.append(xm)
        actual_exit_ts = max(actual_exit_ts, xm_ts)

    # Delta ≤ Match: re-pick each qualifying leg's strike so its premium is
    # closest to the minimum entry premium across all such legs.
    if prem_match_pools:
        ref_prem = min(entry_marks[i] for i in prem_match_pools)
        for i, pool in prem_match_pools.items():
            best = min(pool, key=lambda x: abs(x[2] - ref_prem))
            if best[0] != resolved_legs[i]["strike"]:
                resolved_legs[i]["strike"] = best[0]
                leg = resolved_legs[i]
                em2, _ = get_mark_at_or_before(leg["expiry"], best[0], leg["type"], entry_ts)
                if em2 <= 0:
                    return _skipped(d, f"no_data_leg_{leg['type']}_{best[0]}", entry_ts=entry_ts)
                xm2, _ = get_mark_at_or_before(leg["expiry"], best[0], leg["type"], exit_ts)
                if xm2 <= 0:
                    return _skipped(d, f"no_exit_for_leg_{leg['type']}_{best[0]}", entry_ts=entry_ts)
                entry_marks[i] = em2
                exit_marks[i] = xm2

    # Delta ≤ Align: find the highest resolved delta across all align legs,
    # then re-match any leg with a lower delta using that value as the new target.
    if delta_align_data:
        ref_delta = max(v["initial_delta"] for v in delta_align_data.values())
        for i, data in delta_align_data.items():
            if data["initial_delta"] < ref_delta:
                new_strike = strike_for_closest_delta_below(
                    entry_ts, data["expiry"], data["opt_type"], ref_delta,
                )
                if new_strike > 0 and new_strike != resolved_legs[i]["strike"]:
                    resolved_legs[i]["strike"] = new_strike
                    leg = resolved_legs[i]
                    em2, _ = get_mark_at_or_before(leg["expiry"], new_strike, leg["type"], entry_ts)
                    if em2 <= 0:
                        return _skipped(d, f"no_data_leg_{leg['type']}_{new_strike}", entry_ts=entry_ts)
                    xm2, _ = get_mark_at_or_before(leg["expiry"], new_strike, leg["type"], exit_ts)
                    if xm2 <= 0:
                        return _skipped(d, f"no_exit_for_leg_{leg['type']}_{new_strike}", entry_ts=entry_ts)
                    entry_marks[i] = em2
                    exit_marks[i] = xm2

    spot_at_exit = get_spot_at_or_before(exit_ts)
    if spot_at_exit <= 0:
        spot_at_exit = spot_at_entry

    # Per-leg entry delta + IV via BS (best-effort; 0.0 on failure).
    entry_deltas: list[float] = []
    entry_ivs: list[float] = []
    for leg, em in zip(resolved_legs, entry_marks):
        try:
            exp_ts = int(datetime.strptime(leg["expiry"], "%Y-%m-%d")
                         .replace(tzinfo=timezone.utc, hour=12).timestamp())
            T_e = max(0.0001, (exp_ts - entry_ts) / (365 * 24 * 3600))
            flag = "call" if leg["type"] == "CE" else "put"
            iv_e = implied_vol(em, spot_at_entry, leg["strike"], T_e, 0.0, flag) or 0.0
            dv = compute_greeks(spot_at_entry, leg["strike"], T_e, 0.0, iv_e, flag).delta if iv_e > 0 else 0.0
            entry_deltas.append(round(dv, 4))
            entry_ivs.append(round(iv_e * 100, 2))  # decimal → %
        except Exception:
            entry_deltas.append(0.0)
            entry_ivs.append(0.0)

    # ATM IV at entry — uses the first leg's expiry (or earliest if differing).
    atm_iv_entry = 0.0
    try:
        atm_expiry = min(leg["expiry"] for leg in resolved_legs)
        atm_iv_entry = round(atm_iv_at(entry_ts, atm_expiry) * 100, 2)
    except Exception:
        pass

    # HV (realized volatility) at entry — 7-day rolling window, 5m bars.
    hv_entry = 0.0
    try:
        hv_entry = hv_at(entry_ts, window_days=7, bars_per_day=288)
    except Exception:
        pass

    # Gross P&L: per-leg (exit_mark - entry_mark) × qty × CV × dir
    gross = 0.0
    leg_fills: list[dict] = []
    for leg, em, xm, ed, eiv in zip(resolved_legs, entry_marks, exit_marks, entry_deltas, entry_ivs):
        direction = 1 if leg["action"] == "BUY" else -1
        leg_pnl = (xm - em) * leg["qty"] * CONTRACT_VALUE * direction
        gross += leg_pnl
        leg_fills.append({
            "expiry": leg["expiry"], "strike": leg["strike"], "type": leg["type"],
            "action": leg["action"], "qty": leg["qty"],
            "entry_mark": round(em, 4), "exit_mark": round(xm, 4),
            "leg_pnl": round(leg_pnl, 4),
            "entry_delta": ed,
            "entry_iv": eiv,
        })

    # ── Per-leg SL: compute trigger prices (pct/points) and delta thresholds ──
    leg_sl_configs = [tmpl.get("leg_sl") for tmpl in params["legs"]]
    leg_sl_prices: list[Optional[float]] = []   # pct/points → static price trigger
    leg_sl_deltas: list[Optional[float]] = []   # delta type → abs-delta threshold
    for leg, em, sl_cfg in zip(resolved_legs, entry_marks, leg_sl_configs):
        if not sl_cfg or float(sl_cfg.get("value", 0)) <= 0:
            leg_sl_prices.append(None)
            leg_sl_deltas.append(None)
            continue
        sl_type = sl_cfg.get("type", "pct")
        sl_val  = float(sl_cfg["value"])
        if sl_type == "delta":
            leg_sl_prices.append(None)
            leg_sl_deltas.append(abs(sl_val))
            continue
        if leg["action"] == "SELL":
            trigger = em * (1 + sl_val / 100) if sl_type == "pct" else em + sl_val
        else:
            trigger = max(0.0, em * (1 - sl_val / 100) if sl_type == "pct" else em - sl_val)
        leg_sl_prices.append(trigger)
        leg_sl_deltas.append(None)
    any_leg_sl_price = any(p is not None for p in leg_sl_prices)
    any_leg_sl_delta = any(d is not None for d in leg_sl_deltas)
    any_leg_sl = any_leg_sl_price or any_leg_sl_delta

    # Cache leg expiry timestamps + flags for fast delta computation per bar
    leg_expiry_ts = [
        int(datetime.strptime(leg["expiry"], "%Y-%m-%d")
            .replace(tzinfo=timezone.utc, hour=12).timestamp())
        for leg in resolved_legs
    ]
    leg_flags = ["call" if leg["type"] == "CE" else "put" for leg in resolved_legs]

    def _deltas_at(t: int, marks: list[float]) -> list[float]:
        """Compute per-leg BS delta at time t using current marks (best-effort)."""
        sp = get_spot_at_or_before(t) or spot_at_entry
        out: list[float] = []
        for i, mk in enumerate(marks):
            try:
                Tt = max(0.0001, (leg_expiry_ts[i] - t) / (365 * 24 * 3600))
                iv = implied_vol(mk, sp, resolved_legs[i]["strike"], Tt, 0.0, leg_flags[i]) or 0.0
                if iv > 0:
                    g = compute_greeks(sp, resolved_legs[i]["strike"], Tt, 0.0, iv, leg_flags[i])
                    out.append(g.delta)
                else:
                    out.append(0.0)
            except Exception:
                out.append(0.0)
        return out

    # ── Max / min MTM during the hold (sampled at 1m bars from entry → exit).
    # Walks the union timeline of all legs' bars; missing bars use the most
    # recent prior mark (forward-fill). Tracked GROSS (pre-cost). Also checks
    # per-leg SL; if any leg hits its SL all legs exit at that bar's marks.
    max_mtm: float = -math.inf
    min_mtm: float =  math.inf
    max_mtm_ts: int = actual_exit_ts
    min_mtm_ts: int = actual_exit_ts
    max_marks: list[float] = list(exit_marks)
    min_marks: list[float] = list(exit_marks)
    max_leg_deltas: list[float] = list(entry_deltas)
    min_leg_deltas: list[float] = list(entry_deltas)
    sl_triggered = False
    sl_exit_marks: list[float] = list(exit_marks)
    sl_exit_ts: int = actual_exit_ts
    try:
        leg_series = [
            load_leg_series(leg["expiry"], leg["strike"], leg["type"],
                             entry_ts, actual_exit_ts, "1m")
            for leg in resolved_legs
        ]
        ts_union = sorted({
            int(bar["time"]) for series in leg_series for bar in series
            if entry_ts <= int(bar["time"]) <= actual_exit_ts
        })
        if ts_union:
            idx = [0] * len(resolved_legs)
            last_mark = list(entry_marks)
            for t in ts_union:
                for i, series in enumerate(leg_series):
                    while idx[i] < len(series) and int(series[idx[i]]["time"]) <= t:
                        last_mark[i] = float(series[idx[i]]["close"])
                        idx[i] += 1
                pnl_t = 0.0
                for leg, em, mk in zip(resolved_legs, entry_marks, last_mark):
                    direction = 1 if leg["action"] == "BUY" else -1
                    pnl_t += (mk - em) * leg["qty"] * CONTRACT_VALUE * direction

                # Lazy-compute deltas at this bar — used by max/min tracking
                # and by delta-based SL. Cached per bar to avoid double work.
                cur_deltas: Optional[list[float]] = None

                if pnl_t > max_mtm:
                    max_mtm = pnl_t; max_mtm_ts = t; max_marks = list(last_mark)
                    if cur_deltas is None:
                        cur_deltas = _deltas_at(t, last_mark)
                    max_leg_deltas = list(cur_deltas)
                if pnl_t < min_mtm:
                    min_mtm = pnl_t; min_mtm_ts = t; min_marks = list(last_mark)
                    if cur_deltas is None:
                        cur_deltas = _deltas_at(t, last_mark)
                    min_leg_deltas = list(cur_deltas)

                if any_leg_sl:
                    # Price-based SL check
                    if any_leg_sl_price:
                        for i, (leg, sl_price) in enumerate(zip(resolved_legs, leg_sl_prices)):
                            if sl_price is None:
                                continue
                            hit = (
                                (leg["action"] == "SELL" and last_mark[i] >= sl_price) or
                                (leg["action"] == "BUY"  and last_mark[i] <= sl_price)
                            )
                            if hit:
                                sl_triggered  = True
                                sl_exit_marks = list(last_mark)
                                sl_exit_ts    = t
                                break
                    # Delta-based SL: abs(current delta) crosses the threshold
                    if not sl_triggered and any_leg_sl_delta:
                        if cur_deltas is None:
                            cur_deltas = _deltas_at(t, last_mark)
                        for i, sl_d in enumerate(leg_sl_deltas):
                            if sl_d is None:
                                continue
                            if abs(cur_deltas[i]) >= sl_d:
                                sl_triggered  = True
                                sl_exit_marks = list(last_mark)
                                sl_exit_ts    = t
                                break
                    if sl_triggered:
                        break
    except Exception:
        pass

    # Add the actual exit as a max/min candidate, then override if SL triggered.
    _fin_marks = sl_exit_marks if sl_triggered else exit_marks
    _fin_ts    = sl_exit_ts    if sl_triggered else actual_exit_ts
    _fin_pnl   = sum(
        (mk - em) * leg["qty"] * CONTRACT_VALUE * (1 if leg["action"] == "BUY" else -1)
        for leg, em, mk in zip(resolved_legs, entry_marks, _fin_marks)
    )
    if _fin_pnl > max_mtm or max_mtm == -math.inf:
        max_mtm = _fin_pnl; max_mtm_ts = _fin_ts; max_marks = list(_fin_marks)
        max_leg_deltas = _deltas_at(_fin_ts, _fin_marks)
    if _fin_pnl < min_mtm or min_mtm == math.inf:
        min_mtm = _fin_pnl; min_mtm_ts = _fin_ts; min_marks = list(_fin_marks)
        min_leg_deltas = _deltas_at(_fin_ts, _fin_marks)

    if sl_triggered:
        exit_marks     = sl_exit_marks
        actual_exit_ts = sl_exit_ts
        spot_at_exit   = get_spot_at_or_before(sl_exit_ts) or spot_at_entry
        gross = 0.0
        leg_fills = []
        for leg, em, xm, ed, eiv in zip(resolved_legs, entry_marks, exit_marks, entry_deltas, entry_ivs):
            direction = 1 if leg["action"] == "BUY" else -1
            leg_pnl = (xm - em) * leg["qty"] * CONTRACT_VALUE * direction
            gross += leg_pnl
            leg_fills.append({
                "expiry": leg["expiry"], "strike": leg["strike"], "type": leg["type"],
                "action": leg["action"], "qty": leg["qty"],
                "entry_mark": round(em, 4), "exit_mark": round(xm, 4),
                "leg_pnl": round(leg_pnl, 4),
                "entry_delta": ed,
                "entry_iv": eiv,
            })

    exit_reason_str = (
        "LegSL"      if sl_triggered      else
        "Settlement" if settlement_capped else
        "EOD"        if exit_day_offset == 0 else
        f"+{exit_day_offset}d"
    )

    # Costs — mirrors the Historical StrategyPanel split:
    #   entry slip @ entry conditions  +  exit slip @ exit conditions
    #   entry brk  @ entry conditions  +  exit brk  @ exit conditions
    # Plus peak-time exit slip + exit brk (used by max_pnl_net / min_pnl_net).
    sl_cfg = params.get("slippage", {})
    br_cfg = params.get("brokerage", {})
    entry_slip_total = 0.0
    exit_slip_total = 0.0
    entry_brk_total = 0.0
    exit_brk_total = 0.0
    br_rate = br_cfg.get("rate", "offer")
    br_ref  = br_cfg.get("referral", False)
    sl_enabled = sl_cfg.get("enabled", False)
    sl_mode    = sl_cfg.get("mode", "smart")
    sl_flat    = sl_cfg.get("flat_value", 5.0)
    sl_mult    = sl_cfg.get("mult", 1.0)
    for leg, em, xm in zip(resolved_legs, entry_marks, exit_marks):
        entry_slip_total += slippage_dollars_per_side(
            sl_enabled, sl_mode, sl_flat, sl_mult,
            spot_at_entry, em, leg["strike"], leg["type"] == "CE",
            leg["qty"], entry_ts,
        )
        exit_slip_total += slippage_dollars_per_side(
            sl_enabled, sl_mode, sl_flat, sl_mult,
            spot_at_exit, xm, leg["strike"], leg["type"] == "CE",
            leg["qty"], actual_exit_ts,
        )
        if br_cfg.get("enabled"):
            entry_brk_total += compute_brokerage_one_side(
                spot_at_entry, em, leg["qty"], br_rate, br_ref,
            )
            exit_brk_total += compute_brokerage_one_side(
                spot_at_exit, xm, leg["qty"], br_rate, br_ref,
            )
    slip_total = entry_slip_total + exit_slip_total
    brk_total = entry_brk_total + exit_brk_total
    net_pnl = gross - slip_total - brk_total

    # Peak-time costs — exit slip + exit brk recomputed at max-MTM marks/spot/hour.
    # Used so max_pnl_net = "what you'd net if you closed at peak", with the
    # right exit costs for that moment (different from end-of-trade marks).
    def _exit_slip_at(ts: int, marks: list[float]) -> float:
        if not sl_enabled:
            return 0.0
        sp = get_spot_at_or_before(ts) or spot_at_exit
        return sum(
            slippage_dollars_per_side(
                sl_enabled, sl_mode, sl_flat, sl_mult,
                sp, mk, leg["strike"], leg["type"] == "CE",
                leg["qty"], ts,
            )
            for leg, mk in zip(resolved_legs, marks)
        )

    def _exit_brk_at(ts: int, marks: list[float]) -> float:
        if not br_cfg.get("enabled"):
            return 0.0
        sp = get_spot_at_or_before(ts) or spot_at_exit
        return sum(
            compute_brokerage_one_side(sp, mk, leg["qty"], br_rate, br_ref)
            for leg, mk in zip(resolved_legs, marks)
        )

    peak_exit_slip = _exit_slip_at(max_mtm_ts, max_marks)
    peak_exit_brk  = _exit_brk_at(max_mtm_ts, max_marks)
    trough_exit_slip = _exit_slip_at(min_mtm_ts, min_marks)
    trough_exit_brk  = _exit_brk_at(min_mtm_ts, min_marks)

    # Bake entry slip into max/min MTM so the convention matches the historical
    # StrategyPanel (chart curve has entry slip already deducted; brokerage and
    # exit slip live in the costs panel). User-confirmed: "MTM has entry slip,
    # I'm ok with it" (2026-04-30).
    max_mtm = max_mtm - entry_slip_total
    min_mtm = min_mtm - entry_slip_total
    max_pnl_net = max_mtm - peak_exit_slip - entry_brk_total - peak_exit_brk
    min_pnl_net = min_mtm - trough_exit_slip - entry_brk_total - trough_exit_brk

    # Margin estimate at entry (portfolio-level scenario stress, scripts/margin_engine_v2.py)
    margin_used: Optional[float] = None
    try:
        m_legs: list[MarginLeg] = []
        for leg, em in zip(resolved_legs, entry_marks):
            expiry_ts_utc = int(datetime.strptime(leg["expiry"], "%Y-%m-%d")
                                .replace(tzinfo=timezone.utc, hour=12).timestamp())
            T = max(0.0001, (expiry_ts_utc - entry_ts) / (365 * 24 * 3600))
            flag = "call" if leg["type"] == "CE" else "put"
            iv = implied_vol(em, spot_at_entry, leg["strike"], T, 0.0, flag) or 0.0
            if iv <= 0:
                continue
            m_legs.append(MarginLeg(
                strike=float(leg["strike"]),
                is_call=(leg["type"] == "CE"),
                is_buy=(leg["action"] == "BUY"),
                qty=int(leg["qty"]),
                current_price=float(em) * CONTRACT_VALUE,  # USDT per contract
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

    # Strangle analytics — credit_pct, decomposition, z-scores, quality_score.
    # Mirrors frontend/src/utils/strangleAnalytics.ts exactly.
    analytics_fields: dict = {}
    try:
        from app.services.strangle_analytics import compute_trade_analytics, TradeLeg
        ta_legs: list[TradeLeg] = []
        for leg, em in zip(resolved_legs, entry_marks):
            expiry_ts_utc = int(datetime.strptime(leg["expiry"], "%Y-%m-%d")
                                .replace(tzinfo=timezone.utc, hour=12).timestamp())
            T = max(0.0001, (expiry_ts_utc - entry_ts) / (365 * 24 * 3600))
            flag = "call" if leg["type"] == "CE" else "put"
            iv_e = implied_vol(em, spot_at_entry, leg["strike"], T, 0.0, flag) or 0.0
            if iv_e <= 0:
                continue
            try:
                from app.core.greeks import compute_greeks
                g = compute_greeks(spot_at_entry, leg["strike"], T, 0.0, iv_e, flag)
                ta_legs.append(TradeLeg(
                    type=leg["type"], side=leg["action"],
                    strike=float(leg["strike"]), qty=int(leg["qty"]),
                    mark=float(em), iv=float(iv_e),
                    delta=float(g.delta), gamma=float(g.gamma),
                    vega=float(g.vega), theta=float(g.theta),
                ))
            except Exception:
                continue
        if ta_legs:
            # All legs share the same expiry in this backtest design
            exp_str = resolved_legs[0]["expiry"]
            exp_ts = int(datetime.strptime(exp_str, "%Y-%m-%d")
                         .replace(tzinfo=timezone.utc, hour=12).timestamp())
            dte = (exp_ts - entry_ts) / 86400.0
            analytics_fields = compute_trade_analytics(
                ta_legs, spot_at_entry, dte, entry_ts, exp_ts,
            )
    except Exception:
        analytics_fields = {}

    return {
        "date":            d.isoformat(),
        "entry_time":      _fmt_ist(entry_ts),
        "exit_time":       _fmt_ist(actual_exit_ts),
        "exit_reason":     exit_reason_str,
        "spot_at_entry":   round(spot_at_entry, 2),
        "spot_at_exit":    round(spot_at_exit, 2),
        "legs":            leg_fills,
        "gross_pnl":       round(gross, 4),
        "slippage_cost":   round(slip_total, 4),
        "brokerage_cost":  round(brk_total, 4),
        "entry_slip":      round(entry_slip_total, 4),
        "exit_slip":       round(exit_slip_total, 4),
        "peak_exit_slip":  round(peak_exit_slip, 4),
        "entry_brk":       round(entry_brk_total, 4),
        "exit_brk":        round(exit_brk_total, 4),
        "peak_exit_brk":   round(peak_exit_brk, 4),
        "net_pnl":         round(net_pnl, 4),
        "margin_used":     margin_used,
        "max_mtm":         round(max_mtm, 4),
        "max_mtm_time":    _fmt_ist(max_mtm_ts),
        "max_pnl_net":     round(max_pnl_net, 4),
        "max_leg_marks":   [
            {"type": leg["type"], "strike": leg["strike"], "mark": round(mk, 4)}
            for leg, mk in zip(resolved_legs, max_marks)
        ],
        "max_leg_deltas":  [
            {"type": leg["type"], "strike": leg["strike"], "delta": round(dv, 4)}
            for leg, dv in zip(resolved_legs, max_leg_deltas)
        ],
        "min_mtm":         round(min_mtm, 4),
        "min_mtm_time":    _fmt_ist(min_mtm_ts),
        "min_pnl_net":     round(min_pnl_net, 4),
        "min_leg_marks":   [
            {"type": leg["type"], "strike": leg["strike"], "mark": round(mk, 4)}
            for leg, mk in zip(resolved_legs, min_marks)
        ],
        "min_leg_deltas":  [
            {"type": leg["type"], "strike": leg["strike"], "delta": round(dv, 4)}
            for leg, dv in zip(resolved_legs, min_leg_deltas)
        ],
        "entry_atm_iv":    atm_iv_entry,
        "entry_hv":        hv_entry,
        "is_reentry":      False,
        "skipped":         False,
        # ── Strangle analytics fields (NaN/None when calibration unavailable) ──
        **analytics_fields,
    }


def _skipped(d: date, reason: str, entry_ts: Optional[int] = None) -> dict:
    return {
        "date": d.isoformat(),
        "entry_time": _fmt_ist(entry_ts) if entry_ts else None,
        "exit_time": None, "exit_reason": None,
        "spot_at_entry": None, "spot_at_exit": None,
        "legs": [], "gross_pnl": 0.0, "slippage_cost": 0.0,
        "brokerage_cost": 0.0,
        "entry_slip": 0.0, "exit_slip": 0.0, "peak_exit_slip": 0.0,
        "entry_brk": 0.0, "exit_brk": 0.0, "peak_exit_brk": 0.0,
        "net_pnl": 0.0, "margin_used": None,
        "max_mtm": 0.0, "max_mtm_time": None, "max_pnl_net": 0.0,
        "max_leg_marks": [], "max_leg_deltas": [],
        "min_mtm": 0.0, "min_mtm_time": None, "min_pnl_net": 0.0,
        "min_leg_marks": [], "min_leg_deltas": [],
        "entry_atm_iv": 0.0, "entry_hv": 0.0,
        "is_reentry": False, "skipped": True, "skip_reason": reason,
        # Analytics fields (None for skipped trades)
        "credit_pct": None, "credit_pct_normalized": None,
        "credit_per_day": None, "annualized_credit_pct": None,
        "fair_credit_at_ivp": None, "structural_credit_pct": None,
        "iv_regime_premium_pct": None, "excess_over_fair_pct": None,
        "z_credit_pct": None, "z_atm_iv": None,
        "quality_score": None, "size_band": None,
        "pattern": None, "ivp_atm_7d_90d_at_entry": None,
        "atm_iv_7d_at_entry": None,
        "theta_vega_ratio": None, "gamma_theta_dollar": None,
        "position_delta": None, "position_gamma": None,
        "position_vega": None, "position_theta": None,
        "calibration_source": None,
    }


def _fmt_ist(unix_sec: int) -> str:
    dt = datetime.fromtimestamp(int(unix_sec) + IST_OFFSET_SEC, tz=timezone.utc)
    return dt.strftime("%H:%M:%S IST")


# ── Public entry point ────────────────────────────────────────────────────────

def run_backtest(
    job_id: str,
    params: dict[str, Any],
    cancel_check: Optional[Callable[[], bool]] = None,
) -> dict:
    """Run the full backtest. Updates job progress as days complete."""
    dates = list(_iter_dates(params["start_date"], params["end_date"]))
    days_total = len(dates)
    backtest_jobs.update_progress(job_id, 0, days_total, None)

    trades: list[dict] = []
    for i, d in enumerate(dates, 1):
        if cancel_check and cancel_check():
            break
        try:
            t = _simulate_day(d, params)
        except Exception as e:
            t = _skipped(d, f"error: {e}")
        trades.append(t)
        backtest_jobs.update_progress(job_id, i, days_total, d.isoformat())

    summary = _compute_summary(trades)
    equity = _compute_equity_curve(trades)
    return {"summary": summary, "equity_curve": equity, "trades": trades}

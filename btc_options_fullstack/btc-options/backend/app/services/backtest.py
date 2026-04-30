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
)
from .margin_v2 import MarginLeg, compute_portfolio_margin
from app.core.greeks import implied_vol


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
            "avg_win": 0.0, "avg_loss": 0.0, "expectancy": 0.0,
            "sharpe_daily": 0.0,
            "max_drawdown": 0.0, "max_drawdown_pct": 0.0,
            "max_dd_peak_date": None, "max_dd_trough_date": None,
            "avg_margin_used": 0.0, "return_on_margin_pct": 0.0,
        }

    wins   = [t for t in real_trades if t["net_pnl"] >  0.01]
    losses = [t for t in real_trades if t["net_pnl"] < -0.01]
    breakevens = len(real_trades) - len(wins) - len(losses)

    gross  = sum(t["gross_pnl"] for t in real_trades)
    costs  = sum(t["slippage_cost"] + t["brokerage_cost"] for t in real_trades)
    net    = sum(t["net_pnl"] for t in real_trades)
    avg_win  = sum(t["net_pnl"] for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss = sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0.0
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

    return {
        "total_trades": len(real_trades),
        "wins": len(wins), "losses": len(losses), "breakevens": breakevens,
        "win_rate_pct": round(len(wins) / len(real_trades) * 100, 2),
        "gross_pnl": round(gross, 2),
        "total_costs": round(costs, 2),
        "net_pnl": round(net, 2),
        "avg_win":  round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "sharpe_daily": round(sharpe, 3),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "max_dd_peak_date":   max_dd_peak,
        "max_dd_trough_date": max_dd_trough,
        "avg_margin_used": round(avg_margin, 2),
        "return_on_margin_pct": round(rom, 2),
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

    spot_at_exit = get_spot_at_or_before(exit_ts)
    if spot_at_exit <= 0:
        spot_at_exit = spot_at_entry

    # Gross P&L: per-leg (exit_mark - entry_mark) × qty × CV × dir
    gross = 0.0
    leg_fills: list[dict] = []
    for leg, em, xm in zip(resolved_legs, entry_marks, exit_marks):
        direction = 1 if leg["action"] == "BUY" else -1
        leg_pnl = (xm - em) * leg["qty"] * CONTRACT_VALUE * direction
        gross += leg_pnl
        leg_fills.append({
            "expiry": leg["expiry"], "strike": leg["strike"], "type": leg["type"],
            "action": leg["action"], "qty": leg["qty"],
            "entry_mark": round(em, 4), "exit_mark": round(xm, 4),
            "leg_pnl": round(leg_pnl, 4),
        })

    # Max / min MTM during the hold (sampled at 1m bars from entry → exit).
    # Walks the union timeline of all legs' bars; missing bars use the most
    # recent prior mark (forward-fill). Tracked GROSS (pre-cost) since the
    # historical viewer's "Max P&L" stat is also gross. Also captures per-leg
    # marks at the peak/trough so we can recompute net P&L "if exited there".
    max_mtm: float = gross
    min_mtm: float = gross
    max_mtm_ts: int = actual_exit_ts
    min_mtm_ts: int = actual_exit_ts
    max_marks: list[float] = list(exit_marks)
    min_marks: list[float] = list(exit_marks)
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
            # Per-leg index pointer + last-seen mark for forward-fill.
            idx = [0] * len(resolved_legs)
            last_mark = list(entry_marks)
            for t in ts_union:
                # Advance each leg's pointer up to t, updating last_mark.
                for i, series in enumerate(leg_series):
                    while idx[i] < len(series) and int(series[idx[i]]["time"]) <= t:
                        last_mark[i] = float(series[idx[i]]["close"])
                        idx[i] += 1
                pnl_t = 0.0
                for leg, em, mk in zip(resolved_legs, entry_marks, last_mark):
                    direction = 1 if leg["action"] == "BUY" else -1
                    pnl_t += (mk - em) * leg["qty"] * CONTRACT_VALUE * direction
                if pnl_t > max_mtm:
                    max_mtm = pnl_t; max_mtm_ts = t; max_marks = list(last_mark)
                if pnl_t < min_mtm:
                    min_mtm = pnl_t; min_mtm_ts = t; min_marks = list(last_mark)
    except Exception:
        # Sampling is best-effort — fall back to entry/exit-only (already set above).
        pass

    # Costs — slippage matches the historical viewer's `slipRoundTripUsd`:
    # round-trip = 2 × entry-side slip (entry conditions only). Brokerage is
    # computed independently for entry & exit, same as the historical MTM panel.
    sl_cfg = params.get("slippage", {})
    br_cfg = params.get("brokerage", {})
    slip_total = 0.0
    entry_brk_total = 0.0
    exit_brk_total = 0.0
    br_rate = br_cfg.get("rate", "offer")
    br_ref  = br_cfg.get("referral", False)
    for leg, em, xm in zip(resolved_legs, entry_marks, exit_marks):
        entry_slip = slippage_dollars_per_side(
            sl_cfg.get("enabled", False), sl_cfg.get("mode", "smart"),
            sl_cfg.get("flat_value", 5.0), sl_cfg.get("mult", 1.0),
            spot_at_entry, em, leg["strike"], leg["type"] == "CE",
            leg["qty"], entry_ts,
        )
        slip_total += 2.0 * entry_slip   # round-trip approximation, matches StrategyPanel
        if br_cfg.get("enabled"):
            entry_brk_total += compute_brokerage_one_side(
                spot_at_entry, em, leg["qty"], br_rate, br_ref,
            )
            exit_brk_total += compute_brokerage_one_side(
                spot_at_exit, xm, leg["qty"], br_rate, br_ref,
            )
    brk_total = entry_brk_total + exit_brk_total
    net_pnl = gross - slip_total - brk_total

    # Net P&L "if you had exited at max-MTM / min-MTM moment" — same slip
    # (round-trip approx is entry-side only) but exit brokerage uses the marks
    # & spot at that moment.
    def _exit_brk_at(ts: int, marks: list[float]) -> float:
        if not br_cfg.get("enabled"):
            return 0.0
        sp = get_spot_at_or_before(ts) or spot_at_exit
        return sum(
            compute_brokerage_one_side(sp, mk, leg["qty"], br_rate, br_ref)
            for leg, mk in zip(resolved_legs, marks)
        )

    max_pnl_net = max_mtm - slip_total - entry_brk_total - _exit_brk_at(max_mtm_ts, max_marks)
    min_pnl_net = min_mtm - slip_total - entry_brk_total - _exit_brk_at(min_mtm_ts, min_marks)

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

    return {
        "date":            d.isoformat(),
        "entry_time":      _fmt_ist(entry_ts),
        "exit_time":       _fmt_ist(actual_exit_ts),
        "exit_reason":     "Settlement" if settlement_capped
                            else ("EOD" if exit_day_offset == 0 else f"+{exit_day_offset}d"),
        "spot_at_entry":   round(spot_at_entry, 2),
        "spot_at_exit":    round(spot_at_exit, 2),
        "legs":            leg_fills,
        "gross_pnl":       round(gross, 4),
        "slippage_cost":   round(slip_total, 4),
        "brokerage_cost":  round(brk_total, 4),
        "net_pnl":         round(net_pnl, 4),
        "margin_used":     margin_used,
        "max_mtm":         round(max_mtm, 4),
        "max_mtm_time":    _fmt_ist(max_mtm_ts),
        "max_pnl_net":     round(max_pnl_net, 4),
        "min_mtm":         round(min_mtm, 4),
        "min_mtm_time":    _fmt_ist(min_mtm_ts),
        "min_pnl_net":     round(min_pnl_net, 4),
        "is_reentry":      False,
        "skipped":         False,
    }


def _skipped(d: date, reason: str, entry_ts: Optional[int] = None) -> dict:
    return {
        "date": d.isoformat(),
        "entry_time": _fmt_ist(entry_ts) if entry_ts else None,
        "exit_time": None, "exit_reason": None,
        "spot_at_entry": None, "spot_at_exit": None,
        "legs": [], "gross_pnl": 0.0, "slippage_cost": 0.0,
        "brokerage_cost": 0.0, "net_pnl": 0.0, "margin_used": None,
        "max_mtm": 0.0, "max_mtm_time": None, "max_pnl_net": 0.0,
        "min_mtm": 0.0, "min_mtm_time": None, "min_pnl_net": 0.0,
        "is_reentry": False, "skipped": True, "skip_reason": reason,
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

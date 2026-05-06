#!/usr/bin/env python3
"""
Historical Margin Backtest — reads parquet directly, runs the calibrated
Delta India margin engine for every minute of a position's lifetime.

Usage:
  python3 scripts/historical_margin.py \
      --entry-date 2026-03-15 \
      --entry-time 09:30 \
      --expiry 2026-03-21 \
      --strategy short_strangle \
      --target-delta 0.10 \
      --qty 100 \
      --step-min 5 \
      --out /tmp/margin_history.csv

  Strategies: short_strangle | short_put | short_call | iron_condor

Output CSV columns:
  timestamp_utc, spot, leg{i}_strike, leg{i}_type, leg{i}_action, leg{i}_qty,
  leg{i}_mark, leg{i}_iv, leg{i}_delta,
  total_notional, risk_margin, margin_floor, portfolio_margin,
  ucf, initial_margin, maintenance_margin, binding_constraint,
  net_delta_btc, total_pnl
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Project paths
HERE      = Path(__file__).resolve().parent
ROOT      = HERE.parent
BACKEND   = ROOT / "backend"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

import duckdb

from scripts.margin_engine import (
    CONTRACT_VALUE,
    MarginLeg,
    bs_delta,
    compute_portfolio_margin,
    implied_forward_pcp,
)
from app.core.greeks import implied_vol  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("hist-margin")

# ── Data paths (must exist on this box) ───────────────────────────────────────
SPOT_PARQUET    = "/home/abhis/btc-data/data/spot/BTCUSD_1min.parquet"
OPTIONS_BASE    = "/home/abhis/btc-data/data/options"

EXPIRY_HOUR_UTC = 12   # Delta India settlement: 5:30 PM IST = 12:00 UTC


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(date_str: str, time_str: str) -> datetime:
    """date 'YYYY-MM-DD' + time 'HH:MM' → UTC datetime."""
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(
        tzinfo=timezone.utc
    )


def _expiry_dt(expiry_date: str) -> datetime:
    return datetime.strptime(expiry_date, "%Y-%m-%d").replace(
        tzinfo=timezone.utc, hour=EXPIRY_HOUR_UTC
    )


def _strikes_for_expiry(expiry_date: str) -> list[int]:
    expiry_dir = Path(OPTIONS_BASE) / f"expiry={expiry_date}"
    if not expiry_dir.exists():
        return []
    out = []
    for sd in expiry_dir.iterdir():
        if sd.is_dir() and "=" in sd.name:
            try:
                out.append(int(sd.name.split("=")[1]))
            except ValueError:
                pass
    return sorted(out)


def _spot_at(conn, ts: int) -> float | None:
    row = conn.execute(
        f"SELECT mark_close FROM read_parquet('{SPOT_PARQUET}') "
        f"WHERE timestamp_unix = {ts}"
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _option_mark(conn, expiry_date: str, strike: int,
                 is_call: bool, ts: int) -> float | None:
    fn   = "CE.parquet" if is_call else "PE.parquet"
    path = f"{OPTIONS_BASE}/expiry={expiry_date}/strike={strike}/{fn}"
    if not os.path.exists(path):
        return None
    row = conn.execute(
        f"SELECT mark_close FROM read_parquet('{path}') "
        f"WHERE timestamp_unix = {ts}"
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _bracket_strike(strikes: list[int], target: float) -> int:
    return min(strikes, key=lambda s: abs(s - target))


def _forward_at(conn, expiry_date: str, ts: int, spot: float) -> float:
    """
    Walk strikes outward from ATM to find the closest strike with both call
    and put marks at this timestamp. Compute F via put-call parity:
        F = C − P + K
    Falls back to spot if no usable strike found within ±10 strikes.
    """
    strikes = _strikes_for_expiry(expiry_date)
    if not strikes:
        return spot
    atm_idx = strikes.index(_bracket_strike(strikes, spot))
    # walk both directions in alternating fashion
    for offset in range(0, 11):
        for k_idx in (atm_idx - offset, atm_idx + offset) if offset else (atm_idx,):
            if 0 <= k_idx < len(strikes):
                K = strikes[k_idx]
                c = _option_mark(conn, expiry_date, K, True,  ts)
                p = _option_mark(conn, expiry_date, K, False, ts)
                if c and p and c > 0 and p > 0:
                    return implied_forward_pcp(c, p, K) or spot
    return spot


# ── Strategy construction at entry ────────────────────────────────────────────

def _find_target_delta_strike(conn, expiry: str, entry_ts: int, spot: float,
                              T: float, target_delta: float,
                              is_call: bool) -> tuple[int, float, float] | None:
    """
    Walk OTM strikes outward from ATM. For each strike, compute IV from mark_close,
    then BS delta. Return (strike, mark_per_btc, iv) for first strike whose
    |delta| ≤ target.  Returns None if not found.
    """
    strikes = _strikes_for_expiry(expiry)
    if not strikes:
        return None

    atm_idx = strikes.index(_bracket_strike(strikes, spot))

    # Calls: walk up from ATM. Puts: walk down. Far OTM = lower |delta|.
    walk = strikes[atm_idx:] if is_call else list(reversed(strikes[: atm_idx + 1]))

    best = None  # (strike, mark, iv, |delta|)
    for k in walk:
        m = _option_mark(conn, expiry, k, is_call, entry_ts)
        if m is None or m <= 0:
            continue
        iv = implied_vol(m, spot, k, T, 0.0, "call" if is_call else "put")
        if iv <= 0:
            continue
        d = bs_delta(spot, k, T, iv, is_call)
        if abs(d) < 0.001:   # too far OTM, give up
            break
        if best is None or abs(abs(d) - target_delta) < abs(abs(best[3]) - target_delta):
            best = (k, m, iv, d)
        if abs(d) <= target_delta:
            return (k, m, iv)

    return (best[0], best[1], best[2]) if best else None


def build_strategy_legs(conn, *, strategy: str, expiry: str, entry_ts: int,
                        spot: float, T: float, target_delta: float, qty: int):
    """
    Returns list of dicts:
       [{strike, is_call, is_buy, qty, entry_mark_per_btc, entry_iv, label}, ...]
    """
    legs = []

    def add_short(is_call, target):
        r = _find_target_delta_strike(conn, expiry, entry_ts, spot, T, target, is_call)
        if r is None:
            raise SystemExit(f"No strike found for {'call' if is_call else 'put'} "
                             f"δ≈{target} on {expiry} at entry")
        k, m, iv = r
        legs.append({
            "strike": k, "is_call": is_call, "is_buy": False, "qty": qty,
            "entry_mark_per_btc": m, "entry_iv": iv,
            "label": ("CE" if is_call else "PE") + f"-{k}",
        })

    def add_long(is_call, target):
        r = _find_target_delta_strike(conn, expiry, entry_ts, spot, T, target, is_call)
        if r is None:
            raise SystemExit(f"No long-wing strike found for δ≈{target}")
        k, m, iv = r
        legs.append({
            "strike": k, "is_call": is_call, "is_buy": True, "qty": qty,
            "entry_mark_per_btc": m, "entry_iv": iv,
            "label": ("CE" if is_call else "PE") + f"-{k}-long",
        })

    if strategy == "short_strangle":
        add_short(True,  target_delta)
        add_short(False, target_delta)
    elif strategy == "short_put":
        add_short(False, target_delta)
    elif strategy == "short_call":
        add_short(True, target_delta)
    elif strategy == "iron_condor":
        add_short(True,  target_delta)
        add_long (True,  target_delta * 0.25)   # ~5-delta wing if target=20
        add_short(False, target_delta)
        add_long (False, target_delta * 0.25)
    else:
        raise SystemExit(f"Unknown strategy: {strategy}")

    return legs


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(args) -> None:
    conn = duckdb.connect(database=":memory:")

    entry_dt  = _parse_dt(args.entry_date, args.entry_time)
    expiry_dt = _expiry_dt(args.expiry)
    entry_ts  = int(entry_dt.timestamp())
    expiry_ts = int(expiry_dt.timestamp())

    if entry_ts >= expiry_ts:
        raise SystemExit("entry must be before expiry")

    entry_spot = _spot_at(conn, entry_ts)
    if entry_spot is None:
        raise SystemExit(f"No spot data at entry ts={entry_ts} ({entry_dt.isoformat()})")

    T_entry = (expiry_ts - entry_ts) / (365 * 24 * 3600)

    log.info(f"Entry  : {entry_dt.isoformat()} (spot=${entry_spot:,.2f}, T={T_entry*365:.2f}d)")
    log.info(f"Expiry : {expiry_dt.isoformat()}")

    legs_meta = build_strategy_legs(
        conn, strategy=args.strategy, expiry=args.expiry,
        entry_ts=entry_ts, spot=entry_spot, T=T_entry,
        target_delta=args.target_delta, qty=args.qty,
    )

    log.info(f"Strategy: {args.strategy}  qty={args.qty}  target δ≈{args.target_delta}")
    for L in legs_meta:
        side = "BUY " if L["is_buy"] else "SELL"
        kind = "CE" if L["is_call"] else "PE"
        log.info(f"  {side} {L['qty']}× K={L['strike']} {kind}  "
                 f"entry_mark=${L['entry_mark_per_btc']:.2f}/BTC  "
                 f"IV={L['entry_iv']*100:.1f}%")

    # Header
    fp  = open(args.out, "w", newline="")
    csv_w = csv.writer(fp)
    header = ["timestamp_utc", "spot", "forward"]
    for i, L in enumerate(legs_meta):
        prefix = f"leg{i+1}"
        header += [f"{prefix}_strike", f"{prefix}_type", f"{prefix}_action",
                   f"{prefix}_qty", f"{prefix}_mark_per_btc", f"{prefix}_iv",
                   f"{prefix}_delta"]
    header += ["total_notional", "risk_margin", "margin_floor", "portfolio_margin",
               "ucf", "initial_margin", "maintenance_margin", "binding_constraint",
               "net_delta_btc", "total_pnl"]
    csv_w.writerow(header)

    # Walk through time
    step      = max(1, args.step_min) * 60
    rows      = 0
    skipped   = 0
    for ts in range(entry_ts, expiry_ts + 1, step):
        spot_t = _spot_at(conn, ts)
        if spot_t is None:
            skipped += 1
            continue

        # T at this timestamp
        T_t = max(1e-6, (expiry_ts - ts) / (365 * 24 * 3600))

        # Implied forward F via put-call parity (recovered from chain)
        forward_t = _forward_at(conn, args.expiry, ts, spot_t)

        # Build legs with current marks/IVs (use forward F for IV solving)
        live_legs = []
        leg_iv_marks = []
        all_present = True
        total_pnl = 0.0
        for L in legs_meta:
            mark_btc = _option_mark(conn, args.expiry, L["strike"], L["is_call"], ts)
            if mark_btc is None or mark_btc <= 0:
                # No data for this leg at this minute — skip whole row
                all_present = False
                break
            iv = implied_vol(mark_btc, forward_t, L["strike"], T_t, 0.0,
                             "call" if L["is_call"] else "put")
            if iv <= 0:
                all_present = False
                break
            d = bs_delta(forward_t, L["strike"], T_t, iv, L["is_call"])
            current_price = mark_btc * CONTRACT_VALUE   # USDT per contract
            entry_price   = L["entry_mark_per_btc"] * CONTRACT_VALUE
            sign_pnl      = 1 if L["is_buy"] else -1
            total_pnl    += sign_pnl * L["qty"] * (current_price - entry_price)

            live_legs.append(MarginLeg(
                strike=L["strike"], is_call=L["is_call"], is_buy=L["is_buy"],
                qty=L["qty"], current_price=current_price, iv=iv, T=T_t,
                forward=forward_t,
            ))
            leg_iv_marks.append((mark_btc, iv, d))

        if not all_present:
            skipped += 1
            continue

        r = compute_portfolio_margin(live_legs, spot_t)
        if r is None:
            skipped += 1
            continue

        # Write row
        row = [datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
               f"{spot_t:.2f}", f"{forward_t:.2f}"]
        for L, (mp, iv, d) in zip(legs_meta, leg_iv_marks):
            row += [
                L["strike"],
                "CE" if L["is_call"] else "PE",
                "BUY" if L["is_buy"] else "SELL",
                L["qty"],
                f"{mp:.4f}",
                f"{iv:.6f}",
                f"{d:.6f}",
            ]
        row += [
            f"{r.total_notional:.2f}", f"{r.risk_margin:.2f}",
            f"{r.margin_floor:.2f}",  f"{r.portfolio_margin:.2f}",
            f"{r.ucf:.2f}",            f"{r.initial_margin:.2f}",
            f"{r.maintenance_margin:.2f}", r.binding_constraint,
            f"{r.net_delta_btc:.6f}", f"{total_pnl:.2f}",
        ]
        csv_w.writerow(row)
        rows += 1

    fp.close()
    log.info(f"Wrote {rows} rows ({skipped} skipped) → {args.out}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Historical margin backtest")
    p.add_argument("--entry-date", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--entry-time", default="00:00", help="HH:MM (UTC), default 00:00")
    p.add_argument("--expiry",     required=True, help="YYYY-MM-DD")
    p.add_argument("--strategy",   default="short_strangle",
                   choices=["short_strangle", "short_put", "short_call", "iron_condor"])
    p.add_argument("--target-delta", type=float, default=0.10,
                   help="target |delta| for OTM legs (default 0.10)")
    p.add_argument("--qty",        type=int, default=100, help="contracts per leg")
    p.add_argument("--step-min",   type=int, default=5,
                   help="minutes between samples (default 5)")
    p.add_argument("--out",        default="/tmp/margin_history.csv")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()

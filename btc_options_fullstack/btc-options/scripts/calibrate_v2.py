"""
Comprehensive margin calibration grid v2.

For every expiry bucket × target delta × lots:
  • Picks the strike whose BS-computed delta matches the target
  • Builds a short strangle (or straddle when target_delta=0.5)
  • Calls our engine + Delta's /v2/orders/estimate_margin/basket
  • Appends row to scripts/calibration_v2_history.csv

Buckets:    current, next, next_to_next, weekly, biweekly, monthly, bimonthly
Deltas:     0.10, 0.15, 0.20, 0.25, 0.30, 0.50 (0.50 = ATM straddle)
Lots:       50, 100, 200, 500, 700, 900, 1000, 1200, 1500, 1700, 2000, 2200, 2500

Run via the v2 loop (scripts/calibrate_loop_v2.sh) every 15 minutes.
"""

from __future__ import annotations
import csv, json, math, os, sys, time
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from scripts.margin_engine import (
    CONTRACT_VALUE, MarginLeg, compute_portfolio_margin, implied_forward_pcp,
)
from margin_check import delta_basket_margin


# ── Grid definition ────────────────────────────────────────────────────────
# (bucket_label, target_DTE_days, max_distance_to_accept)
EXPIRY_BUCKETS = [
    ("current",      0.5,  1.0),
    ("next",         1.5,  1.0),
    ("next_to_next", 3.0,  2.0),
    ("weekly",       7.0,  3.0),
    ("biweekly",    14.0,  4.0),
    ("monthly",     30.0,  7.0),
    ("bimonthly",   60.0, 10.0),
]
TARGET_DELTAS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.50]
QTYS = [50, 100, 200, 500, 700, 900, 1000, 1200, 1500, 1700, 2000, 2200, 2500]

HISTORY_CSV = os.path.join(os.path.dirname(__file__), "calibration_v2_history.csv")
SLEEP_BETWEEN_CALLS = 0.10


# ── BS delta helper ────────────────────────────────────────────────────────

def bs_delta(S, K, T, sigma, is_call):
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    return nd1 if is_call else nd1 - 1.0


# ── Chain fetch ────────────────────────────────────────────────────────────

def fetch_chain():
    r = requests.get(
        "https://api.india.delta.exchange/v2/tickers",
        params={"contract_types": "put_options,call_options"},
        timeout=20,
    ).json()["result"]
    chain, expiries_dte = {}, {}
    spot = None
    now = time.time()
    for t in r:
        sym = t.get("symbol", "")
        if "-BTC-" not in sym: continue
        parts = sym.split("-")
        if len(parts) != 4: continue
        kind, _, K_str, exp = parts
        try: K = float(K_str)
        except: continue
        spot = float(t["spot_price"])
        chain.setdefault(exp, {}).setdefault(K, {})[kind] = t
        if exp not in expiries_dte:
            d = datetime.strptime(exp, "%d%m%y").replace(tzinfo=timezone.utc, hour=12)
            expiries_dte[exp] = (d.timestamp() - now) / 86400
    return spot, chain, expiries_dte


def expiry_for_bucket(bucket_dte, max_dist, expiries):
    valid = [(abs(d - bucket_dte), s, d) for s, d in expiries.items() if d > 0.05]
    valid.sort()
    if valid and valid[0][0] <= max_dist:
        return valid[0][1], valid[0][2]
    return None


def compute_forward(chain_for_exp, spot):
    strikes = sorted(chain_for_exp.keys())
    atm_K = min(strikes, key=lambda x: abs(x - spot))
    atm = chain_for_exp[atm_K]
    if "C" in atm and "P" in atm:
        return implied_forward_pcp(
            float(atm["C"]["mark_price"]),
            float(atm["P"]["mark_price"]),
            atm_K
        )
    return spot


def pick_strike_by_delta(chain_for_exp, kind, target_abs_delta, F, T):
    """Pick the strike whose |BS-delta| is closest to target."""
    best, best_diff = None, 1e9
    for K, kinds in chain_for_exp.items():
        if kind not in kinds: continue
        t = kinds[kind]
        iv = float(t.get("mark_vol") or 0)
        if iv <= 0: continue
        d = abs(bs_delta(F, K, T, iv, kind == "C"))
        diff = abs(d - target_abs_delta)
        if diff < best_diff:
            best_diff = diff
            best = (K, t, d)
    return best


def _build_leg(ticker, strike, is_call, is_buy, qty, T, F):
    return MarginLeg(
        strike=strike, is_call=is_call, is_buy=is_buy, qty=qty,
        current_price=float(ticker["mark_price"]) * CONTRACT_VALUE,
        iv=float(ticker.get("mark_vol") or 0),
        T=T, forward=F,
    )


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    run_ts = int(time.time())
    run_label_ist = datetime.fromtimestamp(run_ts, tz=IST).strftime("%Y-%m-%d %H:%M:%S IST")
    print(f"\n{'='*82}")
    print(f"  v2 Calibration run @ {run_label_ist}")
    print('='*82)

    spot, chain, expiries = fetch_chain()
    print(f"  Spot=${spot:,.2f}   live expiries on chain: {sorted(expiries.keys())}\n")

    rows = []

    for bucket_label, bucket_dte, max_dist in EXPIRY_BUCKETS:
        picked = expiry_for_bucket(bucket_dte, max_dist, expiries)
        if picked is None:
            print(f"  [skip] {bucket_label}: no expiry within ±{max_dist}d of {bucket_dte}d")
            continue
        exp, dte = picked
        T = max(dte / 365.0, 1e-6)
        F = compute_forward(chain[exp], spot)
        print(f"\n  >> {bucket_label:<14}  exp={exp}  DTE={dte:5.2f}d  F=${F:,.2f}")

        for tgt_delta in TARGET_DELTAS:
            put_pick  = pick_strike_by_delta(chain[exp], "P", tgt_delta, F, T)
            call_pick = pick_strike_by_delta(chain[exp], "C", tgt_delta, F, T)
            if put_pick is None or call_pick is None:
                print(f"     [skip] target_delta={tgt_delta}: no matching strike")
                continue
            put_K,  pt, put_d  = put_pick
            call_K, ct, call_d = call_pick

            # When target=0.5 → effectively ATM straddle (put_K ≈ call_K)
            strategy = "short_straddle" if tgt_delta >= 0.45 else "short_strangle"

            for qty in QTYS:
                legs = [
                    _build_leg(pt, put_K,  False, False, qty, T, F),
                    _build_leg(ct, call_K, True,  False, qty, T, F),
                ]
                mr = compute_portfolio_margin(legs, spot)
                if mr is None: continue

                bp, arm = delta_basket_margin([
                    {"product_symbol": pt["symbol"], "size": qty, "side": "sell"},
                    {"product_symbol": ct["symbol"], "size": qty, "side": "sell"},
                ])
                if bp is None: continue

                # `ratio` (PM-based, legacy) kept for backward compatibility with
                # earlier rows. `ratio_arm` is the *real* calibration signal —
                # additional_required_margin is what Delta actually deducts when
                # an order is placed (matches the "Order Margin" shown in UI).
                ratio = bp / mr.portfolio_margin if mr.portfolio_margin > 0 else None
                ratio_arm = (arm / mr.portfolio_margin
                             if (arm is not None and mr.portfolio_margin > 0) else None)
                row = {
                    "run_ts": run_ts,
                    "run_dt_ist": run_label_ist,
                    "spot": round(spot, 2),
                    "forward": round(F, 2),
                    "expiry_bucket": bucket_label,
                    "exp": exp,
                    "dte": round(dte, 4),
                    "strategy": strategy,
                    "target_delta": tgt_delta,
                    "qty": qty,
                    "put_K": put_K,
                    "put_actual_abs_delta": round(put_d, 4),
                    "put_mark_per_btc":  round(float(pt["mark_price"]), 4),
                    "put_iv": round(float(pt.get("mark_vol") or 0), 4),
                    "call_K": call_K,
                    "call_actual_abs_delta": round(call_d, 4),
                    "call_mark_per_btc": round(float(ct["mark_price"]), 4),
                    "call_iv": round(float(ct.get("mark_vol") or 0), 4),
                    "total_notional": round(qty * CONTRACT_VALUE * spot * 2, 2),
                    "premium_collected": round((float(pt["mark_price"]) + float(ct["mark_price"])) * qty * CONTRACT_VALUE, 2),
                    "our_risk":      round(mr.risk_margin, 2),
                    "our_floor":     round(mr.margin_floor, 2),
                    "our_pre_scale": round(mr.pre_scale_margin, 2),
                    "our_pm":        round(mr.portfolio_margin, 2),
                    "our_dte_scale": round(mr.dte_scale_applied, 4),
                    "binding":       mr.binding_constraint,
                    "delta_pm":      round(bp, 2),
                    "delta_arm":     round(arm or 0, 2),
                    "ratio":         round(ratio, 3) if ratio else None,
                }
                rows.append(row)
                arm_disp = f"${arm:>9,.2f}" if arm is not None else "      n/a"
                r_arm_disp = f"{ratio_arm:.2f}" if ratio_arm is not None else " n/a"
                print(f"     δ={tgt_delta:.2f} q={qty:>4}  P={put_K:>7,.0f}(δ={put_d:.3f})  "
                      f"C={call_K:>7,.0f}(δ={call_d:.3f})  "
                      f"ours=${mr.portfolio_margin:>9,.2f}  "
                      f"arm={arm_disp}  r_arm={r_arm_disp}")
                time.sleep(SLEEP_BETWEEN_CALLS)

    # ── Append to CSV
    fieldnames = [
        "run_ts","run_dt_ist","spot","forward","expiry_bucket","exp","dte","strategy",
        "target_delta","qty","put_K","put_actual_abs_delta","put_mark_per_btc","put_iv",
        "call_K","call_actual_abs_delta","call_mark_per_btc","call_iv",
        "total_notional","premium_collected",
        "our_risk","our_floor","our_pre_scale","our_pm","our_dte_scale","binding",
        "delta_pm","delta_arm","ratio",
    ]
    file_exists = os.path.exists(HISTORY_CSV)
    with open(HISTORY_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists: w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})
    print(f"\n  → {len(rows)} rows appended to {HISTORY_CSV}")

    # Summary — use ARM-based ratio (delta_arm = actual margin charged by Delta).
    by_bucket = {}
    for r in rows:
        arm_v = r.get("delta_arm"); ours_v = r.get("our_pm")
        if not arm_v or not ours_v: continue
        by_bucket.setdefault(r["expiry_bucket"], []).append(arm_v / ours_v)
    print(f"\n  Median delta_arm / our_pm by expiry bucket  (1.0 = perfect; >1 = under-charge):")
    for bk in [b[0] for b in EXPIRY_BUCKETS]:
        if bk not in by_bucket: continue
        rs = sorted(by_bucket[bk])
        print(f"     {bk:<15}  n={len(rs):>3}  med={rs[len(rs)//2]:.2f}  range=[{min(rs):.2f}, {max(rs):.2f}]")


if __name__ == "__main__":
    main()

"""
Full-coverage margin calibration.

Walks a broad (strategy × DTE × moneyness × qty) grid against Delta's basket
margin endpoint and our engine. Appends results to scripts/calibration_history.csv
with a run timestamp so we can track how the model fits across time + scenarios.

Strategies covered:
  • short_strangle  (sell put OTM, sell call OTM)
  • short_straddle  (sell put ATM, sell call ATM — same strike)
  • short_put       (single sell put)
  • short_call      (single sell call)
  • long_strangle   (buy put OTM, buy call OTM)
  • iron_condor     (4 legs: short OTM strangle inside, long farther OTM strangle outside)
"""

from __future__ import annotations
import csv, json, math, os, sys, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from scripts.margin_engine import (
    CONTRACT_VALUE, MarginLeg, compute_portfolio_margin, implied_forward_pcp,
)
from margin_check import delta_basket_margin


# ── Grid definition ────────────────────────────────────────────────────────
DTE_TARGETS    = [0.5, 2.0, 7.0, 14.0, 30.0, 60.0]
MONEYNESS_PCTS = [0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12]   # ATM → far OTM
QTYS           = [100, 500, 1000, 1500, 2000]
QTYS_SMALL     = [100, 500, 1000]   # for single-leg + iron condor (less weight)
IC_WINGS       = [0.05, 0.08]   # outer wing % for iron condor (inner = m below)

HISTORY_CSV   = os.path.join(os.path.dirname(__file__), "calibration_history.csv")
SNAPSHOT_JSON = os.path.join(os.path.dirname(__file__), "calibration_data.json")
SLEEP_BETWEEN_CALLS = 0.10


# ── Helpers ────────────────────────────────────────────────────────────────

def _bs_delta(S, K, T, sigma, is_call):
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    return nd1 if is_call else nd1 - 1.0


def _pick_strike(strikes_with_kind, target_strike, kind):
    cands = [k for k in strikes_with_kind if kind in strikes_with_kind[k]]
    if not cands: return None
    return min(cands, key=lambda x: abs(x - target_strike))


def _build_leg(ticker, strike, is_call, is_buy, qty, T, F):
    return MarginLeg(
        strike=strike, is_call=is_call, is_buy=is_buy, qty=qty,
        current_price=float(ticker["mark_price"]) * CONTRACT_VALUE,
        iv=float(ticker.get("mark_vol") or 0),
        T=T, forward=F,
    )


def _basket_leg(symbol, qty, side):
    return {"product_symbol": symbol, "size": qty, "side": side}


# ── Main ───────────────────────────────────────────────────────────────────

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


def expiry_for_dte(target_dte, expiries):
    candidates = [(abs(d - target_dte), s, d) for s, d in expiries.items() if d > 0.05]
    if not candidates: return None
    candidates.sort()
    if candidates[0][0] < 2.0:   # within 2 days of target
        return candidates[0][1], candidates[0][2]
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


def run_scenario(strategy, legs, basket_legs, spot, dte, exp, qty, *, m_pct=None, wing_pct=None):
    """Compute our engine + Delta basket for a strategy. Return row dict or None."""
    if not legs or not basket_legs: return None
    mr = compute_portfolio_margin(legs, spot)
    if mr is None or mr.portfolio_margin <= 0: return None
    pm, arm = delta_basket_margin(basket_legs)
    if pm is None: return None
    avg_abs_d = sum(abs(_bs_delta(leg.forward or spot, leg.strike, leg.T, leg.iv, leg.is_call))
                    for leg in legs) / len(legs)
    return {
        "strategy": strategy, "dte": round(dte, 2), "exp": exp, "qty": qty,
        "moneyness_pct": m_pct, "wing_pct": wing_pct,
        "n_legs": len(legs),
        "avg_abs_delta": round(avg_abs_d, 4),
        "our_risk":      round(mr.risk_margin, 2),
        "our_floor":     round(mr.margin_floor, 2),
        "our_pre_scale": round(mr.pre_scale_margin, 2),
        "our_pm":        round(mr.portfolio_margin, 2),
        "our_dte_scale": round(mr.dte_scale_applied, 4),
        "binding":       mr.binding_constraint,
        "delta_pm":      round(pm, 2),
        "delta_arm":     round(arm or 0, 2),
        "ratio_raw":     round(pm / mr.pre_scale_margin, 3) if mr.pre_scale_margin > 0 else None,
        "ratio_scaled":  round(pm / mr.portfolio_margin, 3),
    }


def main():
    run_ts = int(time.time())
    print(f"\n{'='*80}\n  Calibration run @ {datetime.fromtimestamp(run_ts, tz=timezone.utc).isoformat()}")
    print('='*80)

    spot, chain, expiries = fetch_chain()
    print(f"  Spot=${spot:,.2f}   live expiries={len(expiries)}\n")

    rows = []

    for dte_target in DTE_TARGETS:
        picked = expiry_for_dte(dte_target, expiries)
        if picked is None: continue
        exp, dte = picked
        T = max(dte / 365.0, 1e-6)
        F = compute_forward(chain[exp], spot)
        strikes = sorted(chain[exp].keys())

        # ── Short strangles
        for pct in MONEYNESS_PCTS:
            put_K  = _pick_strike(chain[exp], spot * (1 - pct), "P")
            call_K = _pick_strike(chain[exp], spot * (1 + pct), "C")
            if put_K is None or call_K is None: continue
            pt = chain[exp][put_K]["P"]; ct = chain[exp][call_K]["C"]
            for qty in QTYS:
                legs = [
                    _build_leg(pt, put_K,  False, False, qty, T, F),
                    _build_leg(ct, call_K, True,  False, qty, T, F),
                ]
                bl = [_basket_leg(pt["symbol"], qty, "sell"),
                      _basket_leg(ct["symbol"], qty, "sell")]
                row = run_scenario("short_strangle", legs, bl, spot, dte, exp, qty, m_pct=pct)
                if row:
                    rows.append(row)
                    print(f"  short_strangle  DTE={dte:5.2f}  m=±{pct*100:>4.1f}%  q={qty:>4}  "
                          f"raw=${row['our_pre_scale']:>7.2f}  ours=${row['our_pm']:>7.2f}  "
                          f"basket=${row['delta_pm']:>7.2f}  r_scl={row['ratio_scaled']:.2f}")
                time.sleep(SLEEP_BETWEEN_CALLS)

        # ── Short straddles (ATM, both legs same strike)
        atm_K = min(strikes, key=lambda x: abs(x - spot))
        atm = chain[exp][atm_K]
        if "C" in atm and "P" in atm:
            pt, ct = atm["P"], atm["C"]
            for qty in QTYS:
                legs = [
                    _build_leg(pt, atm_K, False, False, qty, T, F),
                    _build_leg(ct, atm_K, True,  False, qty, T, F),
                ]
                bl = [_basket_leg(pt["symbol"], qty, "sell"),
                      _basket_leg(ct["symbol"], qty, "sell")]
                row = run_scenario("short_straddle", legs, bl, spot, dte, exp, qty, m_pct=0.0)
                if row:
                    rows.append(row)
                    print(f"  short_straddle  DTE={dte:5.2f}  K={atm_K:<7.0f}     q={qty:>4}  "
                          f"raw=${row['our_pre_scale']:>7.2f}  ours=${row['our_pm']:>7.2f}  "
                          f"basket=${row['delta_pm']:>7.2f}  r_scl={row['ratio_scaled']:.2f}")
                time.sleep(SLEEP_BETWEEN_CALLS)

        # ── Single short put / single short call
        for kind, opt_pct in [("P", -0.05), ("C", 0.05)]:
            target = spot * (1 + opt_pct)
            K = _pick_strike(chain[exp], target, kind)
            if K is None: continue
            t = chain[exp][K][kind]
            for qty in QTYS_SMALL:
                leg = _build_leg(t, K, kind == "C", False, qty, T, F)
                bl = [_basket_leg(t["symbol"], qty, "sell")]
                strat = "short_put" if kind == "P" else "short_call"
                row = run_scenario(strat, [leg], bl, spot, dte, exp, qty, m_pct=abs(opt_pct))
                if row:
                    rows.append(row)
                    print(f"  {strat:<14}  DTE={dte:5.2f}  m=±{abs(opt_pct)*100:>4.1f}%  q={qty:>4}  "
                          f"raw=${row['our_pre_scale']:>7.2f}  ours=${row['our_pm']:>7.2f}  "
                          f"basket=${row['delta_pm']:>7.2f}  r_scl={row['ratio_scaled']:.2f}")
                time.sleep(SLEEP_BETWEEN_CALLS)

        # ── Iron condors (sell inner strangle 3% OTM, buy outer protection)
        for wing in IC_WINGS:
            inner_p = _pick_strike(chain[exp], spot * (1 - 0.03), "P")
            inner_c = _pick_strike(chain[exp], spot * (1 + 0.03), "C")
            outer_p = _pick_strike(chain[exp], spot * (1 - wing), "P")
            outer_c = _pick_strike(chain[exp], spot * (1 + wing), "C")
            if not all([inner_p, inner_c, outer_p, outer_c]): continue
            if outer_p >= inner_p or outer_c <= inner_c: continue   # geometry check
            ip = chain[exp][inner_p]["P"]; ic = chain[exp][inner_c]["C"]
            op = chain[exp][outer_p]["P"]; oc = chain[exp][outer_c]["C"]
            for qty in QTYS_SMALL:
                legs = [
                    _build_leg(ip, inner_p, False, False, qty, T, F),
                    _build_leg(ic, inner_c, True,  False, qty, T, F),
                    _build_leg(op, outer_p, False, True,  qty, T, F),
                    _build_leg(oc, outer_c, True,  True,  qty, T, F),
                ]
                bl = [_basket_leg(ip["symbol"], qty, "sell"),
                      _basket_leg(ic["symbol"], qty, "sell"),
                      _basket_leg(op["symbol"], qty, "buy"),
                      _basket_leg(oc["symbol"], qty, "buy")]
                row = run_scenario("iron_condor", legs, bl, spot, dte, exp, qty, wing_pct=wing)
                if row:
                    rows.append(row)
                    print(f"  iron_condor     DTE={dte:5.2f}  wing=±{wing*100:>4.1f}%  q={qty:>4}  "
                          f"raw=${row['our_pre_scale']:>7.2f}  ours=${row['our_pm']:>7.2f}  "
                          f"basket=${row['delta_pm']:>7.2f}  r_scl={row['ratio_scaled']:.2f}")
                time.sleep(SLEEP_BETWEEN_CALLS)

    # ── Append to history CSV
    fieldnames = [
        "run_ts", "spot", "strategy", "dte", "exp", "qty",
        "moneyness_pct", "wing_pct", "n_legs", "avg_abs_delta",
        "our_risk", "our_floor", "our_pre_scale", "our_pm", "our_dte_scale",
        "binding", "delta_pm", "delta_arm", "ratio_raw", "ratio_scaled",
    ]
    file_exists = os.path.exists(HISTORY_CSV)
    with open(HISTORY_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            w.writeheader()
        for row in rows:
            row["run_ts"] = run_ts
            row["spot"]   = round(spot, 2)
            w.writerow({k: row.get(k) for k in fieldnames})

    print(f"\n  → {len(rows)} rows appended to {HISTORY_CSV}")

    # ── JSON snapshot of THIS run (overwritten each run; consumed by fit_margin_scale.py)
    snapshot_rows = []
    for row in rows:
        out = dict(row)
        out["run_ts"] = run_ts
        out["spot"]   = round(spot, 2)
        snapshot_rows.append(out)
    with open(SNAPSHOT_JSON, "w") as f:
        json.dump({"spot": spot, "ts": run_ts, "rows": snapshot_rows}, f, indent=2)
    print(f"  → snapshot {SNAPSHOT_JSON}")

    # ── Summary by strategy
    by_strat = {}
    for r in rows:
        if r.get("ratio_scaled") is None: continue
        by_strat.setdefault(r["strategy"], []).append(r["ratio_scaled"])
    print("\n  Median delta_basket / our_scaled by strategy:")
    for s in sorted(by_strat):
        rs = sorted(by_strat[s])
        med = rs[len(rs)//2]
        print(f"     {s:<16}  n={len(rs):>3}  med={med:.2f}  range=[{min(rs):.2f}, {max(rs):.2f}]")


if __name__ == "__main__":
    main()

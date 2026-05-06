"""
Delta Exchange India — Live Margin Validation
Queries live BTC option chain, runs the shared margin engine across diverse
strategies, and compares against Delta's own /v2/orders/estimate_margin endpoint.

Usage:  python3 margin_check.py
"""

import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scripts.margin_engine import (
    CONTRACT_VALUE,
    MarginLeg,
    best_forward_for_expiry,
    compute_portfolio_margin,
)

# ── Credentials ──────────────────────────────────────────────────────────────
API_KEY    = "IKLmOey8YSmReqRzYdClqyvgZZI5aI"
API_SECRET = "9AWJA1gMXAzqpmucUDczd4p4SbjsOixgXgj6VqYuCpCxUkNUSBYDuyXRVGef"
BASE_URL   = "https://api.india.delta.exchange"
CV         = CONTRACT_VALUE   # 0.001 BTC per contract

# ── HMAC auth ─────────────────────────────────────────────────────────────────

def api_get(path: str, params: dict | None = None) -> dict:
    qs  = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    ts  = str(int(time.time()))
    msg = "GET" + ts + path + (("?" + qs) if qs else "")
    sig = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    r   = requests.get(BASE_URL + path, params=params,
                       headers={"api-key": API_KEY, "timestamp": ts, "signature": sig},
                       timeout=15)
    r.raise_for_status()
    return r.json()

def api_post(path: str, body: dict, retries: int = 3) -> dict:
    body_str = json.dumps(body, separators=(",", ":"))
    last_exc: Exception | None = None
    for attempt in range(retries):
        ts  = str(int(time.time()))
        msg = "POST" + ts + path + body_str
        sig = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
        try:
            r = requests.post(BASE_URL + path, data=body_str,
                              headers={"api-key": API_KEY, "timestamp": ts,
                                       "signature": sig, "Content-Type": "application/json"},
                              timeout=30)
            return r.json()
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))
    raise last_exc if last_exc else RuntimeError("api_post failed")

# ── Delta margin endpoint ─────────────────────────────────────────────────────

_debug_printed = False

def delta_leverage(product_id: int) -> dict | None:
    """GET /v2/products/{id}/orders/leverage — Delta's max leverage + implied margin %."""
    try:
        res = api_get(f"/v2/products/{product_id}/orders/leverage")
        if res.get("success"):
            return res.get("result")
    except Exception:
        pass
    return None


def delta_order_margin(product_id: int, qty: int, side: str,
                       limit_price: float) -> tuple[float | None, float | None]:
    """Single-leg margin via /v2/orders/estimate_margin. Returns (pm, arm)."""
    global _debug_printed
    body = {
        "product_id":  product_id,
        "size":        qty,
        "side":        side,
        "order_type":  "limit_order",
        "limit_price": str(round(limit_price, 4)),
    }
    try:
        res = api_post("/v2/orders/estimate_margin", body)
        if res.get("success") and res.get("result"):
            r   = res["result"]
            pm  = float(r["portfolio_margin"])           if r.get("portfolio_margin")           is not None else None
            arm = float(r["additional_required_margin"]) if r.get("additional_required_margin") is not None else None
            return pm, arm
        if not _debug_printed:
            print(f"     [DEBUG] estimate_margin: {res}")
            _debug_printed = True
    except Exception as e:
        if not _debug_printed:
            print(f"     [DEBUG] estimate_margin exception: {e}")
            _debug_printed = True
    return None, None


def delta_basket_margin(legs: list[dict],
                        index_symbol: str = ".DEXBTUSD"
                        ) -> tuple[float | None, float | None]:
    """
    True basket margin via /v2/orders/estimate_margin/basket — accounts for
    cross-leg portfolio netting (matches what Delta UI shows in order ticket).

    `legs`: list of dicts with keys: product_symbol, size, side ('buy'/'sell').
            Optional: order_type (default 'market_order'), time_in_force ('gtc').

    Returns (portfolio_margin, additional_required_margin).
    """
    global _debug_printed
    orders = []
    for L in legs:
        o = {
            "product_symbol": L["product_symbol"],
            "size":           int(L["size"]),
            "side":           L["side"],
            "order_type":     L.get("order_type", "market_order"),
            "time_in_force":  L.get("time_in_force", "gtc"),
        }
        if o["order_type"] == "limit_order" and "limit_price" in L:
            o["limit_price"] = str(round(float(L["limit_price"]), 4))
        orders.append(o)
    body = {"index_symbol": index_symbol, "orders": orders, "source": "api"}
    try:
        res = api_post("/v2/orders/estimate_margin/basket", body)
        if res.get("success") and res.get("result"):
            r = res["result"]
            pm  = float(r["portfolio_margin"])           if r.get("portfolio_margin")           is not None else None
            arm = float(r["additional_required_margin"]) if r.get("additional_required_margin") is not None else None
            return pm, arm
        if not _debug_printed:
            print(f"     [DEBUG] basket: {res}")
            _debug_printed = True
    except Exception as e:
        if not _debug_printed:
            print(f"     [DEBUG] basket exception: {e}")
            _debug_printed = True
    return None, None

# ── Print one strategy block ──────────────────────────────────────────────────

def print_strategy(label: str, legs: list[MarginLeg], spot: float,
                   short_legs_for_delta_api: list[dict],
                   leg_extras: list[dict] | None = None) -> None:
    """leg_extras: optional per-leg display data (delta, mark per BTC)."""
    r = compute_portfolio_margin(legs, spot)
    if r is None:
        print(f"  ── {label} — skipped (no legs) ──")
        return

    print(f"\n  ── {label} ──")
    for i, leg in enumerate(legs):
        action = "BUY " if leg.is_buy else "SELL"
        kind   = "C" if leg.is_call else "P"
        extra  = leg_extras[i] if leg_extras else {}
        d      = extra.get("delta", 0.0)
        per_btc = extra.get("mark_per_btc", leg.current_price / CV)
        print(f"     {action} {leg.qty:>5}× K={leg.strike:>7,.0f}{kind}  "
              f"δ={d:+.3f}  IV={leg.iv*100:.1f}%  "
              f"${leg.current_price:.4f}/contract  (${per_btc:.2f}/BTC)")

    print(f"     Notional ${r.total_notional:>9,.0f}  "
          f"ps=±{r.price_shock_applied*100:.2f}%  "
          f"iv±{r.vol_up_applied*100:.1f}%↑/{r.vol_down_applied*100:.1f}%↓  "
          f"DTE={r.min_dte_days_applied:.2f}  "
          f"OM%={r.om_pct_applied*100:.3f}%")
    print(f"     Risk=${r.risk_margin:>7.2f}  Floor=${r.margin_floor:>7.2f}  "
          f"PM=${r.portfolio_margin:>7.2f}  ({r.binding_constraint} binding)")
    print(f"     UCF=${r.ucf:>+8.2f}  IM=${r.initial_margin:>7.2f}  "
          f"MM=${r.maintenance_margin:>7.2f}")

    total_pm = total_arm = 0.0
    got_pm = got_arm = False
    for sl in short_legs_for_delta_api:
        pm, arm = delta_order_margin(sl["product_id"], sl["qty"], "sell",
                                     sl["mark_usdt_per_btc"])
        if pm  is not None: total_pm  += pm;  got_pm  = True
        if arm is not None: total_arm += arm; got_arm = True

    def _cmp(lbl, dv, our):
        diff = our - dv
        sign = "+" if diff > 0 else ""
        pct  = abs(diff / dv * 100) if dv else 0
        tag  = "OVER" if diff > 0 else "UNDER"
        return f"     {lbl}: ${dv:>8.2f}  | our IM ${our:.2f} → {tag} by {sign}{diff:.2f} ({pct:.1f}%)"

    if got_arm: print(_cmp("Delta ARM (additional_req)", total_arm, r.initial_margin))
    if got_pm:  print(_cmp("Delta PM  (portfolio_marg)", total_pm,  r.initial_margin))
    if not got_pm and not got_arm:
        print("     Delta IM: n/a")

# ── Fetch live chain ──────────────────────────────────────────────────────────

def fetch_chain() -> tuple[float, list[dict]]:
    spot_d = api_get("/v2/tickers/BTCUSD")
    spot   = float(spot_d["result"]["mark_price"])
    print(f"BTC Spot: ${spot:,.2f}")

    now_ts  = time.time()
    options = []
    for ct in ("call_options", "put_options"):
        page = 1
        while page <= 10:
            d = api_get("/v2/products", params={
                "contract_type": ct, "state": "live",
                "underlying_asset_symbol": "BTC",
                "page_size": 200, "page_number": page,
            })
            batch = d.get("result") or []
            if not batch:
                break

            for chunk in [batch[i:i+40] for i in range(0, len(batch), 40)]:
                syms = ",".join(p["symbol"] for p in chunk)
                try:
                    td   = api_get("/v2/tickers", params={"symbols": syms})
                    tmap = {t["symbol"]: t for t in (td.get("result") or [])}
                except Exception:
                    tmap = {}

                for p in chunk:
                    sym = p["symbol"]
                    if "-BTC-" not in sym:
                        continue
                    t = tmap.get(sym) or {}
                    if not t:
                        try:
                            t = api_get(f"/v2/tickers/{sym}").get("result") or {}
                        except Exception:
                            continue

                    mark = float(t.get("mark_price") or 0)
                    if mark <= 0:
                        continue

                    iv_raw    = float(t.get("mark_vol") or
                                      (t.get("quotes") or {}).get("mark_iv") or 0)
                    delta_raw = float((t.get("greeks") or {}).get("delta") or 0)

                    expiry_str = p.get("settlement_time", "")
                    try:
                        exp_ts = datetime.fromisoformat(
                            expiry_str.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        continue

                    dte_days = (exp_ts - now_ts) / 86400
                    if dte_days < 0.25 or dte_days > 60:
                        continue

                    options.append({
                        "symbol":            sym,
                        "product_id":        p["id"],
                        "strike":            float(p["strike_price"]),
                        "expiry_ts":         exp_ts,
                        "dte_days":          dte_days,
                        "expiry_date":       datetime.fromtimestamp(
                                                 exp_ts, tz=timezone.utc
                                             ).strftime("%Y-%m-%d"),
                        "is_call":           ct == "call_options",
                        "mark_usdt_per_btc": mark,
                        "iv":                iv_raw,
                        "delta":             delta_raw,
                    })

            if len(batch) < 200:
                break
            page += 1

    print(f"Loaded {len(options)} live BTC options\n")
    return spot, options

def nearest(opts: list[dict], target: float) -> dict | None:
    return min(opts, key=lambda x: abs(abs(x["delta"]) - target), default=None)

# ── Helpers to build engine legs from option dicts ────────────────────────────

def make_leg(o: dict, is_buy: bool, qty: int, T: float,
             forward: float = 0.0) -> tuple[MarginLeg, dict]:
    leg = MarginLeg(
        strike=o["strike"],
        is_call=o["is_call"],
        is_buy=is_buy,
        qty=qty,
        current_price=o["mark_usdt_per_btc"] * CV,    # USDT per contract
        iv=o["iv"],
        T=T,
        forward=forward,
    )
    extra = {"delta": o["delta"], "mark_per_btc": o["mark_usdt_per_btc"]}
    return leg, extra

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("  Delta Exchange India — Live Portfolio Margin Validation")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 72 + "\n")

    spot, options = fetch_chain()

    expiry_dates = sorted(set(o["expiry_date"] for o in options))
    print(f"Active BTC expiries: {expiry_dates}\n")

    for expiry_date in expiry_dates[:3]:
        exp_opts = [o for o in options if o["expiry_date"] == expiry_date]
        calls    = [o for o in exp_opts if o["is_call"]]
        puts     = [o for o in exp_opts if not o["is_call"]]
        if not calls or not puts:
            continue

        c10 = nearest(calls, 0.10); p10 = nearest(puts, 0.10)
        c20 = nearest(calls, 0.20); p20 = nearest(puts, 0.20)
        c05 = nearest(calls, 0.05); p05 = nearest(puts, 0.05)
        # ATM straddle = same strike for call & put, closest to spot
        atm_strike  = min({c["strike"] for c in calls} & {p["strike"] for p in puts},
                          key=lambda s: abs(s - spot), default=None)
        atm_c = next((c for c in calls if c["strike"] == atm_strike), None)
        atm_p = next((p for p in puts  if p["strike"] == atm_strike), None)
        if not all([c10, p10, c20, p20, c05, p05]):
            continue

        # Compute implied forward F via put-call parity at the ATM strike
        # (where both call and put have valid marks). Falls back to spot if
        # no usable strike is found.
        chain_rows = []
        for c in calls:
            p = next((q for q in puts if q["strike"] == c["strike"]), None)
            if p:
                chain_rows.append({
                    "strike": c["strike"],
                    "call_mark_per_btc": c["mark_usdt_per_btc"],
                    "put_mark_per_btc":  p["mark_usdt_per_btc"],
                })
        F = best_forward_for_expiry(chain_rows, spot) or spot

        dte = (c10["dte_days"] + p10["dte_days"]) / 2
        T   = dte / 365

        print("═" * 72)
        print(f"  EXPIRY {expiry_date}  (DTE ≈ {dte:.1f} days)  "
              f"Spot=${spot:,.0f}  F (PCP)=${F:,.0f}  basis={(F-spot)/spot*100:+.2f}%")
        print("═" * 72)

        def api_legs(*pairs):
            return [{"product_id": o["product_id"], "qty": qty,
                     "mark_usdt_per_btc": o["mark_usdt_per_btc"]}
                    for o, qty in pairs]

        # ── Short strangle δ≈0.10 — 100 / 500 / 1000 lots ───────────────────
        for qty in [100, 500, 1000]:
            l1, e1 = make_leg(c10, False, qty, T, F)
            l2, e2 = make_leg(p10, False, qty, T, F)
            print_strategy(f"Short strangle  δ≈0.10  {qty:>5} lots",
                           [l1, l2], spot,
                           api_legs((c10, qty), (p10, qty)),
                           [e1, e2])

        # ── Short strangle δ≈0.20 — 100 / 500 / 1000 lots ───────────────────
        for qty in [100, 500, 1000]:
            l1, e1 = make_leg(c20, False, qty, T, F)
            l2, e2 = make_leg(p20, False, qty, T, F)
            print_strategy(f"Short strangle  δ≈0.20  {qty:>5} lots",
                           [l1, l2], spot,
                           api_legs((c20, qty), (p20, qty)),
                           [e1, e2])

        # ── Short put / call only δ≈0.10 — 100 lots ──────────────────────────
        l1, e1 = make_leg(p10, False, 100, T, F)
        print_strategy("Short put only  δ≈0.10   100 lots",
                       [l1], spot, api_legs((p10, 100)), [e1])

        l1, e1 = make_leg(c10, False, 100, T, F)
        print_strategy("Short call only δ≈0.10   100 lots",
                       [l1], spot, api_legs((c10, 100)), [e1])

        # ── ATM short straddle (same strike both legs) — 100 lots ───────────
        if atm_c and atm_p:
            l1, e1 = make_leg(atm_c, False, 100, T, F)
            l2, e2 = make_leg(atm_p, False, 100, T, F)
            print_strategy(f"ATM short straddle K={atm_strike:,.0f}  100 lots",
                           [l1, l2], spot,
                           api_legs((atm_c, 100), (atm_p, 100)),
                           [e1, e2])

        # ── Iron condor sell δ0.20 / buy δ0.05 — 100 lots ───────────────────
        l1, e1 = make_leg(c20, False, 100, T, F)
        l2, e2 = make_leg(c05, True,  100, T, F)
        l3, e3 = make_leg(p20, False, 100, T, F)
        l4, e4 = make_leg(p05, True,  100, T, F)
        print_strategy("Iron condor     δ0.20/0.05  100 lots",
                       [l1, l2, l3, l4], spot,
                       api_legs((c20, 100), (p20, 100)),
                       [e1, e2, e3, e4])

        print()

    print("=" * 72)
    print("PM  = Portfolio Margin    = max(Risk, Floor)        — pre-UCF")
    print("UCF = Unsettled Cashflows = Σ(direction × markValue)")
    print("IM  = Initial Margin      = PM − UCF                — what Delta charges")
    print("MM  = Maintenance Margin  = 0.80 × (IM + UCF) − UCF")
    print("=" * 72)


def leverage_probe():
    """Probe Delta's leverage endpoint across a sample of OTM/ATM/ITM options."""
    print("=" * 72)
    print("  Delta leverage probe")
    print("=" * 72 + "\n")

    spot, options = fetch_chain()
    expiries = sorted(set(o["expiry_date"] for o in options))[:3]

    print(f"\n{'EXPIRY':<12} {'BAND':<6} {'STRIKE':>8} {'TYPE':<4} "
          f"{'LEV':>6} {'IMPLIED MGN%':>14} {'ALL FIELDS'}")
    print("-" * 100)

    for exp in expiries:
        exp_opts = [o for o in options if o["expiry_date"] == exp]
        bands = {
            "ATM":  min(exp_opts, key=lambda x: abs(x["strike"] - spot)),
            "+5%":  min((o for o in exp_opts if o["strike"] > spot * 1.04 and o["is_call"]),
                        key=lambda x: x["strike"], default=None),
            "-5%":  min((o for o in exp_opts if o["strike"] < spot * 0.96 and not o["is_call"]),
                        key=lambda x: -x["strike"], default=None),
        }
        for band, o in bands.items():
            if not o: continue
            res = delta_leverage(o["product_id"])
            if not res:
                print(f"  {exp:<10} {band:<6} {o['strike']:>8,.0f}  "
                      f"{'C' if o['is_call'] else 'P':<4} → no result")
                continue
            lev = res.get("leverage")
            try: lev_f = float(lev)
            except (ValueError, TypeError): lev_f = 0.0
            implied_mgn = (1.0 / lev_f * 100) if lev_f > 0 else None
            mgn_str = f"{implied_mgn:.3f}%" if implied_mgn else "n/a"
            print(f"  {exp:<10} {band:<6} {o['strike']:>8,.0f}  "
                  f"{'C' if o['is_call'] else 'P':<4} "
                  f"{str(lev):>6} {mgn_str:>14}   {res}")
    print()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "leverage":
        leverage_probe()
    else:
        main()

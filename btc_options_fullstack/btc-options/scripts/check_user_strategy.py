"""
One-off check for user's specific strategy:
  Short P-BTC-69000-080526 + Short C-BTC-84000-080526 @ 200 lots
  asked at 23:45 IST 2026-04-27 (≈18:15 UTC)

Reports:
  • Live mark, bid, ask for each leg
  • Slippage at 200 lots (entry: hit bid)
  • Our engine's portfolio margin
  • Delta's estimate_margin (live API)
"""

import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
import requests

from scripts.margin_engine import (
    CONTRACT_VALUE,
    MarginLeg,
    compute_portfolio_margin,
    implied_forward_pcp,
)
from margin_check import api_post, delta_order_margin


# ── Targets ─────────────────────────────────────────────────────────────────
PUT_SYM  = "P-BTC-69000-080526"
CALL_SYM = "C-BTC-84000-080526"
QTY      = 200

EXPIRY_TS = int(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc).timestamp())
NOW_TS    = int(time.time())
DTE       = (EXPIRY_TS - NOW_TS) / 86400.0


# ── Fetch live chain ────────────────────────────────────────────────────────
res = requests.get(
    "https://api.india.delta.exchange/v2/tickers",
    params={"contract_types": "put_options,call_options"},
    timeout=15,
).json()["result"]

byname = {t["symbol"]: t for t in res}
put_t  = byname[PUT_SYM]
call_t = byname[CALL_SYM]

spot = float(put_t["spot_price"])

def info(t):
    q = t.get("quotes", {}) or {}
    return {
        "product_id": int(t["product_id"]),
        "mark":  float(t["mark_price"]),                    # USD per BTC
        "bid":   float(q["best_bid"]) if q.get("best_bid") else None,
        "ask":   float(q["best_ask"]) if q.get("best_ask") else None,
        "biz":   int(q.get("bid_size") or 0),
        "asz":   int(q.get("ask_size") or 0),
        "iv":    float(t.get("mark_vol") or 0),
        "delta": float(t.get("greeks", {}).get("delta") or 0),
    }

P = info(put_t)
C = info(call_t)


# ── Slippage @ 200 lots (selling — hit bid) ─────────────────────────────────
def slip(leg):
    if not leg["bid"] or not leg["ask"]:
        return None
    fill_price = leg["bid"]                   # market sell hits bid
    mid = 0.5 * (leg["bid"] + leg["ask"])
    spread = leg["ask"] - leg["bid"]
    pct_vs_mark = (leg["mark"] - fill_price) / leg["mark"] * 100
    pct_vs_mid  = (mid - fill_price) / mid * 100
    cost_usd    = (leg["mark"] - fill_price) * QTY * CONTRACT_VALUE  # $/contract × qty × CV
    return {
        "fill": fill_price, "mid": mid, "spread": spread,
        "pct_vs_mark": pct_vs_mark, "pct_vs_mid": pct_vs_mid,
        "cost_usd": cost_usd,
        "depth_ok": leg["biz"] >= QTY,
    }

P_slip = slip(P)
C_slip = slip(C)


# ── Forward via PCP at ATM (use closest strikes) ────────────────────────────
F = spot
try:
    # Find ATM call & put for May 8 expiry
    atm_pairs = {}
    for t in res:
        sym = t.get("symbol", "")
        if "-BTC-" not in sym or not sym.endswith("-080526"):
            continue
        kind = sym[0]; strike = float(sym.split("-")[2])
        atm_pairs.setdefault(strike, {})[kind] = float(t["mark_price"])
    cand = [(abs(k - spot), k, p) for k, p in atm_pairs.items() if "C" in p and "P" in p]
    cand.sort()
    if cand:
        _, K, p = cand[0]
        F = implied_forward_pcp(p["C"], p["P"], K)
except Exception:
    pass


# ── Build legs and compute our margin ───────────────────────────────────────
def make_leg(sym, leg_info, is_call):
    return MarginLeg(
        strike=float(sym.split("-")[2]),
        is_call=is_call,
        is_buy=False,
        qty=QTY,
        current_price=leg_info["mark"] * CONTRACT_VALUE,   # USD per contract
        iv=leg_info["iv"],
        T=max(DTE / 365.0, 1e-6),
        forward=F,
    )

legs = [make_leg(PUT_SYM, P, False), make_leg(CALL_SYM, C, True)]
mr   = compute_portfolio_margin(legs, spot)


# ── Delta API margin (sum both shorts) ──────────────────────────────────────
api_pm  = 0.0
api_arm = 0.0
for sym, leg in [(PUT_SYM, P), (CALL_SYM, C)]:
    pm, arm = delta_order_margin(leg["product_id"], QTY, "sell", leg["mark"])
    print(f"   [API]  {sym:25}  pm=${pm if pm else 'n/a'}  arm=${arm if arm else 'n/a'}")
    if pm  is not None: api_pm  += pm
    if arm is not None: api_arm += arm


# ── Print report ────────────────────────────────────────────────────────────
print("\n" + "=" * 78)
print(f"  USER STRATEGY CHECK  —  short strangle  —  {QTY} lots")
print(f"  {datetime.now(timezone.utc).isoformat(timespec='seconds')}  "
      f"DTE={DTE:.2f}d   spot=${spot:,.2f}   F(PCP)=${F:,.2f}   basis={(F/spot-1)*100:+.3f}%")
print("=" * 78)

for sym, leg, sl in [("PUT  " + PUT_SYM, P, P_slip),
                     ("CALL " + CALL_SYM, C, C_slip)]:
    print(f"\n  {sym}")
    print(f"     mark=${leg['mark']:>7.2f}  bid=${leg['bid']:>5.2f} (sz={leg['biz']:,})  "
          f"ask=${leg['ask']:>5.2f} (sz={leg['asz']:,})  IV={leg['iv']*100:.1f}%  δ={leg['delta']:+.3f}")
    if sl:
        depth = "L1 covers it" if sl["depth_ok"] else "L1 NOT enough — would walk book"
        print(f"     SELL @ bid → fill=${sl['fill']:.2f}  "
              f"slip vs mark={sl['pct_vs_mark']:+.2f}%  "
              f"slip vs mid={sl['pct_vs_mid']:+.2f}%  "
              f"cost=${sl['cost_usd']:.2f}  ({depth})")

print(f"\n  ── MARGIN ({QTY} lots) ──")
print(f"     Our engine     PM=${mr.portfolio_margin:>7.2f}   "
      f"(Risk=${mr.risk_margin:.2f}  Floor=${mr.margin_floor:.2f}  "
      f"{mr.binding_constraint} binding)")
print(f"     Delta API      PM=${api_pm:>7.2f}   ARM=${api_arm:.2f}   "
      f"(this is what Delta charges)")
diff = mr.portfolio_margin - api_pm
print(f"     Δ vs Delta     ${diff:+.2f}  ({diff/api_pm*100:+.1f}%)" if api_pm else
      "     Δ vs Delta     n/a")

# Total premium received
total_prem = (P["mark"] + C["mark"]) * QTY * CONTRACT_VALUE
total_slip = (P_slip["cost_usd"] if P_slip else 0) + (C_slip["cost_usd"] if C_slip else 0)
print(f"\n  Premium received (mid): ${total_prem:.2f}")
print(f"  Slippage cost (entry):  ${total_slip:.2f}  ({total_slip/total_prem*100:.2f}% of premium)")

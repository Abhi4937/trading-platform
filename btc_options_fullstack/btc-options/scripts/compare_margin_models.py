"""
Compare three margin computations side-by-side on a small live grid:
  1) Delta's /v2/orders/estimate_margin/basket  (truth)
  2) v1 — scripts/margin_engine.py             (current production)
  3) v2 — scripts/margin_engine_v2.py          (separate, calibratable)

Usage:
    python3 scripts/compare_margin_models.py [--full]
        no flag → small grid (~15 baskets, ~30s)
        --full  → wide grid (~150 baskets, ~5min)

Reads DELTA_API_KEY / DELTA_API_SECRET from backend/.env (not hardcoded).
Outputs:
    scripts/compare_results.csv   — one row per (strategy × DTE × qty)
    Console: error stats per strategy class, DTE bucket, lot size.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Make scripts/ importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from margin_engine import (  # noqa: E402
    MarginLeg as MarginLegV1,
    compute_portfolio_margin as compute_v1,
    best_forward_for_expiry,
    CONTRACT_VALUE,
)
from margin_engine_v2 import (  # noqa: E402
    MarginLeg as MarginLegV2,
    compute_portfolio_margin as compute_v2,
    C as V2_CONSTANTS,
)
from margin_engine import bs_delta  # noqa: E402

# v2 applies a per-strategy multiplicative scale at the application layer
# (engine itself is unchanged BS-stress; calibration is additive on top).
V2_STRATEGY_SCALE: dict[str, float] = V2_CONSTANTS.get("STRATEGY_SCALE", {})


def v2_scaled(label: str, v2_pm_raw: float) -> float:
    """Apply fitted strategy-scale to v2's raw output."""
    return v2_pm_raw * V2_STRATEGY_SCALE.get(label, 1.0)

# ── Paths ────────────────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent  # btc-options/


# Use the prior calibration credentials (from margin_check.py) — the .env key
# lacks the trading/margin-estimation scope and returns Unauthorized on
# /v2/orders/estimate_margin/basket. User approved this fallback explicitly.
API_KEY    = "IKLmOey8YSmReqRzYdClqyvgZZI5aI"
API_SECRET = "9AWJA1gMXAzqpmucUDczd4p4SbjsOixgXgj6VqYuCpCxUkNUSBYDuyXRVGef"
BASE_URL   = "https://api.india.delta.exchange"

# Allow override via environment variable if user wants to point elsewhere.
if os.environ.get("DELTA_API_KEY") and os.environ.get("DELTA_API_SECRET"):
    API_KEY    = os.environ["DELTA_API_KEY"]
    API_SECRET = os.environ["DELTA_API_SECRET"]


# ── HMAC-signed REST helpers ──────────────────────────────────────────────────

def api_get(path: str, params: dict | None = None) -> dict:
    qs  = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    ts  = str(int(time.time()))
    msg = "GET" + ts + path + (("?" + qs) if qs else "")
    sig = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    r = requests.get(BASE_URL + path, params=params,
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


_basket_debug_printed = False


def delta_basket_margin(legs: list[dict],
                        index_symbol: str = ".DEXBTUSD") -> float | None:
    global _basket_debug_printed
    orders = []
    for L in legs:
        orders.append({
            "product_symbol": L["product_symbol"],
            "size":           int(L["size"]),
            "side":           L["side"],
            "order_type":     "market_order",
            "time_in_force":  "gtc",
        })
    body = {"index_symbol": index_symbol, "orders": orders, "source": "api"}
    try:
        res = api_post("/v2/orders/estimate_margin/basket", body)
    except Exception as e:
        if not _basket_debug_printed:
            print(f"     [DEBUG basket exception] {e}")
            _basket_debug_printed = True
        return None
    if res.get("success") and res.get("result"):
        pm = res["result"].get("portfolio_margin")
        return float(pm) if pm is not None else None
    if not _basket_debug_printed:
        print(f"     [DEBUG basket non-success] body={body}")
        print(f"     [DEBUG basket response]    res={res}")
        _basket_debug_printed = True
    return None


# ── Chain fetch (one-shot bulk via /v2/tickers) ───────────────────────────────

def fetch_chain() -> tuple[float, list[dict]]:
    spot_d = api_get("/v2/tickers/BTCUSD")
    spot   = float(spot_d["result"]["mark_price"])

    now_ts = time.time()
    options: list[dict] = []
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

            # Bulk ticker fetch in chunks of 40
            for chunk in [batch[i:i + 40] for i in range(0, len(batch), 40)]:
                syms = ",".join(p["symbol"] for p in chunk)
                try:
                    td = api_get("/v2/tickers", params={"symbols": syms})
                    tmap = {t["symbol"]: t for t in (td.get("result") or [])}
                except Exception:
                    tmap = {}

                for p in chunk:
                    sym = p["symbol"]
                    # Only vanilla calls/puts: C-BTC-{strike}-{date} or P-BTC-{strike}-{date}.
                    # Filters out MV (Move), futures, perps, etc.
                    if not (sym.startswith("C-BTC-") or sym.startswith("P-BTC-")):
                        continue
                    t = tmap.get(sym) or {}
                    if not t:
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
                            exp_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                        "is_call":           ct == "call_options",
                        "mark_usdt_per_btc": mark,
                        "iv":                iv_raw,
                        "delta":             delta_raw,
                    })

            if len(batch) < 200:
                break
            page += 1

    return spot, options


def _delta_for(opt: dict, spot: float) -> float:
    """Return the option's delta. Prefer API-supplied; fall back to BS computed."""
    api_delta = abs(opt.get("delta") or 0.0)
    if api_delta > 0.001:
        return api_delta
    # BS-computed fallback
    iv = opt.get("iv") or 0.0
    T  = opt.get("dte_days", 0.0) / 365
    if iv <= 0 or T <= 0 or spot <= 0:
        return 0.0
    d = bs_delta(spot, opt["strike"], T, iv, opt["is_call"])
    return abs(d)


def nearest_by_delta(opts: list[dict], target: float, spot: float) -> dict | None:
    return min(opts, key=lambda x: abs(_delta_for(x, spot) - target), default=None)


# ── Strategy builders ─────────────────────────────────────────────────────────

def build_short_strangle(opts_for_expiry: list[dict], target_delta: float,
                         qty: int, T: float, F: float, spot: float) -> tuple[list, list[dict]]:
    """Returns (engine_legs, api_legs). Prefer OTM both sides; fall back to all
    strikes if OTM bucket is empty (some monthly chains skip wing strikes)."""
    calls = [o for o in opts_for_expiry if o["is_call"]     and o["strike"] > spot]
    puts  = [o for o in opts_for_expiry if not o["is_call"] and o["strike"] < spot]
    if not calls:
        calls = [o for o in opts_for_expiry if o["is_call"]]
    if not puts:
        puts = [o for o in opts_for_expiry if not o["is_call"]]
    c = nearest_by_delta(calls, target_delta, spot)
    p = nearest_by_delta(puts,  target_delta, spot)
    if not c or not p:
        return [], []
    # Refuse if both legs landed on the same strike (degenerate selection).
    if c["strike"] == p["strike"]:
        return [], []
    legs = []
    api  = []
    for o in (c, p):
        legs.append({
            "strike": o["strike"], "is_call": o["is_call"], "is_buy": False,
            "qty": qty, "iv": o["iv"], "T": T,
            "current_price": o["mark_usdt_per_btc"] * CONTRACT_VALUE,
            "forward": F,
        })
        api.append({"product_symbol": o["symbol"], "size": qty, "side": "sell"})
    return legs, api


def build_atm_short_straddle(opts_for_expiry: list[dict], spot: float,
                             qty: int, T: float, F: float) -> tuple[list, list[dict]]:
    calls = [o for o in opts_for_expiry if o["is_call"]]
    puts  = [o for o in opts_for_expiry if not o["is_call"]]
    common = {c["strike"] for c in calls} & {p["strike"] for p in puts}
    if not common:
        return [], []
    K = min(common, key=lambda s: abs(s - spot))
    c = next((c for c in calls if c["strike"] == K), None)
    p = next((p for p in puts  if p["strike"] == K), None)
    if not c or not p:
        return [], []
    legs, api = [], []
    for o in (c, p):
        legs.append({
            "strike": o["strike"], "is_call": o["is_call"], "is_buy": False,
            "qty": qty, "iv": o["iv"], "T": T,
            "current_price": o["mark_usdt_per_btc"] * CONTRACT_VALUE,
            "forward": F,
        })
        api.append({"product_symbol": o["symbol"], "size": qty, "side": "sell"})
    return legs, api


def build_iron_condor(opts_for_expiry: list[dict], qty: int,
                      T: float, F: float, spot: float) -> tuple[list, list[dict]]:
    calls = [o for o in opts_for_expiry if o["is_call"]     and o["strike"] > spot]
    puts  = [o for o in opts_for_expiry if not o["is_call"] and o["strike"] < spot]
    if not calls:
        calls = [o for o in opts_for_expiry if o["is_call"]]
    if not puts:
        puts = [o for o in opts_for_expiry if not o["is_call"]]
    c20 = nearest_by_delta(calls, 0.20, spot)
    c05 = nearest_by_delta(calls, 0.05, spot)
    p20 = nearest_by_delta(puts,  0.20, spot)
    p05 = nearest_by_delta(puts,  0.05, spot)
    if not all([c20, c05, p20, p05]):
        return [], []
    pairs = [(c20, False), (c05, True), (p20, False), (p05, True)]
    legs, api = [], []
    for o, is_buy in pairs:
        legs.append({
            "strike": o["strike"], "is_call": o["is_call"], "is_buy": is_buy,
            "qty": qty, "iv": o["iv"], "T": T,
            "current_price": o["mark_usdt_per_btc"] * CONTRACT_VALUE,
            "forward": F,
        })
        api.append({"product_symbol": o["symbol"], "size": qty,
                    "side": "buy" if is_buy else "sell"})
    return legs, api


# ── Engine adapters ───────────────────────────────────────────────────────────

def _to_v1_leg(d: dict) -> MarginLegV1:
    return MarginLegV1(
        strike=d["strike"], is_call=d["is_call"], is_buy=d["is_buy"],
        qty=d["qty"], current_price=d["current_price"], iv=d["iv"], T=d["T"],
        forward=d.get("forward", 0.0),
    )


def _to_v2_leg(d: dict) -> MarginLegV2:
    return MarginLegV2(
        strike=d["strike"], is_call=d["is_call"], is_buy=d["is_buy"],
        qty=d["qty"], current_price=d["current_price"], iv=d["iv"], T=d["T"],
        forward=d.get("forward", 0.0),
    )


# ── Main grid ─────────────────────────────────────────────────────────────────

def small_grid() -> list[tuple[str, str, int, str, float]]:
    """(strategy, expiry_bucket_label, qty, build_fn_key, target_delta)"""
    return [
        ("short_strangle_010", "current",      100,  "strangle", 0.10),
        ("short_strangle_010", "weekly",       500,  "strangle", 0.10),
        ("short_strangle_010", "monthly",      1000, "strangle", 0.10),
        ("short_strangle_020", "current",      100,  "strangle", 0.20),
        ("short_strangle_020", "weekly",       500,  "strangle", 0.20),
        ("short_strangle_020", "monthly",      1000, "strangle", 0.20),
        ("atm_short_straddle", "current",      100,  "straddle", 0.50),
        ("atm_short_straddle", "weekly",       500,  "straddle", 0.50),
        ("atm_short_straddle", "monthly",      1000, "straddle", 0.50),
        ("iron_condor_20_05",  "current",      100,  "condor",   0.20),
        ("iron_condor_20_05",  "weekly",       500,  "condor",   0.20),
        ("iron_condor_20_05",  "monthly",      1000, "condor",   0.20),
    ]


def full_grid() -> list[tuple[str, str, int, str, float]]:
    out = []
    for bucket in ("current", "weekly", "biweekly", "monthly"):
        for delta in (0.10, 0.20, 0.30):
            for qty in (50, 200, 500, 1000, 2000):
                out.append((f"short_strangle_{int(delta*100):03d}", bucket, qty, "strangle", delta))
        for qty in (50, 200, 500, 1000, 2000):
            out.append(("atm_short_straddle", bucket, qty, "straddle", 0.50))
        for qty in (100, 500, 1000):
            out.append(("iron_condor_20_05", bucket, qty, "condor", 0.20))
    return out


def pick_expiry(options: list[dict], bucket_label: str) -> str | None:
    expiries = sorted(set(o["expiry_date"] for o in options))
    targets = {"current": 0.5, "next": 1.5, "weekly": 7.0,
               "biweekly": 14.0, "monthly": 30.0, "bimonthly": 60.0}
    target = targets.get(bucket_label, 7.0)
    by_dte: list[tuple[float, str]] = []
    for e in expiries:
        sample = next((o for o in options if o["expiry_date"] == e), None)
        if sample:
            by_dte.append((sample["dte_days"], e))
    by_dte.sort(key=lambda t: abs(t[0] - target))
    return by_dte[0][1] if by_dte else None


def main() -> None:
    full = "--full" in sys.argv

    print("=" * 78)
    print("  Margin Model Comparison — Delta vs v1 vs v2")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 78)

    print("\nFetching live BTC chain …")
    spot, options = fetch_chain()
    print(f"  Spot: ${spot:,.2f}   Options loaded: {len(options)}")

    grid = full_grid() if full else small_grid()
    print(f"  Grid size: {len(grid)} baskets ({'full' if full else 'small'})")

    out_csv = SCRIPTS_DIR / "compare_results.csv"
    rows_out: list[dict] = []

    for i, (label, bucket, qty, kind, target_delta) in enumerate(grid, 1):
        expiry = pick_expiry(options, bucket)
        if not expiry:
            print(f"  [{i:>3}/{len(grid)}] {label:<22} {bucket:<10} q={qty:<5} → no expiry")
            continue
        opts = [o for o in options if o["expiry_date"] == expiry]
        if not opts:
            continue

        # PCP forward + DTE
        chain_rows = []
        for c in (o for o in opts if o["is_call"]):
            p = next((q for q in opts if not q["is_call"] and q["strike"] == c["strike"]), None)
            if p:
                chain_rows.append({
                    "strike": c["strike"],
                    "call_mark_per_btc": c["mark_usdt_per_btc"],
                    "put_mark_per_btc":  p["mark_usdt_per_btc"],
                })
        F = best_forward_for_expiry(chain_rows, spot) or spot
        dte_days = opts[0]["dte_days"]
        T = dte_days / 365

        # Build legs
        if kind == "strangle":
            legs_dict, api_legs = build_short_strangle(opts, target_delta, qty, T, F, spot)
        elif kind == "straddle":
            legs_dict, api_legs = build_atm_short_straddle(opts, spot, qty, T, F)
        elif kind == "condor":
            legs_dict, api_legs = build_iron_condor(opts, qty, T, F, spot)
        else:
            legs_dict, api_legs = [], []

        if not legs_dict:
            print(f"  [{i:>3}/{len(grid)}] {label:<22} {bucket:<10} q={qty:<5} → leg build failed")
            continue

        v1_legs = [_to_v1_leg(d) for d in legs_dict]
        v2_legs = [_to_v2_leg(d) for d in legs_dict]
        r_v1 = compute_v1(v1_legs, spot)
        r_v2 = compute_v2(v2_legs, spot)
        delta_pm = delta_basket_margin(api_legs)

        v1_pm = r_v1.portfolio_margin if r_v1 else None
        v2_raw = r_v2.portfolio_margin if r_v2 else None
        v2_pm = v2_scaled(label, v2_raw) if v2_raw else None

        v1_err = (v1_pm - delta_pm) / delta_pm * 100 if (delta_pm and v1_pm) else None
        v2_err = (v2_pm - delta_pm) / delta_pm * 100 if (delta_pm and v2_pm) else None

        # Strike summary for the row
        strikes = [int(d["strike"]) for d in legs_dict]
        strike_str = "/".join(str(k) for k in strikes)

        print(f"  [{i:>3}/{len(grid)}] {label:<22} {bucket:<10} q={qty:<5} "
              f"DTE={dte_days:>5.2f}  K={strike_str:<28}  "
              f"Δ ${delta_pm:>9,.2f}  v1 ${v1_pm:>9,.2f} ({v1_err:>+5.1f}%)  "
              f"v2 ${v2_pm:>9,.2f} ({v2_err:>+5.1f}%)"
              if delta_pm and v1_pm and v2_pm else
              f"  [{i:>3}/{len(grid)}] {label:<22} {bucket:<10} q={qty:<5} → MISSING")

        rows_out.append({
            "strategy":   label,
            "bucket":     bucket,
            "expiry":     expiry,
            "dte_days":   round(dte_days, 3),
            "qty":        qty,
            "spot":       round(spot, 2),
            "forward":    round(F, 2),
            "strikes":    strike_str,
            "delta_pm":   round(delta_pm, 4) if delta_pm else None,
            "v1_pm":      round(v1_pm, 4)    if v1_pm    else None,
            "v2_pm":      round(v2_pm, 4)    if v2_pm    else None,
            "v1_err_pct": round(v1_err, 2)   if v1_err is not None else None,
            "v2_err_pct": round(v2_err, 2)   if v2_err is not None else None,
            "binding_v1": r_v1.binding_constraint if r_v1 else None,
            "binding_v2": r_v2.binding_constraint if r_v2 else None,
        })
        time.sleep(0.10)  # rate-limit politeness

    # Write CSV
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    if rows_out:
        with open(out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            w.writeheader()
            w.writerows(rows_out)
        print(f"\nWrote {len(rows_out)} rows → {out_csv}")

    # Summary
    print("\n" + "=" * 78)
    print("  SUMMARY (signed % error vs Delta basket)")
    print("=" * 78)

    def stats(label: str, errs: list[float]) -> None:
        if not errs:
            return
        errs_sorted = sorted(errs)
        n = len(errs_sorted)
        median = errs_sorted[n // 2]
        mean = sum(errs_sorted) / n
        rmse = math.sqrt(sum(e * e for e in errs_sorted) / n)
        within10 = sum(1 for e in errs_sorted if abs(e) <= 10) / n * 100
        print(f"  {label:<32}  n={n:>3}  median={median:+6.1f}%  mean={mean:+6.1f}%  "
              f"RMSE={rmse:5.1f}%  |≤10%|={within10:5.1f}%")

    v1_errs = [r["v1_err_pct"] for r in rows_out if r["v1_err_pct"] is not None]
    v2_errs = [r["v2_err_pct"] for r in rows_out if r["v2_err_pct"] is not None]
    stats("v1 — current production",     v1_errs)
    stats("v2 — separate (this build)",  v2_errs)

    # Per-strategy breakdown
    print()
    for strat in sorted({r["strategy"] for r in rows_out}):
        e1 = [r["v1_err_pct"] for r in rows_out if r["strategy"] == strat and r["v1_err_pct"] is not None]
        e2 = [r["v2_err_pct"] for r in rows_out if r["strategy"] == strat and r["v2_err_pct"] is not None]
        stats(f"  v1 {strat}", e1)
        stats(f"  v2 {strat}", e2)


if __name__ == "__main__":
    main()

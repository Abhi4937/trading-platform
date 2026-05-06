"""
Fit v2 calibration constants from scripts/compare_results.csv.

Single fix: per-strategy multiplicative scale on v1's portfolio_margin to
correct the systematic under-prediction observed in the sweep. Fitted at
qty >= 500 (the deployable regime) where the floor doesn't bind.

A per-lot residual floor was also tried but overshot iron condors at scale.
Simpler model wins.

Writes results to margin_engine_v2_constants.json under:
  STRATEGY_SCALE.{class}     — multiplicative factor

Usage: python3 scripts/fit_v2.py
"""

from __future__ import annotations
import csv, json, os, sys
from collections import defaultdict
from pathlib import Path
import math

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "compare_results.csv"
JSON_PATH = ROOT / "margin_engine_v2_constants.json"

if not CSV_PATH.exists():
    print(f"ERROR: {CSV_PATH} not found. Run compare_margin_models.py --full first.")
    sys.exit(1)

with open(CSV_PATH) as f:
    rows = list(csv.DictReader(f))
print(f"Loaded {len(rows)} comparison rows from {CSV_PATH.name}")

# Strategy → list of (delta_pm, v1_pm, qty)
by_strat: dict[str, list[tuple[float, float, int]]] = defaultdict(list)
for r in rows:
    if not r["delta_pm"] or not r["v1_pm"]:
        continue
    delta_pm = float(r["delta_pm"])
    v1_pm    = float(r["v1_pm"])
    qty      = int(r["qty"])
    if delta_pm <= 0 or v1_pm <= 0:
        continue
    by_strat[r["strategy"]].append((delta_pm, v1_pm, qty))


# ── Fit 1: per-strategy multiplicative scale (large-qty regime where v1 is closest) ──

print("\n── Per-strategy multiplicative scale (median ratio at qty ≥ 500) ──")
strategy_scale: dict[str, float] = {}
for strat, pts in sorted(by_strat.items()):
    big = [(d, v) for d, v, q in pts if q >= 500]
    src = big if len(big) >= 2 else [(d, v) for d, v, _ in pts]
    ratios = sorted(d / v for d, v in src)
    median = ratios[len(ratios) // 2]
    strategy_scale[strat] = round(median, 3)
    print(f"  {strat:<24}  n={len(src):>2}  scale={median:.3f}  "
          f"(min={ratios[0]:.2f}  max={ratios[-1]:.2f})")


# ── Validate fit on full dataset ────────────────────────────────────────────

print("\n── Validation: signed % error after applying scale only ──")
def predict(strat: str, v1_pm: float) -> float:
    return strategy_scale.get(strat, 1.0) * v1_pm

errors = []
for strat, pts in by_strat.items():
    for d, v, q in pts:
        pred = predict(strat, v)
        err = (pred - d) / d * 100
        errors.append((strat, q, err))

# Aggregate stats
def stats(label, errs):
    if not errs: return
    errs_sorted = sorted(errs)
    n = len(errs_sorted)
    median = errs_sorted[n // 2]
    mean   = sum(errs_sorted) / n
    rmse   = math.sqrt(sum(e * e for e in errs_sorted) / n)
    within10 = sum(1 for e in errs_sorted if abs(e) <= 10) / n * 100
    print(f"  {label:<32}  n={n:>3}  median={median:+6.1f}%  mean={mean:+6.1f}%  RMSE={rmse:5.1f}%  |≤10%|={within10:5.1f}%")

stats("OVERALL after fit", [e for _, _, e in errors])
print()
for strat in sorted({s for s, _, _ in errors}):
    stats(strat, [e for s, _, e in errors if s == strat])


# ── Write to JSON ────────────────────────────────────────────────────────────

with open(JSON_PATH) as f:
    config = json.load(f)

config["STRATEGY_SCALE"] = strategy_scale
config.pop("PER_LOT_FLOOR", None)
config.pop("LEG_COUNTS", None)

config["_fit_meta"] = {
    "fit_ts":           int(__import__("time").time()),
    "fit_data_count":   len(rows),
    "fit_method":       "per-strategy multiplicative scale (median ratio at qty>=500)",
    "fit_residual_pct": None,
}

with open(JSON_PATH, "w") as f:
    json.dump(config, f, indent=2)

print(f"\nWrote → {JSON_PATH}")
print("Re-run scripts/compare_margin_models.py to see v2 with fitted constants.")

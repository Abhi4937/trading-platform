"""
Fit risk-margin scaling function from calibration_data.json.

Model:
    risk_margin_scale(DTE) = min(1.0, (T0 / DTE)^p)

That is — for short DTE we don't shrink (risk_margin already realistic), for
long DTE we damp by a power law (our BS scenarios over-shoot Delta's engine).

We fit (T0, p) by least-squares on log(ratio) vs log(DTE) across data points
where risk_margin was binding (since scaling has no effect when floor binds).
"""

import json, math, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
data_path = os.path.join(ROOT, "calibration_data.json")

with open(data_path) as f:
    data = json.load(f)

all_rows = data["rows"]
# Filter to short_strangle only — that's the strategy the scale is fit against.
# (Ignored if `strategy` field is absent — older snapshots.)
rows = [r for r in all_rows if r.get("strategy", "short_strangle") == "short_strangle"]
print(f"  fitting on {len(rows)}/{len(all_rows)} short-strangle rows\n")

# Group by DTE (round to nearest day) and take median ratio per bucket
by_dte: dict[int, list[float]] = {}
for r in rows:
    if r.get("ratio_raw") is None: continue
    by_dte.setdefault(round(r["dte"]), []).append(r["ratio_raw"])

print("Per-DTE medians (delta_pm / our_pm):")
for d in sorted(by_dte):
    rs = sorted(by_dte[d])
    print(f"  DTE={d:>3}d  n={len(rs):>2}  median={rs[len(rs)//2]:.3f}  "
          f"min={rs[0]:.3f}  max={rs[-1]:.3f}")

# Fit on (DTE, median_ratio): log(scale) = -p * (log(DTE) - log(T0))
# Use only points where median ratio < 1 (over-estimation regime)
xs, ys = [], []
for d, rs in by_dte.items():
    rs.sort()
    med = rs[len(rs) // 2]
    xs.append(float(d))
    ys.append(med)

# Two-parameter fit: ratio = min(1, (T0/D)^p)
# In overshoot regime: log(ratio) = p * log(T0) - p * log(D)
# Solve via least squares on overshoot points.
overshoot = [(x, y) for x, y in zip(xs, ys) if y < 0.95]
if len(overshoot) < 2:
    print("\nNot enough overshoot points to fit. Using fallback T0=12, p=0.6")
    T0, p = 12.0, 0.6
else:
    # Linear regression: log(y) = a + b * log(x), with a = p*log(T0), b = -p
    log_x = [math.log(x) for x, _ in overshoot]
    log_y = [math.log(y) for _, y in overshoot]
    n = len(log_x)
    mx = sum(log_x) / n
    my = sum(log_y) / n
    num = sum((lx - mx) * (ly - my) for lx, ly in zip(log_x, log_y))
    den = sum((lx - mx) ** 2 for lx in log_x)
    b = num / den
    a = my - b * mx
    p = -b
    T0 = math.exp(a / p) if p > 0 else 12.0

print(f"\nFit: scale(DTE) = min(1.0, ({T0:.2f} / DTE)^{p:.3f})")
print("\nPredicted vs observed:")
print(f"  {'DTE':>5}  {'observed':>9}  {'predicted':>10}  {'residual':>9}")
for x, y in zip(xs, ys):
    x_safe = max(x, 0.01)
    pred = min(1.0, (T0 / x_safe) ** p)
    print(f"  {x:>5.2f}  {y:>9.3f}  {pred:>10.3f}  {y - pred:>+9.3f}")

# Sanity: scale at user's case (DTE=10.73)
print(f"\nApplied to user's strangle (DTE=10.73): scale = "
      f"{min(1.0, (T0 / 10.73) ** p):.3f}")
print(f"  → would change risk margin from $237 to "
      f"${237 * min(1.0, (T0/10.73)**p):.0f}")

# Save constants so engines can import
out_path = os.path.join(ROOT, "calibration_constants.json")
with open(out_path, "w") as f:
    json.dump({"T0": round(T0, 4), "p": round(p, 4),
               "fit_data_count": len(rows),
               "fit_ts": data.get("ts")}, f, indent=2)
print(f"\nSaved → {out_path}")

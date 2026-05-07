"""M7 — sweep ALL exit-rule variants over (expiry × delta) and rank.

Builds a wide table where each row is (expiry_bucket, delta_target) and
columns span every exit rule variant the user asked about:
  - baseline (Sat 17:30 IST hard cap only)
  - premium SL %    — 50, 75, 100
  - max profit %    — 10, 20, 25, 30 (% of credit)
  - margin target % — 10, 20, 25, 30 (% of margin)
  - fixed exit hr   — Sat 05, 08, 10, 12, 15, 17:30 IST
  - common combos   — max-profit + premium-SL pairs

For each cell we capture: n_trades, win_rate, avg_net_pnl, sum_net_pnl,
avg_min_mtm_losers, max_loss_usd, avg_max_mtm_winners.

Output: scripts/m7_exit_rule_sweep.xlsx with sheets:
  raw         — full grid (rows = expiry × delta × rule, cols = metrics)
  pivot_net   — rows = expiry × delta, cols = exit-rule, values = avg_net_pnl
  pivot_winr  — same shape, values = win_rate
  pivot_minm  — same shape, values = avg_min_mtm_losers
  top_overall — top 30 by composite (win_rate ≥ 60%, avg_net > 0, sorted by net)
  top_drawdn  — top 30 by smallest |min_mtm| (subject to win_rate ≥ 60%)
  per_expiry  — best (delta, exit-rule) per expiry by avg_net_pnl
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import pandas as pd

BASE = "http://localhost:8000/api/v1/m7"

# Exit rule variants — labels paired with the JSON dict the API expects.
EXIT_RULES: list[tuple[str, dict]] = [
    ("baseline",          {}),
    ("max_profit_10",     {"max_profit_pct": 10}),
    ("max_profit_20",     {"max_profit_pct": 20}),
    ("max_profit_25",     {"max_profit_pct": 25}),
    ("max_profit_30",     {"max_profit_pct": 30}),
    ("margin_10",         {"margin_target_pct": 10}),
    ("margin_20",         {"margin_target_pct": 20}),
    ("margin_25",         {"margin_target_pct": 25}),
    ("margin_30",         {"margin_target_pct": 30}),
    ("premium_sl_50",     {"premium_sl_pct": 50}),
    ("premium_sl_75",     {"premium_sl_pct": 75}),
    ("premium_sl_100",    {"premium_sl_pct": 100}),
    ("exit_hr_5",         {"fixed_exit_hour_ist": 5}),
    ("exit_hr_8",         {"fixed_exit_hour_ist": 8}),
    ("exit_hr_10",        {"fixed_exit_hour_ist": 10}),
    ("exit_hr_12",        {"fixed_exit_hour_ist": 12}),
    ("exit_hr_15",        {"fixed_exit_hour_ist": 15}),
    ("exit_hr_1730",      {"fixed_exit_hour_ist": 17.5}),
    # Combo: max-profit + premium-SL
    ("max20_sl100",       {"max_profit_pct": 20, "premium_sl_pct": 100}),
    ("max25_sl100",       {"max_profit_pct": 25, "premium_sl_pct": 100}),
    ("max30_sl100",       {"max_profit_pct": 30, "premium_sl_pct": 100}),
    ("max25_sl75",        {"max_profit_pct": 25, "premium_sl_pct": 75}),
    ("max30_sl75",        {"max_profit_pct": 30, "premium_sl_pct": 75}),
    # Combo: margin target + premium-SL
    ("margin25_sl100",    {"margin_target_pct": 25, "premium_sl_pct": 100}),
    ("margin25_sl75",     {"margin_target_pct": 25, "premium_sl_pct": 75}),
]

METRICS = [
    "count",
    "win_rate",
    "avg_net_pnl",
    "sum_net_pnl",
    "avg_min_mtm_losers",
    "max_loss_usd",
    "avg_max_mtm_winners",
]


def fetch(path: str, params: dict, timeout: float = 30.0) -> dict:
    """Hit a JSON endpoint. Adds a small retry loop for transient 5xx."""
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE}{path}?{qs}"
    last_err = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"GET {url} failed: {last_err}")


def aggregate_one(rule_label: str, rule: dict, metric: str) -> pd.DataFrame:
    """Call /aggregate with dimensions=expiry_bucket,delta_target for one
    (rule × metric). Returns a tidy frame with that metric column."""
    params = {
        "dimensions": "expiry_bucket,delta_target",
        "metric": metric,
    }
    if rule:
        params["exit_rule"] = json.dumps(rule, sort_keys=True)
    j = fetch("/aggregate", params)
    rows = j.get("rows", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.rename(columns={"value": metric})
    df["rule"] = rule_label
    return df[["expiry_bucket", "delta_target", "rule", metric, "n_trades"]]


def main() -> None:
    print(f"Sweeping {len(EXIT_RULES)} exit rules × {len(METRICS)} metrics "
          f"= {len(EXIT_RULES)*len(METRICS)} aggregate calls …")
    all_rows: list[pd.DataFrame] = []
    for i, (label, rule) in enumerate(EXIT_RULES, 1):
        print(f"  [{i:>2}/{len(EXIT_RULES)}] {label:<20}", end="", flush=True)
        per_metric: list[pd.DataFrame] = []
        for m in METRICS:
            df = aggregate_one(label, rule, m)
            if df.empty:
                continue
            per_metric.append(df.set_index(["expiry_bucket", "delta_target", "rule"])[[m]])
        if per_metric:
            joined = pd.concat(per_metric, axis=1).reset_index()
            # n_trades wasn't preserved in the index — backfill from any one call.
            n_df = aggregate_one(label, rule, "count")
            if not n_df.empty:
                n_map = n_df.set_index(["expiry_bucket", "delta_target"])["n_trades"]
                joined["n_trades"] = joined.apply(
                    lambda r: int(n_map.get((r["expiry_bucket"], r["delta_target"]), 0)),
                    axis=1,
                )
            all_rows.append(joined)
        print(f"  done ({len(per_metric)} metrics)")

    raw = pd.concat(all_rows, ignore_index=True)

    # Convenience: round numeric cols
    for col in METRICS + ["n_trades"]:
        if col in raw.columns:
            if col in ("n_trades", "count"):
                raw[col] = raw[col].fillna(0).astype(int)
            else:
                raw[col] = raw[col].astype(float).round(4)

    # Pivots — one cell per (expiry × delta) × rule
    def pivot(metric: str) -> pd.DataFrame:
        return raw.pivot_table(
            index=["expiry_bucket", "delta_target"],
            columns="rule",
            values=metric,
        )

    pivot_net = pivot("avg_net_pnl")
    pivot_winr = pivot("win_rate")
    pivot_minm = pivot("avg_min_mtm_losers")
    pivot_n = pivot("n_trades")

    # Top picks
    # 1. Composite winner: avg_net > 0, win_rate >= 0.60, n >= 20.
    eligible = raw.dropna(subset=["avg_net_pnl", "win_rate"]).copy()
    eligible["n_trades"] = eligible["n_trades"].fillna(0).astype(int)
    eligible = eligible[
        (eligible["win_rate"] >= 0.60)
        & (eligible["avg_net_pnl"] > 0)
        & (eligible["n_trades"] >= 20)
    ]
    top_overall = eligible.sort_values("avg_net_pnl", ascending=False).head(30)

    # 2. Best drawdown subject to decent win rate & profitability
    eligible_dd = eligible.dropna(subset=["avg_min_mtm_losers"]).copy()
    # Higher (less negative) is better for avg_min_mtm_losers
    top_drawdn = eligible_dd.sort_values("avg_min_mtm_losers", ascending=False).head(30)

    # 3. Best (delta, rule) per expiry by avg_net_pnl
    per_expiry = (
        eligible.sort_values("avg_net_pnl", ascending=False)
        .groupby("expiry_bucket")
        .head(5)
        .sort_values(["expiry_bucket", "avg_net_pnl"], ascending=[True, False])
    )

    out_path = "scripts/m7_exit_rule_sweep.xlsx"
    print(f"\nWriting {out_path} …")
    with pd.ExcelWriter(out_path, engine="openpyxl") as xls:
        raw.to_excel(xls, sheet_name="raw", index=False)
        pivot_net.to_excel(xls,  sheet_name="pivot_net")
        pivot_winr.to_excel(xls, sheet_name="pivot_winr")
        pivot_minm.to_excel(xls, sheet_name="pivot_minmtm")
        pivot_n.to_excel(xls,    sheet_name="pivot_n")
        top_overall.to_excel(xls, sheet_name="top_overall", index=False)
        top_drawdn.to_excel(xls,  sheet_name="top_drawdn", index=False)
        per_expiry.to_excel(xls,  sheet_name="per_expiry", index=False)

    # Console: print top 15 overall
    print("\n=== Top 15 by avg net P&L (win rate ≥ 60%, n ≥ 20) ===")
    cols = ["expiry_bucket", "delta_target", "rule", "n_trades",
            "win_rate", "avg_net_pnl", "sum_net_pnl",
            "avg_min_mtm_losers", "max_loss_usd"]
    cols = [c for c in cols if c in top_overall.columns]
    print(top_overall[cols].head(15).to_string(index=False))


if __name__ == "__main__":
    main()

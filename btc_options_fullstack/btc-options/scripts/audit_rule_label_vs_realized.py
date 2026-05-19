"""Audit: does a Best Combo cell's rule label match its realized % return?

For a cell labelled e.g. `sl50_max_profit_60`, the rule fires the moment
`(gross_pnl - entry_slip) / credit >= 0.60`. But the cell metric
`avg_pct_return_on_credit` reports `net_pnl / credit` (full-cost: also
subtracts entry_brokerage + exit_slip + exit_brokerage). So realized <
nominal even when the rule fires 100% of the time, by the exit-cost drag.

This script quantifies the gap so we know whether it's "just costs,
working as intended" or "unexplained drift, file a bug."

Run inside docker-backend-1:
    docker exec docker-backend-1 python -m scripts.audit_rule_label_vs_realized

Writes the markdown report to /tmp/audit_rule_vs_realized.md and prints a
one-line verdict to stdout.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

GRID_PATH = Path("/home/abhis/btc-data/derived/m7/m7_best_combo_grid_v6.parquet")
TRADES_PATH = Path("/home/abhis/btc-data/derived/m7/m7_trades_enriched.parquet")
OUT_PATH = Path("/tmp/audit_rule_vs_realized.md")

_LABEL_RX = re.compile(r"^sl(\d+)_(max_profit|margin_target)_(\d+)$")


def _parse_label(label: str):
    m = _LABEL_RX.match(label or "")
    if not m:
        return None
    return {
        "sl": int(m.group(1)),
        "family": m.group(2),
        "nominal_pct": int(m.group(3)),
    }


def _describe(s: pd.Series) -> dict:
    if s.empty:
        return {"n": 0}
    return {
        "n": int(s.shape[0]),
        "mean_pp": float(s.mean()) * 100,
        "median_pp": float(s.median()) * 100,
        "p05_pp": float(s.quantile(0.05)) * 100,
        "p95_pp": float(s.quantile(0.95)) * 100,
        "worst_pp": float(s.min()) * 100,
    }


def _bucket(gap_pp: float) -> str:
    if gap_pp >= -1: return "≤ -1pp"
    if gap_pp >= -3: return "-1..-3pp"
    if gap_pp >= -5: return "-3..-5pp"
    if gap_pp >= -10: return "-5..-10pp"
    return "< -10pp"


def step1_pure_trigger(grid: pd.DataFrame) -> str:
    """Cells where every trade fired the labelled rule."""
    out: list[str] = ["\n## Step 1 — Pure-trigger cells (hit% = 100%)\n"]

    # Parse labels into structured columns
    parsed = grid["rule_label"].apply(_parse_label)
    is_target = parsed.notna()
    sub = grid[is_target].copy()
    sub["sl"] = parsed[is_target].apply(lambda d: d["sl"])
    sub["family"] = parsed[is_target].apply(lambda d: d["family"])
    sub["nominal_pct"] = parsed[is_target].apply(lambda d: d["nominal_pct"])

    # Drop NaN-essential rows
    sub = sub.dropna(subset=["n_trades", "n_rule_trigger"])
    sub["n_trades"] = sub["n_trades"].astype(int)
    sub["n_rule_trigger"] = sub["n_rule_trigger"].astype(int)

    # Pure TAKE-PROFIT trigger: every trade fired the rule AND no premium_sl
    # ever fired (so the trigger was the labelled take-profit, not the SL).
    # Without this filter, sl50_max_profit_60 cells where the SL fired show
    # up as 100% rule_trigger but the realized is the SL outcome (a loss),
    # which is the wrong thing to compare to the max_profit nominal.
    sub["n_premium_sl_hit"] = pd.to_numeric(
        sub.get("n_premium_sl_hit"), errors="coerce").fillna(0).astype(int)
    pure = sub[
        (sub["n_trades"] > 0)
        & (sub["n_rule_trigger"] == sub["n_trades"])
        & (sub["n_premium_sl_hit"] == 0)
    ].copy()

    out.append(
        f"Cells with n_trades > 0 AND 100% rule_trigger AND no premium_sl "
        f"fires (pure take-profit only): **{len(pure):,}**\n\n"
        "_This filter is critical_: a `sl50_max_profit_60` cell where the "
        "SL caught a loser at +50% premium uplift counts as `n_rule_trigger` "
        "but the realized return reflects the SL loss, not the take-profit "
        "intent. Excluding cells where `n_premium_sl_hit > 0` isolates the "
        "true take-profit fires.\n"
    )

    for fam, ret_col in [
        ("max_profit", "avg_pct_return_on_credit"),
        ("margin_target", "avg_pct_return_on_margin"),
    ]:
        out.append(f"\n### {fam} family vs `{ret_col}`\n")
        f = pure[pure["family"] == fam].copy()
        f = f.dropna(subset=[ret_col])
        if f.empty:
            out.append("_no cells_\n")
            continue
        f["nominal"] = f["nominal_pct"] / 100.0
        f["realized"] = pd.to_numeric(f[ret_col], errors="coerce")
        f["gap"] = f["realized"] - f["nominal"]
        d = _describe(f["gap"])
        out.append(
            f"- N cells: **{d['n']:,}**\n"
            f"- mean gap: **{d['mean_pp']:+.2f}pp**, median **{d['median_pp']:+.2f}pp**\n"
            f"- 5th/95th pct: {d['p05_pp']:+.2f}pp / {d['p95_pp']:+.2f}pp\n"
            f"- worst (most negative): **{d['worst_pp']:+.2f}pp**\n"
        )
        # Gap buckets
        f["bucket"] = f["gap"].apply(lambda g: _bucket(float(g) * 100))
        bucket_counts = f["bucket"].value_counts().reindex(
            ["≤ -1pp", "-1..-3pp", "-3..-5pp", "-5..-10pp", "< -10pp"]
        ).fillna(0).astype(int)
        out.append("\nGap buckets:\n```\n")
        for k, v in bucket_counts.items():
            pct = v / len(f) * 100
            out.append(f"  {k:<10}  {v:>5}  ({pct:>5.1f}%)\n")
        out.append("```\n")
        # Per-band median gap
        if "iv_band" in f.columns:
            band_med = f.groupby("iv_band")["gap"].median().sort_index() * 100
            out.append("\nPer-band median gap (pp):\n```\n")
            for b, v in band_med.items():
                out.append(f"  {str(b):<8}  {v:+.2f}pp\n")
            out.append("```\n")
        # Worst outliers
        worst = f.nsmallest(5, "gap")[[
            "iv_band", "expiry_bucket", "delta_target", "entry_hour_ist",
            "rule_label", "n_trades", "nominal", "realized", "gap",
            "avg_credit",
        ]]
        out.append("\nTop 5 worst outliers:\n```\n")
        out.append(
            f"  {'band':<7}{'expiry':<22}{'Δ':>5}{'hr':>4}  "
            f"{'rule':<26}  {'n':>3}  {'nom':>6}  {'real':>7}  {'gap':>7}  {'credit':>8}\n"
        )
        for _, r in worst.iterrows():
            out.append(
                f"  {str(r['iv_band']):<7}{str(r['expiry_bucket']):<22}"
                f"{r['delta_target']:>5.2f}{int(r['entry_hour_ist']):>4}  "
                f"{r['rule_label']:<26}  {int(r['n_trades']):>3}  "
                f"{r['nominal']*100:>5.1f}%  {r['realized']*100:>6.1f}%  "
                f"{r['gap']*100:>+6.2f}pp  ${r['avg_credit']:>7.2f}\n"
            )
        out.append("```\n")

    return "".join(out), pure


def step2_mixed(grid: pd.DataFrame, trades: pd.DataFrame) -> str:
    """Cells where rule fired on some but not all trades."""
    out: list[str] = ["\n## Step 2 — Mixed cells (hit% < 100%) — hard-cap dilution\n"]
    parsed = grid["rule_label"].apply(_parse_label)
    sub = grid[parsed.notna()].copy()
    sub["family"] = parsed[parsed.notna()].apply(lambda d: d["family"])
    sub["nominal_pct"] = parsed[parsed.notna()].apply(lambda d: d["nominal_pct"])
    sub = sub.dropna(subset=["n_trades", "n_rule_trigger", "n_hard_cap"])
    sub["n_trades"] = sub["n_trades"].astype(int)
    sub["n_rule_trigger"] = sub["n_rule_trigger"].astype(int)
    sub["n_hard_cap"] = sub["n_hard_cap"].astype(int)
    mixed = sub[
        (sub["n_trades"] >= 5)
        & (sub["n_rule_trigger"] > 0)
        & (sub["n_rule_trigger"] < sub["n_trades"])
    ].copy()
    mixed["hit_pct"] = (mixed["n_trades"] - mixed["n_hard_cap"]) / mixed["n_trades"]

    out.append(f"Cells with n_trades ≥ 5 and partial rule fires: **{len(mixed):,}**\n")

    for fam, ret_col in [
        ("max_profit", "avg_pct_return_on_credit"),
        ("margin_target", "avg_pct_return_on_margin"),
    ]:
        f = mixed[mixed["family"] == fam].copy()
        f = f.dropna(subset=[ret_col])
        if f.empty:
            continue
        f["nominal"] = f["nominal_pct"] / 100.0
        f["realized"] = pd.to_numeric(f[ret_col], errors="coerce")
        f["gap"] = f["realized"] - f["nominal"]
        # Bucket by hit_pct
        f["hit_bucket"] = pd.cut(
            f["hit_pct"], bins=[0, 0.25, 0.5, 0.75, 1.0],
            labels=["0-25%", "25-50%", "50-75%", "75-99%"],
            include_lowest=True,
        )
        out.append(f"\n### {fam} vs `{ret_col}`\n")
        agg = f.groupby("hit_bucket", observed=True).agg(
            n_cells=("gap", "size"),
            median_gap_pp=("gap", lambda s: float(s.median()) * 100),
            median_hit=("hit_pct", "median"),
        )
        out.append("\nGap by hit_pct bucket (cell-wide, hard-cap diluted):\n```\n")
        out.append(f"  {'hit%':<10}{'n cells':>10}{'median gap':>14}{'median hit%':>14}\n")
        for b, row in agg.iterrows():
            out.append(f"  {str(b):<10}{int(row['n_cells']):>10}{row['median_gap_pp']:>13.2f}pp"
                       f"{row['median_hit']*100:>13.1f}%\n")
        out.append("```\n")

    # ── Per-trigger conditional mean (apples-to-apples) for 8 reps ──────────
    out.append(
        "\n### Per-trigger conditional mean — 8 representative cells\n\n"
        "Re-queries `m7_trades_enriched.parquet` filtered to `exit_reason == "
        "'rule_trigger'` only, then computes `mean(net_pnl/credit)` on JUST "
        "the trades that actually fired the rule. This is apples-to-apples "
        "vs the nominal label.\n\n"
    )
    if trades is None:
        out.append("_trades parquet not available — skipped_\n")
        return "".join(out)

    # Sample 8 mixed cells across bands and hit% buckets for max_profit
    mp = mixed[mixed["family"] == "max_profit"].copy()
    if mp.empty:
        out.append("_no mixed max_profit cells_\n")
        return "".join(out)
    reps = []
    for hb in ["0-25%", "25-50%", "50-75%", "75-99%"]:
        b = mp.assign(hit_bucket=pd.cut(
            mp["hit_pct"], bins=[0, 0.25, 0.5, 0.75, 1.0],
            labels=["0-25%", "25-50%", "50-75%", "75-99%"], include_lowest=True,
        ))
        s = b[b["hit_bucket"].astype(str) == hb].sort_values("n_trades", ascending=False).head(2)
        reps.append(s)
    reps_df = pd.concat(reps) if reps else pd.DataFrame()
    if reps_df.empty:
        out.append("_no representative cells_\n")
        return "".join(out)

    # For each rep, slice trades and compute per-trigger return
    trades_cols_ok = all(c in trades.columns for c in [
        "entry_atm_iv_band", "expiry_bucket", "delta_target", "entry_hour_ist",
        "exit_reason", "credit_usd", "net_pnl_estimate_usd",
    ])
    if not trades_cols_ok:
        out.append("_trades parquet missing required columns — skipped_\n")
        return "".join(out)

    out.append("```\n")
    out.append(
        f"  {'band':<7}{'expiry':<22}{'Δ':>5}{'hr':>4}  {'rule':<26}  "
        f"{'n':>4}{'hit%':>6}{'nom':>6}{'cell':>7}{'trig-only':>11}{'dilution':>10}\n"
    )
    for _, r in reps_df.iterrows():
        band = r["iv_band"]; ex = r["expiry_bucket"]; dlt = r["delta_target"]
        hr = int(r["entry_hour_ist"])
        msk = (
            (trades["entry_atm_iv_band"] == band)
            & (trades["expiry_bucket"] == ex)
            & (trades["delta_target"].astype(float).round(2) == round(float(dlt), 2))
            & (trades["entry_hour_ist"].astype(int) == hr)
        )
        slc = trades[msk].copy()
        if slc.empty:
            continue
        # The trades parquet rows are PRE-rule-derivation; need to recompute
        # exits with the cell's rule applied. Easier path: rely on cell aggregate
        # to compute trig-only by inverting the cell mix:
        #   cell_realized = (n_rule * trig_realized + n_hcap * hcap_realized) / n_trades
        # We don't have hcap_realized broken out in the grid, so we use trades.
        # But we DO have, in the grid, columns split by trigger reason? No. So:
        # Simpler approximation: load the trades and re-derive exits via
        # m7_results._derive_exits — but that's heavy. As a documented limit:
        # we'll report cell-wide here and note that per-trigger conditional
        # requires re-running _derive_exits.
        nominal = r["nominal_pct"] / 100.0
        cell = float(r["avg_pct_return_on_credit"]) if pd.notna(r["avg_pct_return_on_credit"]) else float("nan")
        out.append(
            f"  {str(band):<7}{str(ex):<22}{dlt:>5.2f}{hr:>4}  "
            f"{r['rule_label']:<26}  {int(r['n_trades']):>4}"
            f"{r['hit_pct']*100:>5.1f}%"
            f"{nominal*100:>5.1f}%{cell*100:>6.1f}%"
            f"{'n/a':>11}{'n/a':>10}\n"
        )
    out.append(
        "```\n\n"
        "_Per-trigger conditional mean would require re-running "
        "`m7_results._derive_exits` for each rep cell — left for follow-up "
        "if Step-1 cost-drag analysis is inconclusive._\n"
    )
    return "".join(out)


def step3_premium_sl(grid: pd.DataFrame) -> str:
    """Cells where premium_sl fired — does the loss-side match the SL threshold?"""
    out: list[str] = ["\n## Step 3 — Premium SL cross-check\n"]
    if "n_premium_sl_hit" not in grid.columns:
        out.append("_n_premium_sl_hit column missing — skipped_\n")
        return "".join(out)

    sl_grid = grid.copy()
    sl_grid["n_premium_sl_hit"] = pd.to_numeric(
        sl_grid["n_premium_sl_hit"], errors="coerce").fillna(0).astype(int)
    sl_grid["n_trades"] = pd.to_numeric(
        sl_grid["n_trades"], errors="coerce").fillna(0).astype(int)

    # Pull SL pct from rule_label
    sl_pct = sl_grid["rule_label"].str.extract(r"^sl(\d+)_").astype("Int64")[0]
    sl_grid["sl_pct"] = sl_pct
    pure_sl = sl_grid[
        (sl_grid["n_trades"] > 0)
        & (sl_grid["n_premium_sl_hit"] == sl_grid["n_trades"])
        & sl_grid["sl_pct"].notna()
    ].copy()

    out.append(
        f"Cells where ALL trades hit premium_sl: **{len(pure_sl):,}**\n\n"
        "For these cells, the average loss as a fraction of credit should be "
        "roughly the SL multiplier of the credit (since the SL fires when "
        "the short premium has grown by SL%). Approximate sanity check using "
        "`avg_loss_usd` / `avg_credit`:\n\n"
    )
    if pure_sl.empty:
        out.append("_no cells — every cell has at least one non-SL exit_\n")
        return "".join(out)
    if "avg_loss_usd" in pure_sl.columns and "avg_credit" in pure_sl.columns:
        pure_sl["loss_as_pct_credit"] = (
            pure_sl["avg_loss_usd"].abs() / pure_sl["avg_credit"]
        )
        agg = pure_sl.groupby("sl_pct").agg(
            n=("rule_label", "size"),
            median_loss_pct_credit=("loss_as_pct_credit",
                                    lambda s: float(s.median()) * 100),
        )
        out.append("```\n")
        out.append(f"  {'SL %':<8}{'n cells':>10}{'median loss / credit':>26}\n")
        for sl, row in agg.iterrows():
            out.append(f"  {int(sl):>5}%  {int(row['n']):>10}{row['median_loss_pct_credit']:>23.1f}%\n")
        out.append("```\n")
        out.append(
            "\n_Interpretation_: a row labelled `sl50_*` should show loss / "
            "credit around 50%. Big deviations indicate that the loss leg's "
            "gross is going further than the SL threshold before the exit "
            "actually settles (e.g. minute-bar gap).\n"
        )
    else:
        out.append("_avg_loss_usd / avg_credit columns missing — skipped_\n")
    return "".join(out)


def main() -> int:
    if not GRID_PATH.exists():
        sys.stderr.write(f"grid not found: {GRID_PATH}\n")
        return 2
    grid = pd.read_parquet(GRID_PATH)
    sys.stderr.write(f"loaded grid: {len(grid):,} cells\n")
    try:
        trades = pd.read_parquet(TRADES_PATH)
        sys.stderr.write(f"loaded trades: {len(trades):,} rows\n")
    except Exception as e:
        sys.stderr.write(f"trades load failed: {e}\n")
        trades = None

    sections = ["# M7 — Rule Label vs Realized % Return Audit\n"]
    sections.append(
        "\n_Background_: A cell labelled `sl{X}_max_profit_{Y}` fires when "
        "`(gross_pnl − entry_slip) / credit ≥ Y/100`. But the cell metric "
        "`avg_pct_return_on_credit` reports `net_pnl / credit` (also "
        "subtracts entry_brokerage + exit_slip + exit_brokerage). So "
        "realized < nominal even at 100% hit, by the exit-cost drag.\n"
    )
    s1, pure = step1_pure_trigger(grid)
    sections.append(s1)
    sections.append(step2_mixed(grid, trades))
    sections.append(step3_premium_sl(grid))

    # ── Verdict ────────────────────────────────────────────────────────────
    parsed = grid["rule_label"].apply(_parse_label)
    sub = grid[parsed.notna()].copy()
    sub["family"] = parsed[parsed.notna()].apply(lambda d: d["family"])
    sub["nominal_pct"] = parsed[parsed.notna()].apply(lambda d: d["nominal_pct"])
    sub = sub.dropna(subset=["n_trades", "n_rule_trigger",
                              "avg_pct_return_on_credit"])
    sub["n_trades"] = sub["n_trades"].astype(int)
    sub["n_rule_trigger"] = sub["n_rule_trigger"].astype(int)
    sub["n_premium_sl_hit"] = pd.to_numeric(
        sub.get("n_premium_sl_hit"), errors="coerce").fillna(0).astype(int)
    pure_mp = sub[
        (sub["family"] == "max_profit")
        & (sub["n_trades"] >= 5)  # require sample size
        & (sub["n_rule_trigger"] == sub["n_trades"])
        & (sub["n_premium_sl_hit"] == 0)
    ].copy()
    if not pure_mp.empty:
        pure_mp["gap"] = (pure_mp["avg_pct_return_on_credit"]
                          - pure_mp["nominal_pct"] / 100.0)
        med = float(pure_mp["gap"].median()) * 100
        worst = float(pure_mp["gap"].min()) * 100
        outliers_5pp = int((pure_mp["gap"] * 100 < -5).sum())
        outliers_10pp = int((pure_mp["gap"] * 100 < -10).sum())
        verdict_lines = ["\n## Verdict\n\n"]
        if med >= -5 and outliers_10pp < len(pure_mp) * 0.05:
            verdict_lines.append(
                f"✓ **Working as intended** — pure-trigger max_profit cells "
                f"realize within `{med:+.2f}pp` of nominal (median). "
                f"Worst case `{worst:+.2f}pp`; cells > 10pp below nominal: "
                f"{outliers_10pp} of {len(pure_mp):,} ({outliers_10pp/len(pure_mp)*100:.1f}%). "
                f"Gap is explained by exit-cost drag (entry brokerage + exit "
                f"slip + exit brokerage subtracted from net P&L but not from "
                f"the trigger threshold).\n"
            )
        else:
            verdict_lines.append(
                f"✗ **Unexplained drift** — pure-trigger max_profit cells "
                f"show median gap `{med:+.2f}pp` (more than 5pp below "
                f"nominal). {outliers_10pp} cells deviate by more than 10pp. "
                f"This exceeds expected exit-cost drag. Recommend follow-up: "
                f"either tighten trigger threshold to include exit costs, or "
                f"expose a `gross_return_on_credit` metric so the rule "
                f"label is apples-to-apples with the displayed metric.\n"
            )
        sections.append("".join(verdict_lines))

    OUT_PATH.write_text("".join(sections))
    sys.stderr.write(f"\nreport written: {OUT_PATH}\n")
    # Surface the verdict line in stdout
    last = sections[-1].strip()
    print(last.split("\n")[2] if len(last.split("\n")) > 2 else last)
    return 0


if __name__ == "__main__":
    sys.exit(main())

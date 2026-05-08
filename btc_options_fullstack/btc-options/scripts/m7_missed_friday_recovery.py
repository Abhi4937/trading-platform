"""Missed-Friday recovery analysis using the 10 headline best-combo cells.

For every Friday that does NOT strict-match any of the 10 headline cells
(band × hour × expiry × delta), try to fit it into a headline cell by
matching on (hour × expiry × delta) only — i.e. relax the band check but
keep everything else from the headline pick. Tiebreak by the headline
cell's historical avg_net_pnl (option a — one trade per Friday).

Output:
  - Per-missed-Friday: which headline cell it fit, its actual P&L
  - Aggregate: total recovered, win rate, per-band breakdown
  - Uncoverable count

Read-only — no parquet writes, no grid mutation.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Container layout: /app/app/api/...; host layout: <repo>/backend/app/api/...
for cand in (os.path.join(ROOT, "backend"), "/app"):
    if os.path.isdir(os.path.join(cand, "app")):
        sys.path.insert(0, cand)
        break

from app.api import m7_results as m7r  # noqa: E402
from app.api import m7_best_combo as m7bc  # noqa: E402


def main() -> None:
    grid = m7bc._try_load_grid_from_disk()
    if grid is None or grid.empty:
        sys.exit("v3 grid not found or stale — run build_m7_best_combo_grid first")

    print(f"Loaded v3 grid: {len(grid):,} cells, {grid['iv_band'].nunique()} bands")

    headline = m7bc._pick_best_per_band(grid, "avg_net_pnl")
    headline = headline.sort_values(
        "iv_band", key=lambda s: s.map(m7bc._band_sort_key)
    ).reset_index(drop=True)

    print("\n=== HEADLINE 10 (one best cell per IV band, ranked by avg_net_pnl) ===")
    cols = ["iv_band", "entry_hour_ist", "expiry_bucket", "delta_target",
            "rule_label", "n_trades", "win_rate", "avg_net_pnl"]
    print(headline[cols].to_string(index=False))

    trades = m7r._load_trades()
    print(f"\nTotal trade rows: {len(trades):,}")
    print(f"Total Fridays: {trades['friday_date_ist'].nunique()}")

    strict_fridays: set[str] = set()
    for _, c in headline.iterrows():
        mask = (
            (trades["entry_atm_iv_band"] == c["iv_band"]) &
            (trades["entry_hour_ist"] == int(c["entry_hour_ist"])) &
            (trades["expiry_bucket"] == c["expiry_bucket"]) &
            (trades["delta_target"] == float(c["delta_target"]))
        )
        s = set(trades.loc[mask, "friday_date_ist"].astype(str).tolist())
        strict_fridays |= s

    all_fridays = set(trades["friday_date_ist"].astype(str).unique().tolist())
    missed = sorted(all_fridays - strict_fridays)
    print(f"\nStrict-covered Fridays: {len(strict_fridays)} / {len(all_fridays)}")
    print(f"Missed Fridays: {len(missed)}")

    # For each missed Friday, build the set of IV bands it actually touched
    # across all 7 entry hours. A headline cell is only a valid candidate
    # for that Friday if the Friday's IV touched the cell's band at SOME
    # hour during the day — no force-fit into bands the Friday never reached.
    bands_by_friday: dict[str, set[str]] = {}
    for f in missed:
        f_trades = trades[trades["friday_date_ist"].astype(str) == f]
        bands_by_friday[f] = set(
            str(b) for b in f_trades["entry_atm_iv_band"].dropna().unique()
        )

    candidates_by_friday: dict[str, list[dict]] = {f: [] for f in missed}

    print("\n=== Simulating 10 headline rules over missed Fridays ===")
    for i, c in headline.iterrows():
        rule_dict = c["rule"] if isinstance(c["rule"], dict) else {}
        derived = m7r._derive_exits({}, rule_dict)
        if derived is None or derived.empty:
            print(f"  [{i+1}/10] band={c['iv_band']:<7} rule={c['rule_label']:<28} → no derived trades")
            continue

        mask = (
            (derived["entry_hour_ist"] == int(c["entry_hour_ist"])) &
            (derived["expiry_bucket"] == c["expiry_bucket"]) &
            (derived["delta_target"] == float(c["delta_target"]))
        )
        sub = derived.loc[mask].copy()
        if sub.empty:
            print(f"  [{i+1}/10] band={c['iv_band']:<7} rule={c['rule_label']:<28} → 0 matching trades")
            continue

        sub["fri_str"] = sub["friday_date_ist"].astype(str)
        sub_missed = sub[sub["fri_str"].isin(missed)]

        eligible_rows = []
        for _, t in sub_missed.iterrows():
            f = t["fri_str"]
            if c["iv_band"] in bands_by_friday.get(f, set()):
                eligible_rows.append(t)
        n_eligible = len(eligible_rows)
        print(f"  [{i+1}/10] band={c['iv_band']:<7} rule={c['rule_label']:<28} "
              f"→ {n_eligible} eligible / {len(sub_missed)} matching / {len(sub)} total trades")

        for t in eligible_rows:
            f = t["fri_str"]
            candidates_by_friday[f].append({
                "iv_band": c["iv_band"],
                "entry_hour_ist": int(c["entry_hour_ist"]),
                "expiry_bucket": c["expiry_bucket"],
                "delta_target": float(c["delta_target"]),
                "rule_label": c["rule_label"],
                "hist_avg_net_pnl": float(c["avg_net_pnl"]),
                "actual_net_pnl": float(t["net_pnl_estimate_usd"])
                                  if "net_pnl_estimate_usd" in t else None,
                "actual_exit_mtm": float(t["exit_mtm_usd"])
                                   if "exit_mtm_usd" in t else None,
                "actual_max_mtm": float(t["max_mtm_usd"])
                                  if "max_mtm_usd" in t else None,
                "actual_min_mtm": float(t["min_mtm_usd"])
                                  if "min_mtm_usd" in t else None,
                "actual_credit": float(t["credit_collected_usd"])
                                 if "credit_collected_usd" in t else None,
                "actual_margin": float(t["margin_required_usd"])
                                 if "margin_required_usd" in t else None,
                "actual_atm_iv": float(t["entry_atm_iv_pct"])
                                 if "entry_atm_iv_pct" in t else None,
                "actual_band": str(t["entry_atm_iv_band"])
                               if "entry_atm_iv_band" in t else None,
                "is_win": bool(t["is_win"]) if "is_win" in t else None,
                "exit_reason": str(t["exit_reason"]) if "exit_reason" in t else None,
            })

    print("\n=== Tiebreak: pick highest hist_avg_net_pnl candidate per Friday ===")
    chosen: list[dict] = []
    no_candidate: list[str] = []
    for f, cands in candidates_by_friday.items():
        if not cands:
            no_candidate.append(f)
            continue
        cands_sorted = sorted(cands, key=lambda d: d["hist_avg_net_pnl"], reverse=True)
        winner = cands_sorted[0]
        winner["friday"] = f
        winner["n_candidates"] = len(cands)
        winner["all_candidate_bands"] = ",".join(
            sorted(set(x["iv_band"] for x in cands))
        )
        chosen.append(winner)

    print(f"  Fridays with ≥1 candidate: {len(chosen)}")
    print(f"  Fridays with NO candidate (uncoverable): {len(no_candidate)}")
    if no_candidate:
        print(f"    {no_candidate}")

    if chosen:
        df = pd.DataFrame(chosen).sort_values("friday")
        print("\n=== PER-FRIDAY RECOVERY (sorted by Friday) ===")
        cols = [
            "friday", "iv_band", "entry_hour_ist", "expiry_bucket",
            "delta_target", "actual_band", "actual_atm_iv",
            "actual_net_pnl", "is_win", "exit_reason",
            "rule_label", "hist_avg_net_pnl", "n_candidates",
        ]
        print(df[cols].to_string(index=False))

        print("\n=== AGGREGATE ===")
        n_total = len(df)
        n_wins = int(df["is_win"].sum()) if df["is_win"].notna().any() else 0
        n_loss = n_total - n_wins
        total_pnl = float(df["actual_net_pnl"].sum())
        avg_pnl = float(df["actual_net_pnl"].mean())
        win_pnl = float(df.loc[df["is_win"] == True, "actual_net_pnl"].sum())
        loss_pnl = float(df.loc[df["is_win"] == False, "actual_net_pnl"].sum())
        print(f"  Trades:          {n_total}")
        print(f"  Wins / Losses:   {n_wins} / {n_loss} ({100.0*n_wins/n_total:.1f}% win rate)")
        print(f"  Total net P&L:   ${total_pnl:,.2f}")
        print(f"  Avg net per trd: ${avg_pnl:,.2f}")
        print(f"  Win P&L sum:     ${win_pnl:,.2f}")
        print(f"  Loss P&L sum:    ${loss_pnl:,.2f}")
        print(f"  Best trade:      ${df['actual_net_pnl'].max():,.2f}")
        print(f"  Worst trade:     ${df['actual_net_pnl'].min():,.2f}")

        print("\n=== PER-BAND BREAKDOWN (where each missed Friday landed) ===")
        for band, sub in df.groupby("iv_band", sort=False):
            n = len(sub)
            w = int(sub["is_win"].sum()) if sub["is_win"].notna().any() else 0
            print(f"  Band {band:<7}: {n} trades, {w} wins ({100.0*w/n:.0f}%), "
                  f"net=${sub['actual_net_pnl'].sum():>+9,.2f}, "
                  f"avg=${sub['actual_net_pnl'].mean():>+7,.2f}")

        out_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "m7_missed_friday_recovery.csv"
        )
        df[cols + ["actual_max_mtm", "actual_min_mtm",
                   "actual_credit", "actual_margin",
                   "all_candidate_bands"]].to_csv(out_path, index=False)
        print(f"\nFull table saved to {out_path}")


if __name__ == "__main__":
    main()

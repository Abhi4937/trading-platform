"""Audit XLSX for the composite-score v2 + multi-dim bucketing rollout.

Produces a single .xlsx with sheets:
  1. Tab1_v1_vs_v2 — every band-level cell with both composite_score (v1)
     and composite_score_v2, plus rank_status / filter_reason.
  2. Filter_Audit — every cell where rank_status="filtered", grouped by gate.
  3. Slope_Spread — per slope candidate, mean composite_score_v2 in
     backwardation vs contango cells WITHIN each (iv_band, ivrv_bucket).
     The candidate with the largest spread is the most informative slope.
  4. IVRV_Spread — same shape but rich vs cheap, per slope candidate.

Run AFTER:
  - enrich_m7_trades_with_iv_slopes
  - calibrate_m7_slope_cutoffs
  - backend has loaded the per-trade table (so bucket columns are populated)
  - bucketed grids are built (via /iv_band_best_combo?tab=... requests, or
    the script can build them in-process)

    docker compose run --rm backend python -m app.scripts.m7_composite_score_calibration
"""
from __future__ import annotations

import os
import time
from datetime import datetime

import numpy as np
import pandas as pd

from app.api import m7_best_combo as bc
from app.api.m7_ranking_config import COMPOSITE_V2_WEIGHTS


OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts")


def main() -> None:
    t0 = time.time()
    print("Loading base grid...")
    if not bc.try_load_grid_only():
        print("  No grid on disk — run build_m7_best_combo_grid first.")
        return
    base = bc._GRID_STATE["grid"]
    if base is None or base.empty:
        print("  Base grid is empty.")
        return
    print(f"  base grid: {len(base):,} cells")

    print("Building bucketed grids (this may take a while on cold exit cache)...")
    grids: dict[str, pd.DataFrame] = {"band": base}
    for tab_name in ("band_ivrv", "band_ivrv_slope_cn", "band_ivrv_slope_nn",
                     "band_ivrv_slope_cnn", "band_ivrv_ts_legacy"):
        print(f"  → {tab_name}")
        df = bc.get_grid_for_tab(tab_name)
        if df is None or df.empty:
            state = bc._BUCKETED_GRIDS.get(tab_name, {})
            print(f"    skipped: status={state.get('status')} error={state.get('error')}")
            continue
        grids[tab_name] = df
        print(f"    {len(df):,} cells")

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(
        OUT_DIR,
        f"m7_composite_v2_audit_{datetime.now():%Y%m%d_%H%M}.xlsx",
    )
    print(f"Writing {out_path}")

    with pd.ExcelWriter(out_path) as wr:
        # Sheet 1 — Tab1 v1 vs v2 side by side.
        tab1 = base[[
            "iv_band", "entry_hour_ist", "expiry_bucket", "delta_target",
            "rule_label", "n_trades", "win_rate",
            "avg_net_pnl", "avg_pct_return_on_margin",
            "sortino_per_trade", "calmar_like", "cvar_95_net",
            "composite_score", "composite_score_v2",
            "rank_in_band", "rank_status", "filter_reason",
        ]].copy()
        tab1 = tab1.sort_values(
            ["iv_band", "composite_score_v2"],
            ascending=[True, False],
            key=lambda s: s.map(bc._band_sort_key) if s.name == "iv_band" else s,
            na_position="last",
        )
        tab1.to_excel(wr, sheet_name="Tab1_v1_vs_v2", index=False)
        print(f"  Tab1_v1_vs_v2: {len(tab1):,} rows")

        # Sheet 2 — every filtered cell with reasons.
        filt = base[base["rank_status"].astype(str).eq("filtered")].copy()
        if not filt.empty:
            filt = filt[[
                "iv_band", "expiry_bucket", "delta_target", "entry_hour_ist",
                "rule_label", "n_trades", "win_rate", "cvar_95_net", "avg_credit",
                "max_consec_losses", "filter_reason",
            ]].sort_values("filter_reason")
        filt.to_excel(wr, sheet_name="Filter_Audit", index=False)
        print(f"  Filter_Audit: {len(filt):,} rows")

        # Sheet 3 — slope spread. For each slope candidate, the mean
        # composite_score_v2 of BW vs CT cells within each (band, ivrv).
        slope_spread_rows: list[dict] = []
        slope_map = {
            "band_ivrv_slope_cn":   ("slope_cn_bucket",   "current↔next"),
            "band_ivrv_slope_nn":   ("slope_nn_bucket",   "next↔next_to_next"),
            "band_ivrv_slope_cnn":  ("slope_cnn_bucket",  "current↔next_to_next"),
            "band_ivrv_ts_legacy":  ("ts_legacy_bucket",  "7d-30d (control)"),
        }
        for tab_name, (slope_col, label) in slope_map.items():
            g = grids.get(tab_name)
            if g is None or g.empty or slope_col not in g.columns:
                continue
            ranked = g[g["rank_status"].astype(str).eq("ranked")]
            if ranked.empty:
                continue
            for (band, ivrv), sub in ranked.groupby(["iv_band", "ivrv_bucket"],
                                                     dropna=False, sort=False):
                bw = sub[sub[slope_col] == "backwardation"]["composite_score_v2"].mean()
                ct = sub[sub[slope_col] == "contango"]["composite_score_v2"].mean()
                if pd.isna(bw) or pd.isna(ct):
                    continue
                slope_spread_rows.append({
                    "slope_candidate": label,
                    "iv_band": band,
                    "ivrv_bucket": ivrv,
                    "mean_v2_backwardation": bw,
                    "mean_v2_contango": ct,
                    "spread": bw - ct,
                    "n_back": int((sub[slope_col] == "backwardation").sum()),
                    "n_cont": int((sub[slope_col] == "contango").sum()),
                })
        slope_spread = pd.DataFrame(slope_spread_rows)
        if not slope_spread.empty:
            slope_spread = slope_spread.sort_values(
                "spread", key=lambda s: s.abs(), ascending=False,
            )
        slope_spread.to_excel(wr, sheet_name="Slope_Spread", index=False)
        print(f"  Slope_Spread: {len(slope_spread):,} rows")

        # Sheet 4 — IVRV spread, rich vs cheap, per slope candidate.
        ivrv_spread_rows: list[dict] = []
        for tab_name, (slope_col, label) in slope_map.items():
            g = grids.get(tab_name)
            if g is None or g.empty or "ivrv_bucket" not in g.columns:
                continue
            ranked = g[g["rank_status"].astype(str).eq("ranked")]
            if ranked.empty:
                continue
            for (band, slope_b), sub in ranked.groupby(["iv_band", slope_col],
                                                        dropna=False, sort=False):
                rich = sub[sub["ivrv_bucket"] == "rich"]["composite_score_v2"].mean()
                cheap = sub[sub["ivrv_bucket"] == "cheap"]["composite_score_v2"].mean()
                if pd.isna(rich) or pd.isna(cheap):
                    continue
                ivrv_spread_rows.append({
                    "slope_candidate": label,
                    "iv_band": band,
                    "slope_bucket": slope_b,
                    "mean_v2_rich": rich,
                    "mean_v2_cheap": cheap,
                    "spread": rich - cheap,
                    "n_rich": int((sub["ivrv_bucket"] == "rich").sum()),
                    "n_cheap": int((sub["ivrv_bucket"] == "cheap").sum()),
                })
        ivrv_spread = pd.DataFrame(ivrv_spread_rows)
        if not ivrv_spread.empty:
            ivrv_spread = ivrv_spread.sort_values(
                "spread", key=lambda s: s.abs(), ascending=False,
            )
        ivrv_spread.to_excel(wr, sheet_name="IVRV_Spread", index=False)
        print(f"  IVRV_Spread: {len(ivrv_spread):,} rows")

        # Sheet 5 — weights/thresholds in use (for traceability).
        config_df = pd.DataFrame([
            {"key": "composite_v2_weight", "name": k, "value": v}
            for k, v in COMPOSITE_V2_WEIGHTS.items()
        ])
        config_df.to_excel(wr, sheet_name="Config", index=False)

    print(f"Done in {time.time() - t0:.1f}s. Open {out_path}")


if __name__ == "__main__":
    main()

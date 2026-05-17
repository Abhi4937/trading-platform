"""M7 Hybrid endpoint — serves picker results from the 243-rule hybrid grid.

Endpoint prefix: /api/v1/m7_hybrid

Reads from `m7_best_combo_grid_v6_hybrid.parquet` (built by
`build_m7_best_combo_grid_hybrid.py`). All endpoints accept an optional
`categories` query parameter (CSV) to filter visible rule categories
before picker selection — lets the frontend toggle between the
"normal 105", "108", "hybrid 2-way", or "hybrid full" views from a
single grid.

Reuses canonical picker (`m7_best_combo._pick_best_per_band`) so we
get all the same rank / sizing / DD-cap behavior without duplicating
code.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.api import m7_best_combo as bc
from app.api import m7_best_combo_hybrid as bch

log = logging.getLogger(__name__)
router = APIRouter()


# Valid rule_category values (kept in sync with bch.RuleCategory)
_VALID_CATEGORIES = {
    "single_baseline",
    "single_max_profit",
    "single_margin_target",
    "single_exit_hr",
    "single_capital_sl_standalone",
    "hybrid_2way_sl",
    "hybrid_3way",
}


# ── In-memory grid cache ──────────────────────────────────────────────────────

class _GridCache:
    grid: Optional[pd.DataFrame] = None
    mtime: Optional[float] = None


def _load_hybrid_grid() -> pd.DataFrame:
    """Lazy load + cache the hybrid grid parquet. Reloads if file mtime
    changes (e.g., after a rebuild)."""
    path = bch.GRID_PARQUET_PATH
    if not os.path.exists(path):
        return pd.DataFrame()
    mtime = os.path.getmtime(path)
    if _GridCache.grid is None or _GridCache.mtime != mtime:
        log.info("Loading hybrid grid from %s (mtime=%s)", path, mtime)
        _GridCache.grid = bch.load_grid(path)
        _GridCache.mtime = mtime
    return _GridCache.grid


def _filter_by_categories(grid: pd.DataFrame, categories_csv: Optional[str]) -> pd.DataFrame:
    """If `categories_csv` is provided (comma-separated list of valid
    category names), keep only rows in those categories. Empty/None =
    no filter (all 7 categories visible)."""
    if not categories_csv:
        return grid
    wanted = {c.strip() for c in categories_csv.split(",") if c.strip()}
    invalid = wanted - _VALID_CATEGORIES
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid categories: {sorted(invalid)}. "
                   f"Valid: {sorted(_VALID_CATEGORIES)}"
        )
    if "rule_category" not in grid.columns:
        # Older grid without the column — re-derive from label
        grid = grid.copy()
        grid["rule_category"] = grid["rule_label"].apply(bch._label_to_category)
    return grid[grid["rule_category"].isin(wanted)]


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/iv_band_best_combo/status")
def get_status():
    """Quick check of the hybrid grid state — used by the frontend to
    decide whether to show the picker or a 'grid not built yet' banner."""
    path = bch.GRID_PARQUET_PATH
    if not os.path.exists(path):
        return {
            "status": "missing",
            "message": f"Hybrid grid not built yet. Run "
                       f"`python -m app.scripts.build_m7_best_combo_grid_hybrid` "
                       f"(or via docker compose run) to produce it.",
            "path": path,
        }
    grid = _load_hybrid_grid()
    n_rules = int(grid["rule_label"].nunique()) if not grid.empty else 0
    return {
        "status": "ready",
        "path": path,
        "n_cells": len(grid),
        "n_rules": n_rules,
        "expected_rules": 243,
        "mtime": _GridCache.mtime,
    }


# ── Picker — best combo per IV band ───────────────────────────────────────────

@router.get("/iv_band_best_combo")
def get_iv_band_best_combo(
    ranking: str = Query("avg_net_pnl",
        description="Primary metric — must be a column in the grid."),
    secondary: Optional[str] = Query(None,
        description="Optional tiebreak metric. If set, picker is in tiebreak mode."),
    tolerance_pct: float = Query(5.0, ge=0, le=100,
        description="Tiebreak tolerance % — cells within X%% of primary's best "
                    "value are eligible to be re-ranked by `secondary`."),
    categories: Optional[str] = Query(None,
        description="CSV whitelist of rule_category values to keep. "
                    "Valid: single_baseline, single_max_profit, single_margin_target, "
                    "single_exit_hr, single_capital_sl_standalone, hybrid_2way_sl, "
                    "hybrid_3way. Empty/absent = all 7 visible (full 243-rule set)."),
    total_capital_usd: Optional[float] = Query(None, ge=0,
        description="If set, scale per-band lots to fit this capital budget."),
    pct_deploy: float = Query(100.0, ge=0, le=100,
        description="Of total_capital_usd, what % to deploy per band."),
    dd_metric: Optional[str] = Query(None,
        description="Drawdown-cap metric (e.g. 'avg_min_mtm') — cap lots so "
                    "scaled value <= -dd_threshold."),
    dd_threshold: Optional[float] = Query(None, ge=0),
    min_hit_pct: float = Query(50.0, ge=0, le=100,
        description="Drop cells where (n_trades - n_hard_cap) / n_trades < X%%."),
    max_loss_cap_pct: Optional[float] = Query(None, ge=0,
        description="Drop cells whose max_loss_usd / capital > X%%."),
    max_drop_peak_to_trough_pct: Optional[float] = Query(None, ge=0),
    min_n_trades: int = Query(5, ge=0),
    min_win_rate: Optional[float] = Query(None, ge=0, le=100),
    max_losing_streak: Optional[int] = Query(None, ge=1),
):
    """Best-combo picker for the hybrid grid. Returns one cell per IV
    band, optionally filtered by rule_category.

    The frontend uses the `categories` parameter to toggle between
    views:
      - all 7 categories                       → 243-rule hybrid
      - single_* only (drop hybrid + cap_sl)   → 105-rule "normal"
      - + single_capital_sl_standalone         → 108-rule view
      - + hybrid_2way_sl                       → 117-rule view
    """
    grid = _load_hybrid_grid()
    if grid.empty:
        raise HTTPException(
            status_code=503,
            detail="Hybrid grid not built. Run build_m7_best_combo_grid_hybrid first.",
        )

    grid = _filter_by_categories(grid, categories)
    if grid.empty:
        return {
            "ranking": ranking,
            "rows": [],
            "status": "ready",
            "n_rules": 0,
            "n_cells": 0,
            "filtered_by_categories": categories,
        }

    best = bc._pick_best_per_band(
        grid,
        ranking,
        secondary=secondary,
        tolerance_pct=tolerance_pct,
        total_capital_usd=total_capital_usd,
        pct_deploy=pct_deploy,
        dd_metric=dd_metric,
        dd_threshold=dd_threshold,
        min_hit_pct=min_hit_pct,
        max_loss_cap_pct=max_loss_cap_pct,
        max_drop_peak_to_trough_pct=max_drop_peak_to_trough_pct,
        min_n_trades=min_n_trades,
        min_win_rate=min_win_rate,
        max_losing_streak=max_losing_streak,
    )

    return {
        "ranking": ranking,
        "secondary": secondary,
        "filtered_by_categories": categories,
        "status": "ready",
        "rows": bc._records(best) if not best.empty else [],
        "n_rules": int(grid["rule_label"].nunique()),
        "n_cells": len(grid),
    }


# ── Rule comparison ──────────────────────────────────────────────────────────

@router.get("/iv_band_best_combo/rule_comparison")
def get_rule_comparison(
    iv_band: str = Query(..., description="e.g. '20-30'"),
    expiry_bucket: str = Query(..., description="e.g. 'current (Sat)'"),
    delta_target: float = Query(..., ge=0, le=1),
    entry_hour_ist: int = Query(..., ge=0, le=23),
    sort_by: str = Query("avg_net_pnl"),
    categories: Optional[str] = Query(None,
        description="CSV category whitelist to filter rule_comparison rows."),
):
    """Return all rules' metrics at a fixed (band, expiry, delta, hour)
    cell — used by the rule-comparison modal."""
    grid = _load_hybrid_grid()
    if grid.empty:
        raise HTTPException(status_code=503, detail="Hybrid grid not built.")

    grid = _filter_by_categories(grid, categories)

    sub = grid[
        (grid["iv_band"] == iv_band)
        & (grid["expiry_bucket"] == expiry_bucket)
        & (grid["delta_target"].astype(float) == float(delta_target))
        & (grid["entry_hour_ist"].astype("Int64") == int(entry_hour_ist))
    ].copy()

    if sub.empty:
        return {"rows": [], "n": 0, "filtered_by_categories": categories}

    if sort_by in sub.columns:
        sub = sub.sort_values(sort_by, ascending=False)

    return {
        "iv_band": iv_band,
        "expiry_bucket": expiry_bucket,
        "delta_target": delta_target,
        "entry_hour_ist": entry_hour_ist,
        "sort_by": sort_by,
        "filtered_by_categories": categories,
        "rows": bc._records(sub),
        "n": len(sub),
    }


# ── Category breakdown (for frontend filter chip) ────────────────────────────

@router.get("/categories")
def get_categories():
    """List the 7 rule_category values + their counts in the current grid.
    Used by the frontend filter chip to render category toggles."""
    grid = _load_hybrid_grid()
    if grid.empty:
        return {
            "categories": sorted(_VALID_CATEGORIES),
            "counts": {c: 0 for c in _VALID_CATEGORIES},
            "status": "missing",
        }
    if "rule_category" not in grid.columns:
        grid = grid.copy()
        grid["rule_category"] = grid["rule_label"].apply(bch._label_to_category)

    # Count unique rules per category (not cells — rules-per-category is constant
    # across cells; n_rules = grid.groupby('rule_category')['rule_label'].nunique())
    rules_by_cat = (grid.groupby("rule_category")["rule_label"]
                    .nunique().to_dict())
    cells_by_cat = grid.groupby("rule_category").size().to_dict()

    return {
        "categories": sorted(_VALID_CATEGORIES),
        "n_rules_per_category": {c: int(rules_by_cat.get(c, 0))
                                  for c in _VALID_CATEGORIES},
        "n_cells_per_category": {c: int(cells_by_cat.get(c, 0))
                                  for c in _VALID_CATEGORIES},
        "total_rules": int(grid["rule_label"].nunique()),
        "total_cells": len(grid),
        "status": "ready",
    }

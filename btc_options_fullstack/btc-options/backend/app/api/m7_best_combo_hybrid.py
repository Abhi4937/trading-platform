"""M7 hybrid rule menu (243 variants) — separate from the canonical
m7_best_combo.py module to keep this session's work non-conflicting
with the parallel 108-rule build session.

Rule layers:
  - 105 existing single-trigger rules (mirrored from m7_best_combo._rule_variants)
  -   3 standalone capital_sl rules    (no premium_sl, no TP, no time exit)
  -   9 cap_sl × premium_sl 2-way hybrids
  - 126 cap_sl × premium_sl × exit_hr 3-way hybrids
  ─────
  243 total

The cap_sl SQL predicate already exists in m7_results._compute_all_exits
at line 445-454 — no need to duplicate it here. This module just
defines the rule menu + a category tag for frontend filterability.

Output grid: m7_best_combo_grid_v6_hybrid.parquet (parallel to the
canonical m7_best_combo_grid_v6.parquet — precedent set by
m7_best_combo_grid_v6_price_matched.parquet).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Literal

import numpy as np
import pandas as pd

# Read-only imports from canonical modules — no modifications.
from app.api import m7_best_combo as bc
from app.api import m7_results as m7r

log = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

# Reuse canonical constants (read-only).
_PREMIUM_SL_PCTS = bc._PREMIUM_SL_PCTS         # [50, 75, 100]
_PCT_GRID = bc._PCT_GRID                       # [10, 15, 20, 25, 30, 40, 50, 60, 75, 100]
_FIXED_HOURS = bc._FIXED_HOURS                 # [5.0..17.0, 17.4833]
_CAPITAL_SL_PCTS = bc._CAPITAL_SL_PCTS         # [10, 15, 20]

# Output path for this session's grid — separate from the canonical grid
# and from the price-matched grid. Loaded by the m7_hybrid_results router.
GRID_PARQUET_PATH = os.path.join(
    m7r.M7_BASE_DIR, "m7_best_combo_grid_v6_hybrid.parquet")

RuleCategory = Literal[
    "single_baseline",
    "single_max_profit",
    "single_margin_target",
    "single_exit_hr",
    "single_capital_sl_standalone",
    "hybrid_2way_sl",
    "hybrid_3way",
]


# ── Rule menu ─────────────────────────────────────────────────────────────────

def _rule_variants_hybrid() -> list[tuple[str, dict, RuleCategory]]:
    """243 (label, rule_dict, rule_category) tuples covering the full
    hybrid menu. Excludes max_profit and margin_target from cross-products
    per user spec — those families only appear as singles.

    Layer breakdown:
      L1 — 105 existing singles (untouched mirror of bc._rule_variants):
            3 × (1 baseline + 10 max_profit + 10 margin_target + 14 exit_hr)
      L2 —   3 standalone capital_sl singles (NO premium_sl)
      L3 —   9 cap_sl × premium_sl 2-way hybrids
      L4 — 126 cap_sl × premium_sl × exit_hr 3-way hybrids

    See `RuleCategory` for the 7 category tags assigned per layer.
    """
    out: list[tuple[str, dict, RuleCategory]] = []

    # ── L1 — 105 existing singles ─────────────────────────────────────────
    for sl in _PREMIUM_SL_PCTS:
        out.append((f"sl{sl}_baseline",
                    {"premium_sl_pct": sl},
                    "single_baseline"))
        for p in _PCT_GRID:
            out.append((f"sl{sl}_max_profit_{p}",
                        {"premium_sl_pct": sl, "max_profit_pct": p},
                        "single_max_profit"))
        for p in _PCT_GRID:
            out.append((f"sl{sl}_margin_target_{p}",
                        {"premium_sl_pct": sl, "margin_target_pct": p},
                        "single_margin_target"))
        for h in _FIXED_HOURS:
            out.append((f"sl{sl}_exit_hr_{bc._hour_label(h)}",
                        {"premium_sl_pct": sl, "fixed_exit_hour_ist": h},
                        "single_exit_hr"))

    # ── L2 — 3 standalone cap_sl singles (no premium_sl) ──────────────────
    for c in _CAPITAL_SL_PCTS:
        out.append((f"capital_sl_{c}",
                    {"capital_sl_pct": c},
                    "single_capital_sl_standalone"))

    # ── L3 — 9 cap_sl × premium_sl 2-way hybrids ──────────────────────────
    for sl in _PREMIUM_SL_PCTS:
        for c in _CAPITAL_SL_PCTS:
            out.append((f"sl{sl}_capital_sl_{c}",
                        {"premium_sl_pct": sl, "capital_sl_pct": c},
                        "hybrid_2way_sl"))

    # ── L4 — 126 cap_sl × premium_sl × exit_hr 3-way hybrids ──────────────
    for sl in _PREMIUM_SL_PCTS:
        for c in _CAPITAL_SL_PCTS:
            for h in _FIXED_HOURS:
                label = f"sl{sl}_capital_sl_{c}_exit_hr_{bc._hour_label(h)}"
                rule = {
                    "premium_sl_pct": sl,
                    "capital_sl_pct": c,
                    "fixed_exit_hour_ist": h,
                }
                out.append((label, rule, "hybrid_3way"))

    assert len(out) == 243, f"unexpected rule count: {len(out)}"
    return out


def _label_to_category(label: str) -> RuleCategory:
    """Reverse-lookup: rule_label → rule_category. Used for sanity-checking
    and for re-deriving category when loading a grid parquet that was
    written by an older codebase (without the category column).
    """
    if label.startswith("capital_sl_"):
        return "single_capital_sl_standalone"
    # All other labels start with "sl{N}_"
    has_capital = "_capital_sl_" in label
    has_exit = "_exit_hr_" in label
    has_mp = "_max_profit_" in label
    has_mt = "_margin_target_" in label

    if has_capital and has_exit:
        return "hybrid_3way"
    if has_capital:
        return "hybrid_2way_sl"
    if has_exit:
        return "single_exit_hr"
    if has_mp:
        return "single_max_profit"
    if has_mt:
        return "single_margin_target"
    return "single_baseline"


# ── Grid builder (parallel to bc._build_grid) ─────────────────────────────────
#
# Implementation note: we mirror the structure of bc._build_grid but call
# _rule_variants_hybrid() instead of bc._rule_variants(dataset). The
# per-rule exit derivation reuses m7r._derive_exits — which already
# handles capital_sl_pct via m7_results.py:445-454 (no duplication needed).

_BUILD_LOCK = threading.Lock()


def _checkpoint_path(grid_path: str = GRID_PARQUET_PATH) -> str:
    return grid_path + ".partial"


def _write_checkpoint(rows: list[dict], checkpoint_path: str) -> None:
    """Atomic flush of accumulated cell rows to the .partial parquet.
    Safe to call repeatedly — each write overwrites the prior file."""
    if not rows:
        return
    df = pd.DataFrame(rows)
    flat = _flatten_for_parquet(df)
    tmp = checkpoint_path + ".tmp"
    flat.to_parquet(tmp, compression="zstd", index=False)
    os.replace(tmp, checkpoint_path)


def _load_checkpoint(checkpoint_path: str) -> tuple[set[str], list[dict]]:
    """Load a .partial parquet if present. Returns (completed_rule_labels,
    accumulated_row_dicts). Empty set + empty list if no checkpoint."""
    if not os.path.exists(checkpoint_path):
        return set(), []
    df = pd.read_parquet(checkpoint_path)
    if df.empty or "rule_label" not in df.columns:
        return set(), []
    df = _unflatten_after_load(df)
    completed = set(df["rule_label"].astype(str).unique())
    rows = df.to_dict(orient="records")
    return completed, rows


def _build_grid_hybrid(progress_cb=None,
                       checkpoint_every: int = 25,
                       checkpoint_path: str | None = None) -> pd.DataFrame:
    """Build the 243-rule hybrid grid against the delta-match trade dataset.

    Each rule's exit results are derived via m7r._derive_exits (which
    already handles capital_sl_pct in its SQL builder). Per-rule cache
    eviction mirrors bc._build_grid to keep peak memory bounded.

    Checkpointing: every `checkpoint_every` newly-completed rules, the
    accumulated cells are flushed atomically to `checkpoint_path`
    (default: `GRID_PARQUET_PATH + '.partial'`). On entry, if that
    checkpoint exists, its `rule_label`s are skipped to resume an
    interrupted build. Caller is responsible for removing the
    checkpoint after the final atomic write (build script does so).

    `progress_cb(rules_done, rules_total)` is called after each rule
    (counting skipped-from-checkpoint rules too).
    """
    if checkpoint_path is None:
        checkpoint_path = _checkpoint_path()

    variants = _rule_variants_hybrid()
    base_keys = list(bc._BASE_GROUP_KEYS)  # (iv_band, expiry, delta, hour)

    completed_labels, rows = _load_checkpoint(checkpoint_path)
    if completed_labels:
        log.info("Resuming hybrid build from checkpoint: %d/%d rules "
                 "already complete, %d cells accumulated",
                 len(completed_labels), len(variants), len(rows))

    n_done_this_run = 0

    for i, (rule_label, rule_dict, rule_category) in enumerate(variants):
        if rule_label in completed_labels:
            if progress_cb is not None:
                progress_cb(i + 1, len(variants))
            continue

        # Track whether the rule's exits were already cached before our
        # call — we want to evict only entries we populated ourselves.
        m7r._load_trades("delta_match")
        _, _ds_mtime = m7r._TRADES_BY_DATASET.get("delta_match", (None, 0.0))
        rule_key = (
            "delta_match",
            json.dumps(rule_dict or {}, sort_keys=True),
            _ds_mtime,
        )
        was_cached_before = rule_key in m7r._EXIT_CACHE

        derived = m7r._derive_exits({}, rule_dict)
        if progress_cb is not None:
            progress_cb(i + 1, len(variants))

        if derived is not None and not derived.empty:
            mask = (
                derived["entry_atm_iv_band"].notna()
                & derived["expiry_bucket"].notna()
                & derived["delta_target"].notna()
                & derived["entry_hour_ist"].notna()
                & derived["gross_pnl_usd"].notna()
            )
            keep = derived[mask]
            if not keep.empty:
                grouped = keep.groupby(base_keys, dropna=False, sort=False)
                for key_vals, sub in grouped:
                    if sub.empty:
                        continue
                    iv_band, expiry, delta, hour = key_vals
                    cell = bc._compute_cell_metrics(sub)
                    cell.update({
                        "iv_band": iv_band,
                        "expiry_bucket": expiry,
                        "delta_target": float(delta) if delta is not None else None,
                        "entry_hour_ist": int(hour) if hour is not None else None,
                        "rule_label": rule_label,
                        "rule": rule_dict,
                        "rule_category": rule_category,
                    })
                    rows.append(cell)

        # Memory hygiene — evict the rule's cache entry if we populated it.
        if not was_cached_before:
            m7r._EXIT_CACHE.pop(rule_key, None)
        del derived

        n_done_this_run += 1
        if checkpoint_every > 0 and n_done_this_run % checkpoint_every == 0:
            _write_checkpoint(rows, checkpoint_path)
            log.info("Hybrid checkpoint written at rule %d/%d "
                     "(%d cells accumulated)",
                     i + 1, len(variants), len(rows))

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


# ── Parquet I/O ───────────────────────────────────────────────────────────────

def _flatten_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten the nested `rule` dict into 4 numeric columns so pyarrow
    can write the dataframe. rule_category is already a top-level string.
    """
    if df.empty or "rule" not in df.columns:
        return df
    out = df.copy()
    out["rule_premium_sl_pct"] = out["rule"].apply(
        lambda r: (r or {}).get("premium_sl_pct"))
    out["rule_max_profit_pct"] = out["rule"].apply(
        lambda r: (r or {}).get("max_profit_pct"))
    out["rule_margin_target_pct"] = out["rule"].apply(
        lambda r: (r or {}).get("margin_target_pct"))
    out["rule_capital_sl_pct"] = out["rule"].apply(
        lambda r: (r or {}).get("capital_sl_pct"))
    out["rule_fixed_exit_hour_ist"] = out["rule"].apply(
        lambda r: (r or {}).get("fixed_exit_hour_ist"))
    out = out.drop(columns=["rule"])
    return out


def _unflatten_after_load(df: pd.DataFrame) -> pd.DataFrame:
    """Inverse of _flatten_for_parquet — rebuild the `rule` dict column
    from the 5 flat rule_* columns. Lossless float round-trip (no
    int-coercion bug that bites fixed_exit_hour_ist=17.4833)."""
    rule_cols = [
        "rule_premium_sl_pct",
        "rule_max_profit_pct",
        "rule_margin_target_pct",
        "rule_capital_sl_pct",
        "rule_fixed_exit_hour_ist",
    ]
    if not all(c in df.columns for c in rule_cols):
        return df

    field_map = [
        ("rule_premium_sl_pct", "premium_sl_pct"),
        ("rule_max_profit_pct", "max_profit_pct"),
        ("rule_margin_target_pct", "margin_target_pct"),
        ("rule_capital_sl_pct", "capital_sl_pct"),
        ("rule_fixed_exit_hour_ist", "fixed_exit_hour_ist"),
    ]

    def _build_rule(row):
        out = {}
        for src, dst in field_map:
            v = row.get(src)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            # Preserve float — fixed_exit_hour_ist=17.4833 must round-trip
            # without int truncation.
            out[dst] = float(v)
        return out

    df2 = df.copy()
    df2["rule"] = df2.apply(_build_rule, axis=1)
    return df2


def save_grid(df: pd.DataFrame, path: str = GRID_PARQUET_PATH) -> int:
    """Atomic write of the hybrid grid. Returns row count."""
    if df.empty:
        raise ValueError("save_grid: dataframe is empty")
    flat = _flatten_for_parquet(df)
    tmp = path + ".tmp"
    flat.to_parquet(tmp, compression="zstd", index=False)
    os.replace(tmp, path)
    return len(flat)


def load_grid(path: str = GRID_PARQUET_PATH) -> pd.DataFrame:
    """Load the hybrid grid from parquet, unflatten the `rule` dict.
    If `rule_category` is missing (older parquet), re-derive from label.
    """
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df = _unflatten_after_load(df)
    if "rule_category" not in df.columns and "rule_label" in df.columns:
        df["rule_category"] = df["rule_label"].apply(_label_to_category)
    return df

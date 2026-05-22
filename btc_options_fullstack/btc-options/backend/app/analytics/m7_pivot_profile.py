"""M7 Pivot Profile — segment-based per-trade peak/trough/DD, binned by IV band.

Splits each per-minute MTM path into 5 IST clock-anchored windows
(Seg1 entry→05:00, Seg2 05:00→08:00, Seg3 08:00→12:00, Seg4 12:00→15:00,
Seg5 15:00→17:30) and records the peak + trough of each segment plus the
$ swing and % drop from peak. Aggregates across trades grouped by
entry_atm_iv_band, with an entry-hour filter the caller chooses.

Pure-function module; the FastAPI router lives in app/api/m7_pivot_profile.py.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import time, datetime, timezone, timedelta
from typing import Iterable, Optional
import os
import glob

import numpy as np
import pandas as pd


# IST is UTC+5:30; no DST in India. Use a fixed offset rather than zoneinfo
# so the analytics is portable to environments without tzdata installed.
IST_OFFSET = timedelta(hours=5, minutes=30)

# The M7 batch backtester sizes every trade at QTY_LOTS = 100. The
# gross_pnl_usd values in m7_paths/ are therefore per-100-lot. To express a
# trade's MTM at the user-chosen per-band lot count (as displayed in the
# Best Combo "Lots" column), we scale by `cell.lots / BACKTESTER_BASELINE_LOTS`.
BACKTESTER_BASELINE_LOTS = 100

# Segment boundaries on the IST clock. 5 segments → 6 boundaries (the first
# boundary is the trade's actual entry time; the last is 17:30 IST Sat).
IST_SEGMENT_BOUNDS: tuple[time, ...] = (
    time(5, 0),
    time(8, 0),
    time(12, 0),
    time(15, 0),
    time(17, 30),
)
SEG_NAMES = ("Seg1", "Seg2", "Seg3", "Seg4", "Seg5")

MIN_TRADES_PER_BAND_CELL = 5


@dataclass
class SegmentPivot:
    seg: str                       # "Seg1" .. "Seg5"
    n_minutes: int                 # how many 1m bars this trade had inside this segment
    peak_mtm: float
    peak_minute_offset: int        # minutes from trade entry to the peak bar
    peak_ts_ist_minute_of_day: int # IST minute-of-day (0..1439) at the peak; helps averaging
    trough_mtm: float
    trough_minute_offset: int
    trough_ts_ist_minute_of_day: int
    dd_usd: float                  # dd_ref − trough (≥ 0)
    dd_ref_mtm: float              # the peak this DD was measured FROM (current
                                    # segment's peak if it came first, else the
                                    # most recent prior segment's peak; 0 when no
                                    # peak has been seen yet)
    dd_pct_from_peak: Optional[float]  # per-trade dd/dd_ref*100, None if dd_ref<=0
                                       # (band aggregate uses ratio-of-means, NOT
                                       # mean of these per-trade ratios)


def _utc_unix_to_ist_datetime(ts: int) -> datetime:
    """Convert a unix-seconds timestamp to a naive IST datetime."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(
        timezone(IST_OFFSET)).replace(tzinfo=None)


def _ist_minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _segment_index_for_ist_minute(mod: int) -> int:
    """Return 0..4 segment index for an IST minute-of-day.

    Segment definitions (after entry — assume `mod` is already past entry):
        seg 0 ≡ Seg1: entry → 05:00      (mod < 300)
        seg 1 ≡ Seg2: 05:00 → 08:00       (300 ≤ mod < 480)
        seg 2 ≡ Seg3: 08:00 → 12:00       (480 ≤ mod < 720)
        seg 3 ≡ Seg4: 12:00 → 15:00       (720 ≤ mod < 900)
        seg 4 ≡ Seg5: 15:00 → 17:30       (900 ≤ mod ≤ 1050)
    Returns -1 for anything outside the trade's allowable window
    (mod > 1050 or before Sat midnight roll for very-early-entry edge cases).
    """
    if mod < 300:
        return 0
    if mod < 480:
        return 1
    if mod < 720:
        return 2
    if mod < 900:
        return 3
    if mod <= 1050:
        return 4
    return -1


def segment_pivots(
    ts_unix: np.ndarray,
    mtm_usd: np.ndarray,
    entry_ts_unix: int,
) -> list[Optional[SegmentPivot]]:
    """Compute 5 SegmentPivot entries for one trade.

    Inputs:
      ts_unix     — int64 unix seconds, one per 1m bar, sorted ascending
      mtm_usd     — float64 P&L value at each bar (gross_pnl_usd in the path parquet)
      entry_ts_unix — int unix seconds of the trade's entry bar

    Returns a 5-element list (one per segment). Elements are SegmentPivot for
    populated segments and None for segments the trade didn't cover.
    """
    if ts_unix.size == 0:
        return [None] * 5

    # Build IST minute-of-day per bar. Saturday IST runs 0..1050 (00:00..17:30);
    # the few bars from the Friday-night entry tail (hours 21..23 IST) have
    # mod ≥ 1260 — we treat those as "pre-segment" and include them in Seg1
    # by clamping to 0 IFF the entry hour is one of those late-Fri hours.
    #
    # Concretely: bars at IST 22:34 on Fri have mod=1354. They are part of the
    # trade's Seg1 (entry→05:00). To make `_segment_index_for_ist_minute` happy,
    # we anchor "trade-local IST minute" = (mod − entry_mod) mod 1440. That
    # yields a monotone 0..N axis that walks forward through IST time.
    #
    # For Sat-only path bars, this collapses to mod − entry_mod (which is in
    # 0..1050 range, fine).
    entry_ist = _utc_unix_to_ist_datetime(int(entry_ts_unix))
    entry_mod = _ist_minute_of_day(entry_ist)

    # Per-bar IST minute-of-day:
    bars_mod = np.empty(ts_unix.size, dtype=np.int32)
    bars_ist_min: list[int] = []  # used for argmax/argmin → minute-of-day
    for i, ts in enumerate(ts_unix):
        ist = _utc_unix_to_ist_datetime(int(ts))
        m = _ist_minute_of_day(ist)
        bars_ist_min.append(m)
        # Walk-forward minute relative to entry. If we crossed midnight,
        # add 1440. Stays in [0, ~1080].
        delta = m - entry_mod
        if delta < 0:
            delta += 1440
        bars_mod[i] = delta
    bars_ist_arr = np.asarray(bars_ist_min, dtype=np.int32)

    # Now segment by *trade-local* minute. Boundaries shift by entry_mod:
    #   entry → 05:00   →  Seg1 covers [0, (300 − entry_mod) % 1440)
    #   05:00 → 08:00   →  Seg2 ...
    # Cleaner approach: compute the trade-local segment cutoffs once.
    bound_mods = [
        (b.hour * 60 + b.minute - entry_mod) % 1440 for b in IST_SEGMENT_BOUNDS
    ]
    # bound_mods[i] is the trade-local minute at IST boundary i.
    # Seg1: [0, bound_mods[0])
    # Seg2: [bound_mods[0], bound_mods[1])
    # ...
    # Seg5: [bound_mods[3], bound_mods[4]]  (inclusive at the right)
    # The hard 17:30 IST exit cap means anything past bound_mods[4] is
    # dropped from the analysis even if the parquet has a few extra bars.
    seg_of_bar = np.full(ts_unix.size, -1, dtype=np.int8)
    prev_bound = 0
    for seg_idx, b in enumerate(bound_mods):
        mask = (bars_mod >= prev_bound) & (bars_mod < b)
        if seg_idx == 4:
            # Include the final 17:30 IST tick in Seg5
            mask = (bars_mod >= prev_bound) & (bars_mod <= b)
        seg_of_bar[mask] = seg_idx
        prev_bound = b

    out: list[Optional[SegmentPivot]] = []
    # DD is measured from the most recent peak in chronological order across
    # the merged 5-segment timeline — NOT from the same-segment peak. If a
    # segment goes trough-first, its DD references the previous segment's
    # peak (the last_peak seen on the time-ordered walk). When the segment's
    # own peak comes before its trough, last_peak gets updated first and the
    # DD naturally uses the current segment's peak (the old behaviour).
    last_peak_mtm: Optional[float] = None
    for seg_idx in range(5):
        bar_mask = seg_of_bar == seg_idx
        n_bars = int(bar_mask.sum())
        if n_bars == 0:
            out.append(None)
            continue
        seg_mtm = mtm_usd[bar_mask]
        seg_mod = bars_mod[bar_mask]
        seg_ist = bars_ist_arr[bar_mask]
        # Drop NaN bars before locating extrema.
        finite_mask = np.isfinite(seg_mtm)
        if not finite_mask.any():
            out.append(None)
            continue
        if not finite_mask.all():
            seg_mtm = seg_mtm[finite_mask]
            seg_mod = seg_mod[finite_mask]
            seg_ist = seg_ist[finite_mask]
            n_bars = int(seg_mtm.size)
        # Peak / trough
        peak_idx_local = int(np.argmax(seg_mtm))
        trough_idx_local = int(np.argmin(seg_mtm))
        peak_mtm = float(seg_mtm[peak_idx_local])
        trough_mtm = float(seg_mtm[trough_idx_local])
        peak_offset = int(seg_mod[peak_idx_local])
        trough_offset = int(seg_mod[trough_idx_local])

        # Chronological DD reference: the most recent peak BEFORE this
        # segment's trough. If the segment's own peak came first (peak
        # offset ≤ trough offset), it counts as that reference. Otherwise
        # fall back to last_peak from prior segments. If no peak has been
        # seen yet (trade dipped before any rally), reference the entry
        # baseline ($0) and report dd_pct as None.
        peak_first_in_seg = peak_offset <= trough_offset
        if peak_first_in_seg:
            # The segment's own peak is the latest peak before the trough.
            dd_ref = peak_mtm
            # Update last_peak so subsequent segments see this peak.
            last_peak_mtm = peak_mtm
        else:
            # Trough fires before peak — reference is the prior peak.
            dd_ref = last_peak_mtm if last_peak_mtm is not None else 0.0
            # After the trough is recorded, the segment's later peak becomes
            # the new running peak for downstream troughs.
            last_peak_mtm = peak_mtm

        dd_usd = dd_ref - trough_mtm
        dd_pct_per_trade = ((dd_usd / dd_ref) * 100.0
                             if dd_ref > 0 else None)

        out.append(SegmentPivot(
            seg=SEG_NAMES[seg_idx],
            n_minutes=n_bars,
            peak_mtm=peak_mtm,
            peak_minute_offset=peak_offset,
            peak_ts_ist_minute_of_day=int(seg_ist[peak_idx_local]),
            trough_mtm=trough_mtm,
            trough_minute_offset=trough_offset,
            trough_ts_ist_minute_of_day=int(seg_ist[trough_idx_local]),
            dd_usd=dd_usd,
            dd_ref_mtm=float(dd_ref),
            dd_pct_from_peak=dd_pct_per_trade,
        ))
    return out


def _fmt_minute_of_day(m: float) -> str:
    """Render an average minute-of-day as 'HH:MM' IST string."""
    if m is None or (isinstance(m, float) and np.isnan(m)):
        return "—"
    mi = int(round(m)) % 1440
    return f"{mi // 60:02d}:{mi % 60:02d}"


# Mods ≥ 1080 (≥ 18:00 IST) are Friday-evening entries — the trade exit cap
# is 17:30 IST Sat so anything ≥ 18:00 IST must be a late-Fri entry that runs
# into Sat. Shift those to negative before averaging so the wrap-around
# doesn't corrupt the mean. Unshift via `mod 1440` after averaging.
_WRAP_THRESH = 1080


def _circular_mean_mod(mods: np.ndarray) -> float:
    """Circular-aware mean for IST minute-of-day values that may straddle
    midnight (Fri night → Sat). Shifts mods ≥ 1080 to negative, takes
    nanmean, then maps back via mod 1440. The trade-day window is
    [21:00 Fri IST = 1260, 17:30 Sat IST = 1050] which is contiguous in the
    shifted axis (-180 .. 1050)."""
    if mods.size == 0:
        return float("nan")
    shifted = np.where(mods >= _WRAP_THRESH, mods - 1440.0, mods)
    avg = float(np.nanmean(shifted))
    if not np.isfinite(avg):
        return avg
    return avg % 1440


def _agg_segment(pivots: list[SegmentPivot]) -> dict:
    """Aggregate per-segment SegmentPivot records into avg/median/p25/p75
    summaries. Returns the JSON-shaped dict the API serves.
    """
    if not pivots:
        return {
            "n_trades": 0,
            "n_trades_for_dd_pct": 0,
            "avg_peak_ts_ist": None, "avg_peak_minute_offset": None,
            "avg_peak_mtm_usd": None, "median_peak_mtm_usd": None,
            "p25_peak_mtm_usd": None, "p75_peak_mtm_usd": None,
            "avg_trough_ts_ist": None, "avg_trough_minute_offset": None,
            "avg_trough_mtm_usd": None, "median_trough_mtm_usd": None,
            "p25_trough_mtm_usd": None, "p75_trough_mtm_usd": None,
            "avg_dd_usd": None, "median_dd_usd": None,
            "avg_dd_pct_from_peak": None, "median_dd_pct_from_peak": None,
        }
    arr_peak = np.array([p.peak_mtm for p in pivots], dtype=np.float64)
    arr_trough = np.array([p.trough_mtm for p in pivots], dtype=np.float64)
    arr_peak_mod = np.array([p.peak_ts_ist_minute_of_day for p in pivots],
                            dtype=np.float64)
    arr_trough_mod = np.array([p.trough_ts_ist_minute_of_day for p in pivots],
                              dtype=np.float64)
    arr_peak_offset = np.array([p.peak_minute_offset for p in pivots],
                               dtype=np.float64)
    arr_trough_offset = np.array([p.trough_minute_offset for p in pivots],
                                 dtype=np.float64)
    arr_dd = np.array([p.dd_usd for p in pivots], dtype=np.float64)
    arr_dd_ref = np.array([p.dd_ref_mtm for p in pivots], dtype=np.float64)
    # DD% is computed as RATIO OF MEANS:
    #   avg_dd / avg_prev_peak * 100
    # We only average dd_ref for trades whose ref was a real peak (> 0). The
    # n_trades_for_dd_pct count reports how many trades contributed.
    pos_ref_mask = arr_dd_ref > 0
    n_pos_ref = int(pos_ref_mask.sum())
    if n_pos_ref >= 1:
        avg_dd_for_pct = float(np.nanmean(arr_dd[pos_ref_mask]))
        avg_ref_for_pct = float(np.nanmean(arr_dd_ref[pos_ref_mask]))
        avg_dd_pct_from_peak = ((avg_dd_for_pct / avg_ref_for_pct) * 100.0
                                  if avg_ref_for_pct > 0 else None)
        # Median DD% uses ratio of medians for the same reason.
        median_dd_for_pct = float(np.nanmedian(arr_dd[pos_ref_mask]))
        median_ref_for_pct = float(np.nanmedian(arr_dd_ref[pos_ref_mask]))
        median_dd_pct_from_peak = ((median_dd_for_pct / median_ref_for_pct)
                                     * 100.0
                                     if median_ref_for_pct > 0 else None)
    else:
        avg_dd_pct_from_peak = None
        median_dd_pct_from_peak = None
    # Std + count-within-1σ + above/below-avg counts.
    #   within ±1σ → how concentrated the cluster is around the mean
    #   above/below → distribution skew (e.g. 18↑ / 6↓ means most trades
    #                  beat the mean — average pulled down by a few outliers).
    def _stats(arr: np.ndarray) -> tuple[Optional[float], int, int, int]:
        arr_clean = arr[np.isfinite(arr)]
        if arr_clean.size == 0:
            return None, 0, 0, 0
        mean = float(np.nanmean(arr_clean))
        if arr_clean.size < 2:
            sd = 0.0
            within = int(arr_clean.size)
        else:
            sd = float(np.nanstd(arr_clean, ddof=0))
            within = int(np.sum(
                (arr_clean >= mean - sd) & (arr_clean <= mean + sd)))
        # Treat values exactly equal to the mean as neither above nor below
        # — they'll show as `n_trades − above − below`.
        n_above = int(np.sum(arr_clean > mean))
        n_below = int(np.sum(arr_clean < mean))
        return sd, within, n_above, n_below

    std_peak, n_within_peak, n_above_peak, n_below_peak = _stats(arr_peak)
    std_trough, n_within_trough, n_above_trough, n_below_trough = _stats(arr_trough)
    std_dd, n_within_dd, n_above_dd, n_below_dd = _stats(arr_dd)

    return {
        "n_trades": len(pivots),
        "n_trades_for_dd_pct": n_pos_ref,
        "avg_peak_ts_ist": _fmt_minute_of_day(_nan_to_none(_circular_mean_mod(arr_peak_mod))),
        "avg_peak_minute_offset": _nan_to_none(np.nanmean(arr_peak_offset)),
        "avg_peak_mtm_usd": _nan_to_none(np.nanmean(arr_peak)),
        "median_peak_mtm_usd": _nan_to_none(np.nanmedian(arr_peak)),
        "p25_peak_mtm_usd": _nan_to_none(np.nanpercentile(arr_peak, 25)),
        "p75_peak_mtm_usd": _nan_to_none(np.nanpercentile(arr_peak, 75)),
        "std_peak_mtm_usd": std_peak,
        "n_within_1sd_peak": n_within_peak,
        "n_above_avg_peak": n_above_peak,
        "n_below_avg_peak": n_below_peak,
        "avg_trough_ts_ist": _fmt_minute_of_day(_nan_to_none(_circular_mean_mod(arr_trough_mod))),
        "avg_trough_minute_offset": _nan_to_none(np.nanmean(arr_trough_offset)),
        "avg_trough_mtm_usd": _nan_to_none(np.nanmean(arr_trough)),
        "median_trough_mtm_usd": _nan_to_none(np.nanmedian(arr_trough)),
        "p25_trough_mtm_usd": _nan_to_none(np.nanpercentile(arr_trough, 25)),
        "p75_trough_mtm_usd": _nan_to_none(np.nanpercentile(arr_trough, 75)),
        "std_trough_mtm_usd": std_trough,
        "n_within_1sd_trough": n_within_trough,
        "n_above_avg_trough": n_above_trough,
        "n_below_avg_trough": n_below_trough,
        "avg_dd_usd": _nan_to_none(np.nanmean(arr_dd)),
        "median_dd_usd": _nan_to_none(np.nanmedian(arr_dd)),
        "std_dd_usd": std_dd,
        "n_within_1sd_dd": n_within_dd,
        "n_above_avg_dd": n_above_dd,
        "n_below_avg_dd": n_below_dd,
        "avg_dd_pct_from_peak": _nan_to_none(avg_dd_pct_from_peak),
        "median_dd_pct_from_peak": _nan_to_none(median_dd_pct_from_peak),
        "avg_dd_ref_mtm": _nan_to_none(np.nanmean(arr_dd_ref))
            if arr_dd_ref.size else None,
    }


def _nan_to_none(v) -> Optional[float]:
    """Convert numpy NaN/inf to None for JSON-safe serialization."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f


def aggregate_pivot_profile(
    trades_df: pd.DataFrame,
    paths_glob: str,
    entry_hours: Iterable[int],
    cells: Optional[list[dict]] = None,
    winner_tids: Optional[set[str]] = None,
    loser_tids: Optional[set[str]] = None,
    pnl_by_tid: Optional[dict[str, float]] = None,
    progress_cb=None,
) -> dict:
    """Walk every trade in `trades_df` (filtered by entry_hours OR cells),
    pull its 1m path, compute segment pivots, and aggregate by
    entry_atm_iv_band.

    If `cells` is provided, it overrides `entry_hours`: each cell is a dict
    with at least {entry_atm_iv_band, entry_hour_ist}, and a trade is
    included only if it matches some cell on BOTH band AND entry_hour. This
    scopes each band's pivot profile to its own selected best-combo cell.

    If `winner_tids` / `loser_tids` are provided (sets of trade_id strings),
    the response also includes `by_band_winners` and `by_band_losers` blocks
    so the frontend can render an "All / Winners / Losers" toggle without a
    re-fetch.

    Returns the JSON dict the FastAPI route serves.

    `progress_cb(done:int, total:int)` is invoked every ~50 trades for
    long-running calls so the warming-response can publish progress.
    """
    hours_set = set(int(h) for h in entry_hours) if entry_hours else set()
    if not hours_set and not cells:
        hours_set = {21, 22, 23, 0, 1, 2, 3}

    if trades_df is None or trades_df.empty:
        return {
            "by_band": {},
            "by_band_winners": None,
            "by_band_losers": None,
            "params": {
                "entry_hours": sorted(hours_set),
                "cells": cells or [],
                "n_total_trades": 0, "n_after_filter": 0,
                "min_trades_per_band_cell": MIN_TRADES_PER_BAND_CELL,
            },
        }

    # Filter trades on the full (band, hour, expiry_bucket, delta_target)
    # cell tuple when cells is provided; otherwise fall back to the bare
    # entry-hour filter. We also build a {cell_key -> lot_scale} map so each
    # trade's MTM can be rescaled away from the 100-lot backtester baseline.
    cell_scale_map: dict[str, float] = {}
    if cells:
        cell_keys: set[tuple[str, str, str, str]] = set()
        for c in cells:
            band = c.get("entry_atm_iv_band")
            hour = c.get("entry_hour_ist")
            expiry = c.get("expiry_bucket")
            delta = c.get("delta_target")
            lots = c.get("lots")
            if band is None or hour is None:
                continue
            try:
                key_tuple = (
                    str(band),
                    str(int(hour)),
                    str(expiry) if expiry is not None else "",
                    f"{float(delta):.4f}" if delta is not None else "",
                )
                cell_keys.add(key_tuple)
                # Default 1.0 (i.e., 100-lot baseline) when lots is missing/0/<=0.
                if lots is not None:
                    try:
                        n = int(lots)
                        if n > 0:
                            cell_scale_map["|".join(key_tuple)] = (
                                n / float(BACKTESTER_BASELINE_LOTS))
                    except (TypeError, ValueError):
                        pass
            except (TypeError, ValueError):
                continue
        if not cell_keys:
            return {
                "by_band": {},
                "params": {
                    "entry_hours": [],
                    "cells": cells,
                    "n_total_trades": int(len(trades_df)),
                    "n_after_filter": 0,
                    "min_trades_per_band_cell": MIN_TRADES_PER_BAND_CELL,
                    "note": "cells param had no usable (band, hour, expiry, delta) keys",
                },
            }
        # Build the same composite key on every trade row.
        td_bands = trades_df["entry_atm_iv_band"].astype(str)
        td_hours = trades_df["entry_hour_ist"].astype("Int64").astype(str)
        td_expiry = (trades_df.get("expiry_bucket", "")
                     .astype(str) if "expiry_bucket" in trades_df.columns
                     else pd.Series([""] * len(trades_df), index=trades_df.index))
        td_delta = pd.to_numeric(
            trades_df.get("delta_target", 0.0),
            errors="coerce").apply(
                lambda v: f"{float(v):.4f}" if pd.notna(v) else "")
        trade_keys = (td_bands + "|" + td_hours + "|"
                       + td_expiry + "|" + td_delta)
        cell_key_strs = {"|".join(k) for k in cell_keys}
        mask = trade_keys.isin(cell_key_strs)
    else:
        mask = trades_df["entry_hour_ist"].astype("Int64").isin(list(hours_set))
    sel = trades_df.loc[mask, [
        "trade_id", "entry_ts_utc", "entry_hour_ist",
        "entry_atm_iv_band", "friday_date_ist",
    ]].copy()
    n_after = int(len(sel))

    # Group trades by their friday_date_ist partition so we can load each
    # partition parquet once and scan all trade_ids inside it.
    sel["friday_date_ist_str"] = pd.to_datetime(
        sel["friday_date_ist"]).dt.strftime("%Y-%m-%d")
    sel["trade_id_str"] = sel["trade_id"].astype(str)

    # We need expiry_bucket and delta_target to build the per-trade composite
    # key for cell-scale lookup. Pull them from the full trades_df rows that
    # passed the mask, then merge onto `sel` on trade_id.
    if cells and cell_scale_map:
        meta_cols = ["trade_id", "expiry_bucket", "delta_target"]
        meta = trades_df.loc[mask, [c for c in meta_cols
                                     if c in trades_df.columns]].copy()
        meta["trade_id_str"] = meta["trade_id"].astype(str)
        sel = sel.merge(
            meta[["trade_id_str", "expiry_bucket", "delta_target"]],
            on="trade_id_str", how="left")

    # Build a {trade_id_str -> meta} lookup. `meta` carries everything later
    # stages need: entry timestamp (for segment_pivots), the band, MTM scale,
    # plus context (entry hour, friday, lots) used by the per-trade drill-down
    # records.
    sel["entry_ts_unix_int"] = pd.to_numeric(
        sel["entry_ts_utc"], errors="coerce").astype("Int64")
    trade_lookup: dict[str, dict] = {}
    for row in sel.itertuples(index=False):
        ts = getattr(row, "entry_ts_unix_int", None)
        tid = getattr(row, "trade_id_str", None)
        band = getattr(row, "entry_atm_iv_band", None)
        if ts is None or tid is None or band is None or pd.isna(ts):
            continue
        scale = 1.0
        if cells and cell_scale_map:
            try:
                ebucket = getattr(row, "expiry_bucket", "") or ""
                dtgt = getattr(row, "delta_target", None)
                hr = int(getattr(row, "entry_hour_ist"))
                key = "|".join([
                    str(band), str(hr), str(ebucket),
                    f"{float(dtgt):.4f}" if dtgt is not None else "",
                ])
                if key in cell_scale_map:
                    scale = cell_scale_map[key]
            except (TypeError, ValueError, AttributeError):
                pass
        try:
            hr_val = int(getattr(row, "entry_hour_ist"))
        except (TypeError, ValueError, AttributeError):
            hr_val = -1
        trade_lookup[str(tid)] = {
            "entry_ts": int(ts),
            "band": str(band),
            "scale": float(scale),
            "entry_hour_ist": hr_val,
            "friday_date_ist": getattr(row, "friday_date_ist_str", ""),
            "lots": int(round(float(scale) * BACKTESTER_BASELINE_LOTS)),
        }

    # Accumulators: 3 parallel maps (all, winners-only, losers-only). Each
    # is {iv_band -> [list_of_5_lists]} — one list per segment index (0..4).
    band_pivots: dict[str, list[list[SegmentPivot]]] = {}
    band_pivots_winners: dict[str, list[list[SegmentPivot]]] = {}
    band_pivots_losers: dict[str, list[list[SegmentPivot]]] = {}
    have_outcome = bool(winner_tids) or bool(loser_tids)
    winner_set = winner_tids or set()
    loser_set = loser_tids or set()
    pnl_map = pnl_by_tid or {}

    # Per-trade records for the drill-down panels (losers list / best winner /
    # winner with worst min-MTM). Populated regardless of pnl availability —
    # min/max MTM stand on their own.
    trade_records: list[dict] = []

    partitions = sorted(sel["friday_date_ist_str"].unique())
    total_partitions = len(partitions)
    processed_trades = 0
    total_trades = len(trade_lookup)

    # ── Resolve storage form (option C: prefer partitioned dir; fall back to
    # flat parquet with predicate pushdown). ─────────────────────────────────
    partitioned_root: Optional[str] = None
    flat_path: Optional[str] = None
    is_glob_form = "/friday_date=*/" in paths_glob
    if is_glob_form:
        partitioned_root = paths_glob.rsplit("/friday_date=*/", 1)[0]
    else:
        # `paths_glob` here is actually a flat parquet path (e.g. when
        # `_paths_glob_for_dataset` chose the consolidated flat file).
        # Prefer the partitioned sibling directory when it exists — fall
        # back to single-file read with a `trade_id IN (...)` filter.
        if paths_glob.endswith("_flat.parquet"):
            cand = paths_glob[: -len("_flat.parquet")]
            if os.path.isdir(cand):
                partitioned_root = cand
        if partitioned_root is None:
            flat_path = paths_glob

    def _emit(tid: str, ts_arr: np.ndarray, mtm_raw: np.ndarray) -> None:
        """Process one trade's 1m path: compute pivots + accumulate band
        buckets + push a per-trade record."""
        nonlocal processed_trades
        meta = trade_lookup.get(tid)
        if meta is None or ts_arr.size == 0:
            return
        scale = meta["scale"]
        mtm = mtm_raw * scale if scale != 1.0 else mtm_raw
        band = meta["band"]
        pivots = segment_pivots(ts_arr, mtm, meta["entry_ts"])
        band_slots = band_pivots.setdefault(band, [list() for _ in range(5)])
        for i, p in enumerate(pivots):
            if p is not None:
                band_slots[i].append(p)
        if have_outcome:
            if tid in winner_set:
                slots_w = band_pivots_winners.setdefault(
                    band, [list() for _ in range(5)])
                for i, p in enumerate(pivots):
                    if p is not None:
                        slots_w[i].append(p)
            elif tid in loser_set:
                slots_l = band_pivots_losers.setdefault(
                    band, [list() for _ in range(5)])
                for i, p in enumerate(pivots):
                    if p is not None:
                        slots_l[i].append(p)
        finite = np.isfinite(mtm)
        min_mtm = float(np.min(mtm[finite])) if finite.any() else None
        max_mtm = float(np.max(mtm[finite])) if finite.any() else None
        trade_records.append({
            "trade_id": tid,
            "band": band,
            "entry_hour_ist": meta["entry_hour_ist"],
            "friday_date_ist": meta["friday_date_ist"],
            "min_mtm_usd": min_mtm,
            "max_mtm_usd": max_mtm,
            "lots": meta["lots"],
            "net_pnl_usd": pnl_map.get(tid),
        })
        processed_trades += 1
        if progress_cb is not None and processed_trades % 50 == 0:
            progress_cb(processed_trades, total_trades)

    if partitioned_root is not None:
        # Walk friday partitions present in the trade selection.
        for friday_str in partitions:
            part_path = os.path.join(
                partitioned_root, f"friday_date={friday_str}", "part.parquet")
            if not os.path.exists(part_path):
                continue
            try:
                pdf = pd.read_parquet(
                    part_path,
                    columns=["trade_id", "ts", "gross_pnl_usd"],
                )
            except Exception:
                continue
            if pdf.empty:
                continue
            pdf["trade_id_str"] = pdf["trade_id"].astype(str)
            tids_here = (set(pdf["trade_id_str"].unique())
                         & set(trade_lookup.keys()))
            for tid in tids_here:
                grp = pdf.loc[pdf["trade_id_str"] == tid,
                              ["ts", "gross_pnl_usd"]].sort_values("ts")
                if grp.empty:
                    continue
                _emit(tid,
                      grp["ts"].to_numpy(dtype=np.int64),
                      grp["gross_pnl_usd"].to_numpy(dtype=np.float64))
    elif flat_path is not None:
        # Single-file flat parquet — use pyarrow predicate pushdown on
        # trade_id so we don't slurp the whole 2-3 GB file into memory.
        try:
            import pyarrow.parquet as pq
            tids_wanted = list(trade_lookup.keys())
            tbl = pq.read_table(
                flat_path,
                columns=["trade_id", "ts", "gross_pnl_usd"],
                filters=[("trade_id", "in", tids_wanted)],
            )
            pdf = tbl.to_pandas()
        except Exception as exc:  # noqa: BLE001
            # Last-ditch: full read + Python-side filter. Slow but correct.
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "pivot_profile: pyarrow filter on flat parquet failed (%s); "
                "falling back to full-load + isin", exc)
            pdf = pd.read_parquet(
                flat_path,
                columns=["trade_id", "ts", "gross_pnl_usd"])
            pdf = pdf[pdf["trade_id"].astype(str).isin(
                set(trade_lookup.keys()))]
        if not pdf.empty:
            pdf["trade_id_str"] = pdf["trade_id"].astype(str)
            for tid, grp in pdf.groupby("trade_id_str", sort=False):
                grp = grp.sort_values("ts")
                _emit(tid,
                      grp["ts"].to_numpy(dtype=np.int64),
                      grp["gross_pnl_usd"].to_numpy(dtype=np.float64))

    if progress_cb is not None:
        progress_cb(processed_trades, total_trades)

    # Build the response.
    def _build(by_band_pivots: dict[str, list[list[SegmentPivot]]]) -> dict:
        out: dict[str, dict] = {}
        for band, slot_lists in by_band_pivots.items():
            seg_blob: dict[str, dict] = {}
            for i, name in enumerate(SEG_NAMES):
                seg_blob[name] = _agg_segment(slot_lists[i])
            out[band] = seg_blob
        return out

    # Drill-down lists (only when classification ran).
    losers_list: Optional[list[dict]] = None
    best_winner: Optional[dict] = None
    winner_worst_drawdown: Optional[dict] = None
    if have_outcome:
        losers_records = [r for r in trade_records
                          if r["trade_id"] in loser_set]
        # Worst (most negative) loss first. None pnl sinks to the bottom.
        losers_records.sort(
            key=lambda r: (r["net_pnl_usd"] is None,
                           r["net_pnl_usd"]
                           if r["net_pnl_usd"] is not None else 0.0))
        losers_list = losers_records
        winners_with_pnl = [r for r in trade_records
                             if r["trade_id"] in winner_set
                             and r["net_pnl_usd"] is not None]
        if winners_with_pnl:
            best_winner = max(winners_with_pnl,
                               key=lambda r: r["net_pnl_usd"])
        winners_with_min = [r for r in trade_records
                             if r["trade_id"] in winner_set
                             and r["min_mtm_usd"] is not None]
        if winners_with_min:
            winner_worst_drawdown = min(winners_with_min,
                                         key=lambda r: r["min_mtm_usd"])

    return {
        "by_band": _build(band_pivots),
        "by_band_winners": _build(band_pivots_winners) if have_outcome else None,
        "by_band_losers": _build(band_pivots_losers) if have_outcome else None,
        "losers_list": losers_list,
        "best_winner": best_winner,
        "winner_worst_drawdown": winner_worst_drawdown,
        "params": {
            "entry_hours": sorted(hours_set),
            "cells": cells or [],
            "n_total_trades": int(len(trades_df)),
            "n_after_filter": int(n_after),
            "n_processed": int(processed_trades),
            "n_winners": len(winner_set & {str(t) for t in trade_lookup.keys()}) if have_outcome else None,
            "n_losers": len(loser_set & {str(t) for t in trade_lookup.keys()}) if have_outcome else None,
            "min_trades_per_band_cell": MIN_TRADES_PER_BAND_CELL,
        },
    }

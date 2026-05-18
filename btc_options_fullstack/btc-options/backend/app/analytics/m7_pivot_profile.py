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
    dd_usd: float                  # peak − trough (≥ 0 by definition)
    dd_pct_from_peak: Optional[float]  # (peak-trough)/peak*100, None if peak<=0


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
    for seg_idx in range(5):
        bar_mask = seg_of_bar == seg_idx
        n_bars = int(bar_mask.sum())
        if n_bars == 0:
            out.append(None)
            continue
        seg_mtm = mtm_usd[bar_mask]
        seg_mod = bars_mod[bar_mask]
        seg_ist = bars_ist_arr[bar_mask]
        # Drop NaN bars before locating extrema (a few raw parquet rows can
        # have NaN gross_pnl when a leg's mark was missing for one bar; we
        # don't want one bad bar to nuke the entire trade-segment).
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
        dd_usd = peak_mtm - trough_mtm
        dd_pct: Optional[float]
        if peak_mtm > 0:
            dd_pct = (dd_usd / peak_mtm) * 100.0
        else:
            dd_pct = None
        out.append(SegmentPivot(
            seg=SEG_NAMES[seg_idx],
            n_minutes=n_bars,
            peak_mtm=peak_mtm,
            peak_minute_offset=int(seg_mod[peak_idx_local]),
            peak_ts_ist_minute_of_day=int(seg_ist[peak_idx_local]),
            trough_mtm=trough_mtm,
            trough_minute_offset=int(seg_mod[trough_idx_local]),
            trough_ts_ist_minute_of_day=int(seg_ist[trough_idx_local]),
            dd_usd=dd_usd,
            dd_pct_from_peak=dd_pct,
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
    dd_pcts = [p.dd_pct_from_peak for p in pivots
               if p.dd_pct_from_peak is not None]
    arr_ddpct = np.array(dd_pcts, dtype=np.float64) if dd_pcts else None
    return {
        "n_trades": len(pivots),
        "n_trades_for_dd_pct": len(dd_pcts),
        "avg_peak_ts_ist": _fmt_minute_of_day(_nan_to_none(_circular_mean_mod(arr_peak_mod))),
        "avg_peak_minute_offset": _nan_to_none(np.nanmean(arr_peak_offset)),
        "avg_peak_mtm_usd": _nan_to_none(np.nanmean(arr_peak)),
        "median_peak_mtm_usd": _nan_to_none(np.nanmedian(arr_peak)),
        "p25_peak_mtm_usd": _nan_to_none(np.nanpercentile(arr_peak, 25)),
        "p75_peak_mtm_usd": _nan_to_none(np.nanpercentile(arr_peak, 75)),
        "avg_trough_ts_ist": _fmt_minute_of_day(_nan_to_none(_circular_mean_mod(arr_trough_mod))),
        "avg_trough_minute_offset": _nan_to_none(np.nanmean(arr_trough_offset)),
        "avg_trough_mtm_usd": _nan_to_none(np.nanmean(arr_trough)),
        "median_trough_mtm_usd": _nan_to_none(np.nanmedian(arr_trough)),
        "p25_trough_mtm_usd": _nan_to_none(np.nanpercentile(arr_trough, 25)),
        "p75_trough_mtm_usd": _nan_to_none(np.nanpercentile(arr_trough, 75)),
        "avg_dd_usd": _nan_to_none(np.nanmean(arr_dd)),
        "median_dd_usd": _nan_to_none(np.nanmedian(arr_dd)),
        "avg_dd_pct_from_peak": (
            _nan_to_none(np.nanmean(arr_ddpct)) if arr_ddpct is not None else None),
        "median_dd_pct_from_peak": (
            _nan_to_none(np.nanmedian(arr_ddpct)) if arr_ddpct is not None else None),
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
    progress_cb=None,
) -> dict:
    """Walk every trade in `trades_df` (filtered by entry_hours OR cells),
    pull its 1m path, compute segment pivots, and aggregate by
    entry_atm_iv_band.

    If `cells` is provided, it overrides `entry_hours`: each cell is a dict
    with at least {entry_atm_iv_band, entry_hour_ist}, and a trade is
    included only if it matches some cell on BOTH band AND entry_hour. This
    scopes each band's pivot profile to its own selected best-combo cell.

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
            "params": {
                "entry_hours": sorted(hours_set),
                "cells": cells or [],
                "n_total_trades": 0, "n_after_filter": 0,
                "min_trades_per_band_cell": MIN_TRADES_PER_BAND_CELL,
            },
        }

    # Filter trades on the full (band, hour, expiry_bucket, delta_target)
    # cell tuple when cells is provided; otherwise fall back to the bare
    # entry-hour filter.
    if cells:
        cell_keys: set[tuple[str, str, str, str]] = set()
        for c in cells:
            band = c.get("entry_atm_iv_band")
            hour = c.get("entry_hour_ist")
            expiry = c.get("expiry_bucket")
            delta = c.get("delta_target")
            if band is None or hour is None:
                continue
            try:
                cell_keys.add((
                    str(band),
                    str(int(hour)),
                    str(expiry) if expiry is not None else "",
                    f"{float(delta):.4f}" if delta is not None else "",
                ))
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

    # Build a {trade_id_str -> (entry_ts_unix, band)} lookup for fast iteration.
    sel["entry_ts_unix_int"] = pd.to_numeric(
        sel["entry_ts_utc"], errors="coerce").astype("Int64")
    trade_lookup: dict[str, tuple[int, str]] = {}
    for tid, ts, band in zip(sel["trade_id_str"], sel["entry_ts_unix_int"],
                              sel["entry_atm_iv_band"]):
        if pd.isna(ts) or band is None:
            continue
        trade_lookup[str(tid)] = (int(ts), str(band))

    # Accumulator: {iv_band -> [list_of_5_optional_pivots ...]}.
    # Per band we keep a separate list per segment index (0..4).
    band_pivots: dict[str, list[list[SegmentPivot]]] = {}

    # Walk friday partitions present in the trade selection.
    partitions = sorted(sel["friday_date_ist_str"].unique())
    total_partitions = len(partitions)
    processed_trades = 0
    total_trades = len(trade_lookup)

    # Determine the on-disk root from the glob — paths_glob looks like
    # "<root>/m7_paths/friday_date=*/part.parquet".
    paths_root = paths_glob.rsplit("/friday_date=*/", 1)[0]

    for part_idx, friday_str in enumerate(partitions):
        part_path = os.path.join(
            paths_root, f"friday_date={friday_str}", "part.parquet")
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
        # Iterate the trades in this partition that we care about.
        tids_here = (set(pdf["trade_id_str"].unique())
                     & set(trade_lookup.keys()))
        for tid in tids_here:
            entry_ts_unix, band = trade_lookup[tid]
            grp = pdf.loc[pdf["trade_id_str"] == tid,
                          ["ts", "gross_pnl_usd"]].sort_values("ts")
            if grp.empty:
                continue
            pivots = segment_pivots(
                grp["ts"].to_numpy(dtype=np.int64),
                grp["gross_pnl_usd"].to_numpy(dtype=np.float64),
                entry_ts_unix,
            )
            band_slots = band_pivots.setdefault(
                band, [list() for _ in range(5)])
            for i, p in enumerate(pivots):
                if p is not None:
                    band_slots[i].append(p)
            processed_trades += 1
            if progress_cb is not None and processed_trades % 50 == 0:
                progress_cb(processed_trades, total_trades)

    if progress_cb is not None:
        progress_cb(processed_trades, total_trades)

    # Build the response.
    by_band: dict[str, dict] = {}
    for band, slot_lists in band_pivots.items():
        seg_blob: dict[str, dict] = {}
        for i, name in enumerate(SEG_NAMES):
            seg_blob[name] = _agg_segment(slot_lists[i])
        by_band[band] = seg_blob

    return {
        "by_band": by_band,
        "params": {
            "entry_hours": sorted(hours_set),
            "cells": cells or [],
            "n_total_trades": int(len(trades_df)),
            "n_after_filter": int(n_after),
            "n_processed": int(processed_trades),
            "min_trades_per_band_cell": MIN_TRADES_PER_BAND_CELL,
        },
    }

"""M7 Pivot Profile — segment-based per-trade peak/trough/DD, binned by IV band.

Splits each per-minute MTM path into 5 IST clock-anchored windows
(Seg1 entry→05:00, Seg2 05:00→08:00, Seg3 08:00→12:00, Seg4 12:00→15:00,
Seg5 15:00→17:30) and records the peak + trough of each segment plus the
$ swing and % drop from peak. Aggregates across trades grouped by
entry_atm_iv_band, with an entry-hour filter the caller chooses.

Pure-function module; the FastAPI router lives in app/api/m7_pivot_profile.py.

WINNER/LOSER CLASSIFICATION — INLINE
The pivot panel decides per-trade is_win by simulating each cell's exit rule
directly on the 1m path data, scoped only to the cell's matched trades. This
avoids the all-13703-trades DuckDB scan inside `_derive_exits` (which is
~24 min for a non-grid rule). The simulator mirrors `_exit_rule_sql_predicate`
in m7_results.py and uses the same cost helpers (`slippage_dollars_per_side`,
`compute_brokerage_one_side`) as `_add_exit_costs`. Any divergence in is_win
classification between Pivot Profile and the rest of M7 should be reported
as a bug in the simulator below.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import time, datetime, timezone, timedelta
from typing import Iterable, Optional
import os
import glob

import numpy as np
import pandas as pd

# Response shape version — bump when adding/removing top-level result fields
# so disk-cached entries with the old shape get rebuilt on next read.
_PIVOT_RESPONSE_VERSION = 2


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


def _simulate_exit(
    ts_arr: np.ndarray,            # int64 ascending
    gross_pnl_arr: np.ndarray,     # float64 (per-100-lot baseline)
    call_mark_arr: np.ndarray,     # float64
    put_mark_arr: np.ndarray,      # float64
    spot_arr: np.ndarray,          # float64
    meta: dict,                    # per-trade meta (entry marks, costs, etc.)
    rule: dict,                    # exit rule
) -> Optional[dict]:
    """Replicate `_compute_all_exits`'s rule semantics in Python on a single
    trade's 1m path. Returns exit-row data + net_pnl + is_win, all at the
    100-lot baseline (caller can re-scale if a cell-lots override is in play).

    Mirrors the SQL predicate in `_exit_rule_sql_predicate`:
        pnl_after_slip = gross_pnl − entry_slip_call − entry_slip_put
        max_profit_pct        → pnl_after_slip ≥ credit_usd * pct/100
        margin_target_pct     → pnl_after_slip ≥ margin_used * pct/100
        premium_sl_pct        → call_mark ≥ call_entry*(1+pct/100) OR put_mark ≥ put_entry*(1+pct/100)
    Combined with `fixed_exit_hour_ist`: rule fires first if its trigger ts
    is ≤ hour cap; otherwise the hour cap applies. Without a hour cap, the
    last path row is the hard cap.
    """
    n = ts_arr.size
    if n == 0:
        return None

    # Build trigger mask vectorised across the trade's path.
    pnl_after_slip = (gross_pnl_arr
                      - float(meta["entry_slip_call"])
                      - float(meta["entry_slip_put"]))
    trigger = np.zeros(n, dtype=bool)
    if rule.get("max_profit_pct") is not None and meta["credit_usd"] > 0:
        pct = float(rule["max_profit_pct"])
        trigger |= (pnl_after_slip >= meta["credit_usd"] * (pct / 100.0))
    if rule.get("margin_target_pct") is not None and meta["margin_used_usd"] > 0:
        pct = float(rule["margin_target_pct"])
        trigger |= (pnl_after_slip >= meta["margin_used_usd"] * (pct / 100.0))
    if rule.get("premium_sl_pct") is not None:
        pct = float(rule["premium_sl_pct"])
        mult = 1.0 + pct / 100.0
        trigger |= (
            (call_mark_arr >= meta["call_entry_mark"] * mult)
            | (put_mark_arr >= meta["put_entry_mark"] * mult)
        )

    exit_idx: int
    exit_reason: str
    if rule.get("fixed_exit_hour_ist") is not None:
        hour = float(rule["fixed_exit_hour_ist"])
        target_ts = int(meta["friday_ts_utc"]) + int(86400 + hour * 3600 - 19800)
        within_hour = ts_arr <= target_ts
        if not within_hour.any():
            return None  # no path rows before the cap — should be rare
        rule_within = trigger & within_hour
        if rule_within.any():
            exit_idx = int(np.argmax(rule_within))   # first True
            exit_reason = "rule_trigger"
        else:
            hour_idxs = np.where(within_hour)[0]
            exit_idx = int(hour_idxs[-1])             # latest ts ≤ cap
            exit_reason = "fixed_hour_ist"
    else:
        if trigger.any():
            exit_idx = int(np.argmax(trigger))
            exit_reason = "rule_trigger"
        else:
            exit_idx = n - 1                         # hard cap = last row
            exit_reason = "hard_cap"

    gross_at_exit = float(gross_pnl_arr[exit_idx])
    spot_at_exit = float(spot_arr[exit_idx])
    call_at_exit = float(call_mark_arr[exit_idx])
    put_at_exit = float(put_mark_arr[exit_idx])
    ts_at_exit = int(ts_arr[exit_idx])

    # Compute exit costs (same helpers as _add_exit_costs in m7_results.py).
    # If any required input is degenerate (mark ≤ 0 or spot ≤ 0) the helpers
    # return 0 — that matches their existing degenerate-path handling.
    try:
        from app.services.costs import (
            slippage_dollars_per_side, compute_brokerage_one_side,
        )
        qty = int(meta["quantity_lots"]) or 100
        c_slip = slippage_dollars_per_side(
            True, "smart", 5.0, 1.0, spot_at_exit, call_at_exit,
            float(meta["call_strike"]), True, qty, ts_at_exit)
        p_slip = slippage_dollars_per_side(
            True, "smart", 5.0, 1.0, spot_at_exit, put_at_exit,
            float(meta["put_strike"]), False, qty, ts_at_exit)
        c_brk = compute_brokerage_one_side(
            spot_at_exit, call_at_exit, qty, "offer", False)
        p_brk = compute_brokerage_one_side(
            spot_at_exit, put_at_exit, qty, "offer", False)
        total_exit_cost = c_slip + p_slip + c_brk + p_brk
    except Exception:  # noqa: BLE001
        # If the cost helpers blow up for any reason, fall back to entry-cost
        # symmetry. Approximation, but classification is rarely flipped by it.
        total_exit_cost = float(meta["total_entry_cost"])

    net_pnl = gross_at_exit - float(meta["total_entry_cost"]) - total_exit_cost
    return {
        "exit_idx": exit_idx,
        "exit_ts": ts_at_exit,
        "exit_reason": exit_reason,
        "gross_pnl_usd": gross_at_exit,
        "total_exit_cost_usd": float(total_exit_cost),
        "net_pnl_usd": float(net_pnl),
        "is_win": bool(net_pnl > 0),
    }


def aggregate_pivot_profile(
    trades_df: pd.DataFrame,
    paths_glob: str,
    entry_hours: Iterable[int],
    cells: Optional[list[dict]] = None,
    progress_cb=None,
) -> dict:
    """Walk every trade in `trades_df` (filtered by entry_hours OR cells),
    pull its 1m path, classify winners/losers inline using the cell's rule,
    compute segment pivots, and aggregate by entry_atm_iv_band.

    If `cells` is provided, it overrides `entry_hours`: each cell is a dict
    with at least {entry_atm_iv_band, entry_hour_ist, rule}, and a trade is
    included only if it matches some cell on (band, hour, expiry, delta).

    The classification is done in-process using `_simulate_exit` on the same
    1m path data already loaded for pivot computation — no separate DuckDB
    scan needed. This makes a cell with N trades pay O(N) work instead of
    O(13703) for cold rules.

    Returns the JSON dict the FastAPI route serves.

    `progress_cb(done:int, total:int)` is invoked every ~5 trades so the
    warming-response can publish progress for small-cell scopes too.
    """
    hours_set = set(int(h) for h in entry_hours) if entry_hours else set()
    if not hours_set and not cells:
        hours_set = {21, 22, 23, 0, 1, 2, 3}

    EMPTY_RESPONSE = {
        "by_band": {},
        "by_band_winners": None,
        "by_band_losers": None,
        "by_band_best_winner": None,
        "by_band_worst_drawdown_winner": None,
        "by_band_losers_individual": None,
        "best_winners_by_band": None,
        "worst_dd_winners_by_band": None,
        "losers_list_by_band": None,
        "_response_version": _PIVOT_RESPONSE_VERSION,
        "params": {
            "entry_hours": sorted(hours_set),
            "cells": cells or [],
            "n_total_trades": 0, "n_after_filter": 0,
            "min_trades_per_band_cell": MIN_TRADES_PER_BAND_CELL,
        },
    }
    if trades_df is None or trades_df.empty:
        return EMPTY_RESPONSE

    # Filter trades on the full (band, hour, expiry_bucket, delta_target)
    # cell tuple when cells is provided; otherwise fall back to the bare
    # entry-hour filter. We also build {cell_key -> lot_scale} for MTM
    # display rescaling and {cell_key -> rule} for inline classification.
    cell_scale_map: dict[str, float] = {}
    cell_rule_map: dict[str, dict] = {}
    if cells:
        cell_keys: set[tuple[str, str, str, str]] = set()
        for c in cells:
            band = c.get("entry_atm_iv_band")
            hour = c.get("entry_hour_ist")
            expiry = c.get("expiry_bucket")
            delta = c.get("delta_target")
            lots = c.get("lots")
            rule = c.get("rule") or {}
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
                key_str = "|".join(key_tuple)
                # Default 1.0 (i.e., 100-lot baseline) when lots is missing/0/<=0.
                if lots is not None:
                    try:
                        n = int(lots)
                        if n > 0:
                            cell_scale_map[key_str] = (
                                n / float(BACKTESTER_BASELINE_LOTS))
                    except (TypeError, ValueError):
                        pass
                # Rule attached per cell so different cells in the same
                # request can apply different exit rules.
                if isinstance(rule, dict):
                    cell_rule_map[key_str] = {k: v for k, v in rule.items()
                                               if v is not None}
            except (TypeError, ValueError):
                continue
        if not cell_keys:
            empty = dict(EMPTY_RESPONSE)
            empty["params"] = {
                **EMPTY_RESPONSE["params"],
                "cells": cells,
                "n_total_trades": int(len(trades_df)),
                "note": "cells param had no usable (band, hour, expiry, delta) keys",
            }
            return empty
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
    # Expand the projection to carry every column the inline simulator needs.
    # We tolerate missing columns (older parquet schemas) by gating with the
    # comprehension; classification just falls back to safe defaults for
    # trades lacking entry-context columns.
    sel_cols = [
        "trade_id", "entry_ts_utc", "entry_hour_ist",
        "entry_atm_iv_band", "friday_date_ist",
        # cell-key bits + simulator inputs:
        "expiry_bucket", "delta_target",
        "credit_usd", "margin_used_usd_at_entry",
        "call_entry_mark", "put_entry_mark",
        "call_strike", "put_strike", "quantity_lots",
        "entry_slippage_call_usd", "entry_slippage_put_usd",
        "total_entry_cost_usd",
    ]
    sel = trades_df.loc[mask, [c for c in sel_cols
                                if c in trades_df.columns]].copy()
    n_after = int(len(sel))

    # Group trades by their friday_date_ist partition so we can load each
    # partition parquet once and scan all trade_ids inside it.
    sel["friday_date_ist_str"] = pd.to_datetime(
        sel["friday_date_ist"]).dt.strftime("%Y-%m-%d")
    sel["trade_id_str"] = sel["trade_id"].astype(str)

    # Build a {trade_id_str -> meta} lookup. `meta` carries everything later
    # stages need: entry timestamp (for segment_pivots), the band, MTM scale,
    # plus context (entry hour, friday, lots) used by the per-trade drill-down
    # records AND every input the inline rule simulator needs.
    sel["entry_ts_unix_int"] = pd.to_numeric(
        sel["entry_ts_utc"], errors="coerce").astype("Int64")
    trade_lookup: dict[str, dict] = {}

    def _safe_float(row, attr: str, default: float = 0.0) -> float:
        try:
            v = getattr(row, attr, None)
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                return default
            return float(v)
        except (TypeError, ValueError, AttributeError):
            return default

    for row in sel.itertuples(index=False):
        ts = getattr(row, "entry_ts_unix_int", None)
        tid = getattr(row, "trade_id_str", None)
        band = getattr(row, "entry_atm_iv_band", None)
        if ts is None or tid is None or band is None or pd.isna(ts):
            continue
        try:
            hr_val = int(getattr(row, "entry_hour_ist"))
        except (TypeError, ValueError, AttributeError):
            hr_val = -1
        ebucket = (str(getattr(row, "expiry_bucket", "") or "")
                   if hasattr(row, "expiry_bucket") else "")
        dtgt = getattr(row, "delta_target", None)
        cell_key = "|".join([
            str(band), str(hr_val), ebucket,
            f"{float(dtgt):.4f}" if dtgt is not None else "",
        ])
        scale = float(cell_scale_map.get(cell_key, 1.0)) if cells else 1.0
        rule = cell_rule_map.get(cell_key, {}) if cells else {}
        friday_str = getattr(row, "friday_date_ist_str", "")
        try:
            friday_ts_utc = int(pd.Timestamp(str(friday_str),
                                              tz="UTC").timestamp())
        except Exception:  # noqa: BLE001
            friday_ts_utc = 0
        try:
            qty = int(getattr(row, "quantity_lots", 100) or 100)
        except (TypeError, ValueError, AttributeError):
            qty = 100
        trade_lookup[str(tid)] = {
            "entry_ts": int(ts),
            "band": str(band),
            "scale": scale,
            "entry_hour_ist": hr_val,
            "friday_date_ist": friday_str,
            "lots": int(round(scale * BACKTESTER_BASELINE_LOTS)),
            # Inline-simulator inputs (per-100-lot baseline values):
            "rule": rule,
            "credit_usd":       _safe_float(row, "credit_usd"),
            "margin_used_usd":  _safe_float(row, "margin_used_usd_at_entry"),
            "call_entry_mark":  _safe_float(row, "call_entry_mark"),
            "put_entry_mark":   _safe_float(row, "put_entry_mark"),
            "call_strike":      _safe_float(row, "call_strike"),
            "put_strike":       _safe_float(row, "put_strike"),
            "quantity_lots":    qty,
            "entry_slip_call":  _safe_float(row, "entry_slippage_call_usd"),
            "entry_slip_put":   _safe_float(row, "entry_slippage_put_usd"),
            "total_entry_cost": _safe_float(row, "total_entry_cost_usd"),
            "friday_ts_utc":    friday_ts_utc,
        }

    # Accumulators: 3 parallel maps (all, winners-only, losers-only). Each
    # is {iv_band -> [list_of_5_lists]} — one list per segment index (0..4).
    band_pivots: dict[str, list[list[SegmentPivot]]] = {}
    band_pivots_winners: dict[str, list[list[SegmentPivot]]] = {}
    band_pivots_losers: dict[str, list[list[SegmentPivot]]] = {}
    # Per-trade segment data, retained so we can build per-band drill-downs
    # (single best winner / single worst-dd winner / per-trade loser charts).
    per_trade_pivots: dict[str, list[Optional[SegmentPivot]]] = {}
    # Classification is computed inline now — initialise empty sets that the
    # simulator populates as we walk paths.
    winner_set: set[str] = set()
    loser_set: set[str] = set()
    pnl_map: dict[str, float] = {}
    have_outcome = bool(cells)  # we'll only classify when cells (with rules) are given

    # Per-trade records for the drill-down panels (losers list / best winner /
    # winner with worst min-MTM).
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

    def _emit(tid: str,
              ts_arr: np.ndarray,
              gross_pnl_arr: np.ndarray,
              call_mark_arr: np.ndarray,
              put_mark_arr: np.ndarray,
              spot_arr: np.ndarray) -> None:
        """Process one trade's 1m path end-to-end:
          1. Simulate the cell's exit rule → is_win, net_pnl
          2. Compute segment pivots on the scaled MTM
          3. Push aggregates into band buckets + the per-trade record
        """
        nonlocal processed_trades
        meta = trade_lookup.get(tid)
        if meta is None or ts_arr.size == 0:
            return
        scale = meta["scale"]
        band = meta["band"]

        # 1) Inline classification (only when we have a rule from a cell).
        simulated = None
        if have_outcome:
            simulated = _simulate_exit(
                ts_arr, gross_pnl_arr, call_mark_arr,
                put_mark_arr, spot_arr,
                meta, meta.get("rule") or {},
            )
            if simulated is not None:
                # Scale net P&L to the cell's lot count so cards/list values
                # reflect what the trader would have realised at chosen lots.
                net_scaled = simulated["net_pnl_usd"] * scale
                pnl_map[tid] = net_scaled
                if simulated["is_win"]:
                    winner_set.add(tid)
                else:
                    loser_set.add(tid)

        # 2) Segment pivots on the scaled MTM (lots-display convention).
        mtm = gross_pnl_arr * scale if scale != 1.0 else gross_pnl_arr
        pivots = segment_pivots(ts_arr, mtm, meta["entry_ts"])
        per_trade_pivots[tid] = pivots
        band_slots = band_pivots.setdefault(band, [list() for _ in range(5)])
        for i, p in enumerate(pivots):
            if p is not None:
                band_slots[i].append(p)
        if have_outcome and simulated is not None:
            if simulated["is_win"]:
                slots_w = band_pivots_winners.setdefault(
                    band, [list() for _ in range(5)])
                for i, p in enumerate(pivots):
                    if p is not None:
                        slots_w[i].append(p)
            else:
                slots_l = band_pivots_losers.setdefault(
                    band, [list() for _ in range(5)])
                for i, p in enumerate(pivots):
                    if p is not None:
                        slots_l[i].append(p)

        # 3) Per-trade min/max MTM record (on scaled MTM).
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
        if progress_cb is not None and processed_trades % 5 == 0:
            progress_cb(processed_trades, total_trades)

    PATH_COLS = ["trade_id", "ts", "gross_pnl_usd",
                 "call_mark", "put_mark", "spot"]
    SUB_COLS = ["ts", "gross_pnl_usd", "call_mark", "put_mark", "spot"]

    def _emit_from_grp(tid: str, grp: pd.DataFrame) -> None:
        grp = grp.sort_values("ts")
        ts = grp["ts"].to_numpy(dtype=np.int64)
        gp = grp["gross_pnl_usd"].to_numpy(dtype=np.float64)
        # Tolerate older path parquets that may not carry call/put/spot.
        cm = (grp["call_mark"].to_numpy(dtype=np.float64)
              if "call_mark" in grp.columns
              else np.zeros(ts.size, dtype=np.float64))
        pm = (grp["put_mark"].to_numpy(dtype=np.float64)
              if "put_mark" in grp.columns
              else np.zeros(ts.size, dtype=np.float64))
        sp = (grp["spot"].to_numpy(dtype=np.float64)
              if "spot" in grp.columns
              else np.zeros(ts.size, dtype=np.float64))
        _emit(tid, ts, gp, cm, pm, sp)

    if partitioned_root is not None:
        # Walk friday partitions present in the trade selection.
        for friday_str in partitions:
            part_path = os.path.join(
                partitioned_root, f"friday_date={friday_str}", "part.parquet")
            if not os.path.exists(part_path):
                continue
            try:
                pdf = pd.read_parquet(part_path, columns=PATH_COLS)
            except Exception:
                # Fall back to the minimum set if call/put/spot are missing.
                try:
                    pdf = pd.read_parquet(
                        part_path,
                        columns=["trade_id", "ts", "gross_pnl_usd"])
                except Exception:
                    continue
            if pdf.empty:
                continue
            pdf["trade_id_str"] = pdf["trade_id"].astype(str)
            tids_here = (set(pdf["trade_id_str"].unique())
                         & set(trade_lookup.keys()))
            keep_cols = [c for c in SUB_COLS if c in pdf.columns]
            for tid in tids_here:
                grp = pdf.loc[pdf["trade_id_str"] == tid, keep_cols]
                if grp.empty:
                    continue
                _emit_from_grp(tid, grp)
    elif flat_path is not None:
        # Single-file flat parquet — use pyarrow predicate pushdown on
        # trade_id so we don't slurp the whole 2-3 GB file into memory.
        try:
            import pyarrow.parquet as pq
            tids_wanted = list(trade_lookup.keys())
            tbl = pq.read_table(
                flat_path,
                columns=PATH_COLS,
                filters=[("trade_id", "in", tids_wanted)],
            )
            pdf = tbl.to_pandas()
        except Exception as exc:  # noqa: BLE001
            # Last-ditch: full read + Python-side filter. Slow but correct.
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "pivot_profile: pyarrow filter on flat parquet failed (%s); "
                "falling back to full-load + isin", exc)
            try:
                pdf = pd.read_parquet(flat_path, columns=PATH_COLS)
            except Exception:
                pdf = pd.read_parquet(
                    flat_path,
                    columns=["trade_id", "ts", "gross_pnl_usd"])
            pdf = pdf[pdf["trade_id"].astype(str).isin(
                set(trade_lookup.keys()))]
        if not pdf.empty:
            pdf["trade_id_str"] = pdf["trade_id"].astype(str)
            keep_cols = [c for c in SUB_COLS if c in pdf.columns]
            for tid, grp in pdf.groupby("trade_id_str", sort=False):
                _emit_from_grp(tid, grp[keep_cols])

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

    # Per-band drill-downs (only when classification ran).
    by_band_best_winner: Optional[dict] = None
    by_band_worst_dd_winner: Optional[dict] = None
    by_band_losers_individual: Optional[dict] = None
    best_winners_by_band: Optional[dict] = None
    worst_dd_winners_by_band: Optional[dict] = None
    losers_list_by_band: Optional[dict] = None
    if have_outcome:
        # Group trade records by band.
        recs_by_band: dict[str, list[dict]] = {}
        for r in trade_records:
            recs_by_band.setdefault(r["band"], []).append(r)

        best_winners_by_band = {}
        worst_dd_winners_by_band = {}
        losers_list_by_band = {}
        bw_seg_by_band: dict[str, list[list[SegmentPivot]]] = {}
        wd_seg_by_band: dict[str, list[list[SegmentPivot]]] = {}
        losers_seg_by_band: dict[str, list[dict]] = {}

        for band, recs in recs_by_band.items():
            winners = [r for r in recs
                       if r["trade_id"] in winner_set
                       and r["net_pnl_usd"] is not None]
            if winners:
                bw = max(winners, key=lambda r: r["net_pnl_usd"])
                best_winners_by_band[band] = bw
                pivs = per_trade_pivots.get(bw["trade_id"], [])
                # Wrap each per-segment pivot in a 1-element list so the
                # existing _agg_segment can render it as a degenerate aggregate
                # (single trade — n_trades=1, avg=that trade's value).
                slots = [[p] if p is not None else [] for p in pivs]
                bw_seg_by_band[band] = slots
            winners_with_min = [r for r in winners
                                 if r["min_mtm_usd"] is not None]
            if winners_with_min:
                wd = min(winners_with_min,
                         key=lambda r: r["min_mtm_usd"])
                worst_dd_winners_by_band[band] = wd
                pivs = per_trade_pivots.get(wd["trade_id"], [])
                slots = [[p] if p is not None else [] for p in pivs]
                wd_seg_by_band[band] = slots

            losers = [r for r in recs if r["trade_id"] in loser_set]
            losers.sort(
                key=lambda r: (r["net_pnl_usd"] is None,
                               r["net_pnl_usd"]
                               if r["net_pnl_usd"] is not None else 0.0))
            losers_list_by_band[band] = losers
            # Per-trade segment blobs for the stacked mini-charts.
            band_loser_blobs: list[dict] = []
            for r in losers:
                pivs = per_trade_pivots.get(r["trade_id"], [])
                seg_blob: dict[str, dict] = {}
                for i, name in enumerate(SEG_NAMES):
                    seg_blob[name] = _agg_segment(
                        [pivs[i]] if i < len(pivs) and pivs[i] is not None else [])
                band_loser_blobs.append({
                    "trade_id": r["trade_id"],
                    "friday_date_ist": r["friday_date_ist"],
                    "net_pnl_usd": r["net_pnl_usd"],
                    "min_mtm_usd": r["min_mtm_usd"],
                    "max_mtm_usd": r["max_mtm_usd"],
                    "lots": r["lots"],
                    "segments": seg_blob,
                })
            losers_seg_by_band[band] = band_loser_blobs

        by_band_best_winner = _build(bw_seg_by_band)
        by_band_worst_dd_winner = _build(wd_seg_by_band)
        by_band_losers_individual = losers_seg_by_band

    return {
        "by_band": _build(band_pivots),
        "by_band_winners": _build(band_pivots_winners) if have_outcome else None,
        "by_band_losers": _build(band_pivots_losers) if have_outcome else None,
        "by_band_best_winner": by_band_best_winner,
        "by_band_worst_drawdown_winner": by_band_worst_dd_winner,
        "by_band_losers_individual": by_band_losers_individual,
        "best_winners_by_band": best_winners_by_band,
        "worst_dd_winners_by_band": worst_dd_winners_by_band,
        "losers_list_by_band": losers_list_by_band,
        "_response_version": _PIVOT_RESPONSE_VERSION,
        "params": {
            "entry_hours": sorted(hours_set),
            "cells": cells or [],
            "n_total_trades": int(len(trades_df)),
            "n_after_filter": int(n_after),
            "n_processed": int(processed_trades),
            "n_winners": len(winner_set) if have_outcome else None,
            "n_losers": len(loser_set) if have_outcome else None,
            "min_trades_per_band_cell": MIN_TRADES_PER_BAND_CELL,
        },
    }

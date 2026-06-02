"""
Calendar Sweep — Backwardation double-calendar batch backtester (Phase B / Phase 1).

For every backwardation observation in the Phase-A scan
(`backwardation_scan_24h.csv`), simulate a **double calendar** and record a 5-minute
MTM path from entry to near-expiry settlement, plus a single precomputed
`hold_to_settlement` exit. The output parquet schema is M7-compatible so the existing
M7 "best combo per IV band" analysis layer can read it under `dataset="calendar"`.

NO exit-rule sweep — one rule only (`hold_to_settlement`, cap 12:00 UTC on near expiry).
The single exit is precomputed into the trade row so Phase 2 short-circuits
`_compute_all_exits`.

Structure (user decisions 2026-06-02):
  - Double calendar, **same strike across expiries** — per delta D ∈ {0.30, 0.40, 0.50}:
      SELL near CE @ K_ce + SELL near PE @ K_pe   (near legs set the strikes)
      BUY  far  CE @ K_ce + BUY  far  PE @ K_pe   (far legs reuse the same strikes)
    K_ce / K_pe chosen by the **near leg's nearest |Δ| = D** (`strike_for_closest_delta`).
    The far legs reuse those strikes → their delta floats with the extra DTE (expected).

Data-prep filters (applied here, before simulation):
  1. **Gap floor**: drop `bucket == "minimal"` (gap < 5pp). ~70% of raw rows.
  2. **Physical-pair dedupe**: the same physical (near_expiry, far_expiry) at one timestamp
     can appear under two `pair` selector labels (the selector→expiry map is many-to-one).
     Dedupe to unique (date, time_ist, near_expiry, far_expiry).
  3. **Canonical label**: when a group collides, keep the pair whose selectors are
     nearest-relative (lowest combined rank in
     current < next < next_to_next < weekly < next_weekly).

Grid-key mapping (calendar → M7 grid):
  - entry_atm_iv_band ← gap bucket (e.g. "[5,10)")
  - expiry_bucket     ← pair (e.g. "current-next") — PRE-SET (not DTE-derived)
  - delta_target      ← 0.30 / 0.40 / 0.50
  - entry_hour_ist    ← carried context (hour aggregated downstream via "∑ All hours")

Outputs (under ~/btc-data/derived/calendar/):
  calendar_trades.parquet                          — one row per (obs, delta)
  calendar_paths/friday_date=<near_expiry>/part.parquet  — 5m path rows, Hive-partitioned

Run (RULE #5 — dedicated container for the full run):
  python -m app.analytics.calendar_batch_backtester --rebuild
  python -m app.analytics.calendar_batch_backtester --pair current-next --limit 200 --rebuild   # pilot
  python -m app.analytics.calendar_batch_backtester --since 2025-01-01 --append
"""

from __future__ import annotations

import argparse
import csv as _csv
import hashlib
import logging
import math
import os
import sys
import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import duckdb
import pandas as pd

from app.analytics.enrich_options import DERIVED_DIR, load_chain_for_expiry
from app.core.greeks import compute_greeks, implied_vol
from app.services.costs import (
    CONTRACT_VALUE,
    compute_brokerage_one_side,
    slippage_dollars_per_side,
)
from app.services.margin_v2 import MarginLeg, compute_portfolio_margin

# ── Paths & constants ────────────────────────────────────────────────────────

CAL_OUT_DIR = os.path.join(DERIVED_DIR, "calendar")
TRADES_OUT = os.path.join(CAL_OUT_DIR, "calendar_trades.parquet")
PATHS_OUT_DIR = os.path.join(CAL_OUT_DIR, "calendar_paths")
SCAN_CSV = os.path.join(DERIVED_DIR, "backwardation_scan_24h.csv")
SPOT_PATH = "/home/abhis/btc-data/data/spot/BTCUSD_1min.parquet"

TARGET_DELTAS = (0.30, 0.40, 0.50)
QTY_LOTS = 100
IST_OFFSET_SEC = int(5.5 * 3600)
BAR_SEC = 300  # 5-minute path bars
SETTLEMENT_HOUR_UTC = 12  # daily options settle 12:00 UTC = 17:30 IST

# Selector nearness rank for canonical-label dedupe (lower = nearer-relative).
SELECTOR_RANK = {
    "current": 0, "next": 1, "next_to_next": 2, "weekly": 3, "next_weekly": 4,
}

DEFAULT_COST_CFG = {
    "slippage": {"enabled": True, "mode": "smart", "flat_value": 5.0, "mult": 1.0},
    "brokerage": {"enabled": True, "rate": "offer", "referral": False},
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ── Time helpers ──────────────────────────────────────────────────────────────

def ist_to_unix(date_str: str, time_ist: str) -> int:
    """IST wall-clock (date_str 'YYYY-MM-DD', time_ist 'HH:MM') → UTC unix seconds."""
    y, m, d = (int(x) for x in date_str.split("-"))
    hh, mm = (int(x) for x in time_ist.split(":"))
    wall = datetime(y, m, d, hh, mm, tzinfo=timezone.utc)
    return int(wall.timestamp()) - IST_OFFSET_SEC


def settlement_unix(expiry_iso: str) -> int:
    """12:00 UTC on the expiry date."""
    y, m, d = (int(x) for x in expiry_iso.split("-"))
    dt = datetime(y, m, d, SETTLEMENT_HOUR_UTC, 0, 0, tzinfo=timezone.utc)
    return int(dt.timestamp())


def session_date_ist(entry_ts: int) -> str:
    """17:30→17:30 IST trading-session date for the de-overlap toggle.

    Shift the IST wall-clock back to the 17:30 boundary, then take the date.
    """
    ist_wall = entry_ts + IST_OFFSET_SEC - SETTLEMENT_HOUR_UTC * 3600
    return datetime.fromtimestamp(ist_wall, tz=timezone.utc).date().isoformat()


def make_trade_id(near_exp: str, far_exp: str, date_str: str,
                  time_ist: str, delta: float) -> int:
    s = f"{date_str}|{time_ist}|{near_exp}|{far_exp}|{delta:.2f}"
    h = hashlib.sha256(s.encode()).digest()
    return int.from_bytes(h[:8], "big") & ((1 << 63) - 1)


# ── Scan load + data-prep filters ──────────────────────────────────────────────

def _selector_pair_rank(near_selector: str, far_selector: str) -> int:
    return (SELECTOR_RANK.get(near_selector, 99)
            + SELECTOR_RANK.get(far_selector, 99))


def load_and_prep_scan(csv_path: str,
                       since: Optional[str], through: Optional[str],
                       pair_filter: Optional[str]) -> list[dict]:
    """Read the scan CSV and apply the three data-prep filters.

    Returns deduped, gap≥5pp observation rows (each a dict), sorted by near_expiry.
    """
    with open(csv_path, newline="") as f:
        rows = list(_csv.DictReader(f))
    n_raw = len(rows)

    # 1. Gap floor — drop minimal (<5pp).
    rows = [r for r in rows if r["bucket"] != "minimal"]
    n_gap = len(rows)

    # Optional range / pair filters.
    if since:
        rows = [r for r in rows if r["date"] >= since]
    if through:
        rows = [r for r in rows if r["date"] <= through]
    if pair_filter:
        rows = [r for r in rows if r["pair"] == pair_filter]

    # 2 + 3. Physical-pair dedupe with canonical label by DTE-distance.
    by_phys: dict[tuple, dict] = {}
    n_collisions = 0
    for r in rows:
        key = (r["date"], r["time_ist"], r["near_expiry"], r["far_expiry"])
        rank = _selector_pair_rank(r["near_selector"], r["far_selector"])
        prev = by_phys.get(key)
        if prev is None:
            by_phys[key] = {**r, "_rank": rank}
        else:
            n_collisions += 1
            if rank < prev["_rank"]:
                by_phys[key] = {**r, "_rank": rank}
    deduped = list(by_phys.values())
    deduped.sort(key=lambda r: (r["near_expiry"], r["far_expiry"], r["date"], r["time_ist"]))

    log.info(f"Scan rows: raw={n_raw:,} → gap≥5pp={n_gap:,} → "
             f"unique(after dedupe)={len(deduped):,} "
             f"(collisions folded={n_collisions:,})")
    return deduped


# ── Spot 5m series (loaded once per near-expiry group) ─────────────────────────

def load_spot_window(conn: duckdb.DuckDBPyConnection,
                     t_start: int, t_end: int) -> pd.DataFrame:
    q = f"""
    SELECT timestamp_unix, mark_close
    FROM read_parquet('{SPOT_PATH}')
    WHERE timestamp_unix >= {int(t_start)} AND timestamp_unix <= {int(t_end)}
    ORDER BY timestamp_unix ASC
    """
    df = conn.execute(q).df()
    if df.empty:
        return df
    df["timestamp_unix"] = df["timestamp_unix"].astype("int64")
    return df.set_index("timestamp_unix")


def _spot_at(spot_series: pd.DataFrame, t: int) -> float:
    if spot_series.empty:
        return 0.0
    if t in spot_series.index:
        return float(spot_series.loc[t, "mark_close"])
    pos = spot_series.index.searchsorted(t, side="right") - 1
    if pos < 0:
        return 0.0
    return float(spot_series.iloc[pos]["mark_close"])


# ── In-memory per-group chain store (m7 pattern: load each expiry ONCE) ─────────
# The dominant cost on the Windows bind-mount is cold parquet opens. So per near-expiry
# group we load each needed expiry's full chain ONCE (load_chain_for_expiry), 5m-align it,
# and serve EVERYTHING from memory: chain_map (strike picks + entry marks/IV/delta),
# per-leg path series, and ATM IV. No per-snapshot parquet opens anywhere.

import bisect as _bisect

# expiry_iso -> {"ts": [int sorted 5m snaps], "by_ts": {ts: {"CE":{k:mark}, "PE":{k:mark}}}}
_GRP_CHAINS: dict[str, dict] = {}
_GRP_SPOT: Optional[pd.DataFrame] = None
_CHAIN_MAP_MEMO: dict[tuple[str, int], dict] = {}
_ATM_IV_MEMO: dict[tuple[str, int], float] = {}


def _reset_group(spot_series: pd.DataFrame, conn: duckdb.DuckDBPyConnection,
                 expiries: set[str], win_start: int, win_end: int) -> None:
    """Clear per-group caches and preload every needed expiry's chain into memory."""
    global _GRP_SPOT
    _GRP_CHAINS.clear()
    _CHAIN_MAP_MEMO.clear()
    _ATM_IV_MEMO.clear()
    _GRP_SPOT = spot_series
    for exp in expiries:
        _preload_expiry(conn, exp, win_start, win_end)


def _preload_expiry(conn: duckdb.DuckDBPyConnection, expiry_iso: str,
                    win_start: int, win_end: int) -> None:
    if expiry_iso in _GRP_CHAINS:
        return
    exp_date = datetime.strptime(expiry_iso, "%Y-%m-%d").date()
    df = load_chain_for_expiry(conn, exp_date, int(win_start), int(win_end))
    store: dict = {"ts": [], "by_ts": {}}
    if not df.empty:
        df = df[df["timestamp_unix"] % BAR_SEC == 0]
        for ts, grp in df.groupby("timestamp_unix"):
            tdict = {"CE": {}, "PE": {}}
            for strike, opt_type, mark in zip(grp["strike"], grp["opt_type"], grp["mark_close"]):
                if mark is None or pd.isna(mark) or mark <= 0:
                    continue
                tdict[opt_type][int(strike)] = float(mark)
            store["by_ts"][int(ts)] = tdict
        store["ts"] = sorted(store["by_ts"].keys())
    _GRP_CHAINS[expiry_iso] = store


def _nearest_snap(expiry_iso: str, t: int) -> Optional[int]:
    """Latest available 5m chain snapshot at or before t for this expiry."""
    store = _GRP_CHAINS.get(expiry_iso)
    if not store or not store["ts"]:
        return None
    ts = store["ts"]
    pos = _bisect.bisect_right(ts, t) - 1
    return ts[pos] if pos >= 0 else None


def _T_years(expiry_iso: str, t: int) -> float:
    expiry_dt = datetime.strptime(expiry_iso, "%Y-%m-%d").replace(
        tzinfo=timezone.utc, hour=SETTLEMENT_HOUR_UTC)
    now = datetime.fromtimestamp(t, tz=timezone.utc)
    return max(0.0001, (expiry_dt - now).total_seconds() / (365 * 24 * 3600))


def _chain_map(expiry_iso: str, t: int) -> dict:
    """{spot, T, CE:{strike:(mark,iv,delta)}, PE:{...}} from the nearest in-memory snapshot."""
    eff = _nearest_snap(expiry_iso, t)
    key = (expiry_iso, eff if eff is not None else -1)
    cm = _CHAIN_MAP_MEMO.get(key)
    if cm is not None:
        return cm
    if eff is None:
        cm = {"spot": 0.0, "T": 0.0, "CE": {}, "PE": {}}
        _CHAIN_MAP_MEMO[key] = cm
        return cm
    spot = _spot_at(_GRP_SPOT, eff) if _GRP_SPOT is not None else 0.0
    T = _T_years(expiry_iso, eff)
    out: dict = {"spot": float(spot), "T": float(T), "CE": {}, "PE": {}}
    if spot > 0:
        marks = _GRP_CHAINS[expiry_iso]["by_ts"][eff]
        for opt_type, flag in (("CE", "call"), ("PE", "put")):
            for k, mark in marks[opt_type].items():
                iv = implied_vol(float(mark), spot, float(k), T, 0.0, flag)
                if not iv or iv <= 0:
                    continue
                try:
                    d = compute_greeks(spot, float(k), T, 0.0, iv, flag).delta
                except Exception:
                    continue
                out[opt_type][int(k)] = (float(mark), float(iv), float(d))
    _CHAIN_MAP_MEMO[key] = out
    return out


def _pick_strike(cmap: dict, opt_type: str, target_delta: float) -> int:
    """Strike whose |delta| is closest to |target_delta| (matches strike_for_closest_delta)."""
    pool = cmap.get(opt_type, {})
    best, best_diff = 0, float("inf")
    target = abs(target_delta)
    for k, (_mark, _iv, d) in pool.items():
        diff = abs(abs(d) - target)
        if diff < best_diff:
            best_diff, best = diff, int(k)
    return best


def _atm_iv(expiry_iso: str, t: int) -> float:
    """ATM IV (decimal) from the nearest in-memory snapshot, memoized per (expiry, snap)."""
    eff = _nearest_snap(expiry_iso, t)
    if eff is None:
        return 0.0
    key = (expiry_iso, eff)
    v = _ATM_IV_MEMO.get(key)
    if v is not None:
        return v
    spot = _spot_at(_GRP_SPOT, eff) if _GRP_SPOT is not None else 0.0
    marks = _GRP_CHAINS[expiry_iso]["by_ts"][eff]
    if spot <= 0 or not marks["CE"]:
        _ATM_IV_MEMO[key] = 0.0
        return 0.0
    T = _T_years(expiry_iso, eff)
    atm_k = min(marks["CE"].keys(), key=lambda k: abs(k - spot))
    ivs = []
    for opt_type, flag in (("CE", "call"), ("PE", "put")):
        mk = marks[opt_type].get(atm_k)
        if mk and mk > 0:
            iv = implied_vol(float(mk), spot, float(atm_k), T, 0.0, flag)
            if iv and iv > 0:
                ivs.append(iv)
    v = float(sum(ivs) / len(ivs)) if ivs else 0.0
    _ATM_IV_MEMO[key] = v
    return v


def _leg_series_mem(expiry_iso: str, strike: int, opt_type: str,
                    t_start: int, t_end: int) -> tuple[list[int], list[float]]:
    """(ts, mark) arrays for one leg over [t_start, t_end], from the in-memory chain."""
    store = _GRP_CHAINS.get(expiry_iso)
    if not store or not store["ts"]:
        return [], []
    ts_out: list[int] = []
    mk_out: list[float] = []
    lo = _bisect.bisect_left(store["ts"], t_start)
    by_ts = store["by_ts"]
    sk = int(strike)
    for i in range(lo, len(store["ts"])):
        t = store["ts"][i]
        if t > t_end:
            break
        m = by_ts[t][opt_type].get(sk)
        if m is None:
            continue
        ts_out.append(t)
        mk_out.append(m)
    return ts_out, mk_out


# ── Leg 5m series → forward-fill lookup ────────────────────────────────────────

def _ff_mark(ts_arr: list[int], mk_arr: list[float], idx_state: list[int],
             entry_mark: float, t: int) -> float:
    """Forward-fill the latest mark at or before t; idx_state[0] is the cursor."""
    i = idx_state[0]
    last = entry_mark if i == 0 else mk_arr[i - 1]
    while i < len(ts_arr) and ts_arr[i] <= t:
        last = mk_arr[i]
        i += 1
    idx_state[0] = i
    return last


# ── Cost / greeks / margin helpers ─────────────────────────────────────────────

def _leg_cost(spot: float, mark: float, strike: int, is_call: bool,
              ts: int, cost_cfg: dict) -> float:
    """slippage + brokerage for ONE side of ONE leg (entry or exit)."""
    sl = cost_cfg.get("slippage", {}) or {}
    br = cost_cfg.get("brokerage", {}) or {}
    slip = slippage_dollars_per_side(
        sl.get("enabled", False), sl.get("mode", "smart"),
        sl.get("flat_value", 5.0), sl.get("mult", 1.0),
        spot, mark, strike, is_call, QTY_LOTS, ts,
    )
    brok = 0.0
    if br.get("enabled", False):
        brok = compute_brokerage_one_side(
            spot, mark, QTY_LOTS, br.get("rate", "offer"), br.get("referral", False))
    return float(slip) + float(brok)


def _calendar_margin(spot: float, K_ce: int, K_pe: int,
                     near_ce: float, near_pe: float, far_ce: float, far_pe: float,
                     T_near: float, T_far: float,
                     iv_near: float, iv_far: float) -> Optional[float]:
    """Portfolio margin for the 4-leg double calendar (2 short near + 2 long far)."""
    try:
        legs: list[MarginLeg] = []
        for is_call, strike, mark, T, iv, is_buy in (
            (True,  K_ce, near_ce, T_near, iv_near, False),   # SELL near CE
            (False, K_pe, near_pe, T_near, iv_near, False),   # SELL near PE
            (True,  K_ce, far_ce,  T_far,  iv_far,  True),    # BUY  far CE
            (False, K_pe, far_pe,  T_far,  iv_far,  True),    # BUY  far PE
        ):
            if iv <= 0 or mark <= 0:
                continue
            legs.append(MarginLeg(
                strike=float(strike), is_call=is_call, is_buy=is_buy,
                qty=int(QTY_LOTS), current_price=float(mark) * CONTRACT_VALUE,
                iv=float(iv), T=float(T), forward=0.0,
            ))
        if not legs:
            return None
        mr = compute_portfolio_margin(legs, spot)
        return round(mr.portfolio_margin, 2) if mr else None
    except Exception:
        return None


# ── Per-trade builder ───────────────────────────────────────────────────────────

def build_trade(obs: dict, delta: float,
                spot_series: pd.DataFrame,
                cost_cfg: dict) -> tuple[Optional[tuple[dict, list[dict]]], str]:
    """Simulate one double-calendar trade.

    Returns (None, skip_reason) on skip, else ((trade_row, path_rows), "ok").
    """
    near_exp = obs["near_expiry"]
    far_exp = obs["far_expiry"]
    date_str = obs["date"]
    time_ist = obs["time_ist"]

    entry_ts = ist_to_unix(date_str, time_ist)
    near_settle = settlement_unix(near_exp)
    far_settle = settlement_unix(far_exp)
    if near_settle <= entry_ts + 60:
        return None, "near_already_settled"
    if far_settle <= near_settle:
        return None, "far_not_after_near"

    spot_entry = _spot_at(spot_series, entry_ts)
    if spot_entry <= 0:
        return None, "no_spot"

    # One chain scan per (expiry, 5m-snap), memoized — serves all CE/PE × Δ picks
    # plus the entry marks/IVs/deltas for both near and far at the shared strike.
    snap = entry_ts - (entry_ts % BAR_SEC)
    near_cmap = _chain_map(near_exp, snap)
    far_cmap = _chain_map(far_exp, snap)
    if not near_cmap.get("CE") or not near_cmap.get("PE"):
        return None, "no_near_chain"

    # Strikes set by the NEAR leg's nearest |Δ| = D; far reuses the same strikes.
    K_ce = _pick_strike(near_cmap, "CE", delta)
    K_pe = _pick_strike(near_cmap, "PE", -delta)
    if K_ce <= 0 or K_pe <= 0:
        return None, "no_delta_strike"

    near_ce, _near_iv_ce, near_ce_delta = near_cmap["CE"][K_ce]
    near_pe, _near_iv_pe, near_pe_delta = near_cmap["PE"][K_pe]

    # Far legs reuse the same strikes; skip if either is unpriced on the far expiry.
    far_ce_e = far_cmap.get("CE", {}).get(K_ce)
    far_pe_e = far_cmap.get("PE", {}).get(K_pe)
    if far_ce_e is None or far_pe_e is None:
        return None, "far_leg_unpriced"
    far_ce, _far_iv_ce, far_ce_delta = far_ce_e
    far_pe, _far_iv_pe, far_pe_delta = far_pe_e

    T_near_e = float(near_cmap["T"])
    T_far_e = float(far_cmap["T"])

    # ATM IVs (separate from per-strike leg IVs) for margin + record.
    near_iv_e = _atm_iv(near_exp, entry_ts)
    far_iv_e = _atm_iv(far_exp, entry_ts)

    # Net entry premium (signed): + = net credit (near richer), − = net debit.
    net_premium = (near_ce + near_pe) - (far_ce + far_pe)
    credit_usd = net_premium * QTY_LOTS * CONTRACT_VALUE

    # Entry costs across all four legs.
    entry_cost = (
        _leg_cost(spot_entry, near_ce, K_ce, True, entry_ts, cost_cfg)
        + _leg_cost(spot_entry, near_pe, K_pe, False, entry_ts, cost_cfg)
        + _leg_cost(spot_entry, far_ce, K_ce, True, entry_ts, cost_cfg)
        + _leg_cost(spot_entry, far_pe, K_pe, False, entry_ts, cost_cfg)
    )

    margin = _calendar_margin(spot_entry, K_ce, K_pe, near_ce, near_pe, far_ce, far_pe,
                              T_near_e, T_far_e, near_iv_e, far_iv_e)

    # ── 5m path: entry → near settlement (the binding cap) ──────────────────
    walk_end = near_settle
    nce = _leg_series_mem(near_exp, K_ce, "CE", entry_ts, walk_end)
    npe = _leg_series_mem(near_exp, K_pe, "PE", entry_ts, walk_end)
    fce = _leg_series_mem(far_exp, K_ce, "CE", entry_ts, walk_end)
    fpe = _leg_series_mem(far_exp, K_pe, "PE", entry_ts, walk_end)
    if not nce[0] or not npe[0] or not fce[0] or not fpe[0]:
        return None, "no_settlement_marks"

    grid = sorted(set(nce[0]) | set(npe[0]) | set(fce[0]) | set(fpe[0]))
    grid = [t for t in grid if entry_ts <= t <= walk_end]
    if not grid:
        return None, "empty_grid"

    i_nce, i_npe, i_fce, i_fpe = [0], [0], [0], [0]
    path_rows: list[dict] = []
    max_mtm = -float("inf"); min_mtm = float("inf")
    ts_max = grid[0]; ts_min = grid[0]
    last_gross = 0.0
    last_marks = (near_ce, near_pe, far_ce, far_pe)

    for t in grid:
        m_nce = _ff_mark(nce[0], nce[1], i_nce, near_ce, t)
        m_npe = _ff_mark(npe[0], npe[1], i_npe, near_pe, t)
        m_fce = _ff_mark(fce[0], fce[1], i_fce, far_ce, t)
        m_fpe = _ff_mark(fpe[0], fpe[1], i_fpe, far_pe, t)

        # Position: SELL near (entry − mark), BUY far (mark − entry).
        gross = (
            (near_ce - m_nce) + (near_pe - m_npe)       # short near calendar legs
            + (m_fce - far_ce) + (m_fpe - far_pe)        # long far calendar legs
        ) * QTY_LOTS * CONTRACT_VALUE

        sp = _spot_at(spot_series, t)
        n_iv = _atm_iv(near_exp, t)
        f_iv = _atm_iv(far_exp, t)

        path_rows.append({
            "trade_id": 0,  # filled below once tid is known
            "ts": int(t),
            "bar_offset": int((t - entry_ts) // BAR_SEC),
            "gross_pnl_usd": float(gross),
            "near_iv": float(n_iv),
            "far_iv": float(f_iv),
            "iv_gap": float(n_iv - f_iv),
            "spot": float(sp),
        })

        if gross > max_mtm:
            max_mtm = gross; ts_max = t
        if gross < min_mtm:
            min_mtm = gross; ts_min = t
        last_gross = gross
        last_marks = (m_nce, m_npe, m_fce, m_fpe)

    tid = make_trade_id(near_exp, far_exp, date_str, time_ist, delta)
    for pr in path_rows:
        pr["trade_id"] = tid

    # ── Exit = last path bar (hold_to_settlement) ───────────────────────────
    exit_ts = int(grid[-1])
    exit_spot = _spot_at(spot_series, exit_ts)
    e_nce, e_npe, e_fce, e_fpe = last_marks
    exit_cost = (
        _leg_cost(exit_spot, e_nce, K_ce, True, exit_ts, cost_cfg)
        + _leg_cost(exit_spot, e_npe, K_pe, False, exit_ts, cost_cfg)
        + _leg_cost(exit_spot, e_fce, K_ce, True, exit_ts, cost_cfg)
        + _leg_cost(exit_spot, e_fpe, K_pe, False, exit_ts, cost_cfg)
    )
    gross_exit = last_gross
    net_pnl = gross_exit - entry_cost - exit_cost
    is_win = bool(net_pnl > 0)

    near_dte = (near_settle - entry_ts) / 86400.0
    far_dte = (far_settle - entry_ts) / 86400.0
    hour_ist = int(time_ist.split(":")[0])
    gap_bucket = obs["bucket"]
    pair = obs["pair"]

    def _pct(numer: float, denom: Optional[float]) -> float:
        return float(numer / denom * 100.0) if denom and abs(denom) > 1e-9 else float("nan")

    trade_row = {
        "trade_id": tid,
        # ── M7 grid keys (mapped) ──
        "entry_atm_iv_band": gap_bucket,             # gap bucket
        "expiry_bucket": pair,                        # pair (pre-set, not DTE-derived)
        "delta_target": float(delta),
        # Hours are aggregated ("∑ All hours") — best combo is per gap bucket × pair × Δ,
        # NOT per entry hour (user decision 2026-06-02). Use a sentinel so the grid groups
        # all hours into one cell; the real entry hour is carried in entry_hour_actual.
        "entry_hour_ist": -1,
        "entry_hour_actual": int(hour_ist),
        # ── identity / context ──
        "friday_date_ist": near_exp,                 # near-expiry = path partition key
        "session_date_ist": session_date_ist(entry_ts),
        "entry_ts_utc": int(entry_ts),
        "entry_time_label": time_ist,
        "expiry_date": near_exp,
        "dte_days": float(near_dte),
        "is_straddle": bool(delta >= 0.495),
        "quantity_lots": int(QTY_LOTS),
        "contract_size": float(CONTRACT_VALUE),
        "rule_label": "hold_to_settlement",
        # ── calendar-specific carry-through ──
        "pair": pair,
        "near_selector": obs["near_selector"],
        "far_selector": obs["far_selector"],
        "near_expiry": near_exp,
        "far_expiry": far_exp,
        "near_iv_pct": float(obs["near_iv_pct"]),
        "far_iv_pct": float(obs["far_iv_pct"]),
        "gap_pct": float(obs["gap_pct"]),
        "near_dte_days": float(near_dte),
        "far_dte_days": float(far_dte),
        # ── legs (CE/PE share strike across expiries) ──
        "call_strike": int(K_ce),
        "put_strike": int(K_pe),
        "near_call_entry_mark": float(near_ce),
        "near_put_entry_mark": float(near_pe),
        "far_call_entry_mark": float(far_ce),
        "far_put_entry_mark": float(far_pe),
        # M7-compat entry-mark aliases (near legs define the strangle identity)
        "call_entry_mark": float(near_ce),
        "put_entry_mark": float(near_pe),
        "call_entry_delta": float(near_ce_delta),
        "put_entry_delta": float(near_pe_delta),
        "far_call_entry_delta": float(far_ce_delta),
        "far_put_entry_delta": float(far_pe_delta),
        "near_atm_iv": float(near_iv_e),
        "far_atm_iv": float(far_iv_e),
        # ── entry economics ──
        "spot_at_entry": float(spot_entry),
        "net_entry_premium_usd": float(credit_usd),
        "credit_usd": float(credit_usd),
        "net_debit_usd": float(-credit_usd),
        "total_entry_cost_usd": float(entry_cost),
        "margin_used_usd_at_entry": float(margin) if margin else float("nan"),
        # entry_atm_iv (M7 expects decimal) — use the near-expiry ATM IV
        "entry_atm_iv": float(near_iv_e),
        "entry_atm_iv_pct": float(near_iv_e * 100.0),
        # ── precomputed single-rule exit (hold_to_settlement) ──
        "exit_ts": int(exit_ts),
        "exit_reason": "hold_to_settlement",
        "exit_spot": float(exit_spot),
        "total_exit_cost_usd": float(exit_cost),
        "gross_pnl_usd": float(gross_exit),
        "net_pnl_estimate_usd": float(net_pnl),
        "is_win": is_win,
        "is_fallback": False,
        "max_mtm_usd": float(max_mtm),
        "min_mtm_usd": float(min_mtm),
        "ts_at_max_mtm": int(ts_max),
        "ts_at_min_mtm": int(ts_min),
        "exit_mtm_usd": float(gross_exit),
        "pct_return_on_margin": _pct(net_pnl, margin),
        "pct_return_on_credit": _pct(net_pnl, abs(credit_usd)),
        "pct_max_mtm_on_credit": _pct(max_mtm, abs(credit_usd)),
        "pct_min_mtm_on_credit": _pct(min_mtm, abs(credit_usd)),
        # ── path metadata ──
        "n_path_rows": int(len(path_rows)),
        "path_first_ts": int(path_rows[0]["ts"]),
        "path_last_ts": int(path_rows[-1]["ts"]),
        "schema_version": 1,
    }
    return (trade_row, path_rows), "ok"


# ── Trades parquet write ────────────────────────────────────────────────────────

def _write_trades_atomic(new_rows: list[dict], out_path: str, *, append: bool) -> int:
    if not new_rows:
        raise ValueError("_write_trades_atomic: new_rows is empty")
    new_df = pd.DataFrame(new_rows)
    if append and os.path.exists(out_path):
        existing = pd.read_parquet(out_path)
        run_parts = set(new_df["friday_date_ist"].unique())
        existing = existing[~existing["friday_date_ist"].isin(run_parts)]
        merged = pd.concat([existing, new_df], ignore_index=True)
    else:
        merged = new_df
    sort_cols = [c for c in ("friday_date_ist", "far_expiry", "entry_ts_utc", "delta_target")
                 if c in merged.columns]
    if sort_cols:
        merged = merged.sort_values(sort_cols).reset_index(drop=True)
    tmp = out_path + ".tmp"
    merged.to_parquet(tmp, compression="zstd", index=False)
    os.replace(tmp, out_path)
    return len(merged)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    t0 = _time.time()
    log.info("Calendar batch backtester starting")
    effective_append = bool(args.append and not args.rebuild)
    log.info(f"  out dir       = {args.out_dir}")
    log.info(f"  target deltas = {TARGET_DELTAS}")
    log.info(f"  mode          = {'append' if effective_append else 'rebuild'}")
    log.info("─" * 60)

    trades_out = os.path.join(args.out_dir, "calendar_trades.parquet")
    if os.path.exists(trades_out) and not args.append and not args.rebuild:
        raise RuntimeError(
            f"{trades_out} already exists. Pass --append or --rebuild.")

    obs_rows = load_and_prep_scan(args.csv, args.since, args.through, args.pair)
    if args.limit:
        obs_rows = obs_rows[: args.limit]
    if not obs_rows:
        log.error("No observations after filters; aborting.")
        return

    deltas = [float(d) for d in args.deltas.split(",")] if args.deltas else list(TARGET_DELTAS)

    os.makedirs(args.out_dir, exist_ok=True)
    paths_dir = os.path.join(args.out_dir, "calendar_paths")
    os.makedirs(paths_dir, exist_ok=True)

    # Group by near_expiry → one path partition each (resume granularity).
    groups: dict[str, list[dict]] = {}
    for r in obs_rows:
        groups.setdefault(r["near_expiry"], []).append(r)
    near_exps = sorted(groups.keys())
    log.info(f"Observations: {len(obs_rows):,} across {len(near_exps)} near-expiry partitions")

    # Resume: skip partitions already present when appending.
    done_parts: set[str] = set()
    if effective_append and os.path.exists(trades_out):
        try:
            done_parts = set(pd.read_parquet(trades_out, columns=["friday_date_ist"])
                             ["friday_date_ist"].unique())
        except Exception:
            done_parts = set()

    cost_cfg = DEFAULT_COST_CFG
    conn = duckdb.connect()
    all_trades: list[dict] = []
    skip_counts: dict[str, int] = {}
    n_entered = 0

    for gi, near_exp in enumerate(near_exps, 1):
        if near_exp in done_parts:
            log.info(f"  [{gi}/{len(near_exps)}] {near_exp}: already present, skipping (append/resume)")
            continue
        grp = groups[near_exp]
        # Spot window: earliest entry → near settlement (+buffer).
        entry_min = min(ist_to_unix(r["date"], r["time_ist"]) for r in grp)
        win_start = entry_min - 600
        win_end = settlement_unix(near_exp) + 600
        # Preload spot + every needed expiry's chain into memory for this group.
        spot_series = load_spot_window(conn, win_start, win_end)
        expiries = {near_exp} | {r["far_expiry"] for r in grp}
        _reset_group(spot_series, conn, expiries, win_start, win_end)

        grp_trades: list[dict] = []
        grp_paths: list[dict] = []
        for r in grp:
            for d in deltas:
                try:
                    result, reason = build_trade(r, d, spot_series, cost_cfg)
                except Exception as e:
                    log.warning(f"  trade failed {near_exp} {r['time_ist']} d={d}: {e}")
                    skip_counts["exception"] = skip_counts.get("exception", 0) + 1
                    continue
                if result is None:
                    skip_counts[reason] = skip_counts.get(reason, 0) + 1
                    continue
                trade_row, path_rows = result
                grp_trades.append(trade_row)
                grp_paths.extend(path_rows)
                n_entered += 1

        if grp_paths:
            part_dir = os.path.join(paths_dir, f"friday_date={near_exp}")
            os.makedirs(part_dir, exist_ok=True)
            part_file = os.path.join(part_dir, "part.parquet")
            tmp = part_file + ".tmp"
            pd.DataFrame(grp_paths).to_parquet(tmp, compression="zstd", index=False)
            os.replace(tmp, part_file)

        all_trades.extend(grp_trades)
        log.info(f"  [{gi}/{len(near_exps)}] {near_exp}: {len(grp_trades)} trades, "
                 f"{len(grp_paths)} path rows")

        # Incremental snapshot every 10 partitions so partial results are queryable.
        if all_trades and (gi % 10 == 0 or gi == len(near_exps)):
            n = _write_trades_atomic(all_trades, trades_out, append=effective_append)
            log.info(f"    → calendar_trades.parquet snapshot: {n:,} rows")

    conn.close()

    if not all_trades:
        log.error("No trades produced; aborting trades-parquet write.")
        return
    n = _write_trades_atomic(all_trades, trades_out, append=effective_append)

    elapsed = _time.time() - t0
    log.info("─" * 60)
    log.info(f"Calendar done in {elapsed:.1f}s")
    log.info(f"  entered = {n_entered:,} trades → {trades_out} ({n:,} total rows)")
    log.info(f"  paths partitioned by near_expiry under: {paths_dir}")
    if skip_counts:
        log.info("  skip reasons (no silent caps):")
        for reason, c in sorted(skip_counts.items(), key=lambda x: -x[1]):
            log.info(f"    {reason:<24} {c:,}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=str, default=SCAN_CSV, help=f"Scan CSV. Default {SCAN_CSV}")
    p.add_argument("--out-dir", type=str, default=CAL_OUT_DIR)
    p.add_argument("--since", type=str, default=None, help="YYYY-MM-DD min date (inclusive).")
    p.add_argument("--through", type=str, default=None, help="YYYY-MM-DD max date (inclusive).")
    p.add_argument("--pair", type=str, default=None, help="Restrict to one pair (pilot).")
    p.add_argument("--deltas", type=str, default=None, help="CSV of deltas, e.g. 0.50 (pilot).")
    p.add_argument("--limit", type=int, default=None, help="Cap number of observations (pilot).")
    p.add_argument("--append", action="store_true", help="Merge new near-expiry partitions; resume-safe.")
    p.add_argument("--rebuild", action="store_true", help="Force overwrite. Takes precedence over --append.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run(args)
        return 0
    except Exception as e:
        log.exception(f"Calendar backtester failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

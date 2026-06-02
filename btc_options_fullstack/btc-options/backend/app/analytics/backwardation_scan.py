"""
Phase A — IV Backwardation Term-Structure Scan.

For the 5 nearest expiry selectors {current, next, next_to_next, weekly,
next_weekly} there are 10 unordered pairs. For every calendar date in the
available option history, at an intraday cadence (default every 30 min,
09:00-17:30 IST), this module:

  1. Resolves each selector to a concrete expiry date (dedup collapsed ones).
  2. Computes ATM IV per distinct expiry via `atm_iv_at` (cached per ts+expiry).
  3. For each of the 10 pairs: near = earlier-dated expiry, far = later-dated.
     gap_pct = (near_iv - far_iv) * 100. Keeps only gap_pct > 0 (backwardation).
  4. Buckets gap_pct into 5pt bands ([5,10), [10,15), ...) dynamically up to the
     max observed; gaps in (0,5) reported as "minimal".

Reads:
  /home/abhis/btc-data/data/options/expiry=YYYY-MM-DD/...   (folder scan + marks)
  /home/abhis/btc-data/data/spot/BTCUSD_1min.parquet        (spot for IV calc)

Writes:
  /home/abhis/btc-data/derived/backwardation_scan.csv          (every observation)
  /home/abhis/btc-data/derived/backwardation_scan_summary.md   (per-pair tables)

Run (dedicated container, RULE #5):
    docker compose run -d --rm --name bwscan_$(date +%s) \
      -v /home/abhis/btc-data/logs:/logs \
      backend sh -c "python -m app.analytics.backwardation_scan > /logs/bwscan.log 2>&1"
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
import sys
import time as _time
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

from app.core.greeks import implied_vol
from app.services.option_data import (
    OPTIONS_BASE_DIR,
    get_conn,
    load_spot_series,
    resolve_expiry,
)

# ── Config ────────────────────────────────────────────────────────────────────

SELECTORS = ["current", "next", "next_to_next", "weekly", "next_weekly"]
PAIRS = list(combinations(SELECTORS, 2))  # 10 unordered selector pairs

IST = timezone(timedelta(hours=5, minutes=30))

# Intraday sampling window (IST). 09:00-17:30 inclusive.
SAMPLE_START_MIN = 9 * 60          # 09:00 IST
SAMPLE_END_MIN = 17 * 60 + 30      # 17:30 IST (settlement)
DEFAULT_CADENCE_MIN = 30

# Strike band (fraction of the day's spot range) to preload per expiry. Must
# comfortably cover ATM ± a few strikes for the in-memory IV fallback walk.
SPOT_BAND_PCT = 0.08
ATM_FALLBACK = 4  # walk this many strikes each side of ATM if marks missing

DERIVED_DIR = "/home/abhis/btc-data/derived"
CSV_PATH = f"{DERIVED_DIR}/backwardation_scan.csv"
SUMMARY_PATH = f"{DERIVED_DIR}/backwardation_scan_summary.md"
CHECKPOINT_PATH = f"{DERIVED_DIR}/backwardation_scan.checkpoint.json"

# Print an ETA heartbeat at least every N days even when no obs are found.
HEARTBEAT_EVERY_DAYS = 7

CSV_COLUMNS = [
    "date", "time_ist", "pair",
    "near_selector", "near_expiry", "near_iv_pct",
    "far_selector", "far_expiry", "far_iv_pct",
    "gap_pct", "bucket",
]


# ── Helpers ─────────────────────────────────────────────────────────────────--

def detect_date_range() -> tuple[date, date]:
    """Earliest / latest `expiry=YYYY-MM-DD` folder under the options dir."""
    base = Path(OPTIONS_BASE_DIR)
    if not base.exists():
        raise SystemExit(f"Options dir not found: {OPTIONS_BASE_DIR}")
    expiries = sorted(
        d.name.split("=")[1]
        for d in base.iterdir()
        if d.is_dir() and "=" in d.name
    )
    if not expiries:
        raise SystemExit(f"No expiry folders under {OPTIONS_BASE_DIR}")
    return date.fromisoformat(expiries[0]), date.fromisoformat(expiries[-1])


def bucket_for(gap_pct: float) -> str:
    """5pt band label. (0,5) -> 'minimal'; else '[lo,hi)'."""
    if gap_pct < 5.0:
        return "minimal"
    lo = int(gap_pct // 5) * 5
    return f"[{lo},{lo + 5})"


def ist_timestamp(d: date, minute_of_day: int) -> int:
    """UNIX seconds (UTC) for IST date `d` at `minute_of_day` minutes past 00:00 IST."""
    dt = datetime(d.year, d.month, d.day,
                  minute_of_day // 60, minute_of_day % 60, tzinfo=IST)
    return int(dt.timestamp())


def hhmm(minute_of_day: int) -> str:
    return f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"


def _run_signature(start: date, end: date, cadence: int, window: tuple) -> dict:
    return {"start": start.isoformat(), "end": end.isoformat(),
            "cadence": cadence, "window": list(window)}


def save_checkpoint(start: date, end: date, cadence: int, window: tuple,
                    last_done: date, n_obs: int) -> None:
    """Atomically record the last fully-completed date so a killed run resumes."""
    payload = {**_run_signature(start, end, cadence, window),
               "last_done_date": last_done.isoformat(), "n_obs": n_obs}
    tmp = CHECKPOINT_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh)
    os.replace(tmp, CHECKPOINT_PATH)


def load_checkpoint(start: date, end: date, cadence: int, window: tuple) -> date | None:
    """Return the last-done date IF a checkpoint matches this run's signature
    and the CSV still exists; else None (fresh run)."""
    if not (os.path.exists(CHECKPOINT_PATH) and os.path.exists(CSV_PATH)):
        return None
    try:
        with open(CHECKPOINT_PATH) as fh:
            cp = json.load(fh)
    except Exception:
        return None
    if {k: cp.get(k) for k in ("start", "end", "cadence", "window")} != \
            _run_signature(start, end, cadence, window):
        return None  # different parameters/window — don't resume into a mismatched CSV
    try:
        return date.fromisoformat(cp["last_done_date"])
    except Exception:
        return None


def rebuild_aggregates() -> tuple[dict, dict, int]:
    """Reconstruct per-pair bucket aggregates + all-time peaks from the existing
    CSV so a resumed run produces a complete summary."""
    agg: dict[str, dict[str, dict]] = {f"{a}-{b}": {} for a, b in PAIRS}
    pair_alltime: dict[str, dict | None] = {f"{a}-{b}": None for a, b in PAIRS}
    n_obs = 0
    with open(CSV_PATH, newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                gap = float(row["gap_pct"])
            except (TypeError, ValueError, KeyError):
                continue
            row["gap_pct"] = gap
            row["near_iv_pct"] = float(row["near_iv_pct"])
            row["far_iv_pct"] = float(row["far_iv_pct"])
            pl = row["pair"]
            if pl not in agg:
                continue
            n_obs += 1
            cell = agg[pl].setdefault(row["bucket"], {"count": 0, "peak": None})
            cell["count"] += 1
            if cell["peak"] is None or gap > cell["peak"]["gap_pct"]:
                cell["peak"] = row
            if pair_alltime[pl] is None or gap > pair_alltime[pl]["gap_pct"]:
                pair_alltime[pl] = row
    return agg, pair_alltime, n_obs


def _fmt_eta(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


# ── Batch chain reads (hive-pruned, one pass per expiry per day) ──────────────

def _time_to_expiry_years(expiry: str, ts: int) -> float:
    """Years to 12:00 UTC settlement on expiry date. Mirrors atm_iv_at."""
    exp_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=12)
    now_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return max(0.0001, (exp_dt - now_dt).total_seconds() / (365 * 24 * 3600))


def load_expiry_day_marks(
    expiry: str, strike_lo: float, strike_hi: float, t_start: int, t_end: int,
) -> dict[str, dict[int, list]]:
    """One DuckDB query per option type for the whole day, limited to strikes in
    [lo, hi] via hive partition pruning (out-of-band strike files are never
    opened). Returns {'CE': {strike: [(ts, mark), ...asc]}, 'PE': {...}}.
    """
    conn = get_conn()
    out: dict[str, dict[int, list]] = {"CE": {}, "PE": {}}
    for opt in ("CE", "PE"):
        glob = f"{OPTIONS_BASE_DIR}/expiry={expiry}/strike=*/{opt}.parquet"
        sql = (
            f"SELECT strike, timestamp_unix, mark_close "
            f"FROM read_parquet('{glob}', hive_partitioning=true) "
            f"WHERE strike BETWEEN {int(strike_lo)} AND {int(strike_hi)} "
            f"  AND timestamp_unix BETWEEN {int(t_start)} AND {int(t_end)} "
            f"  AND mark_close > 0 "
            f"ORDER BY strike, timestamp_unix"
        )
        try:
            rows = conn.execute(sql).fetchall()
        except Exception:
            rows = []
        for strike, ts, mark in rows:
            if mark is None or ts is None:
                continue
            out[opt].setdefault(int(strike), []).append((int(ts), float(mark)))
    return out


def _mark_at_or_before(series: list, ts: int) -> float:
    """Latest mark at or before ts from an ascending (ts, mark) list (0 if none)."""
    lo, hi, found = 0, len(series) - 1, 0.0
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= ts:
            found = series[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return found


def atm_iv_from_day_marks(
    day_marks: dict[str, dict[int, list]], spot: float, ts: int, expiry: str,
) -> float:
    """ATM IV (decimal) at ts from preloaded day marks. Mirrors atm_iv_at's
    ATM-nearest + ±N fallback walk, but uses at-or-before marks held in memory."""
    if spot <= 0:
        return 0.0
    strikes = sorted(set(day_marks["CE"]) | set(day_marks["PE"]))
    if not strikes:
        return 0.0
    T = _time_to_expiry_years(expiry, ts)
    atm = min(strikes, key=lambda k: abs(k - spot))
    atm_idx = strikes.index(atm)
    candidates = [atm]
    for d in range(1, ATM_FALLBACK + 1):
        if atm_idx - d >= 0:
            candidates.append(strikes[atm_idx - d])
        if atm_idx + d < len(strikes):
            candidates.append(strikes[atm_idx + d])
    for K in candidates:
        ce = _mark_at_or_before(day_marks["CE"].get(K, []), ts)
        pe = _mark_at_or_before(day_marks["PE"].get(K, []), ts)
        ivs = []
        if ce > 0:
            v = implied_vol(ce, spot, K, T, 0.0, "call")
            if v and v > 0:
                ivs.append(v)
        if pe > 0:
            v = implied_vol(pe, spot, K, T, 0.0, "put")
            if v and v > 0:
                ivs.append(v)
        if ivs:
            return sum(ivs) / len(ivs)
    return 0.0


# ── Scan ────────────────────────────────────────────────────────────────────--

def run(args: argparse.Namespace) -> None:
    t0 = _time.time()
    if args.tag:
        global CSV_PATH, SUMMARY_PATH, CHECKPOINT_PATH
        CSV_PATH = f"{DERIVED_DIR}/backwardation_scan_{args.tag}.csv"
        SUMMARY_PATH = f"{DERIVED_DIR}/backwardation_scan_summary_{args.tag}.md"
        CHECKPOINT_PATH = f"{DERIVED_DIR}/backwardation_scan_{args.tag}.checkpoint.json"
    min_d, max_d = detect_date_range()
    start = date.fromisoformat(args.start) if args.start else min_d
    end = date.fromisoformat(args.end) if args.end else max_d
    if args.max_days:
        end = min(end, start + timedelta(days=args.max_days - 1))

    cadence = args.cadence_min
    win_lo = args.start_hour * 60
    win_hi = args.end_hour * 60
    window = (win_lo, win_hi)
    minutes = list(range(win_lo, win_hi + 1, cadence))

    os.makedirs(DERIVED_DIR, exist_ok=True)

    # Resume support: if a matching checkpoint + CSV exist, continue from the
    # day after the last completed date and rebuild aggregates from the CSV.
    resume_after = None if args.no_resume else load_checkpoint(start, end, cadence, window)
    total_days = (end - start).days + 1

    print(f"[bwscan] options data range: {min_d} -> {max_d}", flush=True)
    print(f"[bwscan] scanning {start} -> {end}  ({total_days} days) "
          f"@ {cadence}-min cadence, {args.start_hour:02d}:00-{args.end_hour:02d}:00 IST "
          f"({len(minutes)} samples/day)", flush=True)
    print(f"[bwscan] pairs: {', '.join('-'.join(p) for p in PAIRS)}", flush=True)

    if resume_after is not None:
        agg, pair_alltime, n_obs = rebuild_aggregates()
        scan_from = resume_after + timedelta(days=1)
        csv_mode = "a"
        print(f"[bwscan] RESUMING after {resume_after} "
              f"({n_obs} obs already in CSV) -> starting {scan_from}", flush=True)
    else:
        agg = {f"{a}-{b}": {} for a, b in PAIRS}
        pair_alltime = {f"{a}-{b}": None for a, b in PAIRS}
        n_obs = 0
        scan_from = start
        csv_mode = "w"

    n_samples = 0
    days_done = 0  # this run (for ETA)

    with open(CSV_PATH, csv_mode, newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if csv_mode == "w":
            writer.writeheader()

        d = scan_from
        while d <= end:
            date_iso = d.isoformat()
            day_obs = 0

            # Preload the day's spot once (single warm parquet); small lookback so
            # an early sample still has an at-or-before bar.
            day_start = ist_timestamp(d, win_lo)
            day_end = ist_timestamp(d, win_hi)
            spot_series = load_spot_series(day_start - 7200, day_end)

            if spot_series:
                spot_ts = [r["time"] for r in spot_series]
                spot_px = [r["close"] for r in spot_series]

                def spot_at(ts: int) -> float:
                    i = bisect.bisect_right(spot_ts, ts) - 1
                    return spot_px[i] if i >= 0 else 0.0

                # Resolve selectors per sample time; collect the day's distinct expiries.
                sample_resolved: list[tuple[int, str, dict]] = []
                day_expiries: set[str] = set()
                for mod in minutes:
                    ts = ist_timestamp(d, mod)
                    if spot_at(ts) <= 0:
                        continue
                    time_ist = hhmm(mod)
                    resolved: dict[str, str] = {}
                    for sel in SELECTORS:
                        exp = resolve_expiry(date_iso, time_ist, sel)
                        if exp:
                            resolved[sel] = exp
                    sample_resolved.append((ts, time_ist, resolved))
                    day_expiries.update(resolved.values())

                # Strike band from the day's spot range; preload each expiry's day
                # marks in one hive-pruned query per option type (out-of-band and
                # other-day strike files are never opened).
                band_lo = min(spot_px) * (1 - SPOT_BAND_PCT)
                band_hi = max(spot_px) * (1 + SPOT_BAND_PCT)
                expiry_marks = {
                    exp: load_expiry_day_marks(exp, band_lo, band_hi, day_start, day_end)
                    for exp in day_expiries
                }
                empty = {"CE": {}, "PE": {}}

                for ts, time_ist, resolved in sample_resolved:
                    n_samples += 1
                    spot = spot_at(ts)

                    iv_cache: dict[str, float] = {}
                    for exp in set(resolved.values()):
                        iv_cache[exp] = atm_iv_from_day_marks(
                            expiry_marks.get(exp, empty), spot, ts, exp)

                    for a, b in PAIRS:
                        ea, eb = resolved.get(a), resolved.get(b)
                        if not ea or not eb or ea == eb:
                            continue  # missing or collapsed to same expiry
                        # near = earlier-dated expiry, far = later-dated.
                        if ea < eb:
                            near_sel, near_exp, far_sel, far_exp = a, ea, b, eb
                        else:
                            near_sel, near_exp, far_sel, far_exp = b, eb, a, ea
                        near_iv = iv_cache.get(near_exp, 0.0)
                        far_iv = iv_cache.get(far_exp, 0.0)
                        if near_iv <= 0 or far_iv <= 0:
                            continue
                        gap_pct = (near_iv - far_iv) * 100.0
                        if gap_pct <= 0:
                            continue  # contango / flat — only keep backwardation

                        bucket = bucket_for(gap_pct)
                        pair_label = f"{a}-{b}"
                        row = {
                            "date": date_iso, "time_ist": time_ist, "pair": pair_label,
                            "near_selector": near_sel, "near_expiry": near_exp,
                            "near_iv_pct": round(near_iv * 100, 4),
                            "far_selector": far_sel, "far_expiry": far_exp,
                            "far_iv_pct": round(far_iv * 100, 4),
                            "gap_pct": round(gap_pct, 4), "bucket": bucket,
                        }
                        writer.writerow(row)
                        n_obs += 1
                        day_obs += 1

                        cell = agg[pair_label].setdefault(bucket, {"count": 0, "peak": None})
                        cell["count"] += 1
                        if cell["peak"] is None or gap_pct > cell["peak"]["gap_pct"]:
                            cell["peak"] = row
                        if (pair_alltime[pair_label] is None
                                or gap_pct > pair_alltime[pair_label]["gap_pct"]):
                            pair_alltime[pair_label] = row

            fh.flush()
            os.fsync(fh.fileno())
            save_checkpoint(start, end, cadence, window, d, n_obs)
            days_done += 1

            remaining = (end - d).days
            rate = (_time.time() - t0) / days_done  # sec per day, this run
            eta = _fmt_eta(rate * remaining)
            if day_obs or days_done % HEARTBEAT_EVERY_DAYS == 0 or d == end:
                print(f"[bwscan] {date_iso}: {day_obs} obs "
                      f"(total {n_obs}, {n_samples} samples) | "
                      f"{remaining} days left · {rate:.1f}s/day · ETA {eta}",
                      flush=True)
            d += timedelta(days=1)

    elapsed = _time.time() - t0
    print(f"[bwscan] DONE: {n_obs} backwardation observations from "
          f"{n_samples} samples in {elapsed:.0f}s -> {CSV_PATH}", flush=True)

    write_summary(agg, pair_alltime, start, end, cadence, n_obs, n_samples)


def _bucket_sort_key(b: str) -> tuple[int, int]:
    if b == "minimal":
        return (0, 0)
    return (1, int(b.split(",")[0].lstrip("[")))


def write_summary(agg, pair_alltime, start, end, cadence, n_obs, n_samples) -> None:
    lines: list[str] = []
    lines.append("# Backwardation Term-Structure Scan — Phase A Summary\n")
    lines.append(f"- Date range: **{start} → {end}**")
    lines.append(f"- Cadence: **{cadence} min** (09:00–17:30 IST)")
    lines.append(f"- Total samples: **{n_samples:,}**  ·  "
                 f"backwardation observations: **{n_obs:,}**")
    lines.append(f"- near = earlier-dated expiry, far = later-dated; "
                 f"gap_pct = (near_iv − far_iv) × 100\n")

    for a, b in PAIRS:
        pair_label = f"{a}-{b}"
        buckets = agg[pair_label]
        total = sum(c["count"] for c in buckets.values())
        lines.append(f"\n## {a} – {b}  (n={total})")
        if not buckets:
            lines.append("_No backwardation observed for this pair._")
            continue
        lines.append("")
        lines.append("| bucket (gap pp) | count | peak gap | peak date/time | "
                     "near→far (IV%) |")
        lines.append("|---|---:|---:|---|---|")
        for bk in sorted(buckets, key=_bucket_sort_key):
            c = buckets[bk]
            p = c["peak"]
            lines.append(
                f"| {bk} | {c['count']} | {p['gap_pct']:.2f} | "
                f"{p['date']} {p['time_ist']} | "
                f"{p['near_expiry']} {p['near_iv_pct']:.1f} → "
                f"{p['far_expiry']} {p['far_iv_pct']:.1f} |"
            )
        at = pair_alltime[pair_label]
        if at:
            lines.append(
                f"\n**All-time peak:** gap **{at['gap_pct']:.2f} pp** on "
                f"{at['date']} {at['time_ist']} — "
                f"{at['near_selector']}({at['near_expiry']}) "
                f"IV {at['near_iv_pct']:.1f}% vs "
                f"{at['far_selector']}({at['far_expiry']}) "
                f"IV {at['far_iv_pct']:.1f}%"
            )

    text = "\n".join(lines) + "\n"
    with open(SUMMARY_PATH, "w") as fh:
        fh.write(text)
    print("\n" + text, flush=True)
    print(f"[bwscan] summary -> {SUMMARY_PATH}", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="IV backwardation term-structure scan")
    p.add_argument("--start", default=None, help="YYYY-MM-DD (default: earliest data)")
    p.add_argument("--end", default=None, help="YYYY-MM-DD (default: latest data)")
    p.add_argument("--cadence-min", type=int, default=DEFAULT_CADENCE_MIN,
                   dest="cadence_min", help="intraday sample spacing in minutes")
    p.add_argument("--start-hour", type=int, default=9, dest="start_hour",
                   help="first intraday sample hour, IST (default 9 = 09:00)")
    p.add_argument("--end-hour", type=int, default=23, dest="end_hour",
                   help="last intraday sample hour, IST (default 23 = 23:00, full day)")
    p.add_argument("--max-days", type=int, default=0, dest="max_days",
                   help="cap number of days from start (0 = no cap; for testing)")
    p.add_argument("--no-resume", action="store_true", dest="no_resume",
                   help="ignore any checkpoint and restart from scratch")
    p.add_argument("--tag", default="", dest="tag",
                   help="output filename suffix (keeps separate CSV/summary/checkpoint)")
    return p.parse_args()


def main() -> int:
    run(parse_args())
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Nightly merge job — consolidates /home/abhis/btc-data/data_live/ → /home/abhis/btc-data/data/.

The live WS recorder writes 1-min bars to data_live/. The historical
collector (btc-collector/) writes batched fetches to data/. This job
periodically folds data_live/ into data/, then archives the raw live writes
under data_live/archive/<YYYY-MM-DD>/ for 7-day rollback safety.

Runs as:
  - Backend startup hook (kicks off if last_merge > 20h ago)
  - Self-scheduled background loop (every 1h check)
  - Manual:    docker compose exec backend python -m app.services.merge_live_to_main
  - Dry-run:   ... --dry-run
  - Status:    ... --status
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

log = logging.getLogger(__name__)

DATA_LIVE  = Path("/home/abhis/btc-data/data_live")
DATA_MAIN  = Path("/home/abhis/btc-data/data")
ARCHIVE    = DATA_LIVE / "archive"
STATE_FILE = Path("/home/abhis/btc-data/logs/merge_live_state.json")

ARCHIVE_RETENTION_DAYS = 7
MIN_INTERVAL_HOURS     = 20    # only kick off if last merge older than this
SCHEDULE_CHECK_S       = 3600  # check once per hour

_PARQUET_KW = dict(compression="zstd", compression_level=3, row_group_size=10_000,
                   write_statistics=True, use_dictionary=True)


def _read_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _write_state(d: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(STATE_FILE) + ".tmp"
    Path(tmp).write_text(json.dumps(d, indent=2, default=str))
    os.replace(tmp, STATE_FILE)


def _atomic_write(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    pq.write_table(table, tmp, **_PARQUET_KW)
    os.replace(tmp, path)


def _dedupe_sort(table: pa.Table) -> pa.Table:
    ts = table.column("timestamp_unix").to_pylist()
    keep, seen = [], set()
    for i in range(len(ts) - 1, -1, -1):
        if ts[i] not in seen:
            seen.add(ts[i]); keep.append(i)
    keep.sort()
    deduped = table.take(keep)
    sort_idx = pc.sort_indices(deduped, sort_keys=[("timestamp_unix", "ascending")])
    return deduped.take(sort_idx)


def _walk_live_parquets() -> list[Path]:
    if not DATA_LIVE.exists():
        return []
    out: list[Path] = []
    for p in DATA_LIVE.rglob("*.parquet"):
        # Skip archive subtree.
        try:
            p.relative_to(ARCHIVE)
            continue
        except ValueError:
            pass
        out.append(p)
    return out


def _archive_dest(live_path: Path, today_str: str) -> Path:
    rel = live_path.relative_to(DATA_LIVE)
    return ARCHIVE / today_str / rel


def _prune_archive() -> int:
    """Remove archive subdirs older than ARCHIVE_RETENTION_DAYS. Returns count removed."""
    if not ARCHIVE.exists():
        return 0
    cutoff = datetime.now().date() - timedelta(days=ARCHIVE_RETENTION_DAYS)
    removed = 0
    for d in ARCHIVE.iterdir():
        if not d.is_dir():
            continue
        try:
            day = datetime.strptime(d.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def run_merge(dry_run: bool = False) -> dict:
    """
    Walk data_live/, merge each parquet into data/, archive the live file.
    Returns stats dict.
    """
    start = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    live_files = _walk_live_parquets()
    stats = {
        "started_at":      datetime.now().isoformat(timespec="seconds"),
        "dry_run":         dry_run,
        "files_seen":      len(live_files),
        "files_merged":    0,
        "files_created":   0,
        "rows_appended":   0,
        "rows_deduped":    0,
        "archive_pruned":  0,
        "errors":          0,
        "duration_s":      0.0,
    }

    for live in live_files:
        rel = live.relative_to(DATA_LIVE)
        main = DATA_MAIN / rel
        try:
            # ParquetFile.read() bypasses dataset auto-detection that would
            # otherwise inject phantom hive-partition columns from the
            # `expiry=.../strike=...` directory names.
            new = pq.ParquetFile(str(live)).read()
            if "timestamp_unix" not in new.schema.names:
                log.warning("skip %s: missing timestamp_unix column", live)
                continue
            if main.exists():
                existing = pq.ParquetFile(str(main)).read()
                # Schema must match — if not, log + skip rather than corrupt main.
                if existing.schema != new.schema:
                    log.error("schema mismatch %s — skipping merge", main)
                    stats["errors"] += 1
                    continue
                combined = pa.concat_tables([existing, new])
                pre = len(combined)
                merged = _dedupe_sort(combined)
                stats["rows_appended"] += len(merged) - len(existing)
                stats["rows_deduped"]  += pre - len(merged)
                if not dry_run:
                    _atomic_write(merged, main)
                stats["files_merged"] += 1
            else:
                if not dry_run:
                    main.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write(_dedupe_sort(new), main)
                stats["files_created"] += 1
                stats["rows_appended"] += len(new)

            # Archive live file then remove it from data_live/ root tree.
            if not dry_run:
                arch = _archive_dest(live, today)
                arch.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(live), str(arch))
        except Exception as e:
            log.exception("merge failed for %s: %s", live, e)
            stats["errors"] += 1

    if not dry_run:
        stats["archive_pruned"] = _prune_archive()
        st = _read_state()
        st["last_run_at"]    = datetime.now().isoformat(timespec="seconds")
        st["last_run_stats"] = stats
        _write_state(st)

    stats["duration_s"] = round(time.time() - start, 2)
    log.info("merge: files=%d merged=%d created=%d rows+=%d dedup=%d err=%d in %.1fs",
             stats["files_seen"], stats["files_merged"], stats["files_created"],
             stats["rows_appended"], stats["rows_deduped"],
             stats["errors"], stats["duration_s"])
    return stats


def hours_since_last_merge() -> float | None:
    st = _read_state()
    last = st.get("last_run_at")
    if not last:
        return None
    try:
        ts = datetime.fromisoformat(last)
        return (datetime.now() - ts).total_seconds() / 3600.0
    except Exception:
        return None


# ── Backend-side scheduler (option B + C combo) ──────────────────────────────

async def schedule_loop(min_interval_hours: float = MIN_INTERVAL_HOURS):
    """
    Background coroutine: every SCHEDULE_CHECK_S seconds, check if last merge
    is older than min_interval_hours. If yes, run it (in a thread to avoid
    blocking the event loop).
    """
    while True:
        try:
            h = hours_since_last_merge()
            if h is None or h >= min_interval_hours:
                log.info("merge: scheduler kicking off (hours_since_last=%s)", h)
                await asyncio.to_thread(run_merge, False)
        except Exception as e:
            log.exception("merge scheduler error: %s", e)
        try:
            await asyncio.sleep(SCHEDULE_CHECK_S)
        except asyncio.CancelledError:
            break


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="don't write anything")
    ap.add_argument("--status",  action="store_true", help="print last-run state and exit")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )
    if args.status:
        st = _read_state()
        if not st:
            print("no state file (merge has never run)")
            sys.exit(0)
        print(json.dumps(st, indent=2, default=str))
        h = hours_since_last_merge()
        if h is not None:
            print(f"\nhours_since_last_merge: {h:.2f}")
        sys.exit(0)
    run_merge(dry_run=args.dry_run)


if __name__ == "__main__":
    _cli()

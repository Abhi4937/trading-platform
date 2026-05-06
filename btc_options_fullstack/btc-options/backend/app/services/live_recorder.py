"""
Live WS recorder — captures 1-min OHLC + OI candles for all live BTC option
expiries (ATM ± 40 strikes per expiry, plus spot) and persists to
/home/abhis/btc-data/data_live/.

Architecture:
  Discovery  →  WS subscribe (MARK: + OI: per option, MARK: + OI: for spot)
              ↘ candlestick_1m channel
                 ↘ in-memory current-bar map per symbol
                    ↘ on bar close → push to write queue
                       ↘ writer flushes every 30s → data_live/ parquet append-dedupe

Coexists with btc-collector/ (which writes to ~/btc-data/data/) — no path
conflict. A separate nightly merge job consolidates data_live/ → data/.

Toggle via env BTC_LIVE_RECORDER=0 to disable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import websockets

from app.services.delta_client import get_delta_client
from app.services import ticker_store

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

WS_URL = "wss://socket.india.delta.exchange"

DATA_LIVE_BASE   = Path("/home/abhis/btc-data/data_live")
SPOT_PARQUET     = DATA_LIVE_BASE / "spot" / "BTCUSD_1min.parquet"
OPTIONS_BASE     = DATA_LIVE_BASE / "options"

HALF_WIDTH       = 40         # ATM ± 40 strikes per expiry (matches btc-collector)
STRIKE_INTERVAL  = 100        # $100 grid
SPOT_TRIGGER_USD = 2000       # |Δspot| > $2k → immediate re-discovery
HEARTBEAT_DISC_S = 300        # heartbeat re-discovery every 5 min
NEW_EXPIRY_S     = 3600       # full product refresh every 1h
FLUSH_PERIOD_S   = 30         # writer flush cadence
SUB_BATCH        = 100        # symbols per subscribe message

# Schemas (must match btc-collector/parquet_writer.py exactly so the nightly
# merge can pa.concat_tables without column-order surprises).
SPOT_SCHEMA = pa.schema([
    pa.field("timestamp_ist",  pa.timestamp("s"),  nullable=False),
    pa.field("timestamp_unix", pa.int64(),          nullable=False),
    pa.field("mark_open",      pa.float64()),
    pa.field("mark_high",      pa.float64()),
    pa.field("mark_low",       pa.float64()),
    pa.field("mark_close",     pa.float64()),
    pa.field("ltp_volume",     pa.float64()),
    pa.field("oi_open",        pa.float64()),
    pa.field("oi_high",        pa.float64()),
    pa.field("oi_low",         pa.float64()),
    pa.field("oi_close",       pa.float64()),
])
OPTIONS_SCHEMA = pa.schema([
    pa.field("timestamp_ist",  pa.timestamp("s"),  nullable=False),
    pa.field("timestamp_unix", pa.int64(),          nullable=False),
    pa.field("mark_open",      pa.float64()),
    pa.field("mark_high",      pa.float64()),
    pa.field("mark_low",       pa.float64()),
    pa.field("mark_close",     pa.float64()),
    pa.field("oi_open",        pa.float64()),
    pa.field("oi_high",        pa.float64()),
    pa.field("oi_low",         pa.float64()),
    pa.field("oi_close",       pa.float64()),
])

_PARQUET_KW = dict(compression="zstd", compression_level=3, row_group_size=10_000,
                   write_statistics=True, use_dictionary=True)

# ── Symbol parsing ────────────────────────────────────────────────────────────

_OPT_RE = re.compile(r"^(MARK|OI):(C|P)-BTC-(\d+)-(\d{6})$")
_SPOT_RE = re.compile(r"^(MARK|OI):BTCUSD$")


def parse_option_symbol(symbol: str) -> dict | None:
    """MARK:C-BTC-100000-080525 → {kind:'mark', side:'CE', strike:100000, expiry:'2025-05-08'}."""
    m = _OPT_RE.match(symbol)
    if not m:
        return None
    kind_pfx, side_char, strike_str, ddmmyy = m.groups()
    return {
        "kind":   "mark" if kind_pfx == "MARK" else "oi",
        "side":   "CE" if side_char == "C" else "PE",
        "strike": int(strike_str),
        "expiry": f"20{ddmmyy[4:6]}-{ddmmyy[2:4]}-{ddmmyy[0:2]}",
    }


def parse_spot_symbol(symbol: str) -> str | None:
    """MARK:BTCUSD → 'mark', OI:BTCUSD → 'oi'."""
    m = _SPOT_RE.match(symbol)
    return ("mark" if m.group(1) == "MARK" else "oi") if m else None


def round_atm(spot: float) -> int:
    return int(round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL)


def _ist_naive(unix_seconds: int) -> datetime:
    """Naive IST datetime — matches btc-collector schema."""
    return datetime.fromtimestamp(unix_seconds + 5 * 3600 + 30 * 60, tz=timezone.utc).replace(tzinfo=None)


# ── Discovery ─────────────────────────────────────────────────────────────────

async def discover_target_symbols(spot: float) -> tuple[set[str], int]:
    """
    Returns (target_symbols, atm_used).

    target_symbols includes both `MARK:` and `OI:` variants for every option
    in ATM ± HALF_WIDTH across all live BTC expiries, plus spot.
    """
    client = get_delta_client()
    products = await client.get_btc_option_products(max_pages=20)
    atm = round_atm(spot)
    band = {atm + STRIKE_INTERVAL * k for k in range(-HALF_WIDTH, HALF_WIDTH + 1)}

    out: set[str] = {"MARK:BTCUSD", "OI:BTCUSD"}
    for p in products:
        sym = p.get("symbol", "")
        m = re.match(r"^(C|P)-BTC-(\d+)-(\d{6})$", sym)
        if not m:
            continue
        strike = int(m.group(2))
        if strike in band:
            out.add(f"MARK:{sym}")
            out.add(f"OI:{sym}")

    log.info("discovery: ATM=%d band=%d strikes %d products → %d subs",
             atm, len(band), len(products), len(out))
    return out, atm


# ── Writer ────────────────────────────────────────────────────────────────────

class _BarKey:
    """In-flight current bar identity; closes when a newer minute arrives."""
    __slots__ = ("symbol", "minute_unix")

    def __init__(self, symbol: str, minute_unix: int):
        self.symbol = symbol
        self.minute_unix = minute_unix


class LiveWriter:
    """
    Buffers closed bars in memory, flushes to data_live/ parquets every
    FLUSH_PERIOD_S seconds. Append + dedupe by timestamp_unix on each flush.
    """

    def __init__(self):
        # Pending bars keyed by output file path → list of bar dicts to merge.
        self._spot_buf: dict[str, dict[int, dict]] = defaultdict(dict)   # ts → row
        self._opt_buf:  dict[str, dict[int, dict]] = defaultdict(dict)   # path → ts→row
        self._opt_meta: dict[str, dict] = {}                              # path → {expiry, strike, side}
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._writes_total = 0
        self._last_flush_at: float | None = None

    def queue_spot_bar(self, kind: str, ts: int, o: float, h: float, l: float, c: float, v: float) -> None:
        path = str(SPOT_PARQUET)
        row = self._spot_buf[path].setdefault(ts, {"timestamp_unix": ts})
        if kind == "mark":
            row["mark_open"], row["mark_high"], row["mark_low"], row["mark_close"] = o, h, l, c
            row["ltp_volume"] = row.get("ltp_volume") or v  # spot mark may not carry volume; keep first non-null
        else:  # oi
            row["oi_open"], row["oi_high"], row["oi_low"], row["oi_close"] = o, h, l, c

    def queue_option_bar(self, expiry: str, strike: int, side: str, kind: str,
                         ts: int, o: float, h: float, l: float, c: float) -> None:
        path = str(OPTIONS_BASE / f"expiry={expiry}" / f"strike={strike}" / f"{side}.parquet")
        self._opt_meta.setdefault(path, {"expiry": expiry, "strike": strike, "side": side})
        row = self._opt_buf[path].setdefault(ts, {"timestamp_unix": ts})
        if kind == "mark":
            row["mark_open"], row["mark_high"], row["mark_low"], row["mark_close"] = o, h, l, c
        else:
            row["oi_open"], row["oi_high"], row["oi_low"], row["oi_close"] = o, h, l, c

    async def flush_loop(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=FLUSH_PERIOD_S)
                break
            except asyncio.TimeoutError:
                pass
            try:
                await self._flush_once()
            except Exception as e:
                log.exception("flush_once failed: %s", e)
        # Final flush on stop
        try:
            await self._flush_once()
        except Exception:
            pass

    async def _flush_once(self):
        async with self._lock:
            spot_snap = {k: v for k, v in self._spot_buf.items() if v}
            opt_snap = {k: v for k, v in self._opt_buf.items() if v}
            self._spot_buf.clear()
            self._opt_buf.clear()

        if not spot_snap and not opt_snap:
            return

        # Run blocking parquet IO in a thread to avoid stalling the event loop.
        await asyncio.to_thread(self._do_flush, spot_snap, opt_snap)
        self._last_flush_at = time.time()

    def _do_flush(self, spot_snap: dict, opt_snap: dict):
        for path, ts_to_row in spot_snap.items():
            self._append_parquet(path, list(ts_to_row.values()), SPOT_SCHEMA)
        for path, ts_to_row in opt_snap.items():
            self._append_parquet(path, list(ts_to_row.values()), OPTIONS_SCHEMA)
        self._writes_total += len(spot_snap) + len(opt_snap)

    def _append_parquet(self, path: str, rows: list[dict], schema: pa.Schema):
        if not rows:
            return
        # Build new table from rows, filling missing fields with None.
        cols = {f.name: [] for f in schema}
        for r in rows:
            ts = r.get("timestamp_unix")
            cols["timestamp_ist"].append(_ist_naive(ts))
            cols["timestamp_unix"].append(ts)
            for f in schema:
                if f.name in ("timestamp_ist", "timestamp_unix"):
                    continue
                cols[f.name].append(r.get(f.name))
        new = pa.table(cols, schema=schema)

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if os.path.exists(path):
            try:
                # ParquetFile.read() bypasses dataset auto-detection of hive
                # partition columns from the parent dir names (which would inject
                # phantom `expiry`/`strike` columns into the schema).
                existing = pq.ParquetFile(path).read()
                combined = pa.concat_tables([existing, new])
            except Exception as e:
                log.warning("read existing failed %s: %s — overwriting", path, e)
                combined = new
        else:
            combined = new

        # Dedupe on timestamp_unix (keep last).
        ts_list = combined.column("timestamp_unix").to_pylist()
        keep_idx, seen = [], set()
        for i in range(len(ts_list) - 1, -1, -1):
            t = ts_list[i]
            if t not in seen:
                seen.add(t)
                keep_idx.append(i)
        keep_idx.sort()
        deduped = combined.take(keep_idx)
        sort_idx = pc.sort_indices(deduped, sort_keys=[("timestamp_unix", "ascending")])
        deduped = deduped.take(sort_idx)

        tmp = path + ".tmp"
        pq.write_table(deduped, tmp, **_PARQUET_KW)
        os.replace(tmp, path)

    def stats(self) -> dict:
        return {
            "writes_total":    self._writes_total,
            "last_flush_at":   self._last_flush_at,
            "spot_pending":    sum(len(v) for v in self._spot_buf.values()),
            "options_pending": sum(len(v) for v in self._opt_buf.values()),
        }

    async def stop(self):
        self._stop.set()


# ── WS recorder ───────────────────────────────────────────────────────────────

class LiveRecorder:
    def __init__(self):
        self.writer = LiveWriter()
        self._stop = asyncio.Event()
        self._current_subs: set[str] = set()
        self._last_atm: int | None = None
        # Per-symbol in-flight bar: {(symbol, minute_unix): {o,h,l,c,v}}
        self._inflight: dict[str, tuple[int, dict]] = {}
        self._msgs_total = 0
        self._bars_closed = 0
        # Per-prefix message counters, used to surface the known-missing OI
        # candle stream. Delta's `candlestick_1m` channel is documented as a
        # price stream — OI subscriptions are accepted but no candles fire,
        # so all `oi_*` columns end up NaN. To populate OI properly the
        # recorder needs a separate `v2/ticker` subscription that buckets
        # `oi_contracts` updates into 1m bars. Tracked as cleanup item C1.
        self._mark_msgs = 0
        self._oi_msgs = 0

    async def stop(self):
        self._stop.set()
        await self.writer.stop()

    def stats(self) -> dict:
        return {
            "current_subs": len(self._current_subs),
            "last_atm":     self._last_atm,
            "msgs_total":   self._msgs_total,
            "bars_closed":  self._bars_closed,
            "mark_msgs":    self._mark_msgs,
            "oi_msgs":      self._oi_msgs,
            **self.writer.stats(),
        }

    async def run(self):
        flush_task = asyncio.create_task(self.writer.flush_loop())
        try:
            while not self._stop.is_set():
                try:
                    await self._connect_and_run()
                except Exception as e:
                    log.error("recorder connect failed: %s — retry 5s", e)
                    await asyncio.sleep(5)
        finally:
            flush_task.cancel()
            try:
                await flush_task
            except Exception:
                pass

    async def _connect_and_run(self):
        spot = ticker_store.get_spot() or 0.0
        if not spot:
            client = get_delta_client()
            try:
                spot = await client.get_spot_price()
            except Exception as e:
                log.warning("spot seed failed: %s", e)
                spot = 0.0
        if not spot:
            log.warning("no spot — sleeping 10s before retry")
            await asyncio.sleep(10)
            return

        target, atm = await discover_target_symbols(spot)
        self._last_atm = atm

        async with websockets.connect(
            WS_URL, ping_interval=30, ping_timeout=10, max_size=2 ** 22,
        ) as ws:
            log.info("recorder: WS connected — subscribing %d symbols", len(target))
            await self._subscribe_diff(ws, target)
            self._current_subs = set(target)

            # Periodic discovery + new-expiry refresh.
            disco_task = asyncio.create_task(self._discovery_loop(ws))
            try:
                async for raw in ws:
                    self._msgs_total += 1
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    self._handle_message(msg)
            finally:
                disco_task.cancel()
                try:
                    await disco_task
                except Exception:
                    pass

    async def _subscribe_diff(self, ws, target: set[str]):
        to_add = target - self._current_subs
        to_remove = self._current_subs - target
        for batch in _chunks(sorted(to_add), SUB_BATCH):
            await ws.send(json.dumps({
                "type": "subscribe",
                "payload": {"channels": [{"name": "candlestick_1m", "symbols": batch}]},
            }))
        for batch in _chunks(sorted(to_remove), SUB_BATCH):
            await ws.send(json.dumps({
                "type": "unsubscribe",
                "payload": {"channels": [{"name": "candlestick_1m", "symbols": batch}]},
            }))
        if to_add or to_remove:
            log.info("recorder: sub diff +%d -%d", len(to_add), len(to_remove))

    async def _discovery_loop(self, ws):
        last_full = time.time()
        last_disc = time.time()
        while not self._stop.is_set():
            await asyncio.sleep(30)
            spot = ticker_store.get_spot() or 0.0
            if not spot:
                continue
            now = time.time()
            atm = round_atm(spot)
            spot_moved = self._last_atm is not None and abs((atm - self._last_atm)) * STRIKE_INTERVAL >= SPOT_TRIGGER_USD
            heartbeat = now - last_disc >= HEARTBEAT_DISC_S
            new_expiry_pull = now - last_full >= NEW_EXPIRY_S
            if spot_moved or heartbeat or new_expiry_pull:
                try:
                    target, atm2 = await discover_target_symbols(spot)
                    await self._subscribe_diff(ws, target)
                    self._current_subs = set(target)
                    self._last_atm = atm2
                    last_disc = now
                    if new_expiry_pull:
                        last_full = now
                except Exception as e:
                    log.warning("discovery refresh failed: %s", e)

    def _handle_message(self, msg: dict):
        if msg.get("type") != "candlestick_1m":
            return
        sym = msg.get("symbol", "")
        cs = msg.get("candle_start_time")
        if not sym or cs is None:
            return
        # Track which prefix the candle came from. In practice Delta only
        # emits MARK candles even though we subscribe both — see the OI note
        # in __init__.
        if sym.startswith("OI:"):
            self._oi_msgs += 1
        elif sym.startswith("MARK:"):
            self._mark_msgs += 1
        # Delta sends candle_start_time in MICROSECONDS. Convert to unix seconds.
        try:
            minute_unix = int(int(cs) // 1_000_000)
        except Exception:
            return
        try:
            o = float(msg["open"]); h = float(msg["high"])
            lo = float(msg["low"]); c = float(msg["close"])
            v = float(msg.get("volume") or 0)
        except (KeyError, ValueError, TypeError):
            return

        prev = self._inflight.get(sym)
        # Always keep latest values for current minute.
        self._inflight[sym] = (minute_unix, {"o": o, "h": h, "l": lo, "c": c, "v": v})

        # If minute rolled, the previous (older) bar is closed → persist it.
        if prev is not None and prev[0] != minute_unix:
            self._persist_bar(sym, prev[0], prev[1])
            self._bars_closed += 1

    def _persist_bar(self, sym: str, ts: int, b: dict):
        spot_kind = parse_spot_symbol(sym)
        if spot_kind:
            self.writer.queue_spot_bar(spot_kind, ts, b["o"], b["h"], b["l"], b["c"], b["v"])
            return
        meta = parse_option_symbol(sym)
        if not meta:
            return
        self.writer.queue_option_bar(
            meta["expiry"], meta["strike"], meta["side"], meta["kind"],
            ts, b["o"], b["h"], b["l"], b["c"],
        )


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ── Module-level singleton + entry point ──────────────────────────────────────

_RECORDER: LiveRecorder | None = None


def get_recorder() -> LiveRecorder | None:
    return _RECORDER


async def start_recorder() -> LiveRecorder | None:
    """Spawn the recorder as a background task. Idempotent."""
    global _RECORDER
    if os.environ.get("BTC_LIVE_RECORDER", "1") == "0":
        log.info("recorder disabled via BTC_LIVE_RECORDER=0")
        return None
    if _RECORDER is not None:
        return _RECORDER
    _RECORDER = LiveRecorder()
    asyncio.create_task(_RECORDER.run())
    log.info("live_recorder: started")
    return _RECORDER


async def stop_recorder() -> None:
    global _RECORDER
    if _RECORDER is None:
        return
    await _RECORDER.stop()
    _RECORDER = None

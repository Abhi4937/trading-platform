import asyncio
import logging
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Query, HTTPException, Request

import duckdb
from app.core.greeks import compute_greeks

logger = logging.getLogger(__name__)
router = APIRouter()

# Path to the data
DATA_PATH = "/home/abhis/btc-data/data/options/*/*/*.parquet"

# Initialize a global connection for read-only analytical queries
# Using multiple threads is supported by duckdb
_conn = duckdb.connect(database=':memory:', read_only=False)

def get_conn():
    return _conn

import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Simple in-memory cache to avoid repeated slow disk scans
_cached_data_range = None
_cached_latest_data = None

# Strike index: {expiry_str: sorted list of available strikes}
# Built once on first use by scanning folder names only (no parquet reads)
_strike_index: dict[str, list[int]] = {}
_strike_index_built = False

def _build_strike_index():
    """Scan folder names under each expiry to build strike index. No parquet reads."""
    global _strike_index, _strike_index_built
    base_dir = "/home/abhis/btc-data/data/options"
    if not os.path.exists(base_dir):
        _strike_index_built = True
        return
    try:
        for expiry_dir in Path(base_dir).iterdir():
            if not expiry_dir.is_dir() or '=' not in expiry_dir.name:
                continue
            expiry = expiry_dir.name.split('=')[1]
            strikes = []
            for strike_dir in expiry_dir.iterdir():
                if strike_dir.is_dir() and '=' in strike_dir.name:
                    try:
                        strikes.append(int(strike_dir.name.split('=')[1]))
                    except ValueError:
                        pass
            _strike_index[expiry] = sorted(strikes)
        logger.info(f"Strike index built: {len(_strike_index)} expiries")
    except Exception as e:
        logger.error(f"Error building strike index: {e}")
    _strike_index_built = True

def get_strikes_for_expiry(expiry: str) -> list[int]:
    if not _strike_index_built:
        _build_strike_index()
    return _strike_index.get(expiry, [])

@router.get("/latest-available-data")
async def get_latest_available_data():
    global _cached_latest_data
    if _cached_latest_data:
        return _cached_latest_data

    try:
        base_dir = "/home/abhis/btc-data/data/options"
        if not os.path.exists(base_dir):
            return {"latestDate": "2026-03-12", "latestTime": "00:00", "latestExpiry": "2026-03-12"}

        # 1. Fast filesystem scan for latest expiry folder
        expiries = sorted([d.name.split('=')[1] for d in Path(base_dir).iterdir() if d.is_dir() and '=' in d.name])
        if not expiries:
            return {"latestDate": "2026-03-12", "latestTime": "00:00", "latestExpiry": "2026-03-12"}
        
        latest_expiry = expiries[-1]
        
        # 2. Targeted scan of just the latest expiry's first available strike folder to get max timestamp
        # This is 100x faster than scanning the whole dataset
        try:
            strike_dirs = list(Path(f"{base_dir}/expiry={latest_expiry}").iterdir())
            if not strike_dirs:
                return {"latestDate": latest_expiry, "latestTime": "00:00", "latestExpiry": latest_expiry}
            
            # Just look at the first strike folder to find the day's timing
            target_path = f"{strike_dirs[0]}/*.parquet"
            query = f"SELECT max(timestamp_unix) FROM read_parquet('{target_path}')"
            conn = get_conn()
            max_ts = conn.execute(query).fetchone()[0]
            
            if not max_ts:
                res = {"latestDate": latest_expiry, "latestTime": "00:00", "latestExpiry": latest_expiry}
            else:
                ist_tz = timezone(timedelta(hours=5, minutes=30))
                dt = datetime.fromtimestamp(max_ts, tz=ist_tz)
                res = {
                    "latestDate": dt.strftime("%Y-%m-%d"),
                    "latestTime": dt.strftime("%H:%M"),
                    "latestExpiry": latest_expiry
                }
        except Exception:
            res = {"latestDate": latest_expiry, "latestTime": "00:00", "latestExpiry": latest_expiry}
        
        _cached_latest_data = res
        return res
    except Exception as e:
        logger.error(f"Error in fast latest-data scan: {e}")
        return {"latestDate": "2026-03-12", "latestTime": "00:00", "latestExpiry": "2026-03-12"}

@router.get("/data-range")
async def get_data_range():
    global _cached_data_range
    if _cached_data_range:
        return _cached_data_range

    try:
        base_dir = "/home/abhis/btc-data/data/options"
        if not os.path.exists(base_dir):
            return {"min_ts": 0, "max_ts": 0}

        # Fast filesystem scan for expiries
        expiries = sorted([d.name.split('=')[1] for d in Path(base_dir).iterdir() if d.is_dir() and '=' in d.name])
        if not expiries:
            return {"min_ts": 0, "max_ts": 0}

        # We set min/max based on the dates of the folders (00:00 IST of first day to 23:59 IST of last day)
        # This avoids reading any parquet files at all for this call
        min_date = expiries[0]
        max_date = expiries[-1]
        
        min_ts = int(datetime.strptime(f"{min_date} 00:00:00 +0530", "%Y-%m-%d %H:%M:%S %z").timestamp())
        max_ts = int(datetime.strptime(f"{max_date} 23:59:00 +0530", "%Y-%m-%d %H:%M:%S %z").timestamp())
        
        data = {"min_ts": min_ts, "max_ts": max_ts}
        _cached_data_range = data
        return data
    except Exception as e:
        logger.error(f"Error in fast data-range filesystem scan: {e}")
        return {"min_ts": 0, "max_ts": 0}

@router.get("/expiries")
async def get_historical_expiries(
    target_date: str = Query(..., alias="date"),
    timestamp: int = Query(None) # Optional UNIX timestamp for more precise filtering
):
    try:
        # If timestamp is provided, we only show expiries that have data AT or AFTER that timestamp
        # This ensures that if it's March 10th 6:00 PM, the March 10th expiry (which ended at 5:30 PM) is hidden.
        where_clause = f"WHERE expiry >= '{target_date}'"
        if timestamp:
            # We check for expiries that have data points >= this timestamp
            where_clause = f"WHERE timestamp_unix >= {timestamp}"

        query = f"""
        SELECT DISTINCT expiry 
        FROM read_parquet('{DATA_PATH}', hive_partitioning=true)
        {where_clause}
        ORDER BY expiry ASC
        """
        conn = get_conn()
        df = conn.execute(query).df()
        
        # Ensure expiry column is string date
        expiries = df['expiry'].astype(str).tolist()
        
        # Deduplicate and sort (DISTINCT might return Timestamp objects that need cleaning)
        unique_expiries = sorted(list(set([e.split(' ')[0] for e in expiries])))
        
        # Categorize expiries
        categorized = []
        for i, exp in enumerate(unique_expiries):
            label = exp
            if i == 0: label = f"Current ({exp})"
            elif i == 1: label = f"Next ({exp})"
            elif i == 2: label = f"Next-to-Next ({exp})"
            else: label = f"Weekly ({exp})"
            categorized.append({"date": exp, "label": label})
            
        return {"expiries": categorized}
    except Exception as e:
        logger.error(f"Error fetching expiries: {e}")
        return {"expiries": []}

SPOT_DATA_PATH = "/home/abhis/btc-data/data/spot/BTCUSD_1min.parquet"

@router.get("/option-chain")
async def get_historical_chain(
    request: Request,
    target_date: str = Query(..., alias="date"),
    timestamp: int = Query(...) # UNIX timestamp
):
    conn = get_conn()
    
    # 1. Fetch Actual Spot Price for this minute
    try:
        spot_query = f"SELECT mark_close FROM read_parquet('{SPOT_DATA_PATH}') WHERE timestamp_unix = {timestamp}"
        spot_res = conn.execute(spot_query).fetchone()
        spot = spot_res[0] if spot_res else None
    except Exception as e:
        logger.error(f"Error fetching spot price: {e}")
        spot = None

    # 2. Get all available strikes for this expiry from index (folder names, no parquet reads)
    all_strikes = get_strikes_for_expiry(target_date)
    if not all_strikes:
        return {"chain": [], "atm_strike": 0, "spot_actual": spot or 0}

    # 3. Find ATM strike from spot, then filter to ±20 strikes
    if spot:
        atm_strike = min(all_strikes, key=lambda x: abs(x - spot))
    else:
        atm_strike = all_strikes[len(all_strikes) // 2]  # fallback: middle strike
        spot = atm_strike

    atm_idx = all_strikes.index(atm_strike)
    start_idx = max(0, atm_idx - 20)
    end_idx = min(len(all_strikes), atm_idx + 21)
    filtered_strikes = all_strikes[start_idx:end_idx]

    # 4. Build exact file paths for only the ±20 strikes — no wildcard scan
    base_dir = "/home/abhis/btc-data/data/options"
    ce_paths = [f"{base_dir}/expiry={target_date}/strike={s}/CE.parquet" for s in filtered_strikes]
    pe_paths = [f"{base_dir}/expiry={target_date}/strike={s}/PE.parquet" for s in filtered_strikes]
    ce_paths = [p for p in ce_paths if os.path.exists(p)]
    pe_paths = [p for p in pe_paths if os.path.exists(p)]

    if not ce_paths and not pe_paths:
        return {"chain": [], "atm_strike": atm_strike, "spot_actual": spot or 0}

    def query_legs(paths: list[str], opt_type: str) -> dict:
        if not paths:
            return {}
        path_list = "['" + "','".join(paths) + "']"
        q = f"""
        SELECT strike, mark_close as mark_price
        FROM read_parquet({path_list}, hive_partitioning=true)
        WHERE timestamp_unix = {timestamp}
        """
        try:
            df = conn.execute(q).df()
            return dict(zip(df['strike'].astype(int), df['mark_price']))
        except Exception as e:
            logger.error(f"Error querying {opt_type} legs: {e}")
            return {}

    calls = query_legs(ce_paths, "call")
    puts = query_legs(pe_paths, "put")

    if not calls and not puts:
        return {"chain": [], "atm_strike": atm_strike, "spot_actual": spot or 0}

    # Fallback ATM via parity if spot was missing
    if not spot or spot == atm_strike:
        min_diff = float('inf')
        for s in filtered_strikes:
            diff = abs(calls.get(s, 0) - puts.get(s, 0))
            if diff < min_diff:
                min_diff = diff
                atm_strike = s
        spot = spot or atm_strike

    # Time to expiry in years
    from datetime import datetime, timezone
    expiry_dt = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=12)
    current_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    T = max(0.0001, (expiry_dt - current_dt).total_seconds() / (365 * 24 * 3600))

    r = 0.0 # Risk-free rate
    
    # Check disconnect before starting expensive computation
    if await request.is_disconnected():
        return {"chain": [], "atm_strike": 0, "spot_actual": spot or 0}

    from app.core.greeks import implied_vol

    def compute_strike(s: int) -> dict:
        c_price = float(calls.get(s, 0))
        c_iv = implied_vol(c_price, spot, s, T, r, "call")
        cg = compute_greeks(spot, s, T, r, c_iv if c_iv > 0 else 0.5, "call")

        p_price = float(puts.get(s, 0))
        p_iv = implied_vol(p_price, spot, s, T, r, "put")
        pg = compute_greeks(spot, s, T, r, p_iv if p_iv > 0 else 0.5, "put")

        return {
            "strike": s,
            "is_atm": (s == atm_strike),
            "call": {
                "strike": s, "last_price": c_price, "iv_pct": round(c_iv * 100, 2),
                "delta": cg.delta, "gamma": cg.gamma, "theta": cg.theta, "vega": cg.vega
            },
            "put": {
                "strike": s, "last_price": p_price, "iv_pct": round(p_iv * 100, 2),
                "delta": pg.delta, "gamma": pg.gamma, "theta": pg.theta, "vega": pg.vega
            }
        }

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=8) as executor:
        chain = await loop.run_in_executor(
            executor,
            lambda: list(executor.map(compute_strike, filtered_strikes))
        )
        
    return {
        "expiry": target_date,
        "timestamp": timestamp,
        "atm_strike": atm_strike,
        "spot_actual": spot,
        "chain": chain
    }


@router.get("/chart-data")
async def get_chart_data(
    expiry: str = Query(...),
    strike: float = Query(...),
    opt_type: str = Query(..., alias="type"), # CE or PE
    start_time: int = Query(...),
    timeframe: str = Query(...) # e.g., '1m', '5m', '15m', '1h'
):
    conn = get_conn()
    
    # Map timeframe to duckdb INTERVAL
    interval_map = {
        '1m': '1 minute',
        '5m': '5 minutes',
        '15m': '15 minutes',
        '30m': '30 minutes',
        '1h': '1 hour',
        '4h': '4 hours',
        '1d': '1 day'
    }
    interval = interval_map.get(timeframe, '1 minute')
    
    # Construct exact path to avoid pattern-matching multiple files
    filename = 'CE.parquet' if opt_type.upper() == 'CE' else 'PE.parquet'
    exact_path = f"/home/abhis/btc-data/data/options/expiry={expiry}/strike={int(strike)}/{filename}"
    
    if not os.path.exists(exact_path):
        return {"data": []}

    where_clause = f"WHERE timestamp_unix >= {start_time}" if start_time > 0 else ""
    query = f"""
    SELECT
        time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
        first(mark_open ORDER BY timestamp_unix) AS open,
        max(mark_high) AS high,
        min(mark_low) AS low,
        last(mark_close ORDER BY timestamp_unix) AS close
    FROM read_parquet('{exact_path}')
    {where_clause}
    GROUP BY bucket
    ORDER BY bucket ASC
    """
    
    try:
        df = conn.execute(query).df()
        if df.empty:
            return {"data": []}
            
        # Convert bucket to unix seconds (lightweight charts expects integer seconds)
        # DuckDB to_timestamp results in nanoseconds if converted to int64, 
        # but let's use a safer approach:
        df['time'] = df['bucket'].apply(lambda x: int(x.timestamp()))
        
        # Strictly ensure no duplicates and correct order
        df = df.drop_duplicates(subset=['time']).sort_values('time')
        
        records = df[['time', 'open', 'high', 'low', 'close']].to_dict('records')
        return {"data": records}
    except Exception as e:
        logger.error(f"Error fetching chart data for {exact_path}: {e}")
        return {"data": []}

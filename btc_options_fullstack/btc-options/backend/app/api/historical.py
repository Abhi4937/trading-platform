import logging
from datetime import date, datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Query, HTTPException

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

@router.get("/latest-available-data")
async def get_latest_available_data():
    """
    High-performance initialization endpoint.
    1. Scans filesystem for the latest expiry folder.
    2. Runs a targeted query on that folder to find the max timestamp.
    """
    try:
        base_dir = "/home/abhis/btc-data/data/options"
        if not os.path.exists(base_dir):
            # Fallback for dev environment if path doesn't exist
            return {"latestDate": "2026-03-12", "latestTime": "00:00", "latestExpiry": "2026-03-12"}

        # 1. Get the latest expiry folder (YYYY-MM-DD)
        expiries = sorted([d.name.split('=')[1] for d in Path(base_dir).iterdir() if d.is_dir() and '=' in d.name])
        if not expiries:
            return {"latestDate": "2026-03-12", "latestTime": "00:00", "latestExpiry": "2026-03-12"}
        
        latest_expiry = expiries[-1]
        
        # 2. Find the latest timestamp within this specific expiry to get the latest simulation time
        # We query just the latest expiry folder for maximum performance
        target_path = f"{base_dir}/expiry={latest_expiry}/*/*.parquet"
        query = f"SELECT max(timestamp_unix) FROM read_parquet('{target_path}')"
        
        conn = get_conn()
        res = conn.execute(query).fetchone()
        max_ts = res[0] if res and res[0] else None
        
        if not max_ts:
            return {"latestDate": latest_expiry, "latestTime": "00:00", "latestExpiry": latest_expiry}
            
        # Convert the epoch timestamp to IST (UTC+5:30) string for the frontend picker
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        dt = datetime.fromtimestamp(max_ts, tz=ist_tz)
        
        return {
            "latestDate": dt.strftime("%Y-%m-%d"),
            "latestTime": dt.strftime("%H:%M"),
            "latestExpiry": latest_expiry
        }
    except Exception as e:
        logger.error(f"Error in fast latest-data scan: {e}")
        return {"latestDate": "2026-03-12", "latestTime": "00:00", "latestExpiry": "2026-03-12"}

@router.get("/data-range")
async def get_data_range():
    try:
        query = f"SELECT min(timestamp_unix) as min_ts, max(timestamp_unix) as max_ts FROM read_parquet('{DATA_PATH}')"
        conn = get_conn()
        res = conn.execute(query).fetchone()
        return {"min_ts": res[0], "max_ts": res[1]}
    except Exception as e:
        logger.error(f"Error fetching data range: {e}")
        return {"min_ts": 0, "max_ts": 0}

@router.get("/expiries")
async def get_historical_expiries(target_date: str = Query(..., alias="date")):
    try:
        # Get unique expiries for the selected historical date
        query = f"""
        SELECT DISTINCT expiry 
        FROM read_parquet('{DATA_PATH}', hive_partitioning=true)
        WHERE expiry >= '{target_date}'
        ORDER BY expiry ASC
        """
        conn = get_conn()
        df = conn.execute(query).df()
        
        expiries = df['expiry'].astype(str).tolist()
        
        # Categorize expiries
        categorized = []
        for i, exp in enumerate(expiries):
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

    # 2. Query all strikes for the given expiry and timestamp
    query = f"""
    SELECT 
        strike,
        CASE WHEN filename LIKE '%CE.parquet' THEN 'call' ELSE 'put' END as opt_type,
        mark_close as mark_price
    FROM read_parquet('{DATA_PATH}', hive_partitioning=true, filename=true)
    WHERE expiry = '{target_date}' AND timestamp_unix = {timestamp}
    """
    try:
        df = conn.execute(query).df()
    except Exception as e:
        logger.error(f"Error fetching option chain: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
    if df.empty:
        return {"chain": [], "atm_strike": 0, "spot_actual": spot or 0}
        
    calls = df[df['opt_type'] == 'call'].set_index('strike')['mark_price']
    puts = df[df['opt_type'] == 'put'].set_index('strike')['mark_price']
    
    strikes = sorted(list(set(calls.index).union(puts.index)))
    
    if not strikes:
        return {"chain": [], "atm_strike": 0, "spot_actual": spot or 0}
        
    # 3. Calculate ATM Strike based on actual spot
    # If spot is missing, fallback to parity inference
    if spot:
        atm_strike = min(strikes, key=lambda x: abs(x - spot))
    else:
        # Fallback to premium parity if spot data is missing for this specific minute
        min_diff = float('inf')
        atm_strike = strikes[0]
        for s in strikes:
            c_p = calls.get(s, 0)
            p_p = puts.get(s, 0)
            diff = abs(c_p - p_p)
            if diff < min_diff:
                min_diff = diff
                atm_strike = s
        spot = atm_strike # Use ATM as spot if data missing
            
    # Time to expiry in years
    from datetime import datetime, timezone
    expiry_dt = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=12)
    current_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    T = max(0.0001, (expiry_dt - current_dt).total_seconds() / (365 * 24 * 3600))
    
    r = 0.0 # Risk-free rate
    
    # Filter ±20 strikes
    try:
        atm_idx = strikes.index(atm_strike)
        start_idx = max(0, atm_idx - 20)
        end_idx = min(len(strikes), atm_idx + 21)
        filtered_strikes = strikes[start_idx:end_idx]
    except ValueError:
        filtered_strikes = strikes[:40]
    
    chain = []
    for s in filtered_strikes:
        # Call leg
        c_price = float(calls.get(s, 0))
        from app.core.greeks import implied_vol
        c_iv = implied_vol(c_price, spot, s, T, r, "call")
        cg = compute_greeks(spot, s, T, r, c_iv if c_iv > 0 else 0.5, "call")
        
        call_leg = {
            "strike": s, "last_price": c_price, "iv_pct": round(c_iv * 100, 2),
            "delta": cg.delta, "gamma": cg.gamma, "theta": cg.theta, "vega": cg.vega
        }
        
        # Put leg
        p_price = float(puts.get(s, 0))
        p_iv = implied_vol(p_price, spot, s, T, r, "put")
        pg = compute_greeks(spot, s, T, r, p_iv if p_iv > 0 else 0.5, "put")
        
        put_leg = {
            "strike": s, "last_price": p_price, "iv_pct": round(p_iv * 100, 2),
            "delta": pg.delta, "gamma": pg.gamma, "theta": pg.theta, "vega": pg.vega
        }
        
        chain.append({
            "strike": s,
            "call": call_leg,
            "put": put_leg,
            "is_atm": (s == atm_strike)
        })
        
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
    filename_filter = 'CE.parquet' if opt_type.upper() == 'CE' else 'PE.parquet'
    
    query = f"""
    SELECT 
        time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
        first(mark_open ORDER BY timestamp_unix) AS open,
        max(mark_high) AS high,
        min(mark_low) AS low,
        last(mark_close ORDER BY timestamp_unix) AS close
    FROM read_parquet('{DATA_PATH}', hive_partitioning=true, filename=true)
    WHERE expiry = '{expiry}' AND strike = {strike} AND filename LIKE '%{filename_filter}'
    GROUP BY bucket
    ORDER BY bucket ASC
    """
    
    try:
        df = conn.execute(query).df()
        # Convert bucket to unix timestamp for lightweight-charts
        df['time'] = df['bucket'].astype('int64') // 10**9
        
        # lightweight charts expects time, open, high, low, close
        records = df[['time', 'open', 'high', 'low', 'close']].to_dict('records')
        return {"data": records}
    except Exception as e:
        logger.error(f"Error fetching chart data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

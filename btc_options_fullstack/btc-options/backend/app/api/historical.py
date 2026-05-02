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
        
        return res
    except Exception as e:
        logger.error(f"Error in fast latest-data scan: {e}")
        return {"latestDate": "2026-03-12", "latestTime": "00:00", "latestExpiry": "2026-03-12"}

SPOT_DATA_PATH_RANGE = "/home/abhis/btc-data/data/spot/BTCUSD_1min.parquet"

@router.get("/data-range")
async def get_data_range():
    try:
        base_dir = "/home/abhis/btc-data/data/options"
        if not os.path.exists(base_dir):
            return {"min_ts": 0, "max_ts": 0}

        # min_ts: earliest expiry folder (options data start)
        expiries = sorted([d.name.split('=')[1] for d in Path(base_dir).iterdir() if d.is_dir() and '=' in d.name])
        if not expiries:
            return {"min_ts": 0, "max_ts": 0}

        min_date = expiries[0]
        min_ts = int(datetime.strptime(f"{min_date} 00:00:00 +0530", "%Y-%m-%d %H:%M:%S %z").timestamp())

        # max_ts: latest actual recorded price from spot parquet — not expiry folder name
        # This correctly reflects the last date data was collected, not the last contract expiry
        conn = get_conn()
        spot_max = conn.execute(
            f"SELECT MAX(timestamp_unix) FROM read_parquet('{SPOT_DATA_PATH_RANGE}')"
        ).fetchone()[0]
        max_ts = int(spot_max) if spot_max else min_ts

        return {"min_ts": min_ts, "max_ts": max_ts}
    except Exception as e:
        logger.error(f"Error in data-range: {e}")
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
    timestamp: int = Query(...), # UNIX timestamp
    pin_strikes: str = Query(None) # comma-separated strikes to always include (e.g. strategy legs)
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

    # 3. Find ATM strike from spot, then filter to ±50 strikes
    if spot:
        atm_strike = min(all_strikes, key=lambda x: abs(x - spot))
    else:
        atm_strike = all_strikes[len(all_strikes) // 2]  # fallback: middle strike
        spot = atm_strike

    atm_idx = all_strikes.index(atm_strike)
    start_idx = max(0, atm_idx - 50)
    end_idx = min(len(all_strikes), atm_idx + 51)
    filtered_strikes = list(all_strikes[start_idx:end_idx])

    # Always include pinned strikes (e.g. active strategy legs) even if outside ±50 window
    if pin_strikes:
        pin_set = set(filtered_strikes)
        for s in pin_strikes.split(','):
            try:
                s_int = int(s.strip())
                if s_int in all_strikes and s_int not in pin_set:
                    filtered_strikes.append(s_int)
                    pin_set.add(s_int)
            except ValueError:
                pass
        filtered_strikes.sort()

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
        SELECT strike, mark_close as mark_price, oi_close
        FROM read_parquet({path_list}, hive_partitioning=true)
        WHERE timestamp_unix = {timestamp}
        """
        try:
            df = conn.execute(q).df()
            return dict(zip(df['strike'].astype(int),
                            zip(df['mark_price'], df['oi_close'])))
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
            diff = abs((calls.get(s, (0, 0))[0] or 0) - (puts.get(s, (0, 0))[0] or 0))
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
    import math as _math

    def _safe(x) -> float:
        try:
            v = float(x)
        except Exception:
            return 0.0
        return 0.0 if _math.isnan(v) else v

    def compute_strike(s: int) -> dict:
        c_tuple = calls.get(s, (0, 0))
        c_price = _safe(c_tuple[0])
        c_oi    = _safe(c_tuple[1])
        if c_price > 0:
            c_iv = implied_vol(c_price, spot, s, T, r, "call")
            cg = compute_greeks(spot, s, T, r, c_iv if c_iv > 0 else 0.5, "call")
        else:
            c_iv = 0.0
            cg = None

        p_tuple = puts.get(s, (0, 0))
        p_price = _safe(p_tuple[0])
        p_oi    = _safe(p_tuple[1])
        if p_price > 0:
            p_iv = implied_vol(p_price, spot, s, T, r, "put")
            pg = compute_greeks(spot, s, T, r, p_iv if p_iv > 0 else 0.5, "put")
        else:
            p_iv = 0.0
            pg = None

        # OI in USD: parquet `oi_close` is BTC-notional (contracts already × 0.001),
        # so multiply by spot directly to get USD value (matches Delta's oi_value_usd).
        c_oi_usd = c_oi * spot
        p_oi_usd = p_oi * spot

        return {
            "strike": s,
            "is_atm": (s == atm_strike),
            "call": {
                "strike": s, "last_price": c_price, "iv_pct": round(c_iv * 100, 2),
                "delta": cg.delta if cg else 0.0,
                "gamma": cg.gamma if cg else 0.0,
                "theta": cg.theta if cg else 0.0,
                "vega": cg.vega if cg else 0.0,
                "open_interest": round(c_oi, 2),
                "oi_usd": round(c_oi_usd, 2),
            },
            "put": {
                "strike": s, "last_price": p_price, "iv_pct": round(p_iv * 100, 2),
                "delta": pg.delta if pg else 0.0,
                "gamma": pg.gamma if pg else 0.0,
                "theta": pg.theta if pg else 0.0,
                "vega": pg.vega if pg else 0.0,
                "open_interest": round(p_oi, 2),
                "oi_usd": round(p_oi_usd, 2),
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


@router.get("/chart-data-with-greeks")
async def get_chart_data_with_greeks(
    expiry: str = Query(...),
    strike: float = Query(...),
    opt_type: str = Query(..., alias="type"),
    start_time: int = Query(...),
    timeframe: str = Query(...),
    rv_window_days: int = Query(7),
):
    conn = get_conn()
    interval_map = {
        '1m': '1 minute', '5m': '5 minutes', '15m': '15 minutes',
        '30m': '30 minutes', '1h': '1 hour', '4h': '4 hours', '1d': '1 day'
    }
    interval_secs = {
        '1m': 60, '5m': 300, '15m': 900,
        '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400
    }
    interval = interval_map.get(timeframe, '1 minute')
    bucket_secs = interval_secs.get(timeframe, 60)
    filename = 'CE.parquet' if opt_type.upper() == 'CE' else 'PE.parquet'
    exact_path = f"/home/abhis/btc-data/data/options/expiry={expiry}/strike={int(strike)}/{filename}"
    if not os.path.exists(exact_path):
        return {"data": []}

    where_clause = f"WHERE timestamp_unix >= {start_time}" if start_time > 0 else ""
    opt_query = f"""
    SELECT
        time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
        first(mark_open ORDER BY timestamp_unix) AS open,
        max(mark_high) AS high,
        min(mark_low) AS low,
        last(mark_close ORDER BY timestamp_unix) AS close
    FROM read_parquet('{exact_path}')
    {where_clause}
    GROUP BY bucket ORDER BY bucket ASC
    """
    spot_query = f"""
    SELECT
        time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
        last(mark_close ORDER BY timestamp_unix) AS spot_close
    FROM read_parquet('{SPOT_DATA_PATH}')
    {where_clause}
    GROUP BY bucket ORDER BY bucket ASC
    """
    try:
        opt_df = conn.execute(opt_query).df()
        spot_df = conn.execute(spot_query).df()
        if opt_df.empty:
            return {"data": []}
        opt_df['time'] = opt_df['bucket'].apply(lambda x: int(x.timestamp()))
        spot_df['time'] = spot_df['bucket'].apply(lambda x: int(x.timestamp()))
        merged = opt_df.merge(spot_df[['time', 'spot_close']], on='time', how='left')
        merged = merged.drop_duplicates(subset=['time']).sort_values('time').fillna(0)

        from app.core.greeks import implied_vol as _iv
        expiry_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=12)
        r = 0.0
        opt_type_str = "call" if opt_type.upper() == "CE" else "put"
        K = int(strike)

        # RV (Realized Volatility): rolling stdev of log-returns at the chart's own
        # timeframe frequency, annualized × √(bars_per_year) × 100.
        # This matches Delta Exchange analytics — RV updates every bar (intraday),
        # not just once per day. Window = rv_window_days × bars_per_day bars.
        BARS_PER_DAY = {'1m': 1440, '5m': 288, '15m': 96, '30m': 48, '1h': 24, '4h': 6, '1d': 1}
        bars_per_day = BARS_PER_DAY.get(timeframe, 288)
        rv_rolling_bars = rv_window_days * bars_per_day
        import math as _math
        annualize_factor = _math.sqrt(365 * bars_per_day)
        # When start_time=0 (full contract fetch), anchor lookback to the first actual option bar
        if start_time > 0:
            rv_lookback_start = max(0, start_time - (rv_window_days + 5) * 86400)
        else:
            actual_start = int(merged['time'].min()) if not merged.empty else 0
            rv_lookback_start = max(0, actual_start - (rv_window_days + 5) * 86400)
        rv_by_time: dict[int, float] = {}
        try:
            import numpy as np
            rv_spot_query = f"""
            SELECT
                time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
                last(mark_close ORDER BY timestamp_unix) AS spot_close
            FROM read_parquet('{SPOT_DATA_PATH}')
            WHERE timestamp_unix >= {rv_lookback_start}
            GROUP BY bucket ORDER BY bucket ASC
            """
            rv_df = conn.execute(rv_spot_query).df()
            if not rv_df.empty and len(rv_df) >= 2:
                rv_df['time'] = rv_df['bucket'].apply(lambda x: int(x.timestamp()))
                rv_df['log_ret'] = np.log(rv_df['spot_close'] / rv_df['spot_close'].shift(1))
                rv_df['rv'] = rv_df['log_ret'].rolling(rv_rolling_bars).std() * annualize_factor * 100
                valid = rv_df.dropna(subset=['rv'])
                rv_by_time = dict(zip(valid['time'], valid['rv']))
        except Exception as e:
            logger.warning(f"RV computation failed: {e}")

        records = []
        for _, row in merged.iterrows():
            t = int(row['time'])
            close = float(row['close'])
            spot = float(row['spot_close'])
            rv_val = round(float(rv_by_time.get(t, 0.0)), 2)
            current_dt = datetime.fromtimestamp(t + bucket_secs, tz=timezone.utc)
            T = max(0.0001, (expiry_dt - current_dt).total_seconds() / (365 * 24 * 3600))
            if close > 0 and spot > 0:
                iv = _iv(close, spot, K, T, r, opt_type_str)
                g = compute_greeks(spot, K, T, r, iv if iv > 0 else 0.5, opt_type_str)
                records.append({
                    'time': t, 'open': float(row['open']), 'high': float(row['high']),
                    'low': float(row['low']), 'close': close, 'spot': round(spot, 2),
                    'iv': round(iv * 100, 2), 'rv': rv_val,
                    'delta': g.delta, 'gamma': g.gamma,
                    'theta': g.theta, 'vega': g.vega,
                })
            else:
                records.append({
                    'time': t, 'open': float(row['open']), 'high': float(row['high']),
                    'low': float(row['low']), 'close': close, 'spot': round(spot, 2),
                    'iv': 0.0, 'rv': rv_val,
                    'delta': 0.0, 'gamma': 0.0, 'theta': 0.0, 'vega': 0.0,
                })
        return {"data": records}
    except Exception as e:
        logger.error(f"Error fetching chart data with greeks for {exact_path}: {e}")
        return {"data": []}


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
        df = df.fillna(0)  # NaN in OHLC (no-trade gaps) would break JSON serialization

        records = df[['time', 'open', 'high', 'low', 'close']].to_dict('records')
        return {"data": records}
    except Exception as e:
        logger.error(f"Error fetching chart data for {exact_path}: {e}")
        return {"data": []}


@router.get("/atm-iv-series")
async def get_atm_iv_series(
    expiry: str = Query(...),
    timeframe: str = Query(...),
    rv_window_days: int = Query(7),
):
    """ATM IV time series across the contract lifetime.

    For each timeframe bucket: spot → closest strike → average of CE+PE IV at that strike.
    Returns {time, atm_strike, atm_iv, rv, iv_minus_rv}.
    """
    conn = get_conn()
    interval_map = {
        '1m': '1 minute', '5m': '5 minutes', '15m': '15 minutes',
        '30m': '30 minutes', '1h': '1 hour', '4h': '4 hours', '1d': '1 day',
    }
    interval_secs = {
        '1m': 60, '5m': 300, '15m': 900,
        '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400,
    }
    interval = interval_map.get(timeframe, '5 minutes')
    bucket_secs = interval_secs.get(timeframe, 300)

    strikes = get_strikes_for_expiry(expiry)
    if not strikes:
        return {"data": []}

    # Use middle strike's parquet to find data range for this expiry
    sample_strike = strikes[len(strikes) // 2]
    sample_path = f"/home/abhis/btc-data/data/options/expiry={expiry}/strike={sample_strike}/CE.parquet"
    if not os.path.exists(sample_path):
        sample_path = f"/home/abhis/btc-data/data/options/expiry={expiry}/strike={sample_strike}/PE.parquet"
        if not os.path.exists(sample_path):
            return {"data": []}

    try:
        rng = conn.execute(
            f"SELECT MIN(timestamp_unix), MAX(timestamp_unix) FROM read_parquet('{sample_path}')"
        ).fetchone()
        if not rng or rng[0] is None:
            return {"data": []}
        min_ts, max_ts = int(rng[0]), int(rng[1])

        # Bucketed spot for the contract lifetime
        spot_query = f"""
        SELECT
            time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
            last(mark_close ORDER BY timestamp_unix) AS spot_close
        FROM read_parquet('{SPOT_DATA_PATH}')
        WHERE timestamp_unix >= {min_ts} AND timestamp_unix <= {max_ts}
        GROUP BY bucket ORDER BY bucket ASC
        """
        spot_df = conn.execute(spot_query).df()
        if spot_df.empty:
            return {"data": []}
        spot_df['time'] = spot_df['bucket'].apply(lambda x: int(x.timestamp()))

        import numpy as np
        strikes_arr = np.array(sorted(strikes))
        spot_df['atm_strike'] = spot_df['spot_close'].apply(
            lambda s: int(strikes_arr[np.argmin(np.abs(strikes_arr - s))]) if s and s > 0 else 0
        )

        # Bulk-load CE + PE bucketed marks for each unique ATM strike
        unique_strikes = [int(s) for s in spot_df['atm_strike'].unique() if s > 0]
        strike_data: dict[int, dict[str, dict[int, float]]] = {}
        for K in unique_strikes:
            ce_path = f"/home/abhis/btc-data/data/options/expiry={expiry}/strike={K}/CE.parquet"
            pe_path = f"/home/abhis/btc-data/data/options/expiry={expiry}/strike={K}/PE.parquet"
            ce_data, pe_data = {}, {}
            for path, target in [(ce_path, 'ce'), (pe_path, 'pe')]:
                if not os.path.exists(path):
                    continue
                df = conn.execute(f"""
                    SELECT
                        time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
                        last(mark_close ORDER BY timestamp_unix) AS close
                    FROM read_parquet('{path}')
                    GROUP BY bucket ORDER BY bucket ASC
                """).df()
                if not df.empty:
                    df['time'] = df['bucket'].apply(lambda x: int(x.timestamp()))
                    if target == 'ce':
                        ce_data = dict(zip(df['time'].astype(int), df['close'].astype(float)))
                    else:
                        pe_data = dict(zip(df['time'].astype(int), df['close'].astype(float)))
            strike_data[K] = {'ce': ce_data, 'pe': pe_data}

        # RV — same window/annualization as chart-data-with-greeks
        import math as _math
        BARS_PER_DAY = {'1m': 1440, '5m': 288, '15m': 96, '30m': 48, '1h': 24, '4h': 6, '1d': 1}
        bars_per_day = BARS_PER_DAY.get(timeframe, 288)
        rv_rolling_bars = rv_window_days * bars_per_day
        annualize_factor = _math.sqrt(365 * bars_per_day)
        rv_lookback_start = max(0, min_ts - (rv_window_days + 5) * 86400)
        rv_by_time: dict[int, float] = {}
        try:
            rv_df = conn.execute(f"""
                SELECT
                    time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
                    last(mark_close ORDER BY timestamp_unix) AS spot_close
                FROM read_parquet('{SPOT_DATA_PATH}')
                WHERE timestamp_unix >= {rv_lookback_start}
                GROUP BY bucket ORDER BY bucket ASC
            """).df()
            if not rv_df.empty and len(rv_df) >= 2:
                rv_df['time'] = rv_df['bucket'].apply(lambda x: int(x.timestamp()))
                rv_df['log_ret'] = np.log(rv_df['spot_close'] / rv_df['spot_close'].shift(1))
                rv_df['rv'] = rv_df['log_ret'].rolling(rv_rolling_bars).std() * annualize_factor * 100
                valid = rv_df.dropna(subset=['rv'])
                rv_by_time = dict(zip(valid['time'].astype(int), valid['rv'].astype(float)))
        except Exception as e:
            logger.warning(f"ATM RV computation failed: {e}")

        from app.core.greeks import implied_vol as _iv
        expiry_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=12)
        r = 0.0

        # Strikes we have parquet data loaded for (subset of all expiry strikes).
        # Used as the fallback pool when the ATM strike has no IV at a given bar.
        loaded_strikes = sorted(strike_data.keys())

        records = []
        for _, row in spot_df.iterrows():
            t = int(row['time'])
            spot = float(row['spot_close']) if row['spot_close'] else 0.0
            K_atm = int(row['atm_strike'])
            if spot <= 0 or K_atm == 0:
                continue
            current_dt = datetime.fromtimestamp(t + bucket_secs, tz=timezone.utc)
            T = max(0.0001, (expiry_dt - current_dt).total_seconds() / (365 * 24 * 3600))

            # Try ATM strike first; on missing data, walk loaded strikes by distance
            # from spot and use the first that yields a valid IV.
            candidates = sorted(loaded_strikes, key=lambda s: abs(s - spot))
            if K_atm in candidates:
                candidates = [K_atm] + [k for k in candidates if k != K_atm]

            atm_iv = 0.0
            K_used = K_atm
            for K_try in candidates:
                ce_close = strike_data.get(K_try, {}).get('ce', {}).get(t, 0.0)
                pe_close = strike_data.get(K_try, {}).get('pe', {}).get(t, 0.0)
                ivs = []
                if ce_close > 0:
                    v = _iv(ce_close, spot, K_try, T, r, "call")
                    if v > 0:
                        ivs.append(v)
                if pe_close > 0:
                    v = _iv(pe_close, spot, K_try, T, r, "put")
                    if v > 0:
                        ivs.append(v)
                if ivs:
                    atm_iv = sum(ivs) / len(ivs) * 100.0
                    K_used = K_try
                    break

            if atm_iv <= 0:
                continue

            rv_val = round(float(rv_by_time.get(t, 0.0)), 2)
            records.append({
                'time': t,
                'atm_strike': K_used,
                'atm_iv': round(atm_iv, 2),
                'rv': rv_val,
                'iv_minus_rv': round(atm_iv - rv_val, 2) if rv_val > 0 else 0.0,
            })

        return {"data": records}
    except Exception as e:
        logger.error(f"Error fetching ATM IV series for {expiry}: {e}")
        return {"data": []}


# ── Spot OHLC + technical indicators ─────────────────────────────────────────

# Maps timeframe strings → (DuckDB INTERVAL, bucket_seconds)
_TF_TO_INTERVAL = {
    '1m':  ('1 minute',   60),
    '5m':  ('5 minutes',  300),
    '15m': ('15 minutes', 900),
    '30m': ('30 minutes', 1800),
    '1h':  ('1 hour',     3600),
    '4h':  ('4 hours',    14400),
    '1d':  ('1 day',      86400),
}


def _bucketed_spot_ohlc(start_ts: int, end_ts: int, timeframe: str, lookback_extra_sec: int = 0) -> 'pd.DataFrame':
    """Pull bucketed spot OHLCV from the parquet for the given range.

    Returns DataFrame with columns: time, open, high, low, close, volume.
    `lookback_extra_sec` extends the start backwards (warm-up for indicators).
    """
    import pandas as pd
    interval, _ = _TF_TO_INTERVAL.get(timeframe, _TF_TO_INTERVAL['5m'])
    fetch_start = max(0, int(start_ts) - max(0, int(lookback_extra_sec)))
    conn = get_conn()
    q = f"""
    SELECT
        time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
        first(mark_open  ORDER BY timestamp_unix) AS o,
        max(mark_high)                            AS h,
        min(mark_low)                             AS l,
        last(mark_close  ORDER BY timestamp_unix) AS c,
        sum(ltp_volume)                            AS v
    FROM read_parquet('{SPOT_DATA_PATH}')
    WHERE timestamp_unix >= {fetch_start} AND timestamp_unix <= {int(end_ts)}
    GROUP BY bucket ORDER BY bucket ASC
    """
    try:
        df = conn.execute(q).df()
    except Exception as e:
        logger.error(f"_bucketed_spot_ohlc failed: {e}")
        return pd.DataFrame(columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    if df.empty:
        return pd.DataFrame(columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    df['time']   = df['bucket'].apply(lambda x: int(x.timestamp()))
    df = df.rename(columns={'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'})
    return df[['time', 'open', 'high', 'low', 'close', 'volume']].sort_values('time').reset_index(drop=True)


def _bucketed_leg_ohlc(expiry: str, strike: int, opt_type: str,
                        start_ts: int, end_ts: int, timeframe: str,
                        lookback_extra_sec: int = 0) -> 'pd.DataFrame':
    """Same as _bucketed_spot_ohlc but for a single leg's premium parquet."""
    import pandas as pd
    interval, _ = _TF_TO_INTERVAL.get(timeframe, _TF_TO_INTERVAL['5m'])
    fetch_start = max(0, int(start_ts) - max(0, int(lookback_extra_sec)))
    path = f"/home/abhis/btc-data/data/options/expiry={expiry}/strike={int(strike)}/{opt_type.upper()}.parquet"
    if not os.path.exists(path):
        return pd.DataFrame(columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    conn = get_conn()
    q = f"""
    SELECT
        time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
        first(mark_open  ORDER BY timestamp_unix) AS o,
        max(mark_high)                            AS h,
        min(mark_low)                             AS l,
        last(mark_close  ORDER BY timestamp_unix) AS c,
        last(oi_close    ORDER BY timestamp_unix) AS v
    FROM read_parquet('{path}')
    WHERE timestamp_unix >= {fetch_start} AND timestamp_unix <= {int(end_ts)}
    GROUP BY bucket ORDER BY bucket ASC
    """
    try:
        df = conn.execute(q).df()
    except Exception as e:
        logger.error(f"_bucketed_leg_ohlc failed: {e}")
        return pd.DataFrame(columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    if df.empty:
        return pd.DataFrame(columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    df['time'] = df['bucket'].apply(lambda x: int(x.timestamp()))
    df = df.rename(columns={'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'})
    # Leg parquets don't carry trade volume; v above is oi_close — keep it for OI-flavored
    # indicators or set to NaN for VWAP. For now, treat as 0 so VWAP won't run on legs.
    df['volume'] = 0
    return df[['time', 'open', 'high', 'low', 'close', 'volume']].sort_values('time').reset_index(drop=True)


def _parse_indicators_param(raw: Optional[str]) -> list[dict]:
    """Parse `indicators` query param. Accepts JSON array or empty/None."""
    if not raw:
        return []
    import json
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return [c for c in v if isinstance(c, dict)]
    except Exception:
        pass
    return []


def _max_indicator_lookback_bars(configs: list[dict]) -> int:
    """Estimate the warm-up bars needed for the longest-window indicator."""
    n = 0
    for cfg in configs:
        p = cfg.get("params", {}) or {}
        t = (cfg.get("type") or "").lower()
        if t in ("sma", "ema", "rsi", "atr"):
            n = max(n, int(p.get("period", 20)))
        elif t == "bbands":
            n = max(n, int(p.get("period", 20)))
        elif t == "macd":
            n = max(n, int(p.get("slow", 26)) + int(p.get("signal", 9)))
        elif t == "vwap":
            n = max(n, 0)  # cumulative — no warm-up
    # Add a 50% buffer to ensure the first visible bar has stable values.
    return int(n * 1.5)


@router.get("/spot-ohlc")
async def get_spot_ohlc(
    start_ts: int = Query(..., description="unix sec"),
    end_ts:   int = Query(..., description="unix sec"),
    timeframe: str = Query("5m"),
):
    """Return bucketed spot OHLC (+ volume) for the requested range.

    Powers the historical/backtest spot candlestick chart.
    """
    df = _bucketed_spot_ohlc(start_ts, end_ts, timeframe)
    if df.empty:
        return {"data": []}
    df = df[(df['time'] >= int(start_ts)) & (df['time'] <= int(end_ts))]
    return {"data": df.to_dict(orient='records')}


@router.get("/spot-indicators")
async def get_spot_indicators(
    start_ts: int = Query(...),
    end_ts:   int = Query(...),
    timeframe: str = Query("5m"),
    indicators: str = Query("[]", description="JSON array of indicator configs"),
):
    """Compute technical indicators on the spot price series."""
    from app.services.indicators import compute_indicators
    configs = _parse_indicators_param(indicators)
    if not configs:
        return {"indicators": {}}
    _, bucket_secs = _TF_TO_INTERVAL.get(timeframe, _TF_TO_INTERVAL['5m'])
    warmup_bars = _max_indicator_lookback_bars(configs)
    lookback_sec = warmup_bars * bucket_secs
    df = _bucketed_spot_ohlc(start_ts, end_ts, timeframe, lookback_extra_sec=lookback_sec)
    if df.empty:
        return {"indicators": {}}
    series = compute_indicators(df, configs)
    # Slice each indicator's series to the requested visible window.
    out = {
        iid: [pt for pt in pts if int(pt["time"]) >= int(start_ts) and int(pt["time"]) <= int(end_ts)]
        for iid, pts in series.items()
    }
    return {"indicators": out}


@router.get("/leg-indicators")
async def get_leg_indicators(
    expiry: str = Query(...),
    strike: int = Query(...),
    type:   str = Query(..., description="CE or PE"),
    start_ts: int = Query(...),
    end_ts:   int = Query(...),
    timeframe: str = Query("5m"),
    indicators: str = Query("[]"),
):
    """Compute indicators on a single leg's premium series."""
    from app.services.indicators import compute_indicators
    configs = _parse_indicators_param(indicators)
    if not configs:
        return {"indicators": {}}
    _, bucket_secs = _TF_TO_INTERVAL.get(timeframe, _TF_TO_INTERVAL['5m'])
    warmup_bars = _max_indicator_lookback_bars(configs)
    lookback_sec = warmup_bars * bucket_secs
    df = _bucketed_leg_ohlc(expiry, int(strike), type, start_ts, end_ts, timeframe,
                              lookback_extra_sec=lookback_sec)
    if df.empty:
        return {"indicators": {}}
    series = compute_indicators(df, configs)
    out = {
        iid: [pt for pt in pts if int(pt["time"]) >= int(start_ts) and int(pt["time"]) <= int(end_ts)]
        for iid, pts in series.items()
    }
    return {"indicators": out}


@router.get("/leg-ohlc")
async def get_leg_ohlc(
    expiry: str = Query(...),
    strike: int = Query(...),
    type:   str = Query(...),
    start_ts: int = Query(...),
    end_ts:   int = Query(...),
    timeframe: str = Query("5m"),
):
    """Return bucketed OHLC for one option leg (premium series)."""
    df = _bucketed_leg_ohlc(expiry, int(strike), type, start_ts, end_ts, timeframe)
    if df.empty:
        return {"data": []}
    df = df[(df['time'] >= int(start_ts)) & (df['time'] <= int(end_ts))]
    return {"data": df.to_dict(orient='records')}


# ── Strangle calibration + snapshot-context endpoints ────────────────────────

import json
import math as _math

CALIBRATION_PATH = "/home/abhis/btc-data/derived/calibration.parquet"
CALIBRATION_UNIVERSAL_PATH = "/home/abhis/btc-data/derived/calibration_universal.parquet"
FULL_ENRICHED_5M_PATH = "/home/abhis/btc-data/derived/full_enriched_5m.parquet"

# In-process cache: {(dte, spot, delta, ivp) bucket key → response dict}.
# Cleared on process restart; the parquets rarely change.
_calibration_cache: dict[str, Any] = {}
_calibration_loaded = {"specific": None, "universal": None}


def _load_calibration_specific():
    if _calibration_loaded["specific"] is not None:
        return _calibration_loaded["specific"]
    if not os.path.exists(CALIBRATION_PATH):
        _calibration_loaded["specific"] = "missing"
        return "missing"
    import pandas as _pd
    df = _pd.read_parquet(CALIBRATION_PATH)
    _calibration_loaded["specific"] = df
    return df


def _load_calibration_universal():
    if _calibration_loaded["universal"] is not None:
        return _calibration_loaded["universal"]
    if not os.path.exists(CALIBRATION_UNIVERSAL_PATH):
        _calibration_loaded["universal"] = "missing"
        return "missing"
    import pandas as _pd
    df = _pd.read_parquet(CALIBRATION_UNIVERSAL_PATH)
    _calibration_loaded["universal"] = df
    return df


_DTE_BUCKETS = [(0, 3), (3, 7), (7, 14), (14, 30), (30, 60)]
_SPOT_BUCKETS = [(0, 60_000), (60_000, 90_000), (90_000, 120_000),
                 (120_000, 150_000), (150_000, 10_000_000)]
_IVP_BUCKETS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100.0001)]
_STD_DELTAS = (0.05, 0.10, 0.15, 0.25)


def _label_dte(v: float) -> str:
    for lo, hi in _DTE_BUCKETS:
        if lo <= v < hi:
            return f"{int(lo)}-{int(hi)}"
    return "nan"


def _label_spot(v: float) -> str:
    for lo, hi in _SPOT_BUCKETS:
        if lo <= v < hi:
            if hi >= 10_000_000:
                return f"{int(lo / 1000)}k+"
            return f"{int(lo / 1000)}-{int(hi / 1000)}k"
    return "nan"


def _label_ivp(v: float) -> str:
    for lo, hi in _IVP_BUCKETS:
        if lo <= v < hi:
            return f"{int(lo)}-{int(hi)}"
    return "nan"


def _label_delta(v: float) -> str:
    snapped = min(_STD_DELTAS, key=lambda x: abs(x - v))
    return f"{snapped:.2f}"


@router.get("/calibration")
async def get_calibration(
    dte: float = Query(..., description="Days to expiry"),
    spot: float = Query(..., description="Spot price (USD)"),
    delta_target: float = Query(..., description="Target leg delta, e.g. 0.10"),
    ivp: float = Query(..., description="IVP percentile 0-100"),
):
    """Return bucketed calibration stats (median credit%, IV, std, ...).

    If the specific (DTE × spot × delta × IVP) bucket has n_samples < 30, falls
    back to the universal (delta × IVP) curve scaled by sqrt(DTE).
    """
    dte_b = _label_dte(dte)
    spot_b = _label_spot(spot)
    delta_b = _label_delta(delta_target)
    ivp_b = _label_ivp(ivp)

    cache_key = f"{dte_b}|{spot_b}|{delta_b}|{ivp_b}"
    if cache_key in _calibration_cache:
        return _calibration_cache[cache_key]

    spec = _load_calibration_specific()
    if isinstance(spec, str) and spec == "missing":
        raise HTTPException(503, "Calibration not built yet — "
                                  "run `python -m app.analytics.calibration_builder`.")

    hit = spec[(spec["dte_bucket"] == dte_b)
               & (spec["spot_bucket"] == spot_b)
               & (spec["delta_target"] == delta_b)
               & (spec["ivp_bucket"] == ivp_b)]

    response: dict
    if not hit.empty and int(hit.iloc[0]["n_samples"]) >= 30:
        row = hit.iloc[0]
        response = {
            "bucket": {"dte_bucket": dte_b, "spot_bucket": spot_b,
                       "delta_target": delta_b, "ivp_bucket": ivp_b},
            "source": "specific_bucket",
            "n_samples": int(row["n_samples"]),
            "credit_pct_median": float(row["credit_pct_median"]),
            "credit_pct_mean":   float(row["credit_pct_mean"]),
            "credit_pct_std":    float(row["credit_pct_std"]) if row["credit_pct_std"] is not None else 0.0,
            "credit_pct_p25":    float(row["credit_pct_p25"]),
            "credit_pct_p75":    float(row["credit_pct_p75"]),
            "credit_pct_normalized_median": float(row["credit_pct_normalized_median"]),
            "atm_iv_median": float(row["atm_iv_median"]) if row["atm_iv_median"] is not None else None,
            "atm_iv_mean":   float(row["atm_iv_mean"])   if row["atm_iv_mean"]   is not None else None,
            "atm_iv_std":    float(row["atm_iv_std"])    if row["atm_iv_std"]    is not None else None,
            "strangle_iv_median":     float(row["strangle_iv_median"])     if row["strangle_iv_median"] is not None else None,
            "risk_reversal_25d_median": float(row["risk_reversal_25d_median"]) if row["risk_reversal_25d_median"] is not None else None,
            "butterfly_25d_median":   float(row["butterfly_25d_median"])   if row["butterfly_25d_median"] is not None else None,
            "term_slope_7_30_median": float(row["term_slope_7_30_median"]) if row["term_slope_7_30_median"] is not None else None,
            "iv_rv_spread_7d_median": float(row["iv_rv_spread_7d_median"]) if row["iv_rv_spread_7d_median"] is not None else None,
            "pcr_oi_median":          float(row["pcr_oi_median"]) if row["pcr_oi_median"] is not None else None,
            "structural_baseline":    float(row["structural_baseline"]) if not (row["structural_baseline"] is None or _math.isnan(float(row["structural_baseline"]))) else None,
            "pattern_distribution":   json.loads(row["pattern_distribution"]) if isinstance(row["pattern_distribution"], str) else None,
        }
    else:
        # Fall back to universal curve
        univ = _load_calibration_universal()
        if isinstance(univ, str) and univ == "missing":
            raise HTTPException(503, "Calibration universal curve not built yet.")
        u_hit = univ[(univ["delta_target"] == delta_b) & (univ["ivp_bucket"] == ivp_b)]
        if u_hit.empty:
            raise HTTPException(404, f"No calibration data for {cache_key}")
        u_row = u_hit.iloc[0]
        sqrt_dte = _math.sqrt(max(dte, 1e-6))
        # Scale the normalized median back up by sqrt(dte) for display
        scaled_credit_pct = float(u_row["credit_pct_normalized_median"]) * sqrt_dte
        scaled_credit_std = float(u_row["credit_pct_normalized_std"]) * sqrt_dte if u_row["credit_pct_normalized_std"] is not None else 0.0

        # Structural baseline = same delta_target, IVP=40-60, scaled
        struct_hit = univ[(univ["delta_target"] == delta_b) & (univ["ivp_bucket"] == "40-60")]
        struct_pct = (float(struct_hit.iloc[0]["credit_pct_normalized_median"]) * sqrt_dte
                      if not struct_hit.empty else scaled_credit_pct * 0.7)

        response = {
            "bucket": {"dte_bucket": dte_b, "spot_bucket": spot_b,
                       "delta_target": delta_b, "ivp_bucket": ivp_b},
            "source": "universal_fallback",
            "n_samples": int(u_row["n_samples"]),
            "credit_pct_median": scaled_credit_pct,
            "credit_pct_mean":   scaled_credit_pct,
            "credit_pct_std":    scaled_credit_std,
            "credit_pct_p25":    scaled_credit_pct - scaled_credit_std,
            "credit_pct_p75":    scaled_credit_pct + scaled_credit_std,
            "credit_pct_normalized_median": float(u_row["credit_pct_normalized_median"]),
            "atm_iv_median": float(u_row["atm_iv_median"]) if u_row["atm_iv_median"] is not None else None,
            "atm_iv_std":    float(u_row["atm_iv_std"])    if u_row["atm_iv_std"]    is not None else None,
            "atm_iv_mean":   None,
            "strangle_iv_median":     None,
            "risk_reversal_25d_median": None,
            "butterfly_25d_median":   None,
            "term_slope_7_30_median": None,
            "iv_rv_spread_7d_median": None,
            "pcr_oi_median":          None,
            "structural_baseline":    struct_pct,
            "pattern_distribution":   None,
        }

    _calibration_cache[cache_key] = response
    return response


@router.get("/snapshot-context")
async def get_snapshot_context(
    ts: int = Query(..., description="Unix timestamp (seconds)"),
):
    """Return the M3 enriched-snapshot row at-or-before `ts`.

    Selects only the analytics-relevant context columns (IVP, ATM IV, skew,
    pattern, etc.) — not the full ~310-col row.
    """
    if not os.path.exists(FULL_ENRICHED_5M_PATH):
        raise HTTPException(503, "Enriched snapshot table not built yet — "
                                  "run `python -m app.analytics.enrich_derived --rebuild`.")

    cols = [
        "timestamp_unix", "close",
        "atm_iv_7d", "atm_iv_14d", "atm_iv_30d", "atm_iv_60d",
        "ivp_atm_7d_90d", "ivp_atm_14d_90d", "ivp_atm_30d_90d",
        "ivp_4h", "ivp_1d",
        "rv_7d", "rv_14d", "rv_30d",
        "iv_rv_spread_7d", "iv_rv_spread_30d", "iv_rv_ratio_7d",
        "vrp_pct_7d", "vrp_pct_30d",
        "risk_reversal_25d", "butterfly_25d", "wing_atm_ratio",
        "term_slope_7_30",
        "rvp_4h", "rvp_1d",
        "adx_14_4h", "atr_pct_4h", "rsi_14_4h",
        "spot_ret_1d", "spot_ret_7d_5m_base",
        "pcr_oi", "max_oi_call_strike", "max_oi_put_strike",
        "dist_to_call_wall_pct", "dist_to_put_wall_pct",
        "total_gex", "gex_regime", "dist_to_flip_pct",
        "expected_move_1sigma_7d", "expected_move_1sigma_14d",
        "iv_change_stdev_7d", "vov_ratio",
        "ivp_4h_delta_24h", "ivp_4h_delta_48h",
        "pattern",
    ]

    # Read schema once to drop unknown cols (M3 may evolve)
    avail = duckdb.sql(
        f"SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet('{FULL_ENRICHED_5M_PATH}'))"
    ).df()["column_name"].tolist()
    cols = [c for c in cols if c in avail]
    cols_sql = ", ".join(cols)

    res = duckdb.sql(
        f"SELECT {cols_sql} FROM read_parquet('{FULL_ENRICHED_5M_PATH}') "
        f"WHERE timestamp_unix <= {int(ts)} "
        f"ORDER BY timestamp_unix DESC LIMIT 1"
    ).df()

    if res.empty:
        raise HTTPException(404, f"No snapshot at or before ts={ts}")

    row = res.iloc[0].to_dict()
    # Convert numpy scalars to native Python for clean JSON
    for k, v in list(row.items()):
        if v is None:
            continue
        if hasattr(v, "item"):
            try:
                row[k] = v.item()
            except Exception:
                pass
        if isinstance(row[k], float) and (_math.isnan(row[k]) or _math.isinf(row[k])):
            row[k] = None
    return row

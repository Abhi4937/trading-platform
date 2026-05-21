import asyncio
from contextlib import asynccontextmanager
import logging
import traceback
import uuid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from app.api import expiries, options, plot_data, health, logs, ws, historical, backtest, live_signal, m4_results, m7_results, m7_full_coverage, m7_best_combo, m7_friday_band_best_combo, m7_friday_band_results, m7_joint_match_stats, m7_pivot_profile, m_month_results, m_month_best_combo, m9_friday_weekly_results, m9_friday_weekly_best_combo

# Fresh ID per process — frontend uses this to detect a backend restart and
# wipe its auto-persisted UI state on the next page load.
SESSION_ID = uuid.uuid4().hex
from app.cache.redis_cache import init_cache, close_cache
from app.core.config import settings
from app.core.logging_middleware import LoggingMiddleware
from app.services.delta_ws_client import run_delta_ws
from app.services.live_recorder import start_recorder, stop_recorder
from app.services.merge_live_to_main import schedule_loop as merge_schedule_loop
from app.api.historical import _build_strike_index

import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_fmt = logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def _rotating(filename: str, level: int) -> RotatingFileHandler:
    h = RotatingFileHandler(
        os.path.join(LOG_DIR, filename),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    h.setFormatter(_fmt)
    h.setLevel(level)
    return h

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),                            # terminal (all levels)
        _rotating("api.log", logging.INFO),                # all INFO+ logs
        _rotating("errors.log", logging.ERROR),            # ERROR+ only
    ],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Startup — initialising cache...")
    await init_cache()
    await asyncio.get_event_loop().run_in_executor(None, _build_strike_index)
    logger.info("Startup — Strike index built")
    ws_task = None
    merge_task = None
    if not os.environ.get("DISABLE_LIVE_TICKER"):
        ws_task = asyncio.create_task(run_delta_ws())
        logger.info("Startup — Delta WebSocket client started")
        await start_recorder()
        merge_task = asyncio.create_task(merge_schedule_loop())
        logger.info("Startup — live recorder + merge scheduler started")
    else:
        logger.info("Startup — live ticker DISABLED (DISABLE_LIVE_TICKER is set)")
    # M7 best-combo grid: load from disk if present (instant), otherwise leave
    # status='pending'. NEVER auto-spawn a warmup thread inside the backend
    # process — the heavy DuckDB scans hold the GIL and starve request handlers
    # ("TypeError: Failed to fetch" in the browser).
    # To (re)build the grid, run as a one-shot CLI from the host:
    #   docker exec docker-backend-1 python -m app.scripts.build_m7_best_combo_grid
    # That process has its own GIL and doesn't compete with FastAPI.
    if m7_best_combo.try_load_grid_only():
        logger.info("Startup — M7 best-combo grid loaded from disk cache")
    else:
        logger.info("Startup — M7 best-combo grid NOT cached; "
                    "run scripts/build_m7_best_combo_grid.py to build")
    # M7 Friday-band grid: load synchronously from disk (instant — small file).
    # The 600MB per-trade archive used by B1/D1 modes is deferred to first hit
    # (lazy-loaded inside _load_per_trade_archive). To avoid the 30-45s
    # cold-start hit when a user first switches to B1/D1, schedule a
    # background pre-warm of the archive.
    if m7_friday_band_best_combo._load_grid_and_sat_iv():
        logger.info("Startup — M7 Friday-band A1 grid loaded from disk cache")
        # Pre-warm the per-trade archive + scan the auto-persisted D1 chain
        # cache directory in a background thread so the first user click on
        # B1/D1 doesn't pay the 30-45s load cost, and any previously-built
        # chains are usable from in-memory cache immediately.
        def _prewarm():
            try:
                m7_friday_band_best_combo._load_per_trade_archive()
                logger.info("Startup pre-warm — Friday-band per-trade archive loaded")
            except Exception as e:  # noqa: BLE001
                logger.warning("Friday-band per-trade pre-warm failed: %s", e)
            try:
                import os
                cache_dir = m7_friday_band_best_combo.CHAIN_CACHE_DIR
                if os.path.isdir(cache_dir):
                    n_files = sum(1 for _ in os.scandir(cache_dir)
                                  if _.name.endswith(".parquet"))
                    logger.info("Startup — M7 Friday-band chain cache has %d "
                                "saved D1 chains at %s", n_files, cache_dir)
            except Exception as e:  # noqa: BLE001
                logger.warning("Chain cache scan failed: %s", e)
        asyncio.get_event_loop().run_in_executor(None, _prewarm)
    else:
        logger.info("Startup — M7 Friday-band grid NOT cached")
    yield
    if merge_task is not None:
        logger.info("Shutdown — stopping live recorder + merge scheduler...")
        await stop_recorder()
        merge_task.cancel()
        try:
            await merge_task
        except asyncio.CancelledError:
            pass
    if ws_task is not None:
        logger.info("Shutdown — stopping Delta WebSocket...")
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass
    logger.info("Shutdown — closing cache...")
    await close_cache()


app = FastAPI(
    title="BTC Options Chain API",
    description="""
## BTC Options Chain — Delta Exchange

Real-time BTC options with Black-Scholes Greeks computed server-side.

### Endpoints
- **GET /api/v1/expiries** — available expiry dates
- **GET /api/v1/options** — full option chain for an expiry
- **GET /api/v1/plot-data** — chart data (premium OHLCV, IV smile, IV vs RV)
- **GET /api/v1/spot** — current BTC spot price
- **GET /health** — liveness check
""",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(LoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception  %s %s\n%s",
        request.method,
        request.url,
        traceback.format_exc(),
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/api/v1/session-id", tags=["Health"])
def get_session_id():
    """UUID generated at process startup. Frontend compares against its
    locally-stored copy to detect a backend restart; on mismatch it wipes the
    auto-persisted UI state. Named saved strategies are unaffected."""
    return {"session_id": SESSION_ID}


app.include_router(health.router, tags=["Health"])
app.include_router(expiries.router, prefix="/api/v1", tags=["Expiries"])
app.include_router(options.router,  prefix="/api/v1", tags=["Options Chain"])
app.include_router(plot_data.router, prefix="/api/v1", tags=["Plot Data"])
app.include_router(logs.router,     prefix="/api/v1", tags=["Logs"])
app.include_router(ws.router,       tags=["WebSocket"])
app.include_router(historical.router, prefix="/api/v1/historical", tags=["Historical"])
app.include_router(backtest.router,   prefix="/api/v1/historical", tags=["Backtest"])
app.include_router(live_signal.router, prefix="/api/v1/live-signal", tags=["Live Signal"])
app.include_router(m4_results.router,  prefix="/api/v1/m4",           tags=["M4 Results"])
app.include_router(m7_results.router,  prefix="/api/v1/m7",           tags=["M7 Sweep"])
app.include_router(m7_full_coverage.router, prefix="/api/v1/m7",       tags=["M7 Sweep"])
app.include_router(m7_best_combo.router,    prefix="/api/v1/m7",       tags=["M7 Sweep"])
app.include_router(m7_friday_band_best_combo.router, prefix="/api/v1/m7", tags=["M7 Sweep"])
app.include_router(m7_friday_band_results.router, prefix="/api/v1/m7", tags=["M7 Friday-Band"])
app.include_router(m7_joint_match_stats.router,   prefix="/api/v1/m7",       tags=["M7 Joint Match"])
app.include_router(m7_pivot_profile.router,       prefix="/api/v1/m7",       tags=["M7 Pivot Profile"])
app.include_router(m_month_results.router,    prefix="/api/v1/m_month",  tags=["M-Month Sweep"])
app.include_router(m_month_best_combo.router, prefix="/api/v1/m_month",  tags=["M-Month Sweep"])
app.include_router(m9_friday_weekly_results.router,    prefix="/api/v1/m9", tags=["M9 Friday Weekly"])
app.include_router(m9_friday_weekly_best_combo.router, prefix="/api/v1/m9", tags=["M9 Friday Weekly"])

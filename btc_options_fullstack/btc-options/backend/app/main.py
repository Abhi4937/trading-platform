import asyncio
from contextlib import asynccontextmanager
import logging
import traceback
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from app.api import expiries, options, plot_data, health, logs, ws, historical
from app.db import connection as db_connection, recorder as db_recorder
from app.cache.redis_cache import init_cache, close_cache
from app.core.config import settings
from app.core.logging_middleware import LoggingMiddleware
from app.services.delta_ws_client import run_delta_ws

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
    db_ok = await db_connection.init_db()
    db_recorder.set_enabled(db_ok)
    recorder_task = await db_recorder.start_recorder() if db_ok else None
    ws_task = asyncio.create_task(run_delta_ws())
    logger.info("Startup — Delta WebSocket client started")
    yield
    logger.info("Shutdown — stopping Delta WebSocket...")
    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass
    if recorder_task:
        recorder_task.cancel()
    await db_connection.close_db()
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


app.include_router(health.router, tags=["Health"])
app.include_router(expiries.router, prefix="/api/v1", tags=["Expiries"])
app.include_router(options.router,  prefix="/api/v1", tags=["Options Chain"])
app.include_router(plot_data.router, prefix="/api/v1", tags=["Plot Data"])
app.include_router(logs.router,     prefix="/api/v1", tags=["Logs"])
app.include_router(ws.router,       tags=["WebSocket"])
app.include_router(historical.router, prefix="/api/v1", tags=["Historical"])

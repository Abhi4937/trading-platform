from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.api import expiries, options, plot_data, health
from app.cache.redis_cache import init_cache, close_cache
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Startup — initialising cache...")
    await init_cache()
    yield
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(health.router, tags=["Health"])
app.include_router(expiries.router, prefix="/api/v1", tags=["Expiries"])
app.include_router(options.router,  prefix="/api/v1", tags=["Options Chain"])
app.include_router(plot_data.router, prefix="/api/v1", tags=["Plot Data"])

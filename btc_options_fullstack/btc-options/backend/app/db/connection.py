import asyncpg
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_db() -> bool:
    """Connect to TimescaleDB and create schema. Returns True on success."""
    global _pool
    if not settings.TIMESCALE_RECORD:
        logger.info("TimescaleDB: recording disabled (TIMESCALE_RECORD=False)")
        return False
    try:
        _pool = await asyncpg.create_pool(
            settings.TIMESCALE_URL, min_size=2, max_size=10, command_timeout=10
        )
        await _create_schema()
        logger.info("TimescaleDB: connected and schema ready")
        return True
    except Exception as e:
        logger.warning("TimescaleDB: could not connect (%s) — historical recording disabled", e)
        _pool = None
        return False


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool | None:
    return _pool


async def _create_schema() -> None:
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS option_ticks (
                time         TIMESTAMPTZ NOT NULL,
                symbol       TEXT NOT NULL,
                strike       DOUBLE PRECISION NOT NULL,
                option_type  TEXT NOT NULL,
                expiry       DATE NOT NULL,
                mark_price   DOUBLE PRECISION,
                bid          DOUBLE PRECISION,
                ask          DOUBLE PRECISION,
                iv           DOUBLE PRECISION,
                oi_contracts INTEGER,
                oi_usd       DOUBLE PRECISION,
                volume       INTEGER,
                volume_usd   DOUBLE PRECISION,
                spot_price   DOUBLE PRECISION
            )
        """)
        # Enable TimescaleDB hypertable (silently skips if already exists or extension missing)
        try:
            await conn.execute(
                "SELECT create_hypertable('option_ticks','time',if_not_exists=>TRUE)"
            )
        except Exception:
            pass
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_opt_symbol_time ON option_ticks (symbol, time DESC)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_opt_expiry_time ON option_ticks (expiry, time DESC)
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS spot_ticks (
                time  TIMESTAMPTZ NOT NULL,
                price DOUBLE PRECISION NOT NULL
            )
        """)
        try:
            await conn.execute(
                "SELECT create_hypertable('spot_ticks','time',if_not_exists=>TRUE)"
            )
        except Exception:
            pass

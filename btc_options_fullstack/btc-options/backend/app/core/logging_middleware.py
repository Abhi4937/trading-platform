import logging
import os
import time
from logging.handlers import RotatingFileHandler

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("api")

# ── Slow-request logger ──────────────────────────────────────────────────────
# Any request that completes in > SLOW_REQUEST_THRESHOLD_MS gets a WARNING line
# written to slow_requests.log (alongside the normal INFO line). Helps identify
# endpoints that block the uvicorn event loop and cause the frontend to hang.
# Persists across backend restarts via the shared logs directory.

SLOW_REQUEST_THRESHOLD_MS = float(os.environ.get("SLOW_REQUEST_THRESHOLD_MS", "2000"))

_SLOW_LOG_DIR = "/home/abhis/btc-data/logs"
_SLOW_LOG_PATH = os.path.join(_SLOW_LOG_DIR, "slow_requests.log")

slow_logger = logging.getLogger("api.slow")
slow_logger.setLevel(logging.WARNING)
slow_logger.propagate = False  # don't double-log to the root "api" logger

try:
    os.makedirs(_SLOW_LOG_DIR, exist_ok=True)
    if not any(
        isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == _SLOW_LOG_PATH
        for h in slow_logger.handlers
    ):
        handler = RotatingFileHandler(_SLOW_LOG_PATH, maxBytes=5_000_000, backupCount=3)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        slow_logger.addHandler(handler)
except (OSError, PermissionError) as e:
    # Logging is best-effort; if the dir isn't writable, fall back to stderr-only
    logger.warning("slow_requests log file unavailable (%s) — slow requests will print to stderr only", e)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start) * 1000
        client_ip = request.client.host if request.client else "-"
        query = f"?{request.url.query}" if request.url.query else ""

        logger.info(
            "%s %s%s %s %.1fms %s",
            request.method,
            request.url.path,
            query,
            response.status_code,
            duration_ms,
            client_ip,
        )

        # Slow-request alert: same line shape but only emitted when over threshold.
        # Goes to slow_requests.log via the slow_logger above + WARNING on stderr.
        if duration_ms >= SLOW_REQUEST_THRESHOLD_MS:
            slow_logger.warning(
                "SLOW  %s %s%s  %s  %.1fms  %s",
                request.method,
                request.url.path,
                query,
                response.status_code,
                duration_ms,
                client_ip,
            )

        return response

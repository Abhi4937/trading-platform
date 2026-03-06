import logging
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("api")


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

        return response

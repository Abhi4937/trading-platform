import asyncio
import logging
import time
from datetime import date

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.services import ticker_store
from app.services.option_chain_service import get_option_chain, get_option_chain_from_store

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/chain")
async def chain_ws(websocket: WebSocket, expiry: str = Query(...)):
    await websocket.accept()

    try:
        expiry_date = date.fromisoformat(expiry)
    except ValueError:
        await websocket.close(code=1008, reason="Invalid expiry date")
        return

    logger.info("WS CONNECT   expiry=%s  client=%s", expiry, websocket.client)
    last_push = time.perf_counter()
    push_count = 0

    try:
        while True:
            t0 = time.perf_counter()
            if ticker_store.has_data():
                chain = await get_option_chain_from_store(expiry_date)
                source = "store"
            else:
                chain = await get_option_chain(expiry_date)
                source = "rest"

            await websocket.send_text(chain.model_dump_json())

            now = time.perf_counter()
            elapsed_since_last = (now - last_push) * 1000
            compute_ms = (now - t0) * 1000
            push_count += 1
            last_push = now

            # Log every 10th push to avoid flooding logs
            if push_count % 10 == 0:
                logger.info(
                    "WS PUSH      expiry=%s  source=%-5s  compute=%.1fms  interval=%.0fms  push#%d",
                    expiry, source, compute_ms, elapsed_since_last, push_count,
                )

            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        logger.info("WS DISCONNECT expiry=%s  total_pushes=%d", expiry, push_count)
    except Exception as e:
        logger.error("WS ERROR     expiry=%s  error=%s", expiry, e)

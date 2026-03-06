import asyncio
import logging
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

    logger.info("WS client connected for expiry %s", expiry)
    try:
        while True:
            if ticker_store.has_data():
                chain = await get_option_chain_from_store(expiry_date)
            else:
                chain = await get_option_chain(expiry_date)

            await websocket.send_text(chain.model_dump_json())
            await asyncio.sleep(0.2)   # push every 200ms
    except WebSocketDisconnect:
        logger.info("WS client disconnected for expiry %s", expiry)
    except Exception as e:
        logger.error("WS chain error for %s: %s", expiry, e)

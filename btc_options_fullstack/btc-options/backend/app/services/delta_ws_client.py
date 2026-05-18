import asyncio
import json
import logging

import websockets

from app.services import ticker_store
from app.services.delta_client import get_delta_client

logger = logging.getLogger(__name__)

WS_URL = "wss://socket.india.delta.exchange"

async def _periodic_product_refresh(ws: websockets.WebSocketClientProtocol, symbols_set: set[str]) -> None:
    """Background task to fetch full product list every 30 mins and subscribe to new symbols."""
    client = get_delta_client()
    while True:
        try:
            # Wait 30 minutes between full refreshes
            await asyncio.sleep(1800)
            
            logger.info("Starting background full product fetch...")
            full_products = await client.get_btc_option_products(max_pages=20)
            ticker_store.set_products(full_products)
            
            new_syms = [
                p["symbol"] for p in full_products
                if p.get("symbol") and p["symbol"] not in symbols_set
            ]
            
            if new_syms and ws.open:
                logger.info("Delta WS: subscribing to %d new symbols found in background fetch", len(new_syms))
                for i in range(0, len(new_syms), 100):
                    batch = new_syms[i : i + 100]
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "payload": {"channels": [{"name": "v2/ticker", "symbols": batch}]},
                    }))
                symbols_set.update(new_syms)
                
            logger.info("Background product refresh complete: %d total products", len(full_products))
        except Exception as e:
            logger.error("Background product refresh failed: %s", e)

async def run_delta_ws() -> None:
    """Persistent loop: connect to Delta WS, subscribe to all BTC option tickers."""
    client = get_delta_client()
    
    while True:
        try:
            # 1. Fast initial load (first 2 pages only) to get UI up in < 2s
            if not ticker_store.get_products():
                try:
                    initial_products = await client.get_btc_option_products(max_pages=2)
                    ticker_store.set_products(initial_products)
                    logger.info("Fast initial load complete: %d products registered", len(initial_products))
                except Exception as e:
                    logger.error("Fast initial load failed: %s", e)

            products = ticker_store.get_products()
            if not products:
                await asyncio.sleep(1)
                continue

            symbols = [p["symbol"] for p in products]
            symbols_set = set(symbols)
            symbols_with_spot = symbols + ["BTCUSDT"]

            logger.info("Delta WS: connecting, %d option symbols", len(symbols))

            # Seed spot price immediately
            try:
                spot = await client.get_spot_price()
                if spot > 0:
                    ticker_store.update_spot(spot)
                    logger.info("Delta WS: seeded spot=%.2f", spot)
            except Exception as e:
                logger.warning("Delta WS: spot seed failed: %s", e)

            async with websockets.connect(
                WS_URL,
                ping_interval=30,
                ping_timeout=10,
                max_size=2 ** 22,
            ) as ws:
                ticker_store.set_connected(True)
                logger.info("Delta WS: connected")

                # Subscribe in batches of 100
                for i in range(0, len(symbols_with_spot), 100):
                    batch = symbols_with_spot[i : i + 100]
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "payload": {"channels": [{"name": "v2/ticker", "symbols": batch}]},
                    }))

                # Start the background refresh task for this connection
                refresh_task = asyncio.create_task(_periodic_product_refresh(ws, symbols_set))

                try:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        _handle_message(msg)
                finally:
                    refresh_task.cancel()

        except Exception as e:
            ticker_store.set_connected(False)
            logger.error("Delta WS disconnected: %s — retrying in 5s", e)
            await asyncio.sleep(5)


_first_ticker_logged = False

def _handle_message(msg: dict) -> None:
    global _first_ticker_logged
    if msg.get("type") != "v2/ticker":
        return

    symbol = msg.get("symbol", "")
    if not symbol:
        return

    if symbol == "BTCUSDT":
        price = float(msg.get("close") or msg.get("mark_price") or 0)
        if price > 0:
            ticker_store.update_spot(price)
    else:
        ticker_store.update_ticker(symbol, msg)
        if not _first_ticker_logged:
            _first_ticker_logged = True
            logger.info("ticker_store: first ticker stored — symbol=%s tickers=%d", symbol, len(ticker_store._tickers))

import asyncio
import json
import logging

import websockets

from app.services import ticker_store
from app.services.delta_client import get_delta_client
from app.db import recorder

logger = logging.getLogger(__name__)

WS_URL = "wss://socket.india.delta.exchange"


async def run_delta_ws() -> None:
    """Persistent loop: connect to Delta WS, subscribe to all BTC option tickers."""
    while True:
        try:
            client = get_delta_client()
            products = await client.get_btc_option_products()
            ticker_store.set_products(products)
            symbols = [p["symbol"] for p in products if p.get("symbol")]
            symbols_set = set(symbols)
            # Include spot ticker
            symbols_with_spot = symbols + ["BTCUSDT"]

            logger.info("Delta WS: connecting, %d option symbols", len(symbols))

            # Seed spot price via REST immediately so has_data() returns True
            # as soon as first option tickers arrive — don't wait for BTCUSDT WS push
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

                # Subscribe in batches of 100 (WS message size limit)
                for i in range(0, len(symbols_with_spot), 100):
                    batch = symbols_with_spot[i : i + 100]
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "payload": {"channels": [{"name": "v2/ticker", "symbols": batch}]},
                    }))

                last_refresh = asyncio.get_event_loop().time()

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    _handle_message(msg)

                    # Refresh product list every 5 min to pick up new expiries
                    if asyncio.get_event_loop().time() - last_refresh > 3600:
                        last_refresh = asyncio.get_event_loop().time()
                        new_products = await client.get_btc_option_products()
                        ticker_store.set_products(new_products)
                        new_syms = [
                            p["symbol"] for p in new_products
                            if p.get("symbol") and p["symbol"] not in symbols_set
                        ]
                        if new_syms:
                            for i in range(0, len(new_syms), 100):
                                await ws.send(json.dumps({
                                    "type": "subscribe",
                                    "payload": {"channels": [{"name": "v2/ticker", "symbols": new_syms[i : i + 100]}]},
                                }))
                            symbols_set.update(new_syms)
                            logger.info("Delta WS: subscribed to %d new symbols", len(new_syms))

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
            recorder.record_spot(price)
    else:
        ticker_store.update_ticker(symbol, msg)
        recorder.record_ticker(symbol, msg, ticker_store.get_spot())
        if not _first_ticker_logged:
            _first_ticker_logged = True
            logger.info("ticker_store: first ticker stored — symbol=%s tickers=%d", symbol, len(ticker_store._tickers))

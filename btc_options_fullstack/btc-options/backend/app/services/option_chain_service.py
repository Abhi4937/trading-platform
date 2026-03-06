import asyncio
import logging
import math
from datetime import date, datetime, timezone
from typing import Optional

from app.cache import redis_cache
from app.core.config import settings
from app.core.greeks import compute_greeks, implied_vol
from app.models.models import (
    ChainRow, ExpiryInfo, ExpiryListResponse,
    OptionChainResponse, OptionLeg, SpotResponse,
)
from app.services.delta_client import get_delta_client

logger = logging.getLogger(__name__)


def _years_to_expiry(expiry_dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    if expiry_dt.tzinfo is None:
        expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
    return max(0.0, (expiry_dt - now).total_seconds() / (365 * 24 * 3600))


def _parse_settlement(product: dict) -> Optional[datetime]:
    raw = product.get("settlement_time") or product.get("expiry_time")
    if not raw:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


async def get_spot() -> SpotResponse:
    cached = await redis_cache.get("spot:BTC")
    if cached:
        return SpotResponse(**cached)
    client = get_delta_client()
    price = await client.get_spot_price()
    resp = SpotResponse(symbol="BTCUSD", price=price, fetched_at=datetime.now(timezone.utc))
    await redis_cache.set("spot:BTC", resp.model_dump(), settings.CACHE_TTL_SPOT)
    return resp


async def get_expiries() -> ExpiryListResponse:
    cached = await redis_cache.get("expiries:BTC")
    if cached:
        return ExpiryListResponse(**cached)

    client = get_delta_client()
    spot_task = asyncio.create_task(client.get_spot_price())
    prods_task = asyncio.create_task(client.get_btc_option_products())
    spot, products = await asyncio.gather(spot_task, prods_task)

    seen = {}
    for p in products:
        dt = _parse_settlement(p)
        if not dt:
            continue
        d = dt.date()
        if d not in seen:
            seen[d] = dt

    today = date.today()
    expiry_list = []
    for d in sorted(seen.keys()):
        if d <= today:
            continue
        days = (d - today).days
        label = d.strftime("%d %b %Y").upper()
        expiry_list.append(ExpiryInfo(date=d, label=f"{label} ({days}d)", days=days))

    resp = ExpiryListResponse(underlying="BTC", spot_price=spot, expiries=expiry_list)
    await redis_cache.set("expiries:BTC", resp.model_dump(), settings.CACHE_TTL_EXPIRIES)
    return resp


async def get_option_chain(expiry: date) -> OptionChainResponse:
    cache_key = f"chain:BTC:{expiry}"
    cached = await redis_cache.get(cache_key)
    if cached:
        return OptionChainResponse(**cached)

    client = get_delta_client()
    spot_task = asyncio.create_task(client.get_spot_price())
    prods_task = asyncio.create_task(client.get_btc_option_products())
    spot, all_products = await asyncio.gather(spot_task, prods_task)

    # Filter to this expiry
    exp_products = []
    for p in all_products:
        dt = _parse_settlement(p)
        if dt and dt.date() == expiry:
            exp_products.append(p)

    if not exp_products:
        # Demo mode: generate synthetic chain
        return _build_demo_chain(expiry, spot)

    T = _years_to_expiry(
        datetime.combine(expiry, datetime.min.time()).replace(tzinfo=timezone.utc).replace(hour=8)
    )
    r = settings.RISK_FREE_RATE

    # Collect unique strikes
    strikes = sorted({float(p.get("strike_price", 0)) for p in exp_products if p.get("strike_price")})
    atm = min(strikes, key=lambda k: abs(k - spot))

    # Fetch tickers concurrently (batch of 20 at a time to respect rate limits)
    product_map: dict[tuple[float, str], dict] = {}
    for p in exp_products:
        k = float(p.get("strike_price", 0))
        ct = "call" if "call" in p.get("contract_type", "") else "put"
        product_map[(k, ct)] = p

    async def fetch_leg(strike: float, opt_type: str) -> tuple[float, str, dict, dict]:
        prod = product_map.get((strike, opt_type))
        if not prod:
            return strike, opt_type, {}, {}
        try:
            ticker = await client.get_ticker(prod["symbol"])
            return strike, opt_type, prod, ticker
        except Exception as e:
            logger.warning("Ticker fetch failed %s: %s", prod.get("symbol"), e)
            return strike, opt_type, prod, {}

    tasks = [fetch_leg(s, t) for s in strikes for t in ("call", "put")]
    # Batch into groups of 20
    results = []
    for i in range(0, len(tasks), 20):
        batch = await asyncio.gather(*tasks[i:i+20])
        results.extend(batch)

    # Build chain rows
    leg_map: dict[tuple[float, str], OptionLeg] = {}
    for strike, opt_type, prod, ticker in results:
        if not prod:
            continue
        quotes     = ticker.get("quotes") or {}
        mark_price = float(ticker.get("mark_price") or 0)
        bid  = float(quotes.get("best_bid") or ticker.get("best_bid") or 0)
        ask  = float(quotes.get("best_ask") or ticker.get("best_ask") or 0)
        mid  = (bid + ask) / 2 if bid and ask else mark_price
        oi   = int(ticker.get("oi_contracts") or 0)
        vol  = int(ticker.get("volume") or 0)

        # IV: use mark_price (exchange fair value) for Newton-Raphson
        # mark_iv from quotes is decimal (e.g. 0.65 = 65%), use as sanity check
        raw_iv = quotes.get("mark_iv") or ticker.get("mark_vol")
        if raw_iv:
            iv = float(raw_iv)  # already decimal
        else:
            iv = implied_vol(mark_price, spot, strike, T, r, opt_type) if mark_price > 0 else 0.5

        g = compute_greeks(spot, strike, T, r, iv if iv > 0 else 0.5, opt_type)

        leg_map[(strike, opt_type)] = OptionLeg(
            strike=strike, expiry=expiry, option_type=opt_type,
            symbol=prod.get("symbol", ""),
            last_price=round(mark_price, 2), bid=round(bid, 2),
            ask=round(ask, 2), mid=round(mid, 2),
            volume=vol, open_interest=oi,
            iv=round(iv, 6), iv_pct=round(iv * 100, 2),
            delta=g.delta, gamma=g.gamma, theta=g.theta,
            vega=g.vega, rho=g.rho, price_bs=g.price_bs,
            underlying_price=round(spot, 2),
            days_to_expiry=round(T * 365, 1),
            is_atm=(strike == atm),
        )

    chain = []
    for s in strikes:
        call_leg = leg_map.get((s, "call"))
        put_leg  = leg_map.get((s, "put"))
        chain.append(ChainRow(strike=s, call=call_leg, put=put_leg, is_atm=(s == atm)))

    atm_call = leg_map.get((atm, "call"))
    atm_put  = leg_map.get((atm, "put"))
    resp = OptionChainResponse(
        expiry=expiry, underlying="BTC", spot_price=round(spot, 2),
        atm_strike=atm, days_to_expiry=round(T * 365, 1),
        atm_iv_call=atm_call.iv_pct if atm_call else 0.0,
        atm_iv_put=atm_put.iv_pct  if atm_put  else 0.0,
        chain=chain,
        fetched_at=datetime.now(timezone.utc),
    )
    await redis_cache.set(cache_key, resp.model_dump(), settings.CACHE_TTL_CHAIN)
    return resp


def _build_demo_chain(expiry: date, spot: float) -> OptionChainResponse:
    import math, random
    from datetime import timezone
    T = max(0.01, (datetime.combine(expiry, datetime.min.time(), timezone.utc).replace(hour=8) - datetime.now(timezone.utc)).total_seconds() / (365*24*3600))
    r = settings.RISK_FREE_RATE
    atm = round(spot / 1000) * 1000
    strikes = [atm + i * 1000 for i in range(-10, 11)]
    chain = []
    for s in strikes:
        mon = math.log(s / spot)
        base_iv = 0.65 + 0.15 * mon * mon - 0.05 * mon
        call_iv = max(0.15, base_iv + random.uniform(-0.02, 0.02))
        put_iv  = max(0.15, base_iv + 0.03 + random.uniform(-0.02, 0.02))
        cg = compute_greeks(spot, s, T, r, call_iv, "call")
        pg = compute_greeks(spot, s, T, r, put_iv,  "put")
        oi = int(1000 * math.exp(-2 * mon * mon))
        call_leg = OptionLeg(
            strike=s, expiry=expiry, option_type="call", symbol=f"BTC-DEMO-{s}-C",
            last_price=round(cg.price_bs, 2), bid=round(cg.price_bs*0.98, 2), ask=round(cg.price_bs*1.02, 2),
            mid=round(cg.price_bs, 2), volume=oi//3, open_interest=oi,
            iv=round(call_iv, 6), iv_pct=round(call_iv*100, 2),
            delta=cg.delta, gamma=cg.gamma, theta=cg.theta, vega=cg.vega, rho=cg.rho, price_bs=cg.price_bs,
            underlying_price=round(spot, 2), days_to_expiry=round(T*365, 1), is_atm=(s==atm),
        )
        put_leg = OptionLeg(
            strike=s, expiry=expiry, option_type="put", symbol=f"BTC-DEMO-{s}-P",
            last_price=round(pg.price_bs, 2), bid=round(pg.price_bs*0.98, 2), ask=round(pg.price_bs*1.02, 2),
            mid=round(pg.price_bs, 2), volume=oi//4, open_interest=int(oi*0.9),
            iv=round(put_iv, 6), iv_pct=round(put_iv*100, 2),
            delta=pg.delta, gamma=pg.gamma, theta=pg.theta, vega=pg.vega, rho=pg.rho, price_bs=pg.price_bs,
            underlying_price=round(spot, 2), days_to_expiry=round(T*365, 1), is_atm=(s==atm),
        )
        chain.append(ChainRow(strike=s, call=call_leg, put=put_leg, is_atm=(s==atm)))
    return OptionChainResponse(
        expiry=expiry, underlying="BTC", spot_price=round(spot,2), atm_strike=atm,
        days_to_expiry=round(T*365,1), atm_iv_call=65.0, atm_iv_put=68.0,
        chain=chain, fetched_at=datetime.now(timezone.utc),
    )

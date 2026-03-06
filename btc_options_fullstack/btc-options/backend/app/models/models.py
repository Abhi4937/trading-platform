from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class OptionLeg(BaseModel):
    strike: float
    expiry: date
    option_type: str           # "call" | "put"
    symbol: str
    last_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    volume: int = 0
    open_interest: int = 0
    iv: float = 0.0            # annualised decimal
    iv_pct: float = 0.0        # iv * 100 for display
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    price_bs: float = 0.0
    underlying_price: float = 0.0
    days_to_expiry: float = 0.0
    is_atm: bool = False


class ChainRow(BaseModel):
    strike: float
    call: Optional[OptionLeg] = None
    put: Optional[OptionLeg] = None
    is_atm: bool = False


class OptionChainResponse(BaseModel):
    expiry: date
    underlying: str = "BTC"
    spot_price: float
    atm_strike: float
    days_to_expiry: float
    atm_iv_call: float = 0.0
    atm_iv_put: float = 0.0
    chain: list[ChainRow]
    fetched_at: datetime


class ExpiryInfo(BaseModel):
    date: date
    label: str
    days: int


class ExpiryListResponse(BaseModel):
    underlying: str
    spot_price: float
    expiries: list[ExpiryInfo]


class SpotResponse(BaseModel):
    symbol: str
    price: float
    fetched_at: datetime


class CandleBar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class PremiumChartResponse(BaseModel):
    symbol: str
    strike: float
    option_type: str
    expiry: date
    timeframe: str
    candles: list[CandleBar]


class IVSmilePoint(BaseModel):
    strike: float
    call_iv: float
    put_iv: float
    delta: float
    moneyness: float    # log(K/S)


class IVSmileResponse(BaseModel):
    expiry: date
    spot_price: float
    atm_strike: float
    atm_iv: float
    points: list[IVSmilePoint]


class IVRVPoint(BaseModel):
    label: str          # date string for x-axis
    implied_vol: float  # %
    realised_vol: float # %


class IVRVResponse(BaseModel):
    expiry: date
    window_days: int
    atm_strike: float
    series: list[IVRVPoint]


class ErrorResponse(BaseModel):
    error: str
    detail: str = ""

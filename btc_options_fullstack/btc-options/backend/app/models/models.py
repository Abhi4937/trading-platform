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
    volume_usd: float = 0.0
    open_interest: int = 0
    oi_usd: float = 0.0
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


# ============================================================
# Vol Analytics panel (Historical Dashboard) — point-in-time snapshot of the
# vendored RV/IV engine (app/services/vol/*). All vol numbers are PERCENT.
# ============================================================
class VolSignal(BaseModel):
    level: str            # rich | mildly_rich | fair | mildly_cheap | cheap | unknown
    action: str           # plain-English verdict
    color: str            # red | orange | yellow | light_green | green | gray


class VolHeader(BaseModel):
    spot: float
    atm_strike: int
    atm_iv_call: float    # %
    atm_iv_put: float     # %
    atm_iv_avg: float     # %
    dte_hours: float
    primary_lookback: int            # which lookback the primary ratio uses (e.g. 14)
    primary_estimator: str           # recommended estimator name
    primary_ratio: float             # IV / RV at the primary cell
    signal: VolSignal
    regime_label: str                # short label e.g. "chop" / "trend"
    ts_used: int = 0                 # unix secs the figures actually come from
    snapped: bool = False            # True when ts_used != requested timestamp (nearest-IV fallback)


class RVGridRow(BaseModel):
    lookback: int                    # days
    cc: Optional[float] = None       # close_to_close RV, %
    co: Optional[float] = None       # close_open RV, %
    parkinson: Optional[float] = None
    gk: Optional[float] = None       # garman_klass
    rs: Optional[float] = None       # rogers_satchell


class RatioGridRow(BaseModel):
    lookback: int
    cc: Optional[float] = None       # IV / RV(cc)
    co: Optional[float] = None
    parkinson: Optional[float] = None
    gk: Optional[float] = None
    rs: Optional[float] = None


class VolRegime(BaseModel):
    chop_ratio: float
    trend_intensity: float           # decimal (e.g. 0.32 = 32%)
    chop_label: str
    trend_label: str
    recommended_estimator: str
    rationale: str


class VolTermStructure(BaseModel):
    shape: str                       # contango | inverted | flat | mild_slope | ...
    short_rv: Optional[float] = None  # %
    long_rv: Optional[float] = None   # %
    diff_pct: Optional[float] = None  # decimal
    interpretation: str = ""


class VolFriSat(BaseModel):
    window_count: int
    median_range_pct: Optional[float] = None   # decimal fraction
    median_co_pct: Optional[float] = None      # decimal fraction
    median_move_usd: Optional[float] = None    # median co move in $ at current spot
    annualized_range_vol: Optional[float] = None  # %, RMS-based true sigma
    annualized_co_vol: Optional[float] = None     # %, RMS-based true sigma
    window_iv_rv: Optional[float] = None       # IV / annualized_co_vol
    hold_hours: float = 12.0


class VolSLTier(BaseModel):
    pct: float                       # decimal fraction
    dollars: float


class VolSL(BaseModel):
    tight: VolSLTier
    moderate: VolSLTier
    conservative: VolSLTier
    one_sigma_pct: Optional[float] = None
    one_sigma_dollars: Optional[float] = None
    min_reasonable_sl_pct: Optional[float] = None
    min_reasonable_sl_dollars: Optional[float] = None
    hold_hours: float = 12.0


class VolGammaTheta(BaseModel):
    gamma: float
    theta_per_day: float
    sigma_realized: float            # %, the RV fed into the comparison
    expected_daily_move_1sd: float   # $
    gamma_pnl_per_day_1sd: float     # $
    theta_pnl_per_day: float         # $
    ratio: float
    verdict: str
    breakeven_move_per_day: float    # $


class VolAnalyticsResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    expiry: str
    timestamp: int
    header: VolHeader
    rv_grid: list[RVGridRow] = []
    ratio_grid: list[RatioGridRow] = []
    regime: Optional[VolRegime] = None
    term_structure: Optional[VolTermStructure] = None
    fri_sat: Optional[VolFriSat] = None
    sl: Optional[VolSL] = None
    gamma_theta: Optional[VolGammaTheta] = None

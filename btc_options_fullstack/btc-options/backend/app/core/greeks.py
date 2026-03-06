import math
from dataclasses import dataclass
from typing import Literal


def _norm_cdf(x: float) -> float:
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = -1 if x < 0 else 1
    x = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5*t+a4)*t)+a3)*t+a2)*t+a1)*t*math.exp(-x*x)
    return 0.5*(1.0+sign*y)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5*x*x) / math.sqrt(2*math.pi)


@dataclass(slots=True)
class Greeks:
    iv: float          # annualised decimal e.g. 0.65
    delta: float       # call [0,1] / put [-1,0]
    gamma: float
    theta: float       # per calendar day
    vega: float        # per 1% IV move
    rho: float
    price_bs: float    # theoretical price


def compute_greeks(S, K, T, r, sigma, option_type: Literal["call","put"]) -> Greeks:
    if T <= 1e-6 or sigma <= 1e-6 or S <= 0 or K <= 0:
        intrinsic = max(0.0,S-K) if option_type=="call" else max(0.0,K-S)
        return Greeks(iv=sigma,delta=0.0,gamma=0.0,theta=0.0,vega=0.0,rho=0.0,price_bs=intrinsic)
    sq = math.sqrt(T)
    d1 = (math.log(S/K)+(r+0.5*sigma**2)*T)/(sigma*sq)
    d2 = d1-sigma*sq
    N1,N2 = _norm_cdf(d1),_norm_cdf(d2)
    N1n,N2n = _norm_cdf(-d1),_norm_cdf(-d2)
    n1 = _norm_pdf(d1)
    disc = math.exp(-r*T)
    if option_type=="call":
        price=S*N1-K*disc*N2; delta=N1; rho=K*T*disc*N2/100
    else:
        price=K*disc*N2n-S*N1n; delta=N1-1.0; rho=-K*T*disc*N2n/100
    gamma=n1/(S*sigma*sq)
    vega=S*n1*sq/100
    theta=(-(S*n1*sigma)/(2*sq)-r*K*disc*(N2 if option_type=="call" else N2n))/365
    return Greeks(iv=sigma,delta=round(delta,6),gamma=round(gamma,8),
                  theta=round(theta,4),vega=round(vega,4),rho=round(rho,4),
                  price_bs=round(max(0.0,price),4))


def implied_vol(market_price, S, K, T, r, option_type, max_iter=200, tol=1e-7) -> float:
    if T<=1e-6 or market_price<=0: return 0.0
    sigma = math.sqrt(2*math.pi/T)*market_price/S
    sigma = max(0.01, min(sigma, 5.0))
    for _ in range(max_iter):
        g = compute_greeks(S,K,T,r,sigma,option_type)
        diff = g.price_bs-market_price
        if abs(diff)<tol: return round(sigma,6)
        vf = g.vega*100
        if abs(vf)<1e-12: break
        sigma -= diff/vf
        if sigma<=1e-4: sigma=1e-4
        elif sigma>10.0: sigma=10.0
    return round(sigma,6) if 0<sigma<10 else 0.0

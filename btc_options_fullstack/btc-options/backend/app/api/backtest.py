"""
Backtest API router — async-job + polling pattern.

Endpoints (all under /api/v1/historical via main.py):
    POST   /backtest          — submit a job, returns {job_id, status}
    GET    /backtest/{job_id} — poll status + (when done) result
    DELETE /backtest/{job_id} — cancel a running job
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import backtest as backtest_service
from app.services import backtest_jobs

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request/response schemas ──────────────────────────────────────────────────

class LegSlConfig(BaseModel):
    type: Literal["pct", "points", "delta"] = "pct"
    value: float = 0.0


class BacktestLegTemplate(BaseModel):
    strike_offset: int = 0                            # legacy: ATM ± N positions
    type: Literal["CE", "PE"]
    action: Literal["BUY", "SELL"]
    qty: int = Field(gt=0)
    expiry_selector: Literal[
        "current", "next", "next_to_next",
        "weekly", "next_weekly",
        "monthly", "next_monthly",
        "current_plus_n",
    ]
    expiry_offset: int = 0
    # AlgoTest-style strike criteria — supersede strike_offset when set.
    # Modes: "strike_type" (level="ATM"/"ITM5"/"OTM3"), "closest_premium"
    # (value=target $), "closest_delta" (value=0.0..1.0).
    strike_criteria: Optional[Literal[
        "strike_type", "closest_premium", "closest_delta",
        "closest_delta_below", "closest_delta_prem_match", "closest_delta_align",
        "highest_oi",
    ]] = None
    strike_level: Optional[str]   = None              # for strike_type
    strike_value: Optional[float] = None              # for closest_premium / closest_delta
    leg_sl: Optional[LegSlConfig] = None


class SlippageConfig(BaseModel):
    enabled: bool = False
    mode: Literal["flat", "smart"] = "smart"
    flat_value: float = 5.0
    mult: float = 1.0


class BrokerageConfig(BaseModel):
    enabled: bool = False
    rate: Literal["standard", "offer"] = "offer"
    referral: bool = False


class BacktestRequest(BaseModel):
    start_date: str                                   # YYYY-MM-DD
    end_date: str                                     # YYYY-MM-DD inclusive
    legs: list[BacktestLegTemplate]
    entry_time_ist: str = "09:20"
    weekday_mask: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    forced_exit_time_ist: str = "15:25"
    # 0 = same day (intraday), 1 = next day (BTST), N = N days later (Positional).
    # Capped at the option's settlement on expiry day.
    exit_day_offset: int = 0
    timeframe: Literal["1m", "5m", "15m", "30m", "1h"] = "5m"
    slippage: SlippageConfig = Field(default_factory=SlippageConfig)
    brokerage: BrokerageConfig = Field(default_factory=BrokerageConfig)
    # Phase 3 hooks (accepted but unused in Phase 1):
    stop_loss: Optional[dict] = None
    target: Optional[dict] = None
    trail_sl: Optional[dict] = None
    dte_force_close: Optional[int] = None
    per_leg_exits: Optional[list[dict]] = None
    reentry_after_sl: int = 0
    spot_trigger: Optional[dict] = None
    iv_trigger: Optional[dict] = None
    # Phase 4 hook:
    capital: Optional[dict] = None


class BacktestSubmitResponse(BaseModel):
    job_id: str
    status: str


class BacktestProgress(BaseModel):
    days_done: int
    days_total: int
    current_date: Optional[str] = None
    eta_seconds: Optional[float] = None


class BacktestStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: BacktestProgress
    error: Optional[str] = None
    result: Optional[dict] = None


# ── Worker pool ──────────────────────────────────────────────────────────────

# Shared single-process executor. Backtest day loops are CPU-bound (DuckDB +
# numpy), so 2 workers is plenty given a single-worker uvicorn deploy.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="backtest")


async def _run_job(job_id: str, params: dict[str, Any]) -> None:
    """Run the backtest in the executor; updates jobs registry on completion."""
    job = backtest_jobs.get(job_id)
    if not job:
        return
    loop = asyncio.get_event_loop()

    def _cancel_check() -> bool:
        return job.cancel_event.is_set()

    try:
        result = await loop.run_in_executor(
            _executor, backtest_service.run_backtest, job_id, params, _cancel_check,
        )
        if job.cancel_event.is_set():
            # cancel() already marked status="cancelled"; don't overwrite
            return
        backtest_jobs.mark_done(job_id, result)
    except Exception as e:
        logger.exception(f"Backtest job {job_id} failed")
        backtest_jobs.mark_error(job_id, str(e))


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/backtest", response_model=BacktestSubmitResponse)
async def submit_backtest(req: BacktestRequest):
    params = req.model_dump()
    job = backtest_jobs.submit(params)
    job.task = asyncio.create_task(_run_job(job.job_id, params))
    return BacktestSubmitResponse(job_id=job.job_id, status=job.status)


@router.get("/backtest/{job_id}", response_model=BacktestStatusResponse)
async def get_backtest_status(job_id: str):
    job = backtest_jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    eta: Optional[float] = None
    if job.started_at and job.progress["days_done"] > 0:
        elapsed = (job.finished_at or asyncio.get_event_loop().time()) - job.started_at
        per_day = elapsed / job.progress["days_done"]
        remaining = max(0, job.progress["days_total"] - job.progress["days_done"])
        eta = round(per_day * remaining, 1)

    return BacktestStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=BacktestProgress(
            days_done=job.progress["days_done"],
            days_total=job.progress["days_total"],
            current_date=job.progress["current_date"],
            eta_seconds=eta,
        ),
        error=job.error,
        result=job.result if job.status == "done" else None,
    )


@router.delete("/backtest/{job_id}")
async def cancel_backtest(job_id: str):
    if not backtest_jobs.cancel(job_id):
        raise HTTPException(404, f"Job {job_id} not found")
    return {"ok": True, "job_id": job_id, "status": "cancelled"}

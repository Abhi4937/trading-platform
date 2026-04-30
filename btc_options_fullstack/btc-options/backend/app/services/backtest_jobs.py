"""
In-memory job registry for the multi-day backtest service.

Single-process, single-worker assumption (matches the rest of the platform —
see CLAUDE.md "Backend runs with --workers 1"). Jobs are lost on server
restart; that's an explicit v1 trade-off.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Optional


JobStatus = str  # "queued" | "running" | "done" | "error" | "cancelled"


@dataclass
class JobState:
    job_id: str
    status: JobStatus = "queued"
    params: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=lambda: {
        "days_done": 0, "days_total": 0, "current_date": None,
    })
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None


_jobs: dict[str, JobState] = {}
_lock = Lock()
_MAX_JOBS_RETAINED = 32  # garbage-collect oldest finished jobs beyond this


def submit(params: dict[str, Any]) -> JobState:
    """Create + register a new job. Caller is responsible for scheduling the task."""
    job_id = "bt_" + secrets.token_hex(6)
    with _lock:
        # Garbage-collect old finished jobs first
        if len(_jobs) >= _MAX_JOBS_RETAINED:
            stale = sorted(
                (j for j in _jobs.values() if j.status in ("done", "error", "cancelled")),
                key=lambda j: j.finished_at or j.created_at,
            )
            for j in stale[: max(0, len(_jobs) - _MAX_JOBS_RETAINED + 1)]:
                _jobs.pop(j.job_id, None)
        job = JobState(job_id=job_id, params=params)
        _jobs[job_id] = job
    return job


def get(job_id: str) -> Optional[JobState]:
    return _jobs.get(job_id)


def cancel(job_id: str) -> bool:
    job = _jobs.get(job_id)
    if not job:
        return False
    job.cancel_event.set()
    if job.status in ("queued", "running"):
        job.status = "cancelled"
        job.finished_at = time.time()
    return True


def update_progress(job_id: str, days_done: int, days_total: int,
                    current_date: Optional[str] = None) -> None:
    job = _jobs.get(job_id)
    if not job:
        return
    job.progress["days_done"] = days_done
    job.progress["days_total"] = days_total
    if current_date is not None:
        job.progress["current_date"] = current_date
    if job.status == "queued":
        job.status = "running"
        job.started_at = time.time()


def mark_done(job_id: str, result: dict) -> None:
    job = _jobs.get(job_id)
    if not job:
        return
    job.status = "done"
    job.result = result
    job.finished_at = time.time()


def mark_error(job_id: str, error: str) -> None:
    job = _jobs.get(job_id)
    if not job:
        return
    job.status = "error"
    job.error = error
    job.finished_at = time.time()

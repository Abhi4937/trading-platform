import os
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "logs")
ALLOWED_FILES = {"api", "errors"}


class LogResponse(BaseModel):
    file: str
    lines: list[str]
    total: int


def _tail(filepath: str, n: int) -> list[str]:
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    return [l.rstrip("\n") for l in all_lines[-n:]]


@router.get("/logs", response_model=LogResponse)
async def get_logs(
    file: str = Query("api", description="Log file name: api or errors"),
    lines: int = Query(200, ge=1, le=1000, description="Number of lines to return"),
):
    if file not in ALLOWED_FILES:
        raise HTTPException(status_code=400, detail=f"Invalid log file. Choose from: {ALLOWED_FILES}")
    filepath = os.path.join(LOG_DIR, f"{file}.log")
    data = _tail(filepath, lines)
    return LogResponse(file=file, lines=data, total=len(data))

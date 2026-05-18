from datetime import datetime, timezone
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
@router.get("/healthz")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

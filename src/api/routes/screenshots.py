"""Serve captured page screenshots (v2)."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from src.api.middleware.auth import get_current_user
from src.services.storage import get_storage

router = APIRouter(prefix="/screenshots", tags=["screenshots"])


@router.get("/{job_id}/{name}")
async def get_screenshot(job_id: str, name: str, _user=Depends(get_current_user)) -> Response:
    """Return a PNG screenshot by job id + filename."""
    data = get_storage().read_screenshot(f"{job_id}/{name}")
    if data is None:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return Response(content=data, media_type="image/png")

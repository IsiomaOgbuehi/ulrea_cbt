"""
Connection check before an exam: round-trip time, download and upload speed.

Nothing here touches the database, so the numbers reflect the network path
(browser -> app server -> this service), the same path an exam's saves take.
Bodies are capped so the endpoints can't be used to move large amounts of data.
"""
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from cbt_service.dependencies import get_current_user
from cbt_service.schemas.current_user import CurrentUser

router = APIRouter(prefix="/network", tags=["network"])

MAX_DOWNLOAD_KB: int = 2048
MAX_UPLOAD_BYTES: int = 1024 * 1024
# Random bytes don't compress, so proxies can't shrink the test. Made once.
_PAYLOAD: bytes = os.urandom(MAX_DOWNLOAD_KB * 1024)
_NO_STORE: dict[str, str] = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


class PingRead(BaseModel):
    ok: bool = True
    server_time: datetime


class UploadRead(BaseModel):
    received_bytes: int


''' PING 📶 '''
@router.get("/ping", response_model=PingRead)
def ping(response: Response, _: CurrentUser = Depends(get_current_user)) -> PingRead:
    """A tiny response for measuring round-trip time and dropped requests."""
    response.headers.update(_NO_STORE)
    return PingRead(server_time=datetime.now(timezone.utc))


''' DOWNLOAD ⬇️ '''
@router.get("/download")
def download(
    size_kb: int = Query(default=256, ge=1, le=MAX_DOWNLOAD_KB),
    _: CurrentUser = Depends(get_current_user),
) -> Response:
    """size_kb of random bytes, for measuring download speed."""
    return Response(
        content=_PAYLOAD[: size_kb * 1024],
        media_type="application/octet-stream",
        headers=_NO_STORE,
    )


''' UPLOAD ⬆️ '''
@router.post("/upload", response_model=UploadRead)
async def upload(request: Request, _: CurrentUser = Depends(get_current_user)) -> UploadRead:
    """Accepts up to 1 MB and reports how much arrived, for measuring upload speed."""
    received: int = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Upload test is limited to 1 MB.")
    return UploadRead(received_bytes=received)

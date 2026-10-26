from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import HR_ROLES, require_roles
from app.modules.monitor import service
from app.modules.monitor.schemas import LiveOut

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/live", response_model=LiveOut, dependencies=[Depends(require_roles(*HR_ROLES))])
async def live(
    seconds: int = Query(60, ge=5, le=300),
    feed: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    return await service.live(session, seconds, feed)

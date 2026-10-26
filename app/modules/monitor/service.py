"""Live monitor for HR/admin: what the load test (or real users) is writing right now.

Read-only and composed from the other modules' services, so it owns no tables.
"""
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.attendance import service as attendance_service
from app.modules.employees import service as employees_service
from app.modules.requests import service as requests_service


async def live(session: AsyncSession, seconds: int, feed_size: int) -> dict:
    checks = await attendance_service.live(session, seconds, feed_size)
    names = await employees_service.names_by_ids(session, sorted({e.employee_id for e in checks["latest"]}))
    return {
        "server_time": datetime.now(timezone.utc),
        "window_seconds": seconds,
        "checkins": checks["events"],
        "active_employees": checks["employees"],
        "requests_created": await requests_service.count_recent(session, seconds),
        "per_second": checks["per_second"],
        "latest": [
            {
                "event_id": e.id,
                "code": names.get(e.employee_id, ("?", ""))[0],
                "full_name": names.get(e.employee_id, ("?", ""))[1],
                "event_time": e.event_time,
                "source": e.source,
            }
            for e in checks["latest"]
        ],
    }

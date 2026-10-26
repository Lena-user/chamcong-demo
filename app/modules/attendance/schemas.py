import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class CheckRequest(BaseModel):
    idempotency_key: uuid.UUID
    source: str = Field("web", min_length=1, max_length=20)


class CheckResponse(BaseModel):
    event_id: int
    event_time: datetime
    duplicate: bool


class DailyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_date: date
    first_in: datetime | None
    last_out: datetime | None
    worked_minutes: int
    ot_minutes: int

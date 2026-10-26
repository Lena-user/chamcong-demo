from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class RequestCreate(BaseModel):
    type: Literal["leave", "adjustment", "ot"]
    work_date: date
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    reason: str = Field("", max_length=500)

    @model_validator(mode="after")
    def _check_range(self):
        if self.type in ("adjustment", "ot") and (self.start_at is None or self.end_at is None):
            raise ValueError("start_at and end_at are required for adjustment and ot")
        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValueError("end_at must not be before start_at")
        return self


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    type: str
    work_date: date
    start_at: datetime | None
    end_at: datetime | None
    reason: str
    status: str
    approved_by: int | None
    decided_at: datetime | None
    created_at: datetime
    # True when a repeated approve/reject was answered from the earlier decision.
    replayed: bool = False

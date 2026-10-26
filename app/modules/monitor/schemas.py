from datetime import datetime

from pydantic import BaseModel


class SecondCount(BaseModel):
    ts: datetime
    events: int


class FeedItem(BaseModel):
    event_id: int
    code: str
    full_name: str
    event_time: datetime
    source: str


class LiveOut(BaseModel):
    server_time: datetime
    window_seconds: int
    checkins: int
    active_employees: int
    requests_created: int
    per_second: list[SecondCount]
    latest: list[FeedItem]

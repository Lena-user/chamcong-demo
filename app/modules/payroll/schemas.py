from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PeriodCreate(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)


class PeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    year: int
    month: int
    status: str
    calculated_at: datetime | None = None


class CalculateOut(BaseModel):
    job_id: str


class JobOut(BaseModel):
    job_id: str
    status: str
    success: bool | None = None
    result: dict | None = None
    error: str | None = None


class PayslipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period_id: int
    employee_id: int
    worked_minutes: int
    ot_minutes: int
    base_pay: int
    ot_pay: int
    allowance: int
    gross: int
    social_insurance: int
    health_insurance: int
    unemployment_insurance: int
    taxable_income: int
    income_tax: int
    net_pay: int
    rule_version: int

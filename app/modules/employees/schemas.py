from datetime import date

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    code: str
    password: str


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    full_name: str
    department: str
    hired_on: date
    role: str
    version: int


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    employee: EmployeeOut

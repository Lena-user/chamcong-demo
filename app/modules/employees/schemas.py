from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class EmployeePatch(BaseModel):
    version: int = Field(ge=1)
    full_name: str | None = Field(None, min_length=1, max_length=100)
    department: str | None = Field(None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def _has_changes(self):
        if self.full_name is None and self.department is None:
            raise ValueError("Nothing to update")
        return self


class SensitiveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: int
    national_id: str
    bank_account: str
    tax_code: str
    dependents: int

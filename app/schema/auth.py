"""Patient account request and response schemas."""

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email(value: str) -> str:
    value = value.strip().casefold()
    if len(value) > 254 or not EMAIL_RE.fullmatch(value):
        raise ValueError("请输入有效邮箱")
    return value


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(default="健康用户", max_length=128)

    _normalize_email = field_validator("email")(_email)


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=256)

    _normalize_email = field_validator("email")(_email)


class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)

    @field_validator("display_name")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("昵称不能为空")
        return value


class AccountResponse(BaseModel):
    id: str
    email: str
    display_name: str
    created_at: datetime


class ReportHistoryItem(BaseModel):
    id: int
    report_type: str | None = None
    department: str | None = None
    status: str
    exam_date: datetime | None = None
    created_at: datetime
    metric_count: int = 0
    finding_count: int = 0
    abnormal_count: int = 0

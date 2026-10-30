from pydantic import BaseModel


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    department_code: str | None = None
    club_id: int | None = None
    usn: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


from datetime import date
from typing import Literal

from pydantic import Field, field_validator, model_validator


class EventIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    event_date: date
    venue: str = Field(min_length=2, max_length=200)
    registration_url: str | None = Field(default=None, max_length=500)
    standard_points: int = Field(ge=1, le=50)


class RequestIn(BaseModel):
    event_id: int
    request_type: Literal["NEW_ACTIVITY", "MISSING_FROM_LIST", "CORRECTION"]
    claimed_points: int = Field(ge=1, le=50)
    reason: str = Field(min_length=3, max_length=2000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, v):
        v = v.strip()
        if len(v) < 3:
            raise ValueError("reason too short")
        return v


class ReviewIn(BaseModel):
    action: Literal["VERIFY", "APPROVE", "REJECT"]
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def reject_needs_comment(self):
        if self.action == "REJECT" and not (self.comment or "").strip():
            raise ValueError("A reviewer comment is mandatory when rejecting")
        return self

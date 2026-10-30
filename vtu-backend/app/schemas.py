from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    """The signed-in account. Never includes password_hash."""
    email: str
    role: str
    display_name: str
    student_usn: str | None = None
    department_code: str | None = None
    club_id: int | None = None
    club_name: str | None = None
    last_login_at: datetime | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class StudentSummaryOut(BaseModel):
    usn: str
    name: str
    department_code: str
    entry_type: str
    student_status: str
    required_points: Decimal
    earned_points: Decimal
    remaining_points: Decimal
    target_reached: bool


class LedgerRowOut(BaseModel):
    ledger_id: int
    event_id: int
    event_name: str
    points_awarded: Decimal
    award_date: date
    source_type: str
    status: str
    supersedes_ledger_id: int | None = None


class StudentDetailOut(StudentSummaryOut):
    ledger: list[LedgerRowOut]


# --- Event/request inputs for the routers ported in CP2 (events.py, claims.py; not mounted yet) ---------------
from typing import Literal  # noqa: E402

from pydantic import field_validator, model_validator  # noqa: E402


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

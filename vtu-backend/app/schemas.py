import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class StudentStatusIn(BaseModel):
    """Dean-only lifecycle change. The student record and its history are never deleted."""
    model_config = ConfigDict(extra="forbid")
    student_status: Literal["ACTIVE", "GRADUATED", "INACTIVE", "ARCHIVED"]
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason", mode="before")
    @classmethod
    def strip(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v



# --- events ---------------------------------------------------------------------------------------------------------
# Points columns are NUMERIC(6,2): at most 9999.99, never negative (CLAUDE.md 5; no other range is imposed).
Points = Annotated[Decimal, Field(ge=0, max_digits=6, decimal_places=2)]
ACADEMIC_YEAR = re.compile(r"(\d{4})-(\d{2})")


def _clean(v):
    """Strip text; blank becomes None (so required text fields reject whitespace-only values)."""
    return (v.strip() or None) if isinstance(v, str) else v


class EventFields(BaseModel):
    """Editable event fields. club_id, created_by and event_status are never taken from the client."""
    model_config = ConfigDict(extra="forbid")
    event_name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    event_date: date
    start_time: time | None = None
    end_time: time | None = None
    venue: str | None = Field(default=None, max_length=200)
    registration_url: str | None = Field(default=None, max_length=2000)
    standard_points: Points | None = None
    academic_year: str = Field(max_length=20)

    @field_validator("event_name", "description", "venue", "registration_url", "academic_year", mode="before")
    @classmethod
    def strip(cls, v):
        return _clean(v)

    @field_validator("registration_url")
    @classmethod
    def http_url(cls, v):
        if v is not None and not v.lower().startswith(("https://", "http://")):
            raise ValueError("registration_url must start with http:// or https://")
        return v

    @field_validator("academic_year")
    @classmethod
    def academic_year_format(cls, v):
        m = ACADEMIC_YEAR.fullmatch(v or "")
        if not m or (int(m[1]) + 1) % 100 != int(m[2]):
            raise ValueError("academic_year must look like 2025-26")
        return v


def check_times(start: time | None, end: time | None):
    if start and end and end <= start:
        raise ValueError("end_time must be after start_time")


class EventIn(EventFields):
    @model_validator(mode="after")
    def times(self):
        check_times(self.start_time, self.end_time)
        return self


class EventPatch(EventFields):
    """Partial update: only the fields sent are changed (the merged result is validated again in the service)."""
    event_name: str | None = Field(default=None, min_length=2, max_length=200)
    event_date: date | None = None
    academic_year: str | None = Field(default=None, max_length=20)

    @field_validator("event_name", "event_date", "academic_year", mode="after")
    @classmethod
    def not_null(cls, v):
        if v is None:
            raise ValueError("cannot be cleared")
        return v


class EventStatusIn(BaseModel):
    event_status: Literal["DRAFT", "PUBLISHED", "COMPLETED", "CLOSED"]


class EventOut(BaseModel):
    event_id: int
    event_name: str
    club_id: int | None
    club_name: str | None
    description: str | None
    event_date: date
    start_time: time | None
    end_time: time | None
    venue: str | None
    registration_url: str | None
    standard_points: Decimal | None
    academic_year: str
    event_status: str
    created_by: str


class ClubOut(BaseModel):
    club_id: int
    club_name: str
    club_email: str | None


class ClubAccountabilityOut(BaseModel):
    club_id: int
    club_name: str
    open_club_issues: int
    pending_dean_issues: int
    resolved_issues: int


# --- point requests ---------------------------------------------------------------------------------------------

class RequestIn(BaseModel):
    """usn and submitted_by come from the signed-in student's account, never from the body."""
    model_config = ConfigDict(extra="forbid")
    event_id: int
    request_type: Literal["NEW_ACTIVITY", "MISSING_FROM_LIST", "CORRECTION"]
    claimed_points: Points
    reason: str = Field(max_length=2000)
    correction_of_ledger_id: int | None = None

    @field_validator("reason")
    @classmethod
    def reason_present(cls, v):
        v = v.strip()
        if len(v) < 3:
            raise ValueError("reason must be at least 3 characters")
        return v

    @model_validator(mode="after")
    def correction_link(self):
        if (self.request_type == "CORRECTION") != (self.correction_of_ledger_id is not None):
            raise ValueError("correction_of_ledger_id is required for CORRECTION and only for CORRECTION")
        return self


class _ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("comment", mode="before")
    @classmethod
    def strip(cls, v):
        return _clean(v)

    @model_validator(mode="after")
    def reject_needs_comment(self):
        if self.action == "REJECT" and not self.comment:
            raise ValueError("A comment is required when rejecting")
        return self


class ClubReviewIn(_ReviewIn):
    action: Literal["VERIFY", "REJECT"]


class DeanReviewIn(_ReviewIn):
    """There is no RESOLVED action: RESOLVED is set only by a committed signed-list (OCR) workflow."""
    action: Literal["APPROVE", "REJECT"]


class RequestOut(BaseModel):
    request_id: int
    usn: str
    student_name: str
    event_id: int
    event_name: str
    club_id: int | None
    request_type: str
    claimed_points: Decimal | None
    reason: str
    submitted_by: str
    correction_of_ledger_id: int | None
    status: str
    club_reviewed_by: str | None
    club_reviewed_at: datetime | None
    club_review_comment: str | None
    dean_decided_by: str | None
    dean_decided_at: datetime | None
    dean_decision_comment: str | None
    created_at: datetime
    updated_at: datetime
    ledger_id: int | None = None          # the award this request produced, once APPROVED
    document_count: int = 0               # attached documents that are not soft-deleted (GET /documents?request_id=)


# --- documents ------------------------------------------------------------------------------------------------------
# storage_key is deliberately absent from every document schema: clients never see where a file is stored.

class DocumentOut(BaseModel):
    document_id: int
    event_id: int | None
    event_name: str | None
    document_type: str
    file_name: str
    mime_type: str
    file_size_bytes: int
    file_hash: str
    uploaded_by: str
    uploaded_at: datetime
    document_status: str


class DocumentDetailOut(DocumentOut):
    """Links are filtered by the caller's own request/award scope (a shared signed list shows only your awards)."""
    request_ids: list[int]
    ledger_ids: list[int]


class AttachDocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: int


class RequestDocumentOut(BaseModel):
    request_id: int
    document_id: int
    attached_at: datetime


class LedgerDocumentOut(BaseModel):
    ledger_id: int
    document_id: int
    linked_at: datetime
    linked_by: str


# --- OCR ------------------------------------------------------------------------------------------------------------

class OcrBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: int


class OcrBatchOut(BaseModel):
    ocr_batch_id: int
    document_id: int
    file_name: str
    event_id: int | None
    event_name: str | None
    started_by: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    pages_processed: int | None
    records_detected: int | None          # as extracted; record_counts below reflects the Dean's review so far
    valid_records: int | None
    exception_count: int | None
    extracted_metadata: dict | None
    error_message: str | None
    record_counts: dict[str, int]         # current number of records per validation_status
    issue_counts: dict[str, int]          # EXCEPTION records per issue code (see OcrRecordOut.issues)


class OcrRecordOut(BaseModel):
    ocr_record_id: int
    ocr_batch_id: int
    page_number: int
    row_number: int | None
    extracted_usn: str | None
    matched_student_usn: str | None
    matched_student_name: str | None
    extracted_name: str | None
    extracted_points: Decimal | None
    confidence_score: Decimal | None
    raw_extraction: dict | None
    validation_status: str
    validation_message: str | None
    issues: list[str]                     # issue codes derived from validation_message, e.g. UNKNOWN_USN


class OcrRecordPatch(BaseModel):
    """Dean corrections. extracted_usn and raw_extraction keep what OCR read and cannot be changed (CLAUDE.md 18.5).
    Send only the fields to change; matched_student_usn may be null to clear the selection."""
    model_config = ConfigDict(extra="forbid")
    matched_student_usn: str | None = Field(default=None, max_length=20)
    extracted_name: str | None = Field(default=None, min_length=1, max_length=150)
    extracted_points: Points | None = None

    @field_validator("matched_student_usn", "extracted_name", mode="before")
    @classmethod
    def strip(cls, v):
        return _clean(v)

    @model_validator(mode="after")
    def something_to_change(self):
        if not self.model_fields_set:
            raise ValueError("Send at least one of matched_student_usn, extracted_name, extracted_points")
        for name in ("extracted_name", "extracted_points"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be cleared")
        return self


class OcrReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("comment", mode="before")
    @classmethod
    def strip(cls, v):
        return _clean(v)


class OcrConfirmValidOut(BaseModel):
    """Result of confirming every VALID record of a batch at once."""
    batch: OcrBatchOut
    records_confirmed: int
    confirmed_record_ids: list[int]


class OcrCommitOut(BaseModel):
    """Result of committing a batch to the ledger."""
    batch: OcrBatchOut
    awards_created: int
    ledger_ids: list[int]
    requests_resolved: int
    resolved_request_ids: list[int]
    records_confirmed: int
    records_dismissed: int


# --- audit log ------------------------------------------------------------------------------------------------------

class AuditOut(BaseModel):
    audit_id: int
    user_email: str | None
    action: str
    entity_type: str
    entity_id: int
    old_values: dict | None
    new_values: dict | None
    ip_address: str | None
    created_at: datetime


class AuditPageOut(BaseModel):
    items: list[AuditOut]
    total: int                            # rows matching the filters, ignoring limit/offset
    limit: int
    offset: int

"""Database schema for the VTU Activity Points Ledger."""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey,
                        Index, Integer, Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Role(str, enum.Enum):
    STUDENT = "STUDENT"
    CLUB = "CLUB"
    DEAN = "DEAN"
    HOD = "HOD"


class EntryType(str, enum.Enum):
    REGULAR = "REGULAR"
    LATERAL = "LATERAL"


class DocType(str, enum.Enum):
    CERTIFICATE_PDF = "CERTIFICATE_PDF"
    ATTENDANCE_LIST = "ATTENDANCE_LIST"
    EVENT_PHOTO = "EVENT_PHOTO"
    OFFICIAL_SIGNED_PDF = "OFFICIAL_SIGNED_PDF"
    OTHER = "OTHER"


class DocPurpose(str, enum.Enum):
    OFFICIAL = "OFFICIAL"
    EVIDENCE = "EVIDENCE"


class RequestType(str, enum.Enum):
    NEW_ACTIVITY = "NEW_ACTIVITY"
    MISSING_FROM_LIST = "MISSING_FROM_LIST"
    CORRECTION = "CORRECTION"


class RequestStatus(str, enum.Enum):
    PENDING_CLUB = "PENDING_CLUB"
    PENDING_DEAN = "PENDING_DEAN"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class LedgerStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    CORRECTION = "CORRECTION"


class LedgerSource(str, enum.Enum):
    OCR_BATCH = "OCR_BATCH"
    CLAIM = "CLAIM"


class BatchStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    REVIEW = "REVIEW"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"


class OcrIssue(str, enum.Enum):
    NONE = "NONE"
    UNKNOWN_USN = "UNKNOWN_USN"
    NAME_MISMATCH = "NAME_MISMATCH"
    POINT_ERROR = "POINT_ERROR"


class OcrRecordStatus(str, enum.Enum):
    VALID = "VALID"
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Club(Base):
    __tablename__ = "clubs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(160))
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"))
    department_code: Mapped[str | None] = mapped_column(String(8))          # HOD scope
    club_id: Mapped[int | None] = mapped_column(ForeignKey("clubs.id"))     # club lead scope
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (
        CheckConstraint("role <> 'HOD' OR department_code IS NOT NULL", name="hod_needs_department"),
        CheckConstraint("role <> 'CLUB' OR club_id IS NOT NULL", name="club_lead_needs_club"),
    )


class Student(Base):
    __tablename__ = "students"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), unique=True)
    usn: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(255))
    department_code: Mapped[str] = mapped_column(String(8), index=True)
    entry_type: Mapped[EntryType] = mapped_column(Enum(EntryType, name="entry_type"))
    created_at: Mapped[datetime] = created_at()


class PointRequirement(Base):
    """POINT_REQUIREMENT: target points per entry type (100 Regular, 75 Lateral)."""
    __tablename__ = "point_requirements"
    entry_type: Mapped[EntryType] = mapped_column(Enum(EntryType, name="entry_type"), primary_key=True)
    points_required: Mapped[int] = mapped_column(Integer)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_id: Mapped[int | None] = mapped_column(ForeignKey("clubs.id"))  # NULL = college-level event
    title: Mapped[str] = mapped_column(String(200))
    event_date: Mapped[date] = mapped_column(Date)
    venue: Mapped[str] = mapped_column(String(200))
    registration_url: Mapped[str | None] = mapped_column(String(500))
    standard_points: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (CheckConstraint("standard_points > 0", name="event_points_positive"),)


class Document(Base):
    """Every uploaded file. Bytes live in object storage; this row holds metadata + SHA-256."""
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("students.id"), index=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(500))
    doc_type: Mapped[DocType] = mapped_column(Enum(DocType, name="doc_type"))
    purpose: Mapped[DocPurpose] = mapped_column(Enum(DocPurpose, name="doc_purpose"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"))
    created_at: Mapped[datetime] = created_at()


class PointRequest(Base):
    __tablename__ = "point_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    request_type: Mapped[RequestType] = mapped_column(Enum(RequestType, name="request_type"))
    claimed_points: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus, name="request_status"), index=True)
    reviewer_comment: Mapped[str | None] = mapped_column(Text)
    club_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    dean_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("claimed_points BETWEEN 1 AND 50", name="claimed_points_range"),
        CheckConstraint("status <> 'REJECTED' OR reviewer_comment IS NOT NULL", name="rejection_needs_comment"),
    )


class RequestAttachment(Base):
    """Many files per claim."""
    __tablename__ = "request_attachments"
    request_id: Mapped[int] = mapped_column(ForeignKey("point_requests.id", ondelete="CASCADE"), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)


class OcrBatch(Base):
    __tablename__ = "ocr_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[BatchStatus] = mapped_column(Enum(BatchStatus, name="batch_status"), default=BatchStatus.UPLOADED)
    pages_processed: Mapped[int] = mapped_column(Integer, default=0)
    records_detected: Mapped[int] = mapped_column(Integer, default=0)
    detected_event_text: Mapped[str | None] = mapped_column(String(200))
    detected_club_text: Mapped[str | None] = mapped_column(String(200))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"))   # Dean-resolved match
    club_id: Mapped[int | None] = mapped_column(ForeignKey("clubs.id"))     # Dean-resolved match
    created_at: Mapped[datetime] = created_at()
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OcrRecord(Base):
    __tablename__ = "ocr_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("ocr_batches.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    raw_usn: Mapped[str | None] = mapped_column(String(32))
    raw_name: Mapped[str | None] = mapped_column(String(200))
    raw_points: Mapped[str | None] = mapped_column(String(16))
    usn: Mapped[str | None] = mapped_column(String(16))           # editable by Dean
    name: Mapped[str | None] = mapped_column(String(200))
    points: Mapped[int | None] = mapped_column(Integer)
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("students.id"))
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    issue: Mapped[OcrIssue] = mapped_column(Enum(OcrIssue, name="ocr_issue"), default=OcrIssue.NONE)
    status: Mapped[OcrRecordStatus] = mapped_column(Enum(OcrRecordStatus, name="ocr_record_status"), default=OcrRecordStatus.VALID)
    __table_args__ = (Index("ix_ocr_records_batch_status", "batch_id", "status"),)


class PointLedger(Base):
    """POINT_LEDGER: the official record. Rows are never deleted, only SUPERSEDED."""
    __tablename__ = "point_ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    points: Mapped[int] = mapped_column(Integer)
    status: Mapped[LedgerStatus] = mapped_column(Enum(LedgerStatus, name="ledger_status"), default=LedgerStatus.ACTIVE)
    source: Mapped[LedgerSource] = mapped_column(Enum(LedgerSource, name="ledger_source"))
    source_batch_id: Mapped[int | None] = mapped_column(ForeignKey("ocr_batches.id"))
    source_request_id: Mapped[int | None] = mapped_column(ForeignKey("point_requests.id"))
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("point_ledger.id"))
    awarded_on: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (
        CheckConstraint("points > 0", name="ledger_points_positive"),
        # one live (non-superseded) award per student per event
        Index("uq_ledger_live_award", "student_id", "event_id", unique=True, postgresql_where="status <> 'SUPERSEDED'"),
    )


class LedgerEvidence(Base):
    """Evidence-only uploads attached to already-awarded rows (no duplicate point claim)."""
    __tablename__ = "ledger_evidence"
    ledger_id: Mapped[int] = mapped_column(ForeignKey("point_ledger.id"), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()

"""The 14-table relational schema from CLAUDE.md Section 5 (plus the STUDENT_POINT_SUMMARY view).

Status/type columns are VARCHAR + CHECK (not PostgreSQL ENUM types). Allowed values live in the StrEnum classes
below and are the single source for the CHECK constraints. Domains not defined by the source document were approved
by the project owner (CLAUDE.md 18.5).
"""
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (CHAR, BigInteger, CheckConstraint, Column, Date, DateTime, ForeignKey, Identity, Index,
                        Integer, MetaData, Numeric, SmallInteger, String, Table, Text, Time, Boolean, func, text)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EntryType(StrEnum):
    REGULAR = "REGULAR"
    LATERAL = "LATERAL"


class StudentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    GRADUATED = "GRADUATED"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class Role(StrEnum):
    STUDENT = "STUDENT"
    CLUB_LEAD = "CLUB_LEAD"
    HOD = "HOD"
    DEAN = "DEAN"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class EventStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"


class DocumentType(StrEnum):
    SIGNED_POINT_LIST = "SIGNED_POINT_LIST"
    CERTIFICATE = "CERTIFICATE"
    ATTENDANCE_SHEET = "ATTENDANCE_SHEET"
    PHOTO = "PHOTO"
    REPORT = "REPORT"
    OTHER = "OTHER"


class DocumentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


class OcrBatchStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    REVIEW = "REVIEW"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"


class ValidationStatus(StrEnum):
    VALID = "VALID"
    EXCEPTION = "EXCEPTION"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"


class RequestType(StrEnum):
    NEW_ACTIVITY = "NEW_ACTIVITY"
    MISSING_FROM_LIST = "MISSING_FROM_LIST"
    CORRECTION = "CORRECTION"


class RequestStatus(StrEnum):
    PENDING_CLUB = "PENDING_CLUB"
    PENDING_DEAN = "PENDING_DEAN"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RESOLVED = "RESOLVED"
    WITHDRAWN = "WITHDRAWN"


class LedgerSourceType(StrEnum):
    BULK_APPROVAL = "BULK_APPROVAL"
    INDIVIDUAL_CLAIM = "INDIVIDUAL_CLAIM"
    MANUAL_CORRECTION = "MANUAL_CORRECTION"


class LedgerStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"


def one_of(column: str, values: type[StrEnum]) -> str:
    """SQL for a CHECK that restricts a column to an enum's values, e.g. "role IN ('STUDENT', 'HOD')"."""
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


def now_default():
    return {"server_default": func.now()}


USER_FK = "user_account.email"


class Department(Base):
    __tablename__ = "department"
    department_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    department_name: Mapped[str] = mapped_column(String(100), unique=True)


class PointRequirement(Base):
    __tablename__ = "point_requirement"
    entry_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    required_points: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    __table_args__ = (
        CheckConstraint(one_of("entry_type", EntryType), name="entry_type"),
        CheckConstraint("required_points > 0", name="required_points"),
    )


class Student(Base):
    __tablename__ = "student"
    usn: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(150), unique=True)
    department_code: Mapped[str] = mapped_column(ForeignKey("department.department_code"), index=True)
    entry_type: Mapped[str] = mapped_column(ForeignKey("point_requirement.entry_type"))
    admission_year: Mapped[int] = mapped_column(SmallInteger)
    current_semester: Mapped[int | None] = mapped_column(SmallInteger)
    student_status: Mapped[str] = mapped_column(String(20))
    __table_args__ = (
        CheckConstraint(one_of("entry_type", EntryType), name="entry_type"),
        CheckConstraint("current_semester BETWEEN 1 AND 8", name="current_semester"),
        CheckConstraint(one_of("student_status", StudentStatus), name="student_status"),
    )


class Club(Base):
    __tablename__ = "club"
    club_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    club_name: Mapped[str] = mapped_column(String(150), unique=True)
    club_email: Mapped[str | None] = mapped_column(String(150), unique=True)


class UserAccount(Base):
    __tablename__ = "user_account"
    email: Mapped[str] = mapped_column(String(150), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20))
    account_status: Mapped[str] = mapped_column(String(20))
    student_usn: Mapped[str | None] = mapped_column(ForeignKey("student.usn"), index=True)
    department_code: Mapped[str | None] = mapped_column(ForeignKey("department.department_code"))
    club_id: Mapped[int | None] = mapped_column(ForeignKey("club.club_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **now_default())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(one_of("role", Role), name="role"),
        CheckConstraint(one_of("account_status", AccountStatus), name="account_status"),
        # CLAUDE.md 3.5: exactly the association column(s) for the role are populated.
        CheckConstraint(
            "(role = 'STUDENT' AND student_usn IS NOT NULL AND department_code IS NULL AND club_id IS NULL)"
            " OR (role = 'HOD' AND student_usn IS NULL AND department_code IS NOT NULL AND club_id IS NULL)"
            " OR (role = 'CLUB_LEAD' AND student_usn IS NULL AND department_code IS NULL AND club_id IS NOT NULL)"
            " OR (role = 'DEAN' AND student_usn IS NULL AND department_code IS NULL AND club_id IS NULL)",
            name="role_association"),
    )


class Event(Base):
    __tablename__ = "event"
    event_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_name: Mapped[str] = mapped_column(String(200))
    club_id: Mapped[int | None] = mapped_column(ForeignKey("club.club_id"), index=True)   # NULL = non-club event
    description: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[date] = mapped_column(Date)
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    venue: Mapped[str | None] = mapped_column(String(200))
    registration_url: Mapped[str | None] = mapped_column(Text)
    standard_points: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    academic_year: Mapped[str] = mapped_column(String(20))
    event_status: Mapped[str] = mapped_column(String(30))
    created_by: Mapped[str] = mapped_column(ForeignKey(USER_FK))
    __table_args__ = (
        CheckConstraint("standard_points >= 0", name="standard_points"),
        CheckConstraint(one_of("event_status", EventStatus), name="event_status"),
    )


class Document(Base):
    """Metadata only; the file itself lives in private storage under storage_key."""
    __tablename__ = "document"
    document_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("event.event_id"), index=True)
    document_type: Mapped[str] = mapped_column(String(30))
    file_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    file_hash: Mapped[str] = mapped_column(CHAR(64), index=True)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey(USER_FK), index=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **now_default())
    document_status: Mapped[str] = mapped_column(String(20))
    __table_args__ = (
        CheckConstraint(one_of("document_type", DocumentType), name="document_type"),
        CheckConstraint("file_size_bytes > 0", name="file_size_bytes"),
        CheckConstraint(one_of("document_status", DocumentStatus), name="document_status"),
    )


class OcrBatch(Base):
    """One OCR processing run over one document (a document can be processed more than once)."""
    __tablename__ = "ocr_batch"
    ocr_batch_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.document_id"), index=True)
    started_by: Mapped[str] = mapped_column(ForeignKey(USER_FK))
    status: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **now_default())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pages_processed: Mapped[int | None] = mapped_column(Integer)
    records_detected: Mapped[int | None] = mapped_column(Integer)
    valid_records: Mapped[int | None] = mapped_column(Integer)
    exception_count: Mapped[int | None] = mapped_column(Integer)
    extracted_metadata: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint(one_of("status", OcrBatchStatus), name="status"),
        CheckConstraint("pages_processed >= 0", name="pages_processed"),
        CheckConstraint("records_detected >= 0", name="records_detected"),
        CheckConstraint("valid_records >= 0", name="valid_records"),
        CheckConstraint("exception_count >= 0", name="exception_count"),
    )


class OcrRecord(Base):
    """One extracted row, staged for Dean review. Never an award by itself."""
    __tablename__ = "ocr_record"
    ocr_record_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    ocr_batch_id: Mapped[int] = mapped_column(ForeignKey("ocr_batch.ocr_batch_id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    row_number: Mapped[int | None] = mapped_column(Integer)
    extracted_usn: Mapped[str | None] = mapped_column(String(20))           # what OCR read
    matched_student_usn: Mapped[str | None] = mapped_column(ForeignKey("student.usn"), index=True)  # who it is
    extracted_name: Mapped[str | None] = mapped_column(String(150))
    extracted_points: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    raw_extraction: Mapped[dict | None] = mapped_column(JSONB)
    validation_status: Mapped[str] = mapped_column(String(30))
    validation_message: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("confidence_score BETWEEN 0 AND 1", name="confidence_score"),
        CheckConstraint(one_of("validation_status", ValidationStatus), name="validation_status"),
    )


class PointRequest(Base):
    """A student's request for an Activity Point action. Not an award."""
    __tablename__ = "point_request"
    request_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    usn: Mapped[str] = mapped_column(ForeignKey("student.usn"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.event_id"), index=True)
    request_type: Mapped[str] = mapped_column(String(30))
    claimed_points: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    reason: Mapped[str] = mapped_column(Text)
    submitted_by: Mapped[str] = mapped_column(ForeignKey(USER_FK))
    # point_request <-> point_ledger reference each other, so this FK is added after both tables exist (use_alter).
    correction_of_ledger_id: Mapped[int | None] = mapped_column(
        ForeignKey("point_ledger.ledger_id", use_alter=True))
    status: Mapped[str] = mapped_column(String(30), index=True)
    club_reviewed_by: Mapped[str | None] = mapped_column(ForeignKey(USER_FK))
    club_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    club_review_comment: Mapped[str | None] = mapped_column(Text)
    dean_decided_by: Mapped[str | None] = mapped_column(ForeignKey(USER_FK))
    dean_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dean_decision_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **now_default())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), **now_default())
    __table_args__ = (
        CheckConstraint(one_of("request_type", RequestType), name="request_type"),
        CheckConstraint("claimed_points >= 0", name="claimed_points"),
        CheckConstraint(one_of("status", RequestStatus), name="status"),
        # A CORRECTION must point at the award it challenges; other request types must not.
        CheckConstraint("(request_type = 'CORRECTION') = (correction_of_ledger_id IS NOT NULL)", name="correction_link"),
        # A rejection carries a non-blank comment from whoever rejected it (Dean if decided, else club).
        CheckConstraint(
            "status <> 'REJECTED'"
            " OR (dean_decided_by IS NOT NULL AND btrim(coalesce(dean_decision_comment, '')) <> '')"
            " OR (dean_decided_by IS NULL AND club_reviewed_by IS NOT NULL"
            " AND btrim(coalesce(club_review_comment, '')) <> '')",
            name="rejection_comment"),
    )


class PointRequestDocument(Base):
    __tablename__ = "point_request_document"
    request_id: Mapped[int] = mapped_column(ForeignKey("point_request.request_id"), primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.document_id"), primary_key=True, index=True)
    attached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **now_default())


class PointLedger(Base):
    """The official award record. Rows are never deleted; corrections supersede them."""
    __tablename__ = "point_ledger"
    ledger_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    usn: Mapped[str] = mapped_column(ForeignKey("student.usn"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.event_id"), index=True)
    points_awarded: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    award_date: Mapped[date] = mapped_column(Date)
    source_type: Mapped[str] = mapped_column(String(30))
    source_request_id: Mapped[int | None] = mapped_column(ForeignKey("point_request.request_id"), index=True)
    source_ocr_batch_id: Mapped[int | None] = mapped_column(ForeignKey("ocr_batch.ocr_batch_id"), index=True)
    approved_by: Mapped[str] = mapped_column(ForeignKey(USER_FK))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))
    supersedes_ledger_id: Mapped[int | None] = mapped_column(ForeignKey("point_ledger.ledger_id"))
    __table_args__ = (
        CheckConstraint("points_awarded >= 0", name="points_awarded"),
        CheckConstraint(one_of("source_type", LedgerSourceType), name="source_type"),
        CheckConstraint(one_of("status", LedgerStatus), name="status"),
        # Approved source rules (CLAUDE.md 18.5): the source columns must agree with source_type.
        CheckConstraint(
            "(source_type = 'BULK_APPROVAL' AND source_ocr_batch_id IS NOT NULL AND source_request_id IS NULL)"
            " OR (source_type = 'INDIVIDUAL_CLAIM' AND source_request_id IS NOT NULL AND source_ocr_batch_id IS NULL)"
            " OR (source_type = 'MANUAL_CORRECTION' AND supersedes_ledger_id IS NOT NULL)",
            name="source_consistency"),
        # At most one ACTIVE award per student per event (superseded/revoked rows are history and don't count).
        Index("ux_point_ledger_one_active_award", "usn", "event_id", unique=True,
              postgresql_where=text("status = 'ACTIVE'")),
    )


class PointLedgerDocument(Base):
    __tablename__ = "point_ledger_document"
    ledger_id: Mapped[int] = mapped_column(ForeignKey("point_ledger.ledger_id"), primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.document_id"), primary_key=True, index=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **now_default())
    linked_by: Mapped[str] = mapped_column(ForeignKey(USER_FK))


class AuditLog(Base):
    """Append-only (a database trigger blocks UPDATE and DELETE). entity_type/entity_id is a generic reference."""
    __tablename__ = "audit_log"
    audit_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_email: Mapped[str | None] = mapped_column(ForeignKey(USER_FK), index=True)
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int] = mapped_column(BigInteger)
    old_values: Mapped[dict | None] = mapped_column(JSONB)
    new_values: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **now_default())
    __table_args__ = (Index("ix_audit_log_entity", "entity_type", "entity_id"),)


# --- STUDENT_POINT_SUMMARY (a view, not a table) -----------------------------------------------------------------
# Declared on a separate MetaData so table tooling (create_all, autogenerate) never tries to create it as a table.
# The view itself is created by the baseline migration.
view_metadata = MetaData()
student_point_summary = Table(
    "student_point_summary", view_metadata,
    Column("usn", String(20), primary_key=True),
    Column("name", String(150)),
    Column("required_points", Numeric(6, 2)),
    Column("earned_points", Numeric),
    Column("remaining_points", Numeric),
    Column("target_reached", Boolean),
)

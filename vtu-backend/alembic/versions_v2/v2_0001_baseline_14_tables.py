"""Baseline: the 14-table schema from CLAUDE.md Section 5, the STUDENT_POINT_SUMMARY view and the append-only
audit trigger.

Replaces the superseded UUID-schema history in alembic/versions/ (Git tag: pre-schema-rebuild). This is a new root
revision: it does not upgrade an old-schema database; such a database must be recreated.

Constraint names are explicit and must equal the names the models produce through the naming convention in
app/db.py. tests/test_schema_offline.py compares this migration's SQL with the models' SQL.

Revision ID: v2_0001
Revises: (none - new baseline)
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v2_0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade():
    # Creation order follows foreign-key dependencies (parents first). The one cycle,
    # point_request.correction_of_ledger_id <-> point_ledger.source_request_id, is closed with ALTER TABLE below.
    op.create_table(
        "department",
        sa.Column("department_code", sa.String(10), nullable=False),
        sa.Column("department_name", sa.String(100), nullable=False),
        sa.PrimaryKeyConstraint("department_code", name="pk_department"),
        sa.UniqueConstraint("department_name", name="uq_department_department_name"),
    )
    op.create_table(
        "point_requirement",
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("required_points", sa.Numeric(6, 2), nullable=False),
        sa.CheckConstraint("entry_type IN ('REGULAR', 'LATERAL')", name=op.f("ck_point_requirement_entry_type")),
        sa.CheckConstraint("required_points > 0", name=op.f("ck_point_requirement_required_points")),
        sa.PrimaryKeyConstraint("entry_type", name="pk_point_requirement"),
    )
    op.create_table(
        "club",
        sa.Column("club_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("club_name", sa.String(150), nullable=False),
        sa.Column("club_email", sa.String(150), nullable=True),
        sa.PrimaryKeyConstraint("club_id", name="pk_club"),
        sa.UniqueConstraint("club_email", name="uq_club_club_email"),
        sa.UniqueConstraint("club_name", name="uq_club_club_name"),
    )
    op.create_table(
        "student",
        sa.Column("usn", sa.String(20), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("email", sa.String(150), nullable=False),
        sa.Column("department_code", sa.String(10), nullable=False),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("admission_year", sa.SmallInteger(), nullable=False),
        sa.Column("current_semester", sa.SmallInteger(), nullable=True),
        sa.Column("student_status", sa.String(20), nullable=False),
        sa.CheckConstraint("entry_type IN ('REGULAR', 'LATERAL')", name=op.f("ck_student_entry_type")),
        sa.CheckConstraint("current_semester BETWEEN 1 AND 8", name=op.f("ck_student_current_semester")),
        sa.CheckConstraint("student_status IN ('ACTIVE', 'GRADUATED', 'INACTIVE', 'ARCHIVED')",
                           name=op.f("ck_student_student_status")),
        sa.ForeignKeyConstraint(["department_code"], ["department.department_code"],
                                name="fk_student_department_code_department"),
        sa.ForeignKeyConstraint(["entry_type"], ["point_requirement.entry_type"],
                                name="fk_student_entry_type_point_requirement"),
        sa.PrimaryKeyConstraint("usn", name="pk_student"),
        sa.UniqueConstraint("email", name="uq_student_email"),
    )
    op.create_index("ix_student_department_code", "student", ["department_code"])

    op.create_table(
        "user_account",
        sa.Column("email", sa.String(150), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("account_status", sa.String(20), nullable=False),
        sa.Column("student_usn", sa.String(20), nullable=True),
        sa.Column("department_code", sa.String(10), nullable=True),
        sa.Column("club_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('STUDENT', 'CLUB_LEAD', 'HOD', 'DEAN')", name=op.f("ck_user_account_role")),
        sa.CheckConstraint("account_status IN ('ACTIVE', 'DISABLED')", name=op.f("ck_user_account_account_status")),
        sa.CheckConstraint(
            "(role = 'STUDENT' AND student_usn IS NOT NULL AND department_code IS NULL AND club_id IS NULL)"
            " OR (role = 'HOD' AND student_usn IS NULL AND department_code IS NOT NULL AND club_id IS NULL)"
            " OR (role = 'CLUB_LEAD' AND student_usn IS NULL AND department_code IS NULL AND club_id IS NOT NULL)"
            " OR (role = 'DEAN' AND student_usn IS NULL AND department_code IS NULL AND club_id IS NULL)",
            name=op.f("ck_user_account_role_association")),
        sa.ForeignKeyConstraint(["club_id"], ["club.club_id"], name="fk_user_account_club_id_club"),
        sa.ForeignKeyConstraint(["department_code"], ["department.department_code"],
                                name="fk_user_account_department_code_department"),
        sa.ForeignKeyConstraint(["student_usn"], ["student.usn"], name="fk_user_account_student_usn_student"),
        sa.PrimaryKeyConstraint("email", name="pk_user_account"),
    )
    op.create_index("ix_user_account_club_id", "user_account", ["club_id"])
    op.create_index("ix_user_account_student_usn", "user_account", ["student_usn"])

    op.create_table(
        "event",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_name", sa.String(200), nullable=False),
        sa.Column("club_id", sa.BigInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("venue", sa.String(200), nullable=True),
        sa.Column("registration_url", sa.Text(), nullable=True),
        sa.Column("standard_points", sa.Numeric(6, 2), nullable=True),
        sa.Column("academic_year", sa.String(20), nullable=False),
        sa.Column("event_status", sa.String(30), nullable=False),
        sa.Column("created_by", sa.String(150), nullable=False),
        sa.CheckConstraint("standard_points >= 0", name=op.f("ck_event_standard_points")),
        sa.CheckConstraint("event_status IN ('DRAFT', 'PUBLISHED', 'COMPLETED', 'CLOSED')",
                           name=op.f("ck_event_event_status")),
        sa.ForeignKeyConstraint(["club_id"], ["club.club_id"], name="fk_event_club_id_club"),
        sa.ForeignKeyConstraint(["created_by"], ["user_account.email"], name="fk_event_created_by_user_account"),
        sa.PrimaryKeyConstraint("event_id", name="pk_event"),
    )
    op.create_index("ix_event_club_id", "event", ["club_id"])

    op.create_table(
        "document",
        sa.Column("document_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("document_type", sa.String(30), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_hash", sa.CHAR(64), nullable=False),
        sa.Column("uploaded_by", sa.String(150), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("document_status", sa.String(20), nullable=False),
        sa.CheckConstraint("document_type IN ('SIGNED_POINT_LIST', 'CERTIFICATE', 'ATTENDANCE_SHEET', 'PHOTO', "
                           "'REPORT', 'OTHER')", name=op.f("ck_document_document_type")),
        sa.CheckConstraint("file_size_bytes > 0", name=op.f("ck_document_file_size_bytes")),
        sa.CheckConstraint("document_status IN ('ACTIVE', 'ARCHIVED', 'DELETED')", name=op.f("ck_document_document_status")),
        sa.ForeignKeyConstraint(["event_id"], ["event.event_id"], name="fk_document_event_id_event"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["user_account.email"], name="fk_document_uploaded_by_user_account"),
        sa.PrimaryKeyConstraint("document_id", name="pk_document"),
        sa.UniqueConstraint("storage_key", name="uq_document_storage_key"),
    )
    op.create_index("ix_document_event_id", "document", ["event_id"])
    op.create_index("ix_document_file_hash", "document", ["file_hash"])
    op.create_index("ix_document_uploaded_by", "document", ["uploaded_by"])

    op.create_table(
        "ocr_batch",
        sa.Column("ocr_batch_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("started_by", sa.String(150), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pages_processed", sa.Integer(), nullable=True),
        sa.Column("records_detected", sa.Integer(), nullable=True),
        sa.Column("valid_records", sa.Integer(), nullable=True),
        sa.Column("exception_count", sa.Integer(), nullable=True),
        sa.Column("extracted_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('QUEUED', 'PROCESSING', 'REVIEW', 'COMMITTED', 'FAILED')",
                           name=op.f("ck_ocr_batch_status")),
        sa.CheckConstraint("pages_processed >= 0", name=op.f("ck_ocr_batch_pages_processed")),
        sa.CheckConstraint("records_detected >= 0", name=op.f("ck_ocr_batch_records_detected")),
        sa.CheckConstraint("valid_records >= 0", name=op.f("ck_ocr_batch_valid_records")),
        sa.CheckConstraint("exception_count >= 0", name=op.f("ck_ocr_batch_exception_count")),
        sa.ForeignKeyConstraint(["document_id"], ["document.document_id"], name="fk_ocr_batch_document_id_document"),
        sa.ForeignKeyConstraint(["started_by"], ["user_account.email"], name="fk_ocr_batch_started_by_user_account"),
        sa.PrimaryKeyConstraint("ocr_batch_id", name="pk_ocr_batch"),
    )
    op.create_index("ix_ocr_batch_document_id", "ocr_batch", ["document_id"])

    op.create_table(
        "ocr_record",
        sa.Column("ocr_record_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("ocr_batch_id", sa.BigInteger(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("extracted_usn", sa.String(20), nullable=True),
        sa.Column("matched_student_usn", sa.String(20), nullable=True),
        sa.Column("extracted_name", sa.String(150), nullable=True),
        sa.Column("extracted_points", sa.Numeric(6, 2), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("raw_extraction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("validation_status", sa.String(30), nullable=False),
        sa.Column("validation_message", sa.Text(), nullable=True),
        sa.CheckConstraint("confidence_score BETWEEN 0 AND 1", name=op.f("ck_ocr_record_confidence_score")),
        sa.CheckConstraint("validation_status IN ('VALID', 'EXCEPTION', 'CONFIRMED', 'DISMISSED')",
                           name=op.f("ck_ocr_record_validation_status")),
        sa.ForeignKeyConstraint(["matched_student_usn"], ["student.usn"],
                                name="fk_ocr_record_matched_student_usn_student"),
        sa.ForeignKeyConstraint(["ocr_batch_id"], ["ocr_batch.ocr_batch_id"], name="fk_ocr_record_ocr_batch_id_ocr_batch"),
        sa.PrimaryKeyConstraint("ocr_record_id", name="pk_ocr_record"),
    )
    op.create_index("ix_ocr_record_matched_student_usn", "ocr_record", ["matched_student_usn"])
    op.create_index("ix_ocr_record_ocr_batch_id", "ocr_record", ["ocr_batch_id"])

    # point_request is created WITHOUT its correction_of_ledger_id FK; point_ledger does not exist yet.
    op.create_table(
        "point_request",
        sa.Column("request_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("usn", sa.String(20), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("request_type", sa.String(30), nullable=False),
        sa.Column("claimed_points", sa.Numeric(6, 2), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("submitted_by", sa.String(150), nullable=False),
        sa.Column("correction_of_ledger_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("club_reviewed_by", sa.String(150), nullable=True),
        sa.Column("club_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("club_review_comment", sa.Text(), nullable=True),
        sa.Column("dean_decided_by", sa.String(150), nullable=True),
        sa.Column("dean_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dean_decision_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint("request_type IN ('NEW_ACTIVITY', 'MISSING_FROM_LIST', 'CORRECTION')",
                           name=op.f("ck_point_request_request_type")),
        sa.CheckConstraint("claimed_points >= 0", name=op.f("ck_point_request_claimed_points")),
        sa.CheckConstraint("status IN ('PENDING_CLUB', 'PENDING_DEAN', 'APPROVED', 'REJECTED', 'RESOLVED', "
                           "'WITHDRAWN')", name=op.f("ck_point_request_status")),
        sa.CheckConstraint("(request_type = 'CORRECTION') = (correction_of_ledger_id IS NOT NULL)",
                           name=op.f("ck_point_request_correction_link")),
        sa.CheckConstraint(
            "status <> 'REJECTED'"
            " OR (dean_decided_by IS NOT NULL AND btrim(coalesce(dean_decision_comment, '')) <> '')"
            " OR (dean_decided_by IS NULL AND club_reviewed_by IS NOT NULL"
            " AND btrim(coalesce(club_review_comment, '')) <> '')",
            name=op.f("ck_point_request_rejection_comment")),
        sa.ForeignKeyConstraint(["club_reviewed_by"], ["user_account.email"],
                                name="fk_point_request_club_reviewed_by_user_account"),
        sa.ForeignKeyConstraint(["dean_decided_by"], ["user_account.email"],
                                name="fk_point_request_dean_decided_by_user_account"),
        sa.ForeignKeyConstraint(["event_id"], ["event.event_id"], name="fk_point_request_event_id_event"),
        sa.ForeignKeyConstraint(["submitted_by"], ["user_account.email"],
                                name="fk_point_request_submitted_by_user_account"),
        sa.ForeignKeyConstraint(["usn"], ["student.usn"], name="fk_point_request_usn_student"),
        sa.PrimaryKeyConstraint("request_id", name="pk_point_request"),
    )
    op.create_index("ix_point_request_event_id", "point_request", ["event_id"])
    op.create_index("ix_point_request_status", "point_request", ["status"])
    op.create_index("ix_point_request_usn", "point_request", ["usn"])

    op.create_table(
        "point_request_document",
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("attached_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["document.document_id"],
                                name="fk_point_request_document_document_id_document"),
        sa.ForeignKeyConstraint(["request_id"], ["point_request.request_id"],
                                name="fk_point_request_document_request_id_point_request"),
        sa.PrimaryKeyConstraint("request_id", "document_id", name="pk_point_request_document"),
    )
    op.create_index("ix_point_request_document_document_id", "point_request_document", ["document_id"])

    op.create_table(
        "point_ledger",
        sa.Column("ledger_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("usn", sa.String(20), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("points_awarded", sa.Numeric(6, 2), nullable=False),
        sa.Column("award_date", sa.Date(), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_request_id", sa.BigInteger(), nullable=True),
        sa.Column("source_ocr_batch_id", sa.BigInteger(), nullable=True),
        sa.Column("approved_by", sa.String(150), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("supersedes_ledger_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("points_awarded >= 0", name=op.f("ck_point_ledger_points_awarded")),
        sa.CheckConstraint("source_type IN ('BULK_APPROVAL', 'INDIVIDUAL_CLAIM', 'MANUAL_CORRECTION')",
                           name=op.f("ck_point_ledger_source_type")),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUPERSEDED', 'REVOKED')", name=op.f("ck_point_ledger_status")),
        sa.CheckConstraint(
            "(source_type = 'BULK_APPROVAL' AND source_ocr_batch_id IS NOT NULL AND source_request_id IS NULL)"
            " OR (source_type = 'INDIVIDUAL_CLAIM' AND source_request_id IS NOT NULL AND source_ocr_batch_id IS NULL)"
            " OR (source_type = 'MANUAL_CORRECTION' AND supersedes_ledger_id IS NOT NULL)",
            name=op.f("ck_point_ledger_source_consistency")),
        sa.ForeignKeyConstraint(["approved_by"], ["user_account.email"], name="fk_point_ledger_approved_by_user_account"),
        sa.ForeignKeyConstraint(["event_id"], ["event.event_id"], name="fk_point_ledger_event_id_event"),
        sa.ForeignKeyConstraint(["source_ocr_batch_id"], ["ocr_batch.ocr_batch_id"],
                                name="fk_point_ledger_source_ocr_batch_id_ocr_batch"),
        sa.ForeignKeyConstraint(["source_request_id"], ["point_request.request_id"],
                                name="fk_point_ledger_source_request_id_point_request"),
        sa.ForeignKeyConstraint(["supersedes_ledger_id"], ["point_ledger.ledger_id"],
                                name="fk_point_ledger_supersedes_ledger_id_point_ledger"),
        sa.ForeignKeyConstraint(["usn"], ["student.usn"], name="fk_point_ledger_usn_student"),
        sa.PrimaryKeyConstraint("ledger_id", name="pk_point_ledger"),
    )
    op.create_index("ix_point_ledger_event_id", "point_ledger", ["event_id"])
    op.create_index("ix_point_ledger_source_ocr_batch_id", "point_ledger", ["source_ocr_batch_id"])
    op.create_index("ix_point_ledger_source_request_id", "point_ledger", ["source_request_id"])
    op.create_index("ix_point_ledger_usn", "point_ledger", ["usn"])
    op.create_index("ux_point_ledger_one_active_award", "point_ledger", ["usn", "event_id"], unique=True,
                    postgresql_where=sa.text("status = 'ACTIVE'"))

    # Close the point_request <-> point_ledger cycle now that both tables exist.
    op.create_foreign_key("fk_point_request_correction_of_ledger_id_point_ledger", "point_request", "point_ledger",
                          ["correction_of_ledger_id"], ["ledger_id"])

    op.create_table(
        "point_ledger_document",
        sa.Column("ledger_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("linked_by", sa.String(150), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["document.document_id"],
                                name="fk_point_ledger_document_document_id_document"),
        sa.ForeignKeyConstraint(["ledger_id"], ["point_ledger.ledger_id"],
                                name="fk_point_ledger_document_ledger_id_point_ledger"),
        sa.ForeignKeyConstraint(["linked_by"], ["user_account.email"],
                                name="fk_point_ledger_document_linked_by_user_account"),
        sa.PrimaryKeyConstraint("ledger_id", "document_id", name="pk_point_ledger_document"),
    )
    op.create_index("ix_point_ledger_document_document_id", "point_ledger_document", ["document_id"])

    op.create_table(
        "audit_log",
        sa.Column("audit_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_email", sa.String(150), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("old_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["user_email"], ["user_account.email"], name="fk_audit_log_user_email_user_account"),
        sa.PrimaryKeyConstraint("audit_id", name="pk_audit_log"),
    )
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])
    op.create_index("ix_audit_log_user_email", "audit_log", ["user_email"])

    # STUDENT_POINT_SUMMARY: derived totals (CLAUDE.md Section 6). Only ACTIVE ledger rows count; students with no
    # awards get 0 (LEFT JOIN + COALESCE); earned is not capped at the target; remaining never goes below 0.
    op.execute("""
        CREATE VIEW student_point_summary AS
        SELECT s.usn,
               s.name,
               pr.required_points,
               COALESCE(SUM(pl.points_awarded), 0) AS earned_points,
               GREATEST(pr.required_points - COALESCE(SUM(pl.points_awarded), 0), 0) AS remaining_points,
               COALESCE(SUM(pl.points_awarded), 0) >= pr.required_points AS target_reached
        FROM student s
        JOIN point_requirement pr ON pr.entry_type = s.entry_type
        LEFT JOIN point_ledger pl ON pl.usn = s.usn AND pl.status = 'ACTIVE'
        GROUP BY s.usn, s.name, pr.required_points
    """)

    # AUDIT_LOG is append-only: any UPDATE or DELETE raises an error.
    op.execute("""
        CREATE FUNCTION audit_log_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'audit_log is append-only'; END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()
    """)


def downgrade():
    op.execute("DROP TRIGGER audit_log_no_change ON audit_log")
    op.execute("DROP FUNCTION audit_log_immutable()")
    op.execute("DROP VIEW student_point_summary")
    op.drop_table("audit_log")
    op.drop_table("point_ledger_document")
    op.drop_constraint("fk_point_request_correction_of_ledger_id_point_ledger", "point_request", type_="foreignkey")
    op.drop_table("point_ledger")
    op.drop_table("point_request_document")
    op.drop_table("point_request")
    op.drop_table("ocr_record")
    op.drop_table("ocr_batch")
    op.drop_table("document")
    op.drop_table("event")
    op.drop_table("user_account")
    op.drop_table("student")
    op.drop_table("club")
    op.drop_table("point_requirement")
    op.drop_table("department")

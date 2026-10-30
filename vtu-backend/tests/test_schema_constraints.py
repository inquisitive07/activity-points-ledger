"""Database constraint tests (CLAUDE.md 15.1). Need PostgreSQL: they run against the throw-away <db>_test database
created by conftest.py (migrated to head and seeded). Every test's changes are rolled back.
"""
import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import Base, Event, PointLedger, student_point_summary

EVENT = "(SELECT event_id FROM event WHERE event_name = '{}')"
ROBOWARS, RHYTHM, HACK = EVENT.format("RoboWars 2026"), EVENT.format("Rhythm Fest"), EVENT.format("HackRVCE 24h")
BATCH = "(SELECT min(ocr_batch_id) FROM ocr_batch)"


def rejected(db, sql, error=IntegrityError):
    """Run one statement in a savepoint and return the database error it must raise."""
    with pytest.raises(error) as exc:
        with db.begin_nested():
            db.execute(text(sql))
    return str(exc.value.orig)


def ledger_sql(usn="1RV23CY402", event=ROBOWARS, source="BULK_APPROVAL", batch=BATCH, request="NULL",
               status="ACTIVE", supersedes="NULL", points="5"):
    return (f"INSERT INTO point_ledger (usn, event_id, points_awarded, award_date, source_type, source_request_id, "
            f"source_ocr_batch_id, approved_by, approved_at, status, supersedes_ledger_id) VALUES ('{usn}', {event}, "
            f"{points}, current_date, '{source}', {request}, {batch}, 'dean@vtu.edu.in', now(), '{status}', {supersedes})")


# --- structure ---------------------------------------------------------------------------------------------------

def test_all_14_tables_and_the_view_exist(db):
    insp = inspect(db.connection())
    assert set(insp.get_table_names()) - {"alembic_version"} == set(Base.metadata.tables)
    assert insp.get_view_names() == ["student_point_summary"]


# --- keys, nullability, checks -----------------------------------------------------------------------------------

def test_primary_key_rejects_duplicates(db):
    assert "pk_department" in rejected(db, "INSERT INTO department VALUES ('CS', 'Duplicate CS')")


def test_identity_columns_cannot_be_set_by_hand(db):
    rejected(db, "INSERT INTO club (club_id, club_name) VALUES (999, 'Manual Id Club')", DBAPIError)


def test_foreign_key_rejects_unknown_department(db):
    msg = rejected(db, "INSERT INTO student VALUES ('1RV22XX001', 'X', 'x@vtu.edu.in', 'XX', 'REGULAR', 2022, 1, 'ACTIVE')")
    assert "fk_student_department_code_department" in msg


def test_student_entry_type_must_exist_in_point_requirement(db):
    db.execute(text("ALTER TABLE student DROP CONSTRAINT ck_student_entry_type"))   # isolate the FK
    msg = rejected(db, "INSERT INTO student VALUES ('1RV22CS900', 'X', 'x@vtu.edu.in', 'CS', 'OTHER', 2022, 1, 'ACTIVE')")
    assert "fk_student_entry_type_point_requirement" in msg


def test_not_null_is_enforced(db):
    msg = rejected(db, "INSERT INTO student VALUES ('1RV22CS901', NULL, 'y@vtu.edu.in', 'CS', 'REGULAR', 2022, 1, 'ACTIVE')")
    assert 'null value in column "name"' in msg


@pytest.mark.parametrize("sql, constraint", [
    ("INSERT INTO student VALUES ('1RV22CS902', 'X', 'z@vtu.edu.in', 'CS', 'REGULAR', 2022, 9, 'ACTIVE')",
     "ck_student_current_semester"),
    ("INSERT INTO student VALUES ('1RV22CS903', 'X', 'w@vtu.edu.in', 'CS', 'REGULAR', 2022, 1, 'EXPELLED')",
     "ck_student_student_status"),
    ("UPDATE event SET event_status = 'CANCELLED' WHERE event_name = 'RoboWars 2026'", "ck_event_event_status"),
    ("UPDATE event SET standard_points = -1 WHERE event_name = 'RoboWars 2026'", "ck_event_standard_points"),
    ("UPDATE document SET file_size_bytes = 0 WHERE document_id = (SELECT min(document_id) FROM document)",
     "ck_document_file_size_bytes"),
    ("UPDATE ocr_record SET confidence_score = 1.5 WHERE ocr_record_id = (SELECT min(ocr_record_id) FROM ocr_record)",
     "ck_ocr_record_confidence_score"),
    ("UPDATE point_requirement SET required_points = 0 WHERE entry_type = 'REGULAR'",
     "ck_point_requirement_required_points"),
    ("UPDATE user_account SET account_status = 'LOCKED' WHERE email = 'dean@vtu.edu.in'",
     "ck_user_account_account_status"),
])
def test_check_constraints(db, sql, constraint):
    assert constraint in rejected(db, sql)


def test_storage_key_is_unique(db):
    msg = rejected(db, "INSERT INTO document (document_type, file_name, storage_key, mime_type, file_size_bytes, "
                       "file_hash, uploaded_by, document_status) SELECT 'OTHER', 'copy.pdf', storage_key, mime_type, "
                       "file_size_bytes, file_hash, uploaded_by, 'ACTIVE' FROM document LIMIT 1")
    assert "uq_document_storage_key" in msg


# --- role-to-association rule (CLAUDE.md 3.5) --------------------------------------------------------------------

@pytest.mark.parametrize("role, usn, dept, club", [
    ("STUDENT", "NULL", "NULL", "NULL"),                 # student without a student
    ("STUDENT", "'1RV23CY402'", "'CS'", "NULL"),         # student with an extra department
    ("HOD", "NULL", "NULL", "NULL"),                     # HOD without a department
    ("CLUB_LEAD", "NULL", "'CS'", "NULL"),               # club lead with a department instead of a club
    ("DEAN", "NULL", "NULL", "(SELECT min(club_id) FROM club)"),
])
def test_role_association_rule(db, role, usn, dept, club):
    msg = rejected(db, f"INSERT INTO user_account (email, password_hash, role, account_status, student_usn, "
                       f"department_code, club_id) VALUES ('new@vtu.edu.in', 'x', '{role}', 'ACTIVE', {usn}, {dept}, {club})")
    assert "ck_user_account_role_association" in msg


def test_role_association_accepts_valid_accounts(db):
    db.execute(text("INSERT INTO user_account (email, password_hash, role, account_status, department_code) "
                    "VALUES ('hod.new@vtu.edu.in', 'x', 'HOD', 'ACTIVE', 'EC')"))


# --- point_request rules -----------------------------------------------------------------------------------------

def request_sql(type_="NEW_ACTIVITY", status="PENDING_CLUB", correction="NULL", extra_cols="", extra_vals=""):
    return (f"INSERT INTO point_request (usn, event_id, request_type, claimed_points, reason, submitted_by, "
            f"correction_of_ledger_id, status{extra_cols}) VALUES ('1RV23CY402', {HACK}, '{type_}', 15, 'r', "
            f"'student6@vtu.edu.in', {correction}, '{status}'{extra_vals})")


def test_correction_request_must_reference_a_ledger_row(db):
    assert "ck_point_request_correction_link" in rejected(db, request_sql("CORRECTION"))
    one_award = "(SELECT min(ledger_id) FROM point_ledger)"
    assert "ck_point_request_correction_link" in rejected(db, request_sql("NEW_ACTIVITY", correction=one_award))


def test_rejection_requires_a_comment(db):
    club = ", club_reviewed_by, club_review_comment"
    assert "ck_point_request_rejection_comment" in rejected(db, request_sql(status="REJECTED"))
    assert "ck_point_request_rejection_comment" in rejected(
        db, request_sql(status="REJECTED", extra_cols=club, extra_vals=", 'coding.lead@vtu.edu.in', '   '"))
    db.execute(text(request_sql(status="REJECTED", extra_cols=club,
                                extra_vals=", 'coding.lead@vtu.edu.in', 'Not on the attendance sheet'")))


def test_dean_rejection_needs_the_dean_comment(db):
    cols = ", club_reviewed_by, club_review_comment, dean_decided_by"
    vals = ", 'coding.lead@vtu.edu.in', 'Verified', 'dean@vtu.edu.in'"
    assert "ck_point_request_rejection_comment" in rejected(db, request_sql(status="REJECTED", extra_cols=cols,
                                                                            extra_vals=vals))


def test_request_cannot_reference_unknown_ledger_row(db):
    assert "fk_point_request_correction_of_ledger_id_point_ledger" in rejected(db, request_sql("CORRECTION",
                                                                                              correction="999999"))


# --- point_ledger rules ------------------------------------------------------------------------------------------

def test_ledger_source_columns_must_match_source_type(db):
    any_request = "(SELECT min(request_id) FROM point_request)"
    for sql in (ledger_sql(source="BULK_APPROVAL", batch="NULL"),
                ledger_sql(source="BULK_APPROVAL", request=any_request),
                ledger_sql(source="INDIVIDUAL_CLAIM", batch="NULL"),
                ledger_sql(source="INDIVIDUAL_CLAIM", request=any_request),           # both sources set
                ledger_sql(source="MANUAL_CORRECTION", batch="NULL")):                # supersedes missing
        assert "ck_point_ledger_source_consistency" in rejected(db, sql)


def test_only_one_active_award_per_student_and_event(db):
    msg = rejected(db, ledger_sql(usn="1RV22CS001", event=ROBOWARS))   # already has an ACTIVE RoboWars award
    assert "ux_point_ledger_one_active_award" in msg
    db.execute(text(ledger_sql(usn="1RV22CS001", event=ROBOWARS, status="REVOKED")))   # history rows are allowed


def test_points_cannot_be_negative(db):
    assert "ck_point_ledger_points_awarded" in rejected(db, ledger_sql(points="-1"))


def test_junction_tables_reject_duplicate_links(db):
    msg = rejected(db, "INSERT INTO point_ledger_document (ledger_id, document_id, linked_by) "
                       "SELECT ledger_id, document_id, linked_by FROM point_ledger_document LIMIT 1")
    assert "pk_point_ledger_document" in msg
    msg = rejected(db, "INSERT INTO point_request_document (request_id, document_id) "
                       "SELECT request_id, document_id FROM point_request_document LIMIT 1")
    assert "pk_point_request_document" in msg


def test_correction_preserves_the_original_award(db):
    rhythm = select(Event.event_id).where(Event.event_name == "Rhythm Fest").scalar_subquery()
    rows = db.execute(select(PointLedger.ledger_id, PointLedger.status, PointLedger.points_awarded,
                             PointLedger.supersedes_ledger_id)
                      .where(PointLedger.usn == "1RV22CS001", PointLedger.event_id == rhythm)
                      .order_by(PointLedger.ledger_id)).all()
    (orig_id, orig_status, orig_pts, _), (_, new_status, new_pts, supersedes) = rows
    assert (orig_status, orig_pts, new_status, new_pts) == ("SUPERSEDED", 15, "ACTIVE", 20)
    assert supersedes == orig_id


# --- audit log ---------------------------------------------------------------------------------------------------

def test_audit_log_is_append_only(db):
    assert "append-only" in rejected(db, "UPDATE audit_log SET action = 'X'", DBAPIError)
    assert "append-only" in rejected(db, "DELETE FROM audit_log", DBAPIError)
    db.execute(text("INSERT INTO audit_log (user_email, action, entity_type, entity_id) "
                    "VALUES ('dean@vtu.edu.in', 'TEST', 'event', 1)"))


# --- STUDENT_POINT_SUMMARY view ----------------------------------------------------------------------------------

def summary(db, usn):
    return db.execute(select(student_point_summary).where(student_point_summary.c.usn == usn)).one()


def test_view_counts_only_active_awards(db):
    s = summary(db, "1RV22CS001")   # RoboWars 10 + Rhythm 20 (corrected; the superseded 15 is ignored) + NSS 40
    assert (s.required_points, s.earned_points, s.remaining_points, s.target_reached) == (100, 70, 30, False)


def test_view_gives_zero_for_a_student_with_no_awards(db):
    s = summary(db, "1RV23CY402")   # lateral entry, no ledger rows
    assert (s.required_points, s.earned_points, s.remaining_points, s.target_reached) == (75, 0, 75, False)


def test_view_does_not_cap_earned_points(db):
    s = summary(db, "1RV22IS014")
    assert (s.earned_points, s.remaining_points, s.target_reached) == (100, 0, True)
    db.execute(text(ledger_sql(usn="1RV22IS014", event=EVENT.format("Cloud Computing Workshop"), points="5")))
    s = summary(db, "1RV22IS014")
    assert (s.earned_points, s.remaining_points, s.target_reached) == (105, 0, True)


def test_view_lists_every_student(db):
    assert db.scalar(select(func.count()).select_from(student_point_summary)) == db.scalar(text("SELECT count(*) FROM student"))


# --- transactions ------------------------------------------------------------------------------------------------

def test_failed_bulk_commit_rolls_back_every_award(db):
    """One bad row in a multi-student commit must leave no award from that commit (CLAUDE.md 15.5)."""
    before = db.scalar(select(func.count()).select_from(PointLedger))
    with pytest.raises(IntegrityError):
        with db.begin_nested():                                     # stands in for the commit transaction
            db.execute(text(ledger_sql(usn="1RV23CY402", event=HACK)))   # valid
            db.execute(text(ledger_sql(usn="1RV22CD033", event=HACK)))   # valid
            db.execute(text(ledger_sql(usn="1RV22CS001", event=ROBOWARS)))   # duplicate ACTIVE award -> fails
    assert db.scalar(select(func.count()).select_from(PointLedger)) == before


# --- seed ----------------------------------------------------------------------------------------------------------

def test_seed_is_idempotent(db_engine, capsys):
    from scripts.seed import run
    with db_engine.connect() as c:
        counts = {t: c.scalar(text(f"SELECT count(*) FROM {t}")) for t in Base.metadata.tables}
    run()
    assert "nothing to add" in capsys.readouterr().out
    with db_engine.connect() as c:
        assert counts == {t: c.scalar(text(f"SELECT count(*) FROM {t}")) for t in Base.metadata.tables}
    assert all(counts.values()), {t: n for t, n in counts.items() if not n}   # seed fills every table


def test_seed_document_metadata_matches_the_stored_files(db):
    import hashlib
    from pathlib import Path
    from app.config import settings
    for key, size, digest in db.execute(text("SELECT storage_key, file_size_bytes, file_hash FROM document")):
        data = (Path(settings.storage_dir) / key).read_bytes()
        assert (len(data), hashlib.sha256(data).hexdigest()) == (size, digest)
        assert data.startswith(b"%PDF-")

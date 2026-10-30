"""CP4: OCR batches, extraction, matching, validation and Dean review.

Uses a deterministic fake extractor (no PaddleOCR model is imported or downloaded) and runs background processing in
the `db` fixture's transaction, so nothing persists. Files go to the temporary test storage. Seed facts come from
scripts/seed.py: Aarav (1RV22CS001) and Diya (1RV22IS014) hold ACTIVE RoboWars awards; nobody holds an award for the
Coding Club's "Cloud Computing Workshop".
"""
import sys
from contextlib import nullcontext
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import (AuditLog, Club, Document, Event, OcrBatch, OcrRecord, PointLedger, PointRequest)
from app.ocr import ExtractedRow, Extraction, ExtractionError, Extractor, get_extractor, get_ocr_session_factory
from app.ocr.paddle import PaddleExtractor
from app.ocr.pipeline import UNEXPECTED, process_batch
from app.ocr.table import TextLine, parse_row, rows_from_lines
from app.ocr.validation import names_match, normalize_usn, parse_points
from app.storage import get_storage
from scripts.seed import tiny_pdf

PW = "vtu@123"
DEAN, HOD, AARAV, ROBOTICS = "dean@vtu.edu.in", "hod@vtu.edu.in", "student@vtu.edu.in", "club@vtu.edu.in"
CLOUD, ROBOWARS = "Cloud Computing Workshop", "RoboWars 2026"
CLOUD_TITLE = "Cloud Computing Workshop - Coding Club - Activity Points List"
NEHA, ISHITA, KARTHIK, AARAV_USN = "1RV22CD033", "1RV22EC008", "1RV23EI419", "1RV22CS001"


# --- fake extractor and fixtures -----------------------------------------------------------------------------------

def row(usn, name, points="10", confidence=0.98, page=1, number=None, issue=None):
    return ExtractedRow(page_number=page, row_number=number, usn=usn, name=name, points=points,
                        confidence=confidence, raw={"text": f"{usn} {name} {points}"}, structure_issue=issue)


def good_rows():
    return [row(NEHA, "Neha Patil", number=1), row(ISHITA, "Ishita Rao", number=2),
            row(KARTHIK, "Karthik Bhat", "7.5", number=3)]


class FakeExtractor(Extractor):
    name = "fake"

    def __init__(self):
        self.rows, self.title, self.error, self.calls = good_rows(), CLOUD_TITLE, None, 0

    def extract(self, data, mime_type):
        self.calls += 1
        assert data.startswith(b"%PDF-") and mime_type == "application/pdf"     # bytes came through storage
        if self.error:
            raise self.error
        for i, r in enumerate(self.rows, 1):
            r.row_number = r.row_number or i
        return Extraction(pages_processed=1, rows=self.rows, title_text=self.title, metadata={"fake": True})


@pytest.fixture
def fake():
    return FakeExtractor()


@pytest.fixture
def api(client, db, fake):
    from app.main import app
    app.dependency_overrides[get_extractor] = lambda: fake
    app.dependency_overrides[get_ocr_session_factory] = lambda: (lambda: nullcontext(db))
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_extractor, None)
        app.dependency_overrides.pop(get_ocr_session_factory, None)


@pytest.fixture
def as_(api):
    cache = {}

    def headers(email):
        if email not in cache:
            r = api.post("/auth/login", json={"email": email, "password": PW})
            assert r.status_code == 200, r.text
            cache[email] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return cache[email]
    return headers


def event_id(db, name):
    return db.scalar(select(Event.event_id).where(Event.event_name == name))


def signed_list(api, as_, db, event=CLOUD, doc_type="SIGNED_POINT_LIST", label="signed list") -> int:
    form = {"document_type": doc_type}
    if event:
        form["event_id"] = str(event_id(db, event))
    r = api.post("/documents", headers=as_(DEAN), data=form,
                 files={"file": ("list.pdf", tiny_pdf([label]), "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["document_id"]


def start(api, as_, document_id, expect=202) -> dict:
    r = api.post("/ocr/batches", headers=as_(DEAN), json={"document_id": document_id})
    assert r.status_code == expect, r.text
    return r.json()


def run(api, as_, db, event=CLOUD) -> tuple[dict, list[dict]]:
    """Upload a signed list, process it, return (batch, records)."""
    created = start(api, as_, signed_list(api, as_, db, event))
    assert created["status"] == "QUEUED"
    return batch(api, as_, created["ocr_batch_id"]), records(api, as_, created["ocr_batch_id"])


def batch(api, as_, batch_id) -> dict:
    r = api.get(f"/ocr/batches/{batch_id}", headers=as_(DEAN))
    assert r.status_code == 200, r.text
    return r.json()


def records(api, as_, batch_id) -> list[dict]:
    r = api.get(f"/ocr/batches/{batch_id}/records", headers=as_(DEAN))
    assert r.status_code == 200, r.text
    return r.json()


def by_usn(recs, usn):
    return next(r for r in recs if r["extracted_usn"] == usn)


def audits(db, action, entity_id=None):
    stmt = select(AuditLog).where(AuditLog.action == action)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    return db.scalars(stmt.order_by(AuditLog.audit_id)).all()


def official_state(db):
    """Everything CP4 must never change: the ledger and the requests, row by row."""
    return (db.execute(text("SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY ledger_id), '')) "
                            "FROM point_ledger t")).scalar(),
            db.execute(text("SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY request_id), '')) "
                            "FROM point_request t")).scalar())


# --- extraction and batch processing -------------------------------------------------------------------------------

def test_successful_extraction(api, as_, db, fake):
    b, recs = run(api, as_, db)
    assert b["status"] == "REVIEW" and b["completed_at"] and b["error_message"] is None
    assert (b["pages_processed"], b["records_detected"], b["valid_records"], b["exception_count"]) == (1, 3, 3, 0)
    assert b["record_counts"] == {"VALID": 3, "EXCEPTION": 0, "CONFIRMED": 0, "DISMISSED": 0}
    assert b["event_name"] == CLOUD and b["extracted_metadata"]["fake"] is True
    check = b["extracted_metadata"]["event_check"]
    assert (check["event_status"], check["club_status"]) == ("MATCHED", "MATCHED")
    assert [r["validation_status"] for r in recs] == ["VALID"] * 3
    karthik = by_usn(recs, KARTHIK)
    assert karthik["matched_student_usn"] == KARTHIK and karthik["matched_student_name"] == "Karthik Bhat"
    assert Decimal(karthik["extracted_points"]) == Decimal("7.50") and karthik["validation_message"] is None
    assert karthik["raw_extraction"]["points"] == "7.5" and Decimal(karthik["confidence_score"]) == Decimal("0.98")
    assert fake.calls == 1
    bid = b["ocr_batch_id"]
    assert [len(audits(db, a, bid)) for a in ("OCR_BATCH_CREATED", "OCR_BATCH_PROCESSING",
                                               "OCR_BATCH_REVIEW_READY")] == [1, 1, 1]
    assert audits(db, "OCR_BATCH_CREATED", bid)[0].user_email == DEAN


def test_list_batches_filters(api, as_, db):
    b, _ = run(api, as_, db)
    r = api.get("/ocr/batches", headers=as_(DEAN), params={"status": "REVIEW"})
    assert r.status_code == 200 and b["ocr_batch_id"] in [x["ocr_batch_id"] for x in r.json()]
    assert all(x["status"] == "REVIEW" for x in r.json())
    r = api.get("/ocr/batches", headers=as_(DEAN), params={"document_id": b["document_id"]})
    assert [x["ocr_batch_id"] for x in r.json()] == [b["ocr_batch_id"]]
    assert api.get("/ocr/batches/999999", headers=as_(DEAN)).status_code == 404
    assert api.get("/ocr/batches/999999/records", headers=as_(DEAN)).status_code == 404
    r = api.get(f"/ocr/batches/{b['ocr_batch_id']}/records", headers=as_(DEAN),
                params={"validation_status": "EXCEPTION"})
    assert r.json() == []


# --- validation rules ----------------------------------------------------------------------------------------------

def single(api, as_, db, fake, r, event=CLOUD) -> dict:
    fake.rows = [r]
    _, recs = run(api, as_, db, event)
    assert len(recs) == 1
    return recs[0]


def test_unknown_usn(api, as_, db, fake):
    rec = single(api, as_, db, fake, row("1RV22CS999", "Sanjay K"))
    assert rec["validation_status"] == "EXCEPTION" and rec["matched_student_usn"] is None
    assert "Unknown USN: 1RV22CS999" in rec["validation_message"]


@pytest.mark.parametrize("read", ["1RV22CS0O1", "12345", "RV22CS001", "1RV22CS0011"])
def test_invalid_usn_format_is_never_corrected(api, as_, db, fake, read):
    rec = single(api, as_, db, fake, row(read, "Aarav Sharma"))
    assert rec["validation_status"] == "EXCEPTION" and rec["matched_student_usn"] is None
    assert rec["extracted_usn"] == read and rec["raw_extraction"]["usn"] == read
    assert "Invalid USN format" in rec["validation_message"]


def test_usn_is_only_trimmed_and_uppercased():
    assert normalize_usn(" 1rv22cd033 ") == "1RV22CD033"
    assert normalize_usn("1RV22CS0O1") == "1RV22CS0O1"           # no O -> 0 guessing
    assert normalize_usn("  ") is None


def test_missing_usn(api, as_, db, fake):
    rec = single(api, as_, db, fake, row(None, "Neha Patil"))
    assert rec["validation_status"] == "EXCEPTION" and "Missing USN" in rec["validation_message"]


def test_name_mismatch(api, as_, db, fake):
    rec = single(api, as_, db, fake, row(NEHA, "Sneha Rao"))
    assert rec["validation_status"] == "EXCEPTION" and rec["matched_student_usn"] == NEHA
    assert "Name mismatch: the list says 'Sneha Rao', the student record says 'Neha Patil'" in rec["validation_message"]


def test_name_match_ignores_case_order_and_punctuation(api, as_, db, fake):
    assert names_match("PATIL, Neha", "Neha Patil") and names_match("néha  patil", "Neha Patil")
    assert not names_match("Neha P", "Neha Patil") and not names_match(None, "Neha Patil")
    assert single(api, as_, db, fake, row(NEHA, "PATIL NEHA"))["validation_status"] == "VALID"


def test_missing_name(api, as_, db, fake):
    rec = single(api, as_, db, fake, row(NEHA, None))
    assert rec["validation_status"] == "EXCEPTION" and "Missing name" in rec["validation_message"]


def test_missing_points(api, as_, db, fake):
    rec = single(api, as_, db, fake, row(NEHA, "Neha Patil", None))
    assert rec["validation_status"] == "EXCEPTION" and rec["extracted_points"] is None
    assert "Missing points" in rec["validation_message"]


@pytest.mark.parametrize("read, why", [("1O", "not a number"), ("-5", "cannot be negative"),
                                       ("2.555", "up to 2 decimals"), ("10000", "at most 9999.99")])
def test_invalid_points(api, as_, db, fake, read, why):
    rec = single(api, as_, db, fake, row(NEHA, "Neha Patil", read))
    assert rec["validation_status"] == "EXCEPTION" and rec["extracted_points"] is None
    assert rec["raw_extraction"]["points"] == read
    assert f"Invalid points: OCR read '{read}'" in rec["validation_message"] and why in rec["validation_message"]


def test_points_parsing():
    assert parse_points("10") == (Decimal("10.00"), None)
    assert parse_points(" 7.5 ") == (Decimal("7.50"), None)
    assert parse_points("0") == (Decimal("0.00"), None)
    assert parse_points("") == (None, "Missing points")


def test_duplicate_rows_within_a_batch(api, as_, db, fake):
    fake.rows = [row(NEHA, "Neha Patil"), row(ISHITA, "Ishita Rao"), row(NEHA, "Neha Patil", "15")]
    b, recs = run(api, as_, db)
    dups = [r for r in recs if r["extracted_usn"] == NEHA]
    assert [r["validation_status"] for r in dups] == ["EXCEPTION", "EXCEPTION"]
    assert "Duplicate row: 1RV22CD033 also appears at page 1 row 3" in dups[0]["validation_message"]
    assert "also appears at page 1 row 1" in dups[1]["validation_message"]
    assert by_usn(recs, ISHITA)["validation_status"] == "VALID"
    assert (b["valid_records"], b["exception_count"]) == (1, 2)


def test_existing_active_award_is_flagged_and_cannot_be_confirmed(api, as_, db, fake):
    fake.title = "RoboWars 2026 - Robotics Club - Activity Points List"
    fake.rows = [row(AARAV_USN, "Aarav Sharma"), row(ISHITA, "Ishita Rao")]
    _, recs = run(api, as_, db, ROBOWARS)
    aarav = by_usn(recs, AARAV_USN)
    assert aarav["validation_status"] == "EXCEPTION" and aarav["matched_student_usn"] == AARAV_USN
    assert "Possible duplicate award: 1RV22CS001 already has an ACTIVE award for 'RoboWars 2026'" \
        in aarav["validation_message"]
    assert by_usn(recs, ISHITA)["validation_status"] == "VALID"
    r = api.post(f"/ocr/records/{aarav['ocr_record_id']}/confirm", headers=as_(DEAN))
    assert r.status_code == 409 and "never replaces" in r.json()["detail"]


def test_low_confidence_is_never_matched_automatically(api, as_, db, fake):
    rec = single(api, as_, db, fake, row(NEHA, "Neha Patil", confidence=0.61))
    assert rec["validation_status"] == "EXCEPTION" and rec["matched_student_usn"] is None
    assert "Low OCR confidence (0.61, below 0.90)" in rec["validation_message"]
    assert "not matched automatically because the OCR confidence is low" in rec["validation_message"]
    # The Dean selects the student; the low confidence stays visible, and the Dean may confirm over it.
    r = api.patch(f"/ocr/records/{rec['ocr_record_id']}", headers=as_(DEAN), json={"matched_student_usn": NEHA})
    assert r.status_code == 200 and r.json()["matched_student_usn"] == NEHA
    assert r.json()["validation_status"] == "EXCEPTION" and "Low OCR confidence" in r.json()["validation_message"]
    r = api.post(f"/ocr/records/{rec['ocr_record_id']}/confirm", headers=as_(DEAN))
    assert r.status_code == 200 and r.json()["validation_status"] == "CONFIRMED"


def test_event_and_club_mismatch_are_suggestions_only(api, as_, db, fake):
    events, clubs = db.scalar(select(func.count(Event.event_id))), db.scalar(select(func.count(Club.club_id)))
    fake.title = "RoboWars 2026 - Robotics Club - Activity Points List"         # list tagged to Cloud Computing
    b, recs = run(api, as_, db)
    check = b["extracted_metadata"]["event_check"]
    assert (check["event_status"], check["club_status"]) == ("MISMATCH", "MISMATCH")
    assert [e["event_name"] for e in check["suggested_events"]] == [ROBOWARS]
    assert [c["club_name"] for c in check["suggested_clubs"]] == ["Robotics Club"]
    assert all(r["validation_status"] == "EXCEPTION" for r in recs) and b["valid_records"] == 0
    msg = recs[0]["validation_message"]
    assert "Event mismatch: the document is tagged to 'Cloud Computing Workshop'" in msg
    assert "Club mismatch: 'Cloud Computing Workshop' is a Coding Club event" in msg
    assert (db.scalar(select(func.count(Event.event_id))), db.scalar(select(func.count(Club.club_id)))) \
        == (events, clubs)                                                        # nothing created from OCR text
    # The Dean is the final authority and may confirm over the mismatch.
    r = api.post(f"/ocr/records/{recs[0]['ocr_record_id']}/confirm", headers=as_(DEAN))
    assert r.status_code == 200


def test_event_not_found_in_document_text(api, as_, db, fake):
    fake.title = "Activity Points List"
    b, recs = run(api, as_, db)
    assert b["extracted_metadata"]["event_check"]["event_status"] == "NOT_DETECTED"
    assert all("Unmatched event: 'Cloud Computing Workshop' was not found" in r["validation_message"] for r in recs)


def test_untagged_signed_list_cannot_be_confirmed(api, as_, db, fake):
    b, recs = run(api, as_, db, event=None)
    check = b["extracted_metadata"]["event_check"]
    assert check["event_status"] == "NO_EVENT_TAG" and check["suggested_events"][0]["event_name"] == CLOUD
    assert "not tagged to an event" in recs[0]["validation_message"]
    r = api.post(f"/ocr/records/{recs[0]['ocr_record_id']}/confirm", headers=as_(DEAN))
    assert r.status_code == 422 and "not tagged to an event" in r.json()["detail"]


def test_unexpected_row_structure(api, as_, db, fake):
    fake.rows = [row(NEHA, None, "10", issue="Row has 2 value(s); expected USN, NAME and POINTS"),
                 row(ISHITA, "Ishita Rao")]
    _, recs = run(api, as_, db)
    neha = by_usn(recs, NEHA)
    assert neha["validation_status"] == "EXCEPTION"
    assert "Unexpected row structure: Row has 2 value(s)" in neha["validation_message"]
    assert by_usn(recs, ISHITA)["validation_status"] == "VALID"


def test_unusable_table_fails_the_batch(api, as_, db, fake):
    fake.error = ExtractionError("No participant table was found: expected a header row naming the USN, NAME "
                                 "and POINTS columns.")
    b, recs = run(api, as_, db)
    assert b["status"] == "FAILED" and "No participant table was found" in b["error_message"]
    assert recs == [] and b["records_detected"] is None
    assert len(audits(db, "OCR_BATCH_FAILED", b["ocr_batch_id"])) == 1


def test_empty_extraction_fails_the_batch(api, as_, db, fake):
    fake.rows = []
    b, _ = run(api, as_, db)
    assert b["status"] == "FAILED" and b["error_message"] == "No participant rows were found in the document."


# --- table reconstruction (engine-independent) ---------------------------------------------------------------------

def line(text, x, y, conf=0.99, page=1, w=200, h=20):
    return TextLine(page, text, conf, (x, y, x + w, y + h))


def test_rows_from_lines_split_cells_merged_lines_serials_and_footers():
    lines = [line("RoboWars 2026 - Robotics Club", 50, 10, w=500),
             line("Sl.", 10, 60), line("USN", 60, 60), line("NAME", 260, 61), line("POINTS", 460, 59),
             line("1", 10, 100, w=20), line("1RV22CS001", 60, 101, conf=0.95), line("Aarav Sharma", 260, 99),
             line("10", 460, 100, conf=0.80),
             line("1RV22IS014 Diya Nair 7.5", 60, 140, w=500),                           # one merged line
             line("Signed: Dean of Student Affairs", 60, 220, w=500),
             line("1RV22EC008 15", 60, 260, page=2, w=300)]                               # page 2, no header
    ex = rows_from_lines(lines, pages=2)
    assert ex.pages_processed == 2 and ex.title_text == "RoboWars 2026 - Robotics Club"
    assert ex.metadata["header"] == "Sl. USN NAME POINTS"
    assert ex.metadata["unparsed_lines"] == [{"page_number": 1, "text": "Signed: Dean of Student Affairs"}]
    first, second, third = ex.rows
    assert (first.usn, first.name, first.points, first.confidence, first.row_number) == \
        ("1RV22CS001", "Aarav Sharma", "10", 0.8, 1)
    assert (second.usn, second.name, second.points, second.structure_issue) == ("1RV22IS014", "Diya Nair", "7.5", None)
    assert (third.page_number, third.row_number, third.usn, third.name, third.points) == (2, 1, "1RV22EC008", None, "15")
    assert "expected USN, NAME and POINTS" in third.structure_issue
    assert len(first.raw["cells"]) == 4


def test_rows_from_lines_without_a_header_is_unusable():
    with pytest.raises(ExtractionError, match="No participant table"):
        rows_from_lines([line("Just a letter", 10, 10), line("1RV22CS001 Aarav 10", 10, 50)], pages=1)
    with pytest.raises(ExtractionError, match="no participant rows"):
        rows_from_lines([line("USN NAME POINTS", 10, 10, w=400)], pages=1)


def test_parse_row_never_guesses():
    assert parse_row(["Aarav", "Sharma", "10"]) == (None, "Aarav Sharma", "10", None)
    assert parse_row(["1RV22CS001", "Aarav", "Sharma"]) == ("1RV22CS001", "Aarav Sharma", None, None)


def test_paddle_extractor_reports_a_missing_engine(monkeypatch):
    monkeypatch.setitem(sys.modules, "paddleocr", None)                 # import fails; no model is loaded
    with pytest.raises(ExtractionError, match="not installed"):
        PaddleExtractor().extract(tiny_pdf(["x"]), "application/pdf")
    with pytest.raises(ExtractionError, match="cannot read files"):
        PaddleExtractor().extract(b"x", "text/plain")


# --- reprocessing, failure and retry -------------------------------------------------------------------------------

def test_reprocessing_creates_a_new_batch_and_keeps_the_old_one(api, as_, db, fake):
    doc = signed_list(api, as_, db)
    first = start(api, as_, doc)["ocr_batch_id"]
    before = records(api, as_, first)
    fake.rows = [row(NEHA, "Neha Patil", "12")]
    second = start(api, as_, doc)["ocr_batch_id"]
    assert second != first
    assert records(api, as_, first) == before and batch(api, as_, first)["status"] == "REVIEW"
    assert [r["extracted_points"] for r in records(api, as_, second)] == ["12.00"]
    r = api.get("/ocr/batches", headers=as_(DEAN), params={"document_id": doc})
    assert [b["ocr_batch_id"] for b in r.json()] == [second, first]


def test_unexpected_failure_is_recorded_without_internal_details(api, as_, db, fake):
    fake.error = RuntimeError("secret internal path /srv/x")
    b, recs = run(api, as_, db)
    assert b["status"] == "FAILED" and b["error_message"] == UNEXPECTED and "secret" not in b["error_message"]
    assert recs == []


def test_missing_stored_file_fails_the_batch(api, as_, db, fake):
    doc = signed_list(api, as_, db)
    get_storage().discard(db.get(Document, doc).storage_key)
    b = batch(api, as_, start(api, as_, doc)["ocr_batch_id"])
    assert b["status"] == "FAILED" and "stored file" in b["error_message"] and fake.calls == 0


def test_retry_creates_a_new_batch_and_preserves_the_failed_one(api, as_, db, fake):
    fake.error = ExtractionError("The OCR engine (PaddleOCR) is not installed on the server.")
    failed, _ = run(api, as_, db)
    fake.error = None
    r = api.post(f"/ocr/batches/{failed['ocr_batch_id']}/retry", headers=as_(DEAN))
    assert r.status_code == 202, r.text
    new_id = r.json()["ocr_batch_id"]
    assert r.json()["extracted_metadata"] == {"retry_of_batch_id": failed["ocr_batch_id"]}
    old = batch(api, as_, failed["ocr_batch_id"])
    assert old["status"] == "FAILED" and old["error_message"] == failed["error_message"]
    new = batch(api, as_, new_id)
    assert new["status"] == "REVIEW" and new["document_id"] == failed["document_id"]
    assert new["extracted_metadata"]["retry_of_batch_id"] == failed["ocr_batch_id"]
    assert len(records(api, as_, new_id)) == 3 and records(api, as_, failed["ocr_batch_id"]) == []
    entry = audits(db, "OCR_BATCH_RETRIED", new_id)[0]
    assert entry.new_values["retry_of_batch_id"] == failed["ocr_batch_id"]
    # A failed batch is retried at most once, and only FAILED batches can be retried.
    r = api.post(f"/ocr/batches/{failed['ocr_batch_id']}/retry", headers=as_(DEAN))
    assert r.status_code == 409 and "already been processed again" in r.json()["detail"]
    r = api.post(f"/ocr/batches/{new_id}/retry", headers=as_(DEAN))
    assert r.status_code == 409 and "Only FAILED batches" in r.json()["detail"]
    assert api.post("/ocr/batches/999999/retry", headers=as_(DEAN)).status_code == 404


def queued_batch(db, doc, status="QUEUED") -> int:
    b = OcrBatch(document_id=doc, started_by=DEAN, status=status)
    db.add(b)
    db.commit()             # releases the test savepoint, so the API's rollback of its own 409 cannot undo it
    return b.ocr_batch_id


@pytest.mark.parametrize("status", ["QUEUED", "PROCESSING"])
def test_no_new_batch_or_retry_while_one_is_running(api, as_, db, fake, status):
    fake.error = ExtractionError("boom")
    failed, _ = run(api, as_, db)
    running = queued_batch(db, failed["document_id"], status)
    r = start(api, as_, failed["document_id"], expect=409)
    assert f"OCR batch {running} for this document is still running" in r["detail"]
    r = api.post(f"/ocr/batches/{running}/retry", headers=as_(DEAN))
    assert r.status_code == 409 and f"this one is {status}" in r.json()["detail"]


def test_pipeline_processes_only_queued_batches(api, as_, db, fake):
    b, recs = run(api, as_, db)
    process_batch(b["ocr_batch_id"], lambda: nullcontext(db), fake, get_storage())
    assert fake.calls == 1 and records(api, as_, b["ocr_batch_id"]) == recs


# --- Dean review ---------------------------------------------------------------------------------------------------

def test_dean_corrections_are_audited_and_keep_what_ocr_read(api, as_, db, fake):
    fake.rows = [row("1RV22CD0З3", "Neha Patl", "1O", confidence=0.95)]       # misread USN (Cyrillic З), name, points
    _, recs = run(api, as_, db)
    rec = recs[0]
    rid, raw = rec["ocr_record_id"], rec["raw_extraction"]
    assert rec["validation_status"] == "EXCEPTION" and rec["matched_student_usn"] is None

    r = api.patch(f"/ocr/records/{rid}", headers=as_(DEAN),
                  json={"matched_student_usn": " 1rv22cd033 ", "extracted_name": "Neha Patil",
                        "extracted_points": "10"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["matched_student_usn"], out["extracted_name"], out["extracted_points"]) == (NEHA, "Neha Patil", "10.00")
    assert out["extracted_usn"] == "1RV22CD0З3" and out["raw_extraction"] == raw       # OCR's reading is kept
    assert out["validation_status"] == "EXCEPTION"
    assert "Selected student 1RV22CD033 differs from the USN OCR read" in out["validation_message"]
    assert "Name mismatch" not in out["validation_message"] and "points" not in out["validation_message"]

    entry = audits(db, "OCR_RECORD_CORRECTED", rid)[-1]
    assert entry.user_email == DEAN
    assert entry.old_values["matched_student_usn"] is None and entry.new_values["matched_student_usn"] == NEHA
    assert entry.old_values["extracted_name"] == "Neha Patl" and entry.new_values["extracted_name"] == "Neha Patil"
    assert entry.old_values["extracted_points"] is None and entry.new_values["extracted_points"] == "10.00"
    assert entry.new_values["corrected_fields"] == ["extracted_name", "extracted_points", "matched_student_usn"]


@pytest.mark.parametrize("body, status, detail", [
    ({"extracted_usn": "1RV22CD033"}, 422, None),          # OCR's reading cannot be overwritten
    ({"raw_extraction": {}}, 422, None),
    ({"validation_status": "CONFIRMED"}, 422, None),       # status changes only through the review actions
    ({}, 422, None),
    ({"extracted_name": None}, 422, None),
    ({"extracted_name": "   "}, 422, None),
    ({"extracted_points": "-1"}, 422, None),
    ({"extracted_points": None}, 422, None),
    ({"matched_student_usn": "1RV22CS999"}, 422, "No student has the USN 1RV22CS999"),
    ({"extracted_name": "Neha Patil"}, 422, "Nothing to change"),
])
def test_correction_input_rules(api, as_, db, fake, body, status, detail):
    fake.rows = [row(NEHA, "Neha Patil")]
    _, recs = run(api, as_, db)
    r = api.patch(f"/ocr/records/{recs[0]['ocr_record_id']}", headers=as_(DEAN), json=body)
    assert r.status_code == status, r.text
    if detail:
        assert detail in r.json()["detail"]


def test_clearing_the_matched_student(api, as_, db, fake):
    fake.rows = [row(NEHA, "Neha Patil")]
    _, recs = run(api, as_, db)
    r = api.patch(f"/ocr/records/{recs[0]['ocr_record_id']}", headers=as_(DEAN), json={"matched_student_usn": None})
    assert r.status_code == 200 and r.json()["matched_student_usn"] is None
    assert r.json()["validation_status"] == "EXCEPTION"
    r = api.post(f"/ocr/records/{recs[0]['ocr_record_id']}/confirm", headers=as_(DEAN))
    assert r.status_code == 422 and "Select the matched student" in r.json()["detail"]


def test_confirm_dismiss_and_reopen_transitions(api, as_, db, fake):
    _, recs = run(api, as_, db)
    neha, ishita = by_usn(recs, NEHA)["ocr_record_id"], by_usn(recs, ISHITA)["ocr_record_id"]

    def act(rid, action, expect=200, **body):
        r = api.post(f"/ocr/records/{rid}/{action}", headers=as_(DEAN), json=body or None)
        assert r.status_code == expect, r.text
        return r.json()

    assert act(neha, "confirm", comment="Checked against the signed list")["validation_status"] == "CONFIRMED"
    assert "reopen it" in act(neha, "confirm", 409)["detail"]
    assert "reopen it" in act(neha, "dismiss", 409)["detail"]
    r = api.patch(f"/ocr/records/{neha}", headers=as_(DEAN), json={"extracted_points": "11"})
    assert r.status_code == 409
    assert act(neha, "reopen")["validation_status"] == "VALID"
    assert "only CONFIRMED or DISMISSED" in act(neha, "reopen", 409)["detail"]

    assert act(ishita, "dismiss", comment="Not on the attendance sheet")["validation_status"] == "DISMISSED"
    assert act(ishita, "reopen")["validation_status"] == "VALID"
    assert act(ishita, "confirm")["validation_status"] == "CONFIRMED"
    assert api.post("/ocr/records/999999/confirm", headers=as_(DEAN)).status_code == 404

    confirmed = audits(db, "OCR_RECORD_CONFIRMED", neha)[0]
    assert confirmed.old_values["validation_status"] == "VALID"
    assert confirmed.new_values["validation_status"] == "CONFIRMED"
    assert confirmed.new_values["comment"] == "Checked against the signed list"
    dismissed = audits(db, "OCR_RECORD_DISMISSED", ishita)[0]
    assert dismissed.new_values["comment"] == "Not on the attendance sheet"
    assert len(audits(db, "OCR_RECORD_REOPENED", neha)) == 1 and len(audits(db, "OCR_RECORD_REOPENED", ishita)) == 1
    counts = batch(api, as_, recs[0]["ocr_batch_id"])["record_counts"]
    assert counts == {"VALID": 2, "EXCEPTION": 0, "CONFIRMED": 1, "DISMISSED": 0}


def test_dismissing_a_duplicate_revalidates_the_other_row(api, as_, db, fake):
    fake.rows = [row(NEHA, "Neha Patil"), row(NEHA, "Neha Patil", "15")]
    _, recs = run(api, as_, db)
    first, second = (r["ocr_record_id"] for r in recs)
    assert {r["validation_status"] for r in recs} == {"EXCEPTION"}
    r = api.post(f"/ocr/records/{second}/dismiss", headers=as_(DEAN))
    assert r.status_code == 200
    after = {x["ocr_record_id"]: x for x in records(api, as_, recs[0]["ocr_batch_id"])}
    assert after[first]["validation_status"] == "VALID" and after[first]["validation_message"] is None
    entry = audits(db, "OCR_RECORD_DISMISSED", second)[0]
    assert entry.new_values["revalidated_records"] == [{"ocr_record_id": first, "old": "EXCEPTION", "new": "VALID"}]


def test_only_one_confirmed_record_per_student_in_a_batch(api, as_, db, fake):
    fake.rows = [row(NEHA, "Neha Patil"), row(NEHA, "Neha Patil", "15")]
    _, recs = run(api, as_, db)
    first, second = (r["ocr_record_id"] for r in recs)
    assert api.post(f"/ocr/records/{first}/confirm", headers=as_(DEAN)).status_code == 200
    r = api.post(f"/ocr/records/{second}/confirm", headers=as_(DEAN))
    assert r.status_code == 409 and "already confirmed" in r.json()["detail"]


def test_confirm_needs_points(api, as_, db, fake):
    fake.rows = [row(NEHA, "Neha Patil", None)]
    _, recs = run(api, as_, db)
    r = api.post(f"/ocr/records/{recs[0]['ocr_record_id']}/confirm", headers=as_(DEAN))
    assert r.status_code == 422 and "Enter the points" in r.json()["detail"]


def test_records_of_batches_outside_review_cannot_change(api, as_, db):
    # Seeded RoboWars batch is COMMITTED; its records are history.
    rid = db.scalar(select(OcrRecord.ocr_record_id).join(OcrBatch).where(OcrBatch.status == "COMMITTED")
                    .order_by(OcrRecord.ocr_record_id).limit(1))
    for method, path, body in [("patch", f"/ocr/records/{rid}", {"extracted_points": "1"}),
                               ("post", f"/ocr/records/{rid}/confirm", None),
                               ("post", f"/ocr/records/{rid}/dismiss", None),
                               ("post", f"/ocr/records/{rid}/reopen", None)]:
        r = getattr(api, method)(path, headers=as_(DEAN), json=body)
        assert r.status_code == 409 and "records can be reviewed only while it is in REVIEW" in r.json()["detail"]


# --- access control ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("email", [AARAV, ROBOTICS, HOD])
def test_non_dean_roles_are_refused_everywhere(api, as_, db, email):
    bid = db.scalar(select(OcrBatch.ocr_batch_id).order_by(OcrBatch.ocr_batch_id).limit(1))
    rid = db.scalar(select(OcrRecord.ocr_record_id).order_by(OcrRecord.ocr_record_id).limit(1))
    doc = db.scalar(select(Document.document_id).where(Document.document_type == "SIGNED_POINT_LIST").limit(1))
    h = as_(email)
    calls = [("post", "/ocr/batches", {"document_id": doc}), ("get", "/ocr/batches", None),
             ("get", f"/ocr/batches/{bid}", None), ("get", f"/ocr/batches/{bid}/records", None),
             ("post", f"/ocr/batches/{bid}/retry", None), ("patch", f"/ocr/records/{rid}", {"extracted_name": "X"}),
             ("post", f"/ocr/records/{rid}/confirm", None), ("post", f"/ocr/records/{rid}/dismiss", None),
             ("post", f"/ocr/records/{rid}/reopen", None)]
    count = db.scalar(select(func.count(OcrBatch.ocr_batch_id)))
    for method, path, body in calls:
        kwargs = {"json": body} if body is not None else {}
        r = getattr(api, method)(path, headers=h, **kwargs)
        assert r.status_code == 403, (path, r.status_code)
    assert db.scalar(select(func.count(OcrBatch.ocr_batch_id))) == count


def test_unauthenticated_requests_are_refused(api):
    assert api.get("/ocr/batches").status_code == 401
    assert api.post("/ocr/batches", json={"document_id": 1}).status_code == 401


def test_document_rules_for_batch_creation(api, as_, db):
    cert = signed_list(api, as_, db, doc_type="CERTIFICATE")
    r = start(api, as_, cert, expect=422)
    assert "signed point lists only" in r["detail"]
    assert start(api, as_, 999999, expect=404)["detail"] == "Document not found"
    archived = signed_list(api, as_, db)
    assert api.post(f"/documents/{archived}/archive", headers=as_(DEAN)).status_code == 200
    assert "this one is ARCHIVED" in start(api, as_, archived, expect=409)["detail"]
    deleted = signed_list(api, as_, db)
    assert api.post(f"/documents/{deleted}/delete", headers=as_(DEAN)).status_code == 200
    assert "this one is DELETED" in start(api, as_, deleted, expect=409)["detail"]
    r = api.post("/ocr/batches", headers=as_(DEAN), json={"document_id": cert, "storage_key": "seed/x.pdf"})
    assert r.status_code == 422                                     # no client-supplied paths or keys


def test_signed_list_with_an_ocr_batch_cannot_be_deleted(api, as_, db):
    b, _ = run(api, as_, db)
    r = api.post(f"/documents/{b['document_id']}/delete", headers=as_(DEAN))
    assert r.status_code == 409 and "used by OCR processing" in r.json()["detail"]


# --- strict separation from awarding -------------------------------------------------------------------------------

def test_ocr_never_awards_points_or_changes_requests(api, as_, db, fake):
    before = official_state(db)
    ledger_audits = db.scalar(select(func.count(AuditLog.audit_id)).where(AuditLog.entity_type == "point_ledger"))
    ledger_rows = db.scalar(select(func.count(PointLedger.ledger_id)))
    statuses = dict(db.execute(select(PointRequest.request_id, PointRequest.status)).all())
    # Ishita has an open MISSING_FROM_LIST request for RoboWars; her row on a new RoboWars list must not resolve it.
    fake.title = "RoboWars 2026 - Robotics Club - Activity Points List"
    fake.rows = [row(ISHITA, "Ishita Rao"), row(NEHA, "Neha Patil"), row("1RV22CS999", "Unknown")]
    b, recs = run(api, as_, db, ROBOWARS)
    for r in recs[:2]:
        assert api.post(f"/ocr/records/{r['ocr_record_id']}/confirm", headers=as_(DEAN)).status_code == 200
    assert api.post(f"/ocr/records/{recs[2]['ocr_record_id']}/dismiss", headers=as_(DEAN)).status_code == 200

    assert official_state(db) == before
    assert db.scalar(select(func.count(PointLedger.ledger_id))) == ledger_rows
    assert dict(db.execute(select(PointRequest.request_id, PointRequest.status)).all()) == statuses
    assert batch(api, as_, b["ocr_batch_id"])["status"] == "REVIEW"           # COMMITTED belongs to CP5
    assert db.scalar(select(func.count(PointLedger.ledger_id))
                     .where(PointLedger.source_ocr_batch_id == b["ocr_batch_id"])) == 0
    assert db.scalar(select(func.count(AuditLog.audit_id))
                     .where(AuditLog.entity_type == "point_ledger")) == ledger_audits

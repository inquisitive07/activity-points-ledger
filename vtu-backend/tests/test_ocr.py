"""CP4 + CP5: OCR batches, extraction, matching, validation, Dean review, and the ledger commit.

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
             ("post", f"/ocr/batches/{bid}/retry", None), ("post", f"/ocr/batches/{bid}/commit", None),
             ("patch", f"/ocr/records/{rid}", {"extracted_name": "X"}),
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


# ===================================================================================================================
# CP5: committing Dean-confirmed records to the ledger
# ===================================================================================================================

import threading                                                    # noqa: E402
import time                                                         # noqa: E402

from fastapi import HTTPException                                   # noqa: E402
from sqlalchemy.orm import Session                                  # noqa: E402

from app.db import write_transaction                                # noqa: E402
from app.models import PointLedgerDocument, Student, UserAccount, student_point_summary  # noqa: E402
from app.services import ocr as ocr_svc                             # noqa: E402

ROBO_TITLE = "RoboWars 2026 - Robotics Club - Activity Points List"


def reviewed(api, as_, db, fake, rows, event=CLOUD, title=None, dismiss=()) -> tuple[dict, list[dict]]:
    """Process a list and review every record: confirm all except the USNs in `dismiss`."""
    fake.rows, fake.title = rows, title or (ROBO_TITLE if event == ROBOWARS else CLOUD_TITLE)
    b, recs = run(api, as_, db, event)
    for r in recs:
        action = "dismiss" if r["extracted_usn"] in dismiss else "confirm"
        res = api.post(f"/ocr/records/{r['ocr_record_id']}/{action}", headers=as_(DEAN))
        assert res.status_code == 200, res.text
    return b, records(api, as_, b["ocr_batch_id"])


def commit(api, as_, batch_id, expect=200) -> dict:
    r = api.post(f"/ocr/batches/{batch_id}/commit", headers=as_(DEAN))
    assert r.status_code == expect, r.text
    return r.json()


def ledger_of(db, batch_id):
    return db.scalars(select(PointLedger).where(PointLedger.source_ocr_batch_id == batch_id)
                      .order_by(PointLedger.ledger_id)).all()


def request_statuses(db):
    return dict(db.execute(select(PointRequest.request_id, PointRequest.status)).all())


def counts(db):
    return tuple(db.scalar(select(func.count()).select_from(t)) for t in (PointLedger, PointLedgerDocument, AuditLog))


def test_commit_awards_every_confirmed_record(api, as_, db, fake):
    b, recs = reviewed(api, as_, db, fake, good_rows() + [row("1RV22CS999", "Unknown", number=4)],
                       dismiss={"1RV22CS999"})
    bid, doc = b["ocr_batch_id"], b["document_id"]
    out = commit(api, as_, bid)
    assert (out["awards_created"], out["records_confirmed"], out["records_dismissed"]) == (3, 3, 1)
    assert out["requests_resolved"] == 0 and out["resolved_request_ids"] == []
    assert out["batch"]["status"] == "COMMITTED"
    assert out["batch"]["record_counts"] == {"VALID": 0, "EXCEPTION": 0, "CONFIRMED": 3, "DISMISSED": 1}
    assert out["batch"]["extracted_metadata"] == b["extracted_metadata"]       # preserved
    assert records(api, as_, bid) == recs                                       # records preserved

    awards = ledger_of(db, bid)
    assert [a.ledger_id for a in awards] == out["ledger_ids"]
    assert {(a.usn, a.points_awarded) for a in awards} == {(NEHA, Decimal("10.00")), (ISHITA, Decimal("10.00")),
                                                           (KARTHIK, Decimal("7.50"))}
    cloud = event_id(db, CLOUD)
    for a in awards:
        assert (a.source_type, a.source_ocr_batch_id, a.source_request_id) == ("BULK_APPROVAL", bid, None)
        assert (a.event_id, a.status, a.approved_by, a.supersedes_ledger_id) == (cloud, "ACTIVE", DEAN, None)
        assert a.approved_at is not None and a.award_date is not None
        links = db.scalars(select(PointLedgerDocument).where(PointLedgerDocument.ledger_id == a.ledger_id)).all()
        assert [(l.document_id, l.linked_by) for l in links] == [(doc, DEAN)]
    assert "1RV22CS999" not in {a.usn for a in awards}                          # dismissed: no award

    created = audits(db, "LEDGER_AWARD_CREATED")
    mine = [e for e in created if e.entity_id in out["ledger_ids"]]
    assert len(mine) == 3 and all(e.user_email == DEAN and e.entity_type == "point_ledger" for e in mine)
    assert all(e.new_values["source_ocr_batch_id"] == bid and e.new_values["document_ids"] == [doc] for e in mine)
    entry = audits(db, "OCR_BATCH_COMMITTED", bid)
    assert len(entry) == 1 and entry[0].old_values == {"status": "REVIEW"}
    assert entry[0].new_values["status"] == "COMMITTED" and entry[0].new_values["ledger_ids"] == out["ledger_ids"]


def test_commit_resolves_missing_from_list_requests(api, as_, db, fake):
    # Seed: Ishita has a PENDING_DEAN MISSING_FROM_LIST request for RoboWars.
    req = db.scalar(select(PointRequest).where(PointRequest.usn == ISHITA, PointRequest.request_type ==
                                               "MISSING_FROM_LIST"))
    assert req.status == "PENDING_DEAN"
    before = request_statuses(db)
    b, _ = reviewed(api, as_, db, fake, [row(ISHITA, "Ishita Rao"), row(NEHA, "Neha Patil")], event=ROBOWARS)
    out = commit(api, as_, b["ocr_batch_id"])
    assert out["resolved_request_ids"] == [req.request_id] and out["requests_resolved"] == 1
    db.refresh(req)
    assert (req.status, req.dean_decided_by) == ("RESOLVED", DEAN)
    assert f"OCR batch {b['ocr_batch_id']}" in req.dean_decision_comment
    after = request_statuses(db)
    assert {k: v for k, v in after.items() if k != req.request_id} == \
        {k: v for k, v in before.items() if k != req.request_id}                 # nothing else changed
    entry = audits(db, "REQUEST_RESOLVED", req.request_id)
    ishita_award = next(a for a in ledger_of(db, b["ocr_batch_id"]) if a.usn == ISHITA)
    assert len(entry) == 1 and entry[0].old_values["status"] == "PENDING_DEAN"
    assert entry[0].new_values["status"] == "RESOLVED" and entry[0].new_values["ledger_id"] == ishita_award.ledger_id
    # The request API shows the resolution.
    r = api.get(f"/requests/{req.request_id}", headers=as_(DEAN))
    assert r.json()["status"] == "RESOLVED"


def test_commit_resolves_pending_club_missing_from_list(api, as_, db, fake):
    # Seed: Aarav has a PENDING_CLUB MISSING_FROM_LIST request for HackRVCE and no HackRVCE award.
    req = db.scalar(select(PointRequest).where(PointRequest.usn == AARAV_USN,
                                               PointRequest.request_type == "MISSING_FROM_LIST"))
    assert req.status == "PENDING_CLUB"
    b, _ = reviewed(api, as_, db, fake, [row(AARAV_USN, "Aarav Sharma", "15")], event="HackRVCE 24h",
                    title="HackRVCE 24h - Coding Club - Activity Points List")
    assert commit(api, as_, b["ocr_batch_id"])["resolved_request_ids"] == [req.request_id]
    db.refresh(req)
    assert req.status == "RESOLVED" and req.club_reviewed_by is None


def test_commit_leaves_new_activity_and_correction_requests_alone(api, as_, db, fake):
    robowars = event_id(db, ROBOWARS)
    committed_batch = db.scalar(select(OcrBatch.ocr_batch_id).where(OcrBatch.status == "COMMITTED"))
    # Karthik: an earlier RoboWars award was revoked; his CORRECTION of it is still pending. Neha: a pending
    # NEW_ACTIVITY request for RoboWars.
    revoked = PointLedger(usn=KARTHIK, event_id=robowars, points_awarded=Decimal(5), award_date=func.current_date(),
                          source_type="BULK_APPROVAL", source_ocr_batch_id=committed_batch, approved_by=DEAN,
                          approved_at=func.now(), status="REVOKED")
    db.add(revoked)
    db.flush()
    correction = PointRequest(usn=KARTHIK, event_id=robowars, request_type="CORRECTION", claimed_points=Decimal(10),
                              reason="Should be 10", submitted_by="student5@vtu.edu.in",
                              correction_of_ledger_id=revoked.ledger_id, status="PENDING_DEAN")
    new_activity = PointRequest(usn=NEHA, event_id=robowars, request_type="NEW_ACTIVITY",
                                claimed_points=Decimal(10), reason="Took part", submitted_by="student4@vtu.edu.in",
                                status="PENDING_CLUB")
    db.add_all([correction, new_activity])
    db.commit()
    b, _ = reviewed(api, as_, db, fake, [row(KARTHIK, "Karthik Bhat"), row(NEHA, "Neha Patil")], event=ROBOWARS)
    out = commit(api, as_, b["ocr_batch_id"])
    assert out["awards_created"] == 2 and out["requests_resolved"] == 0
    db.refresh(correction), db.refresh(new_activity)
    assert (correction.status, new_activity.status) == ("PENDING_DEAN", "PENDING_CLUB")
    assert correction.dean_decided_by is None and new_activity.dean_decided_by is None


def test_student_summary_and_view_reflect_the_new_awards(api, as_, db, fake):
    def view(usn):
        return db.execute(select(student_point_summary).where(student_point_summary.c.usn == usn)).one()

    before_api = api.get(f"/students/{KARTHIK}", headers=as_(DEAN)).json()
    before_view = view(KARTHIK)
    b, _ = reviewed(api, as_, db, fake, good_rows())
    commit(api, as_, b["ocr_batch_id"])
    after_api = api.get(f"/students/{KARTHIK}", headers=as_(DEAN)).json()
    assert Decimal(after_api["earned_points"]) - Decimal(before_api["earned_points"]) == Decimal("7.50")
    assert Decimal(after_api["remaining_points"]) == Decimal(before_api["remaining_points"]) - Decimal("7.50")
    assert [(l["event_name"], l["source_type"]) for l in after_api["ledger"]][-1] == (CLOUD, "BULK_APPROVAL")
    after_view = view(KARTHIK)
    assert after_view.earned_points - before_view.earned_points == Decimal("7.50")
    assert after_view.remaining_points == max(after_view.required_points - after_view.earned_points, 0)
    # The student sees it too (their own record).
    assert Decimal(api.get(f"/students/{KARTHIK}", headers=as_("student5@vtu.edu.in")).json()["earned_points"]) \
        == Decimal(after_api["earned_points"])


def refused(api, as_, db, batch_id, status, phrase):
    """A refused commit changes nothing at all."""
    before, reqs = counts(db), request_statuses(db)
    r = api.post(f"/ocr/batches/{batch_id}/commit", headers=as_(DEAN))
    assert r.status_code == status, r.text
    assert phrase in r.json()["detail"], r.json()["detail"]
    assert counts(db) == before and request_statuses(db) == reqs
    assert batch(api, as_, batch_id)["status"] == "REVIEW" and ledger_of(db, batch_id) == []


def test_refused_while_a_valid_record_remains(api, as_, db, fake):
    fake.rows = good_rows()
    b, recs = run(api, as_, db)
    for r in recs[:2]:
        api.post(f"/ocr/records/{r['ocr_record_id']}/confirm", headers=as_(DEAN))
    refused(api, as_, db, b["ocr_batch_id"], 409, f"1 record(s) have not been reviewed (still VALID or EXCEPTION: "
                                                  f"{recs[2]['ocr_record_id']})")


def test_refused_while_an_exception_record_remains(api, as_, db, fake):
    fake.rows = [row(NEHA, "Neha Patil"), row("1RV22CS999", "Unknown")]
    b, recs = run(api, as_, db)
    api.post(f"/ocr/records/{by_usn(recs, NEHA)['ocr_record_id']}/confirm", headers=as_(DEAN))
    assert by_usn(records(api, as_, b["ocr_batch_id"]), "1RV22CS999")["validation_status"] == "EXCEPTION"
    refused(api, as_, db, b["ocr_batch_id"], 409, "have not been reviewed")


def test_refused_without_confirmed_records(api, as_, db, fake):
    b, _ = reviewed(api, as_, db, fake, good_rows(), dismiss={NEHA, ISHITA, KARTHIK})
    refused(api, as_, db, b["ocr_batch_id"], 422, "No record is CONFIRMED")


@pytest.mark.parametrize("action, status", [("archive", "ARCHIVED")])
def test_refused_when_the_document_is_not_active(api, as_, db, fake, action, status):
    b, _ = reviewed(api, as_, db, fake, good_rows())
    assert api.post(f"/documents/{b['document_id']}/{action}", headers=as_(DEAN)).status_code == 200
    refused(api, as_, db, b["ocr_batch_id"], 409, f"The signed list is {status}")


def test_refused_when_the_document_has_no_event(api, as_, db, fake):
    fake.rows = [row(NEHA, "Neha Patil")]
    b, recs = run(api, as_, db, event=None)
    api.post(f"/ocr/records/{recs[0]['ocr_record_id']}/dismiss", headers=as_(DEAN))
    refused(api, as_, db, b["ocr_batch_id"], 422, "not tagged to an event")


def tamper(db, record_id, **values):
    """Change a confirmed record behind the API's back, to prove the commit re-checks everything."""
    db.execute(OcrRecord.__table__.update().where(OcrRecord.ocr_record_id == record_id).values(**values))
    db.commit()


def test_refused_when_a_confirmed_record_has_no_matched_student(api, as_, db, fake):
    b, recs = reviewed(api, as_, db, fake, good_rows())
    tamper(db, recs[1]["ocr_record_id"], matched_student_usn=None)
    refused(api, as_, db, b["ocr_batch_id"], 409, f"record {recs[1]['ocr_record_id']} has no matched student")


@pytest.mark.parametrize("points, phrase", [(None, "has no points"), (Decimal("-5"), "is not a valid points value")])
def test_refused_when_a_confirmed_record_has_invalid_points(api, as_, db, fake, points, phrase):
    b, recs = reviewed(api, as_, db, fake, good_rows())
    tamper(db, recs[2]["ocr_record_id"], extracted_points=points)
    refused(api, as_, db, b["ocr_batch_id"], 409, phrase)


def test_refused_when_a_student_is_confirmed_twice(api, as_, db, fake):
    b, recs = reviewed(api, as_, db, fake, good_rows())
    tamper(db, recs[1]["ocr_record_id"], matched_student_usn=NEHA)
    refused(api, as_, db, b["ocr_batch_id"], 409, f"{NEHA} is also confirmed in record {recs[0]['ocr_record_id']}")


def test_refused_when_an_active_award_appeared_after_confirmation(api, as_, db, fake):
    b, _ = reviewed(api, as_, db, fake, [row(ISHITA, "Ishita Rao"), row(NEHA, "Neha Patil")], event=ROBOWARS)
    committed_batch = db.scalar(select(OcrBatch.ocr_batch_id).where(OcrBatch.status == "COMMITTED"))
    db.add(PointLedger(usn=NEHA, event_id=event_id(db, ROBOWARS), points_awarded=Decimal(10),
                       award_date=func.current_date(), source_type="BULK_APPROVAL",
                       source_ocr_batch_id=committed_batch, approved_by=DEAN, approved_at=func.now(), status="ACTIVE"))
    db.commit()
    refused(api, as_, db, b["ocr_batch_id"], 409, f"{NEHA} already has an ACTIVE award for 'RoboWars 2026'")
    # Ishita's MISSING_FROM_LIST request was not resolved either (checked by refused()).


@pytest.mark.parametrize("status", ["GRADUATED", "INACTIVE", "ARCHIVED"])
def test_student_eligibility_is_rechecked_at_commit(api, as_, db, fake, status):
    b, _ = reviewed(api, as_, db, fake, good_rows())
    db.execute(Student.__table__.update().where(Student.usn == KARTHIK).values(student_status=status))
    db.commit()
    refused(api, as_, db, b["ocr_batch_id"], 409, f"{KARTHIK} is {status}; only ACTIVE students can receive awards")


def test_refused_for_a_draft_event(api, as_, db, fake):
    b, _ = reviewed(api, as_, db, fake, good_rows())
    db.execute(Event.__table__.update().where(Event.event_name == CLOUD).values(event_status="DRAFT"))
    db.commit()
    refused(api, as_, db, b["ocr_batch_id"], 409, "still a DRAFT event")


def test_a_signed_list_is_committed_only_once(api, as_, db, fake):
    doc = signed_list(api, as_, db)
    fake.rows = [row(NEHA, "Neha Patil")]
    first = start(api, as_, doc)["ocr_batch_id"]
    fake.rows = [row(ISHITA, "Ishita Rao")]
    second = start(api, as_, doc)["ocr_batch_id"]
    for bid in (first, second):
        api.post(f"/ocr/records/{records(api, as_, bid)[0]['ocr_record_id']}/confirm", headers=as_(DEAN))
    commit(api, as_, first)
    refused(api, as_, db, second, 409, f"already committed through batch {first}")


def test_repeated_commit_is_refused_and_changes_nothing(api, as_, db, fake):
    b, _ = reviewed(api, as_, db, fake, [row(ISHITA, "Ishita Rao")], event=ROBOWARS)
    commit(api, as_, b["ocr_batch_id"])
    before, reqs = counts(db), request_statuses(db)
    r = api.post(f"/ocr/batches/{b['ocr_batch_id']}/commit", headers=as_(DEAN))
    assert r.status_code == 409 and "is COMMITTED" in r.json()["detail"]
    assert counts(db) == before and request_statuses(db) == reqs
    assert len(ledger_of(db, b["ocr_batch_id"])) == 1
    assert api.post("/ocr/batches/999999/commit", headers=as_(DEAN)).status_code == 404


def test_committed_batches_are_read_only(api, as_, db, fake):
    b, recs = reviewed(api, as_, db, fake, good_rows())
    commit(api, as_, b["ocr_batch_id"])
    rid = recs[0]["ocr_record_id"]
    for method, path, body in [("patch", f"/ocr/records/{rid}", {"extracted_points": "1"}),
                               ("post", f"/ocr/records/{rid}/confirm", None),
                               ("post", f"/ocr/records/{rid}/dismiss", None),
                               ("post", f"/ocr/records/{rid}/reopen", None)]:
        r = getattr(api, method)(path, headers=as_(DEAN), json=body)
        assert r.status_code == 409 and "is COMMITTED" in r.json()["detail"]
    r = api.post(f"/ocr/batches/{b['ocr_batch_id']}/retry", headers=as_(DEAN))
    assert r.status_code == 409 and "this one is COMMITTED" in r.json()["detail"]
    assert records(api, as_, b["ocr_batch_id"]) == recs
    r = api.post(f"/documents/{b['document_id']}/delete", headers=as_(DEAN))
    assert r.status_code == 409                                     # evidence for awards now


def test_service_refuses_non_dean_accounts(db):
    for email in (AARAV, ROBOTICS, HOD):
        with pytest.raises(HTTPException) as exc:
            ocr_svc.commit_batch(db, db.get(UserAccount, email), 1)
        assert exc.value.status_code == 403


# --- separate sessions: real rollback and real concurrency ---------------------------------------------------------
# These tests commit for real in vtu_ledger_test (through their own connections, not the rollback-only `db`
# fixture) so other connections can see and contend for the data. They use their own event and students, and delete
# them afterwards. The audit rows they commit cannot be deleted (audit_log is append-only); they reference only
# these throw-away entities and disappear when the test database is dropped at the end of the session.

SCENARIO_USNS = ["1RV25CS901", "1RV25CS902", "1RV25CS903"]


@pytest.fixture
def scenario(db_engine):
    with Session(db_engine) as s, s.begin():
        assert s.execute(text("SELECT current_database()")).scalar() == "vtu_ledger_test"
        coding = s.scalar(select(Club.club_id).where(Club.club_name == "Coding Club"))
        event = Event(event_name="CP5 Concurrency Fest", club_id=coding, event_date=func.current_date(),
                      academic_year="2026-27", event_status="COMPLETED", created_by="coding.lead@vtu.edu.in")
        s.add(event)
        for i, usn in enumerate(SCENARIO_USNS):
            s.add(Student(usn=usn, name=f"Test Student {i}", email=f"cp5.{usn.lower()}@example.invalid",
                          department_code="CS", entry_type="REGULAR", admission_year=2025, current_semester=1,
                          student_status="ACTIVE"))
        s.flush()
        doc = Document(event_id=event.event_id, document_type="SIGNED_POINT_LIST", file_name="cp5.pdf",
                       storage_key=f"test/cp5-{event.event_id}.pdf", mime_type="application/pdf",
                       file_size_bytes=1, file_hash="0" * 64, uploaded_by=DEAN, document_status="ACTIVE")
        s.add(doc)
        s.flush()
        review = OcrBatch(document_id=doc.document_id, started_by=DEAN, status="REVIEW")
        other = OcrBatch(document_id=doc.document_id, started_by=DEAN, status="FAILED")   # source for a rival award
        s.add_all([review, other])
        s.flush()
        for i, usn in enumerate(SCENARIO_USNS, 1):
            s.add(OcrRecord(ocr_batch_id=review.ocr_batch_id, page_number=1, row_number=i, extracted_usn=usn,
                            matched_student_usn=usn, extracted_name=f"Test Student {i - 1}",
                            extracted_points=Decimal(10), confidence_score=Decimal("0.99"),
                            validation_status="CONFIRMED"))
        request = PointRequest(usn=SCENARIO_USNS[0], event_id=event.event_id, request_type="MISSING_FROM_LIST",
                               claimed_points=Decimal(10), reason="Missing from the list", submitted_by=AARAV,
                               status="PENDING_DEAN")
        s.add(request)
        s.flush()
        ids = {"event": event.event_id, "batch": review.ocr_batch_id, "other": other.ocr_batch_id,
               "doc": doc.document_id, "request": request.request_id}
    yield ids
    with Session(db_engine) as s, s.begin():
        ledger = select(PointLedger.ledger_id).where(PointLedger.event_id == ids["event"])
        s.execute(PointLedgerDocument.__table__.delete().where(PointLedgerDocument.ledger_id.in_(ledger)))
        s.execute(PointLedger.__table__.delete().where(PointLedger.event_id == ids["event"]))
        s.execute(PointRequest.__table__.delete().where(PointRequest.event_id == ids["event"]))
        s.execute(OcrRecord.__table__.delete().where(OcrRecord.ocr_batch_id.in_([ids["batch"], ids["other"]])))
        s.execute(OcrBatch.__table__.delete().where(OcrBatch.document_id == ids["doc"]))
        s.execute(Document.__table__.delete().where(Document.document_id == ids["doc"]))
        s.execute(Event.__table__.delete().where(Event.event_id == ids["event"]))
        s.execute(Student.__table__.delete().where(Student.usn.in_(SCENARIO_USNS)))


def committed_state(engine, ids):
    """What a fresh connection sees: awards, links, request status, batch status, audit rows for these entities."""
    with Session(engine) as s:
        ledger_ids = s.scalars(select(PointLedger.ledger_id).where(PointLedger.event_id == ids["event"])).all()
        return {
            "bulk_awards": s.scalar(select(func.count()).select_from(PointLedger)
                                    .where(PointLedger.source_ocr_batch_id == ids["batch"])),
            "all_awards": len(ledger_ids),
            "links": s.scalar(select(func.count()).select_from(PointLedgerDocument)
                              .where(PointLedgerDocument.document_id == ids["doc"])),
            "request": s.scalar(select(PointRequest.status).where(PointRequest.request_id == ids["request"])),
            "batch": s.scalar(select(OcrBatch.status).where(OcrBatch.ocr_batch_id == ids["batch"])),
            "audit": s.scalar(select(func.count()).select_from(AuditLog).where(
                ((AuditLog.entity_type == "ocr_batch") & (AuditLog.entity_id == ids["batch"]))
                | ((AuditLog.entity_type == "point_request") & (AuditLog.entity_id == ids["request"]))
                | ((AuditLog.entity_type == "point_ledger") & AuditLog.entity_id.in_(ledger_ids or [0])))),
            "all_audit": s.scalar(select(func.count()).select_from(AuditLog)),
        }


UNTOUCHED = {"bulk_awards": 0, "all_awards": 0, "links": 0, "request": "PENDING_DEAN", "batch": "REVIEW", "audit": 0}


def untouched(state):
    return {k: v for k, v in state.items() if k != "all_audit"} == UNTOUCHED


def commit_in_own_session(engine, batch_id, outcome: dict):
    """One Dean request: its own connection and transaction, exactly like the API (write_transaction)."""
    try:
        with Session(engine) as s:
            with write_transaction(s):
                outcome["result"] = ocr_svc.commit_batch(s, s.get(UserAccount, DEAN), batch_id)
            outcome["status"] = 200
    except HTTPException as exc:
        outcome["status"], outcome["detail"] = exc.status_code, exc.detail
    except Exception as exc:                                 # surfaced by the test, never swallowed
        outcome["status"], outcome["error"] = "error", repr(exc)


def wait_until_blocked(engine, timeout=15.0) -> bool:
    """True once some connection to the test database is waiting on a lock (seen from a third connection)."""
    deadline = time.monotonic() + timeout
    with engine.connect() as c:
        while time.monotonic() < deadline:
            waiting = c.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                                     "AND wait_event_type = 'Lock'")).scalar()
            c.commit()                                       # new snapshot of pg_stat_activity next time
            if waiting:
                return True
            time.sleep(0.05)
    return False


def test_failure_partway_rolls_back_everything(db_engine, scenario, monkeypatch):
    """Real transaction, real ROLLBACK: the 1st award, its link, its audit row and the resolution of student 1's
    MISSING_FROM_LIST request are all written before the injected failure on the 2nd award's audit entry."""
    real_audit, seen = ocr_svc.audit, []

    def failing_audit(db, user_email, action, *args, **kwargs):
        if action == "LEDGER_AWARD_CREATED":
            seen.append(action)
            if len(seen) == 2:
                raise RuntimeError("simulated failure during the commit")
        return real_audit(db, user_email, action, *args, **kwargs)

    monkeypatch.setattr(ocr_svc, "audit", failing_audit)
    before = committed_state(db_engine, scenario)
    assert untouched(before)
    with Session(db_engine) as s:
        with pytest.raises(RuntimeError, match="simulated failure"):
            with write_transaction(s):
                ocr_svc.commit_batch(s, s.get(UserAccount, DEAN), scenario["batch"])
                pytest.fail("the injected failure did not happen")
    assert len(seen) == 2                                    # it really failed part way through
    after = committed_state(db_engine, scenario)
    assert untouched(after) and after["all_audit"] == before["all_audit"]
    # The same batch then commits cleanly.
    monkeypatch.setattr(ocr_svc, "audit", real_audit)
    outcome = {}
    commit_in_own_session(db_engine, scenario["batch"], outcome)
    assert outcome["status"] == 200, outcome
    done = committed_state(db_engine, scenario)
    assert (done["bulk_awards"], done["links"], done["request"], done["batch"]) == (3, 3, "RESOLVED", "COMMITTED")


def test_concurrent_commits_award_once(db_engine, scenario):
    """Two Dean requests commit the same batch at the same time on separate connections. The first holds the batch
    lock with its awards written but not yet committed; the second is verified to be blocked on that lock, then
    gets 409 once the first commits. Exactly one set of awards, links and audit entries exists."""
    first = Session(db_engine)
    try:
        result = ocr_svc.commit_batch(first, first.get(UserAccount, DEAN), scenario["batch"])
        assert result["awards_created"] == 3
        outcome = {}
        rival = threading.Thread(target=commit_in_own_session, args=(db_engine, scenario["batch"], outcome))
        rival.start()
        assert wait_until_blocked(db_engine), "the second commit never waited for the first one's lock"
        assert rival.is_alive()
        first.commit()
    finally:
        first.close()
    rival.join(30)
    assert not rival.is_alive()
    assert outcome["status"] == 409 and "is COMMITTED" in outcome["detail"], outcome
    state = committed_state(db_engine, scenario)
    assert (state["bulk_awards"], state["links"], state["request"], state["batch"]) == (3, 3, "RESOLVED", "COMMITTED")
    with Session(db_engine) as s:
        assert s.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "OCR_BATCH_COMMITTED", AuditLog.entity_id == scenario["batch"])) == 1
        assert s.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "REQUEST_RESOLVED", AuditLog.entity_id == scenario["request"])) == 1


def test_award_racing_the_commit_is_caught_by_the_unique_index(db_engine, scenario):
    """An ACTIVE award for student 2 is written by another transaction after the commit's duplicate check could see
    it. The commit has already written student 1's award and resolved student 1's request; its insert for student 2
    waits on the one-ACTIVE-award index, fails when the rival commits, and the whole commit rolls back (409)."""
    rival = Session(db_engine)
    try:
        rival.add(PointLedger(usn=SCENARIO_USNS[1], event_id=scenario["event"], points_awarded=Decimal(5),
                              award_date=func.current_date(), source_type="BULK_APPROVAL",
                              source_ocr_batch_id=scenario["other"], approved_by=DEAN, approved_at=func.now(),
                              status="ACTIVE"))
        rival.flush()                                        # holds the index entry, not yet visible to others
        outcome = {}
        committer = threading.Thread(target=commit_in_own_session, args=(db_engine, scenario["batch"], outcome))
        committer.start()
        assert wait_until_blocked(db_engine), "the commit never waited on the rival award"
        rival.commit()
    finally:
        rival.close()
    committer.join(30)
    assert not committer.is_alive()
    assert outcome["status"] == 409 and "conflicts with data saved in the meantime" in outcome["detail"], outcome
    state = committed_state(db_engine, scenario)
    assert (state["bulk_awards"], state["all_awards"], state["links"], state["request"], state["batch"]) == \
        (0, 1, 0, "PENDING_DEAN", "REVIEW")                  # only the rival's award exists
    with Session(db_engine) as s:
        assert s.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.entity_type == "ocr_batch", AuditLog.entity_id == scenario["batch"])) == 0
        assert s.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.entity_type == "point_request", AuditLog.entity_id == scenario["request"])) == 0

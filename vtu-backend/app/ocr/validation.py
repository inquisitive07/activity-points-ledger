"""Matching and validation of OCR records (CLAUDE.md 9.3, 9.4).

OCR proposes, the database validates and suggests, the Dean confirms. Nothing here corrects what OCR read: USNs are
only trimmed and upper-cased (never "O" -> "0" or similar), a student is matched only on an exact, confident USN, and
event/club names found in the document are reported as suggestions. Every problem becomes a sentence in the record's
validation_message; a record with no problems is VALID, otherwise EXCEPTION.

PROPOSED IMPLEMENTATION DECISIONS (not source requirements; awaiting project-owner approval):
  USN_PATTERN     1RV + 2-digit year + 2-letter department + 3-digit serial (the demo RVCE format, e.g. 1RV22CS001).
  LOW_CONFIDENCE  0.90: a row read with lower confidence is an exception and its USN is never matched automatically.
  Name match      case, accents, punctuation and word order are ignored; any other difference is a mismatch.
"""
import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (Club, Event, LedgerStatus, OcrRecord, PointLedger, Student, StudentStatus, ValidationStatus)

USN_PATTERN = re.compile(r"1RV\d{2}[A-Z]{2}\d{3}")
USN_EXAMPLE = "1RV22CS001"
LOW_CONFIDENCE = Decimal("0.90")
MAX_POINTS = Decimal("9999.99")                     # NUMERIC(6,2)
_POINTS = re.compile(r"\d+(\.\d+)?")
OPEN = (ValidationStatus.VALID, ValidationStatus.EXCEPTION)

# Issue codes for the frontend's exception counters. Every problem sentence written below starts with one of these
# prefixes, and validation_message joins the sentences with "; ", so the codes are derived from the stored message
# (no extra column). A fragment that starts with no known prefix continues the previous sentence; a message whose
# first sentence matches nothing (for example older seeded text) is OTHER.
ISSUE_PREFIXES = (
    ("Unexpected row structure", "UNEXPECTED_STRUCTURE"),
    ("Selected student", "SELECTED_STUDENT_DIFFERS"),
    ("Missing USN", "MISSING_USN"),
    ("Invalid USN format", "INVALID_USN"),
    ("USN ", "UNMATCHED_LOW_CONFIDENCE"),
    ("Unknown USN", "UNKNOWN_USN"),
    ("Missing name", "MISSING_NAME"),
    ("Name mismatch", "NAME_MISMATCH"),
    ("Student status", "STUDENT_NOT_ACTIVE"),
    ("Missing points", "MISSING_POINTS"),
    ("Invalid points", "INVALID_POINTS"),
    ("Low OCR confidence", "LOW_CONFIDENCE"),
    ("Duplicate row", "DUPLICATE_ROW"),
    ("Possible duplicate award", "POSSIBLE_DUPLICATE_AWARD"),
    ("Unmatched event", "UNMATCHED_EVENT"),
    ("Event mismatch", "EVENT_MISMATCH"),
    ("Club mismatch", "CLUB_MISMATCH"),
)


def issue_codes(message: str | None) -> list[str]:
    codes = []
    for i, fragment in enumerate((message or "").split("; ")):
        code = next((c for prefix, c in ISSUE_PREFIXES if fragment.startswith(prefix)), None)
        if code is None and i == 0 and fragment:
            code = "OTHER"
        if code and code not in codes:
            codes.append(code)
    return codes


# --- values -----------------------------------------------------------------------------------------------------

def normalize_usn(raw: str | None) -> str | None:
    """Trim and upper-case only. Characters OCR may have misread are left exactly as read."""
    value = re.sub(r"\s+", "", raw or "").upper()
    return value or None


def usn_format_ok(usn: str | None) -> bool:
    return bool(usn and USN_PATTERN.fullmatch(usn))


def _words(text: str | None) -> list[str]:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", " ", plain).split()


def names_match(read: str | None, actual: str) -> bool:
    return bool(read) and sorted(_words(read)) == sorted(_words(actual))


def parse_points(raw: str | None) -> tuple[Decimal | None, str | None]:
    """(points, problem). Points must be a plain non-negative number with at most 2 decimals that fits NUMERIC(6,2)."""
    text = (raw or "").strip()
    if not text:
        return None, "Missing points"
    if text.startswith("-"):
        return None, f"Invalid points: OCR read '{text}'; points cannot be negative"
    if not _POINTS.fullmatch(text):
        return None, f"Invalid points: OCR read '{text}', which is not a number"
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, f"Invalid points: OCR read '{text}', which is not a number"
    if value != value.quantize(Decimal("0.01")) or value > MAX_POINTS:
        return None, f"Invalid points: OCR read '{text}' (at most 9999.99, with up to 2 decimals)"
    return value.quantize(Decimal("0.01")), None


def confident(confidence) -> bool:
    return confidence is not None and Decimal(str(confidence)) >= LOW_CONFIDENCE


def auto_match(db: Session, raw_usn: str | None, confidence) -> str | None:
    """The student a freshly extracted row belongs to, or None. Only an exact, well-formed, confidently read USN of a
    registered student is matched; anything else is left for the Dean to select."""
    usn = normalize_usn(raw_usn)
    if not usn_format_ok(usn) or not confident(confidence):
        return None
    return db.scalar(select(Student.usn).where(Student.usn == usn))


# --- event and club (batch level) -------------------------------------------------------------------------------

def _contains(text: str, name: str) -> bool:
    words = " ".join(_words(name))
    return bool(words) and f" {words} " in f" {' '.join(_words(text))} "


def check_event(db: Session, event: Event | None, title_text: str) -> tuple[dict, list[str]]:
    """Compare the event the document is tagged to with the event/club names found in the document's title text.
    Returns (report for extracted_metadata, problems that apply to every record). Never creates anything."""
    found_events = [e for e in db.scalars(select(Event).order_by(Event.event_id)) if _contains(title_text, e.event_name)]
    found_clubs = [c for c in db.scalars(select(Club).order_by(Club.club_id)) if _contains(title_text, c.club_name)]
    report = {"detected_text": title_text,
              "document_event_id": event.event_id if event else None,
              "suggested_events": [{"event_id": e.event_id, "event_name": e.event_name,
                                    "academic_year": e.academic_year} for e in found_events],
              "suggested_clubs": [{"club_id": c.club_id, "club_name": c.club_name} for c in found_clubs]}
    problems = []
    names = ", ".join(f"'{e.event_name}' ({e.academic_year}, id {e.event_id})" for e in found_events)

    if event is None:
        report["event_status"] = "NO_EVENT_TAG"
        problems.append("Unmatched event: the signed list is not tagged to an event, so its rows cannot be awarded"
                        + (f"; the document text names {names}" if found_events else
                           "; no known event name was found in the document text"))
    elif any(e.event_id == event.event_id for e in found_events):
        report["event_status"] = "MATCHED"
    elif found_events:
        report["event_status"] = "MISMATCH"
        problems.append(f"Event mismatch: the document is tagged to '{event.event_name}', but its text names {names}")
    else:
        report["event_status"] = "NOT_DETECTED"
        problems.append(f"Unmatched event: '{event.event_name}' was not found in the document text; confirm the list "
                        f"belongs to this event")

    expected_club = event.club_id if event else None
    if not found_clubs:
        report["club_status"] = "NOT_DETECTED"
    elif expected_club is not None and any(c.club_id == expected_club for c in found_clubs):
        report["club_status"] = "MATCHED"
    elif event is None:
        report["club_status"] = "NOT_APPLICABLE"
    else:
        report["club_status"] = "MISMATCH"
        club_names = ", ".join(f"'{c.club_name}'" for c in found_clubs)
        owner = db.get(Club, expected_club).club_name if expected_club else None
        problems.append(f"Club mismatch: '{event.event_name}' is "
                        + (f"a {owner} event" if owner else "a non-club event")
                        + f", but the document names {club_names}")
    return report, problems


# --- records ----------------------------------------------------------------------------------------------------

def _label(r: OcrRecord) -> str:
    return f"page {r.page_number} row {r.row_number}" if r.row_number is not None else f"page {r.page_number}"


def validate_records(db: Session, records: list[OcrRecord], event: Event | None,
                     batch_problems: list[str]) -> list[dict]:
    """Recompute validation_status/validation_message of every VALID/EXCEPTION record of one batch, using the
    values as they are now (including Dean corrections). CONFIRMED and DISMISSED records are left alone, but
    CONFIRMED ones still count for duplicate detection. Returns the records whose status changed."""
    live = [r for r in records if r.validation_status != ValidationStatus.DISMISSED]

    def key(r: OcrRecord) -> str | None:
        return r.matched_student_usn or normalize_usn(r.extracted_usn)

    groups = defaultdict(list)
    for r in live:
        if key(r):
            groups[key(r)].append(r)

    lookup = {r.matched_student_usn for r in live if r.matched_student_usn}
    lookup |= {u for r in live if usn_format_ok(u := normalize_usn(r.extracted_usn))}
    students = {s.usn: s for s in db.scalars(select(Student).where(Student.usn.in_(lookup)))} if lookup else {}
    awarded = set()
    if event is not None and lookup:
        awarded = set(db.scalars(select(PointLedger.usn).where(PointLedger.event_id == event.event_id,
                                                               PointLedger.status == LedgerStatus.ACTIVE,
                                                               PointLedger.usn.in_(lookup))))
    changes = []
    for r in records:
        if r.validation_status not in OPEN:
            continue
        problems = record_problems(r, students, awarded, event, groups.get(key(r), []))
        old = r.validation_status
        r.validation_status = ValidationStatus.EXCEPTION if problems or batch_problems else ValidationStatus.VALID
        r.validation_message = "; ".join(problems + batch_problems) or None
        if old != r.validation_status:
            changes.append({"ocr_record_id": r.ocr_record_id, "old": old, "new": r.validation_status})
    return changes


def record_problems(r: OcrRecord, students: dict, awarded: set, event: Event | None,
                    same_key: list[OcrRecord]) -> list[str]:
    raw = r.raw_extraction or {}
    problems = []
    if raw.get("structure_issue"):
        problems.append(f"Unexpected row structure: {raw['structure_issue']}")

    usn, matched = normalize_usn(r.extracted_usn), r.matched_student_usn
    if matched is not None:
        if usn != matched:              # the Dean picked someone other than what OCR read: keep that visible
            problems.append(f"Selected student {matched} differs from the USN OCR read ('{r.extracted_usn or ''}')")
    elif usn is None:
        problems.append("Missing USN; select the student if you can identify them")
    elif not usn_format_ok(usn):
        problems.append(f"Invalid USN format: OCR read '{r.extracted_usn}' (expected a USN like {USN_EXAMPLE}); "
                        f"select the student if you can identify them")
    elif usn in students:
        problems.append(f"USN {usn} belongs to a registered student but was not matched automatically because "
                        f"the OCR confidence is low; select the student to confirm")
    else:
        problems.append(f"Unknown USN: {usn} is not a registered student")

    student = students.get(matched) if matched else None
    if not (r.extracted_name or "").strip():
        problems.append("Missing name")
    elif student is not None and not names_match(r.extracted_name, student.name):
        problems.append(f"Name mismatch: the list says '{r.extracted_name}', the student record says "
                        f"'{student.name}'")
    # A warning, not a block: awards follow the same eligibility as an individual Dean approval, which does not
    # depend on student_status (only submitting a new request does). The Dean must confirm such a row explicitly.
    if student is not None and student.student_status != StudentStatus.ACTIVE:
        problems.append(f"Student status: {matched} is {student.student_status}, not ACTIVE; confirm only if the "
                        f"signed list really awards this student")

    if r.extracted_points is None:
        raw_points = (raw.get("points") or "").strip()
        problems.append(parse_points(raw_points)[1] if raw_points else "Missing points")

    if r.confidence_score is not None and not confident(r.confidence_score):
        problems.append(f"Low OCR confidence ({Decimal(str(r.confidence_score)):.2f}, below {LOW_CONFIDENCE:.2f})")

    if len(same_key) > 1:
        others = ", ".join(_label(o) for o in same_key if o is not r)
        problems.append(f"Duplicate row: {matched or usn} also appears at {others} in this batch")

    if matched is not None and matched in awarded:
        problems.append(f"Possible duplicate award: {matched} already has an ACTIVE award for "
                        f"'{event.event_name}'; a bulk commit never replaces an existing award")
    return problems

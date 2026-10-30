"""Load demo data into the 14-table schema. Safe to re-run.

Every row is looked up by its natural key and inserted only if missing, so a second run changes nothing. The whole
run is one transaction: if anything fails, nothing is written. Seeded documents get a small real PDF in private
storage, and their stored hash/size are computed from those bytes.

LOCAL DEVELOPMENT / DEMO ONLY: every seeded account uses the password below (see README.md for the list). Each
account gets its own bcrypt hash and salt. Never run this seed against a shared or production database.
"""
import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import (AuditLog, Club, Department, Document, Event, OcrBatch, OcrRecord, PointLedger,
                        PointLedgerDocument, PointRequest, PointRequestDocument, PointRequirement, Student,
                        UserAccount)
from app.security import hash_password

PASSWORD = "vtu@123"   # demo-only, documented in README.md
DEAN = "dean@vtu.edu.in"

# RVCE department codes provided for the demo. Fictional demo USNs follow 1RV + joining year (YY) + department
# code (DD) + serial (NNN), e.g. 1RV24IS063.
DEPARTMENTS = [("IS", "Information Science and Engineering"), ("CS", "Computer Science and Engineering"),
               ("CY", "Computer Science and Engineering – Cyber Security"),
               ("CD", "Computer Science and Engineering – Data Science"),
               ("AI", "Artificial Intelligence and Machine Learning"),
               ("EC", "Electronics and Communication Engineering"),
               ("EI", "Electronics and Instrumentation Engineering"),
               ("ME", "Mechanical Engineering"), ("CV", "Civil Engineering")]
REQUIREMENTS = [("REGULAR", 100), ("LATERAL", 75)]
# usn, name, contact email, department, entry type, admission year, semester, status.
# Contact emails are DEMO values kept from the first seed (they predate the RVCE USNs); they are not login emails.
STUDENTS = [
    ("1RV22CS001", "Aarav Sharma", "1vt22cs001@vtu.edu.in", "CS", "REGULAR", 2022, 7, "ACTIVE"),
    ("1RV22IS014", "Diya Nair", "1vt22cs014@vtu.edu.in", "IS", "REGULAR", 2022, 7, "ACTIVE"),
    ("1RV23CY402", "Rohan Gowda", "1vt22cs027@vtu.edu.in", "CY", "LATERAL", 2023, 7, "ACTIVE"),  # earned 0 of 75
    ("1RV22CD033", "Neha Patil", "1vt22cs033@vtu.edu.in", "CD", "REGULAR", 2022, 7, "ACTIVE"),
    ("1RV22EC008", "Ishita Rao", "1vt22ec008@vtu.edu.in", "EC", "REGULAR", 2022, 7, "ACTIVE"),
    ("1RV23EI419", "Karthik Bhat", "1vt22ec019@vtu.edu.in", "EI", "LATERAL", 2023, 7, "ACTIVE"),
    ("1RV19AI050", "Ananya Hegde", "1vt19cs050@vtu.edu.in", "AI", "REGULAR", 2019, None, "GRADUATED"),  # kept for history
]
CLUBS = [("Robotics Club", "robotics@vtu.edu.in", "club@vtu.edu.in"),
         ("Cultural Club", "cultural@vtu.edu.in", "cultural.lead@vtu.edu.in"),
         ("NSS Unit", "nss@vtu.edu.in", "nss.lead@vtu.edu.in"),
         ("Coding Club", "coding@vtu.edu.in", "coding.lead@vtu.edu.in")]
# email, role, student usn, department, account status (club leads come from CLUBS)
ACCOUNTS = [("student@vtu.edu.in", "STUDENT", "1RV22CS001", None, "ACTIVE"),
            ("student2@vtu.edu.in", "STUDENT", "1RV22IS014", None, "ACTIVE"),
            ("student3@vtu.edu.in", "STUDENT", "1RV22EC008", None, "ACTIVE"),
            ("student4@vtu.edu.in", "STUDENT", "1RV22CD033", None, "ACTIVE"),
            ("student5@vtu.edu.in", "STUDENT", "1RV23EI419", None, "ACTIVE"),
            ("student6@vtu.edu.in", "STUDENT", "1RV23CY402", None, "DISABLED"),   # login must be refused
            (DEAN, "DEAN", None, None, "ACTIVE"),
            ("hod@vtu.edu.in", "HOD", None, "CS", "ACTIVE"),
            ("hod.ece@vtu.edu.in", "HOD", None, "EC", "ACTIVE")]
ACCOUNT_OF = {usn: email for email, role, usn, _, _ in ACCOUNTS if role == "STUDENT"}
# name, club (None = non-club, created by the Dean), date, venue, standard points, academic year, status
EVENTS = [("RoboWars 2026", "Robotics Club", "2026-03-14", "Main Auditorium", 10, "2025-26", "COMPLETED"),
          ("Rhythm Fest", "Cultural Club", "2026-02-20", "Open Air Theatre", 20, "2025-26", "COMPLETED"),
          ("NSS Village Camp", "NSS Unit", "2026-04-05", "Hesaraghatta", 40, "2025-26", "CLOSED"),
          ("HackRVCE 24h", "Coding Club", "2026-01-18", "CS Lab Block", 15, "2025-26", "COMPLETED"),
          ("Blood Donation Drive", None, "2026-02-02", "Campus Clinic", 10, "2025-26", "COMPLETED"),
          ("Cloud Computing Workshop", "Coding Club", "2026-11-20", "Seminar Hall 2", 5, "2026-27", "PUBLISHED"),
          ("Robo Soccer Trials", "Robotics Club", "2026-12-05", "Robotics Lab", None, "2026-27", "DRAFT")]

# Approved individual claims: (usn, event, points). Each gets an APPROVED request and an INDIVIDUAL_CLAIM award.
APPROVED_CLAIMS = [("1RV22CS001", "NSS Village Camp", 40), ("1RV22IS014", "NSS Village Camp", 45),
                   ("1RV22IS014", "Rhythm Fest", 20), ("1RV22IS014", "HackRVCE 24h", 15),
                   ("1RV22IS014", "Blood Donation Drive", 10), ("1RV22CS001", "Rhythm Fest", 15)]

# Text of each seeded demo PDF, by storage key (seed/rvce/ since the RVCE correction).
# scripts/rvce_correction.py regenerates the same bytes from here.
DOCUMENT_LINES = {
    "seed/rvce/robowars-2026-signed-list.pdf": [
        "RoboWars 2026 - Robotics Club - Activity Points List", "USN NAME POINTS",
        "1RV22CS001 Aarav Sharma 10", "1RV22IS014 Diya Nair 10", "1RV22CS099 Sanjay K 10"],
    "seed/rvce/1RV22CS001-rhythm-fest-certificate.pdf": [
        "Certificate of Participation", "Aarav Sharma (1RV22CS001)", "Rhythm Fest 2026"],
    "seed/rvce/1RV22CS001-archive-internship-letter.pdf": ["Internship completion letter", "Aarav Sharma"],
    "seed/rvce/1RV22CS001-hackrvce-certificate.pdf": [
        "Certificate of Participation", "Aarav Sharma (1RV22CS001)", "HackRVCE 24h"],
    "seed/rvce/hackrvce-24h-signed-list.pdf": [
        "HackRVCE 24h - Coding Club - Activity Points List", "USN NAME POINTS",
        "1RV22CD033 Neha Patil 15", "1RV22CS0O1 Aarav Sharma 15", "1RV22IS014 Diya Nair 15"],
}


def at(day: str, hour: int = 10) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, tzinfo=timezone.utc)


def tiny_pdf(lines: list[str]) -> bytes:
    """A minimal valid one-page PDF showing the given lines (Helvetica)."""
    def esc(s): return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = "BT /F1 14 Tf 50 780 Td 22 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
               "/Resources << /Font << /F1 5 0 R >> >> >>",
               f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class Seeder:
    def __init__(self, db):
        self.db, self.created = db, 0

    def get_or_add(self, model, key: dict, **values):
        """Return the row matching `key`, inserting it (with `values`) if it does not exist yet."""
        row = self.db.scalars(select(model).filter_by(**key)).first()
        if row is None:
            row = model(**key, **values)
            self.db.add(row)
            self.db.flush()
            self.created += 1
        return row

    def account(self, email, **values):
        """Insert a login account if missing. The password is hashed per account (own salt), and only on insert."""
        if self.db.get(UserAccount, email) is None:
            self.get_or_add(UserAccount, {"email": email}, password_hash=hash_password(PASSWORD), **values)

    def document(self, storage_key, file_name, doc_type, event, uploaded_by, uploaded_at):
        data = tiny_pdf(DOCUMENT_LINES[storage_key])
        path = Path(settings.storage_dir) / storage_key
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return self.get_or_add(Document, {"storage_key": storage_key}, file_name=file_name, document_type=doc_type,
                               event_id=event.event_id if event else None, mime_type="application/pdf",
                               file_size_bytes=len(data), file_hash=hashlib.sha256(data).hexdigest(),
                               uploaded_by=uploaded_by, uploaded_at=uploaded_at, document_status="ACTIVE")

    def audit(self, user, action, entity_type, entity_id, new_values, when):
        self.get_or_add(AuditLog, {"action": action, "entity_type": entity_type, "entity_id": entity_id},
                        user_email=user, new_values=new_values, created_at=when)


def run():
    with SessionLocal() as db, db.begin():
        s = Seeder(db)

        # --- reference data, people, clubs ----------------------------------------------------------------------
        for code, name in DEPARTMENTS:
            s.get_or_add(Department, {"department_code": code}, department_name=name)
        for entry_type, points in REQUIREMENTS:
            s.get_or_add(PointRequirement, {"entry_type": entry_type}, required_points=Decimal(points))
        for usn, name, email, dept, entry, year, sem, status in STUDENTS:
            s.get_or_add(Student, {"usn": usn}, name=name, email=email, department_code=dept,
                         entry_type=entry, admission_year=year, current_semester=sem, student_status=status)
        clubs, lead_of = {}, {}
        for name, club_email, lead in CLUBS:
            clubs[name] = s.get_or_add(Club, {"club_name": name}, club_email=club_email)
            lead_of[name] = lead
            s.account(lead, role="CLUB_LEAD", account_status="ACTIVE", club_id=clubs[name].club_id)
        for email, role, usn, dept, status in ACCOUNTS:
            s.account(email, role=role, account_status=status, student_usn=usn, department_code=dept)

        # --- events ---------------------------------------------------------------------------------------------
        events = {}
        for name, club, day, venue, points, year, status in EVENTS:
            events[name] = s.get_or_add(
                Event, {"event_name": name, "academic_year": year},
                club_id=clubs[club].club_id if club else None, event_date=date.fromisoformat(day), venue=venue,
                description=f"{name} ({club or 'non-club activity'})", start_time=None, end_time=None,
                registration_url=None, standard_points=None if points is None else Decimal(points),
                event_status=status, created_by=lead_of[club] if club else DEAN)

        def club_lead(event_name):
            club_id = events[event_name].club_id
            return next((lead_of[n] for n, c in clubs.items() if c.club_id == club_id), None)

        # --- official bulk award: signed list -> committed OCR batch -> ledger ----------------------------------
        robowars = events["RoboWars 2026"]
        signed = s.document("seed/rvce/robowars-2026-signed-list.pdf", "RoboWars 2026 signed list.pdf",
                            "SIGNED_POINT_LIST", robowars, DEAN, at("2026-03-18"))
        batch = s.get_or_add(OcrBatch, {"document_id": signed.document_id}, started_by=DEAN, status="COMMITTED",
                             started_at=at("2026-03-18", 11), completed_at=at("2026-03-18", 12), pages_processed=1,
                             records_detected=3, valid_records=2, exception_count=1,
                             extracted_metadata={"detected_event": "RoboWars 2026", "detected_club": "Robotics Club"})
        for row, (usn, matched, name, status, message) in enumerate([
                ("1RV22CS001", "1RV22CS001", "Aarav Sharma", "CONFIRMED", None),
                ("1RV22IS014", "1RV22IS014", "Diya Nair", "CONFIRMED", None),
                ("1RV22CS099", None, "Sanjay K", "DISMISSED", "Unknown USN; dismissed by Dean")], start=1):
            s.get_or_add(OcrRecord, {"ocr_batch_id": batch.ocr_batch_id, "page_number": 1, "row_number": row},
                         extracted_usn=usn, matched_student_usn=matched, extracted_name=name,
                         extracted_points=Decimal(10), confidence_score=Decimal("0.9800"),
                         raw_extraction={"text": f"{usn} {name} 10"}, validation_status=status,
                         validation_message=message)
        for usn in ("1RV22CS001", "1RV22IS014"):
            award = s.get_or_add(PointLedger, {"usn": usn, "event_id": robowars.event_id, "source_type": "BULK_APPROVAL"},
                                 points_awarded=Decimal(10), award_date=date(2026, 3, 18),
                                 source_ocr_batch_id=batch.ocr_batch_id, approved_by=DEAN,
                                 approved_at=at("2026-03-18", 12), status="ACTIVE")
            s.get_or_add(PointLedgerDocument, {"ledger_id": award.ledger_id, "document_id": signed.document_id},
                         linked_at=at("2026-03-18", 12), linked_by=DEAN)
            s.audit(DEAN, "LEDGER_AWARD_CREATED", "point_ledger", award.ledger_id,
                    {"usn": usn, "event_id": robowars.event_id, "points_awarded": "10.00"}, at("2026-03-18", 12))
        s.audit(DEAN, "OCR_BATCH_COMMITTED", "ocr_batch", batch.ocr_batch_id, {"awards": 2}, at("2026-03-18", 12))

        # --- approved individual claims: request -> club review (club events) -> Dean -> ledger ------------------
        claim_awards = {}
        for usn, event_name, points in APPROVED_CLAIMS:
            event, lead = events[event_name], club_lead(event_name)
            reviewed = at(event.event_date.isoformat(), 9)
            req = s.get_or_add(PointRequest, {"usn": usn, "event_id": event.event_id, "request_type": "NEW_ACTIVITY"},
                               claimed_points=Decimal(points), reason=f"Participated in {event_name}.",
                               submitted_by=ACCOUNT_OF[usn],
                               status="APPROVED", club_reviewed_by=lead, club_reviewed_at=reviewed if lead else None,
                               club_review_comment="Verified against attendance." if lead else None,
                               dean_decided_by=DEAN, dean_decided_at=reviewed.replace(hour=15),
                               dean_decision_comment="Approved.", created_at=reviewed.replace(hour=8),
                               updated_at=reviewed.replace(hour=15))
            claim_awards[(usn, event_name)] = s.get_or_add(
                PointLedger, {"usn": usn, "event_id": event.event_id, "source_type": "INDIVIDUAL_CLAIM"},
                points_awarded=Decimal(points), award_date=reviewed.date(), source_request_id=req.request_id,
                approved_by=DEAN, approved_at=reviewed.replace(hour=15),
                # Aarav's Rhythm Fest award is corrected below; the original is kept as SUPERSEDED history.
                status="SUPERSEDED" if (usn, event_name) == ("1RV22CS001", "Rhythm Fest") else "ACTIVE")

        # --- correction: 15 -> 20 for Aarav / Rhythm Fest. The original row stays, the new row supersedes it ----
        rhythm, original = events["Rhythm Fest"], claim_awards[("1RV22CS001", "Rhythm Fest")]
        correction = s.get_or_add(
            PointRequest, {"usn": "1RV22CS001", "event_id": rhythm.event_id, "request_type": "CORRECTION"},
            claimed_points=Decimal(20), reason="Lead performer: the signed list shows 20, not 15.",
            submitted_by="student@vtu.edu.in", correction_of_ledger_id=original.ledger_id, status="APPROVED",
            club_reviewed_by="cultural.lead@vtu.edu.in", club_reviewed_at=at("2026-03-02"),
            club_review_comment="Confirmed: lead performers receive 20.", dean_decided_by=DEAN,
            dean_decided_at=at("2026-03-03"), dean_decision_comment="Corrected to 20.", created_at=at("2026-03-01"),
            updated_at=at("2026-03-03"))
        corrected = s.get_or_add(
            PointLedger, {"usn": "1RV22CS001", "event_id": rhythm.event_id, "source_type": "MANUAL_CORRECTION"},
            points_awarded=Decimal(20), award_date=date(2026, 3, 3), source_request_id=correction.request_id,
            approved_by=DEAN, approved_at=at("2026-03-03"), status="ACTIVE", supersedes_ledger_id=original.ledger_id)
        s.audit(DEAN, "LEDGER_AWARD_SUPERSEDED", "point_ledger", original.ledger_id,
                {"status": "SUPERSEDED", "superseded_by": corrected.ledger_id}, at("2026-03-03"))

        # --- evidence-only upload: certificate attached to an existing award (no new award) -----------------------
        certificate = s.document("seed/rvce/1RV22CS001-rhythm-fest-certificate.pdf", "Rhythm Fest certificate.pdf",
                                 "CERTIFICATE", rhythm, "student@vtu.edu.in", at("2026-03-05"))
        s.get_or_add(PointLedgerDocument, {"ledger_id": corrected.ledger_id, "document_id": certificate.document_id},
                     linked_at=at("2026-03-05"), linked_by="student@vtu.edu.in")
        s.document("seed/rvce/1RV22CS001-archive-internship-letter.pdf", "Internship letter.pdf", "OTHER", None,
                   "student@vtu.edu.in", at("2026-04-10"))

        # --- open requests for the workflow screens ---------------------------------------------------------------
        hack, blood = events["HackRVCE 24h"], events["Blood Donation Drive"]
        missing = s.get_or_add(
            PointRequest, {"usn": "1RV22CS001", "event_id": hack.event_id, "request_type": "MISSING_FROM_LIST"},
            claimed_points=Decimal(15), reason="I took part in HackRVCE but my USN is not on the signed list.",
            submitted_by="student@vtu.edu.in", status="PENDING_CLUB", created_at=at("2026-01-25"),
            updated_at=at("2026-01-25"))
        hack_cert = s.document("seed/rvce/1RV22CS001-hackrvce-certificate.pdf", "HackRVCE certificate.pdf", "CERTIFICATE",
                               hack, "student@vtu.edu.in", at("2026-01-25"))
        s.get_or_add(PointRequestDocument, {"request_id": missing.request_id, "document_id": hack_cert.document_id},
                     attached_at=at("2026-01-25"))
        s.audit("student@vtu.edu.in", "REQUEST_SUBMITTED", "point_request", missing.request_id,
                {"request_type": "MISSING_FROM_LIST", "status": "PENDING_CLUB"}, at("2026-01-25"))
        s.get_or_add(PointRequest, {"usn": "1RV22EC008", "event_id": robowars.event_id, "request_type": "MISSING_FROM_LIST"},
                     claimed_points=Decimal(10), reason="Team member in RoboWars; missing from the list.",
                     submitted_by=ACCOUNT_OF["1RV22EC008"], status="PENDING_DEAN", club_reviewed_by="club@vtu.edu.in",
                     club_reviewed_at=at("2026-03-21"), club_review_comment="Verified: registered team member.",
                     created_at=at("2026-03-20"), updated_at=at("2026-03-21"))
        s.get_or_add(PointRequest, {"usn": "1RV22CD033", "event_id": blood.event_id, "request_type": "NEW_ACTIVITY"},
                     claimed_points=Decimal(10), reason="Donated blood at the campus drive.",
                     submitted_by=ACCOUNT_OF["1RV22CD033"], status="PENDING_DEAN",
                     created_at=at("2026-02-05"), updated_at=at("2026-02-05"))
        s.get_or_add(PointRequest, {"usn": "1RV23EI419", "event_id": rhythm.event_id, "request_type": "NEW_ACTIVITY"},
                     claimed_points=Decimal(20), reason="Volunteered at Rhythm Fest.",
                     submitted_by=ACCOUNT_OF["1RV23EI419"], status="REJECTED",
                     club_reviewed_by="cultural.lead@vtu.edu.in", club_reviewed_at=at("2026-02-25"),
                     club_review_comment="Not on the volunteer roster.", created_at=at("2026-02-22"),
                     updated_at=at("2026-02-25"))

        # --- OCR batch awaiting Dean review (exceptions to resolve; nothing is awarded yet) -----------------------
        hack_list = s.document("seed/rvce/hackrvce-24h-signed-list.pdf", "HackRVCE 24h signed list.pdf",
                               "SIGNED_POINT_LIST", hack, DEAN, at("2026-02-01"))
        review = s.get_or_add(OcrBatch, {"document_id": hack_list.document_id}, started_by=DEAN, status="REVIEW",
                              started_at=at("2026-02-01", 11), completed_at=at("2026-02-01", 11), pages_processed=1,
                              records_detected=3, valid_records=1, exception_count=2,
                              extracted_metadata={"detected_event": "HackRVCE 24h", "detected_club": "Coding Club"})
        for row, (usn, matched, name, conf, status, message) in enumerate([
                ("1RV22CD033", "1RV22CD033", "Neha Patil", "0.9700", "VALID", None),
                ("1RV22CS0O1", None, "Aarav Sharma", "0.6100", "EXCEPTION", "Unknown USN (possible OCR misread)"),
                ("1RV22IS014", "1RV22IS014", "Diya Nair", "0.9600", "EXCEPTION",
                 "Student already has an ACTIVE award for this event")], start=1):
            s.get_or_add(OcrRecord, {"ocr_batch_id": review.ocr_batch_id, "page_number": 1, "row_number": row},
                         extracted_usn=usn, matched_student_usn=matched, extracted_name=name,
                         extracted_points=Decimal(15), confidence_score=Decimal(conf),
                         raw_extraction={"text": f"{usn} {name} 15"}, validation_status=status,
                         validation_message=message)

    print(f"Seed complete: {s.created} new rows." if s.created else "Already seeded: nothing to add.")


if __name__ == "__main__":
    run()

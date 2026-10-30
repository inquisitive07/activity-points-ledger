"""OCR batches and the Dean's review of OCR records (CLAUDE.md 4.1, 4.6, 9, 18.5).

Functions flush but never commit: routers wrap them in db.write_transaction, so each change and its audit entry are
committed together. Processing itself runs in the background (app/ocr/pipeline.py).

Batch states:   QUEUED -> PROCESSING -> REVIEW | FAILED;  REVIEW -> COMMITTED (commit_batch, below).
  * A document can be processed again: every run is a new batch, earlier batches are never overwritten.
  * While a batch of the document is QUEUED or PROCESSING, no new batch is started for it.
  * Retry: a FAILED batch stays as it is (with its error message); retrying creates a new batch for the same
    document. Only the document's latest batch can be retried, so a failed batch is retried at most once.

Record states (review only while the batch is in REVIEW):
  VALID | EXCEPTION  -- correct -->  re-validated: VALID | EXCEPTION
  VALID | EXCEPTION  -- confirm -->  CONFIRMED      (the only records the CP5 commit may award)
  VALID | EXCEPTION  -- dismiss -->  DISMISSED
  CONFIRMED | DISMISSED -- reopen --> re-validated: VALID | EXCEPTION
After every action the batch's open records are re-validated, because one record's change can settle or create a
duplicate on another. Status changes caused that way are listed in the action's audit entry.

Only commit_batch writes to point_ledger and point_request, and only from CONFIRMED records of a fully reviewed
batch. Extraction and review never award points.
"""
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, Request
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.audit import audit, snapshot
from app.models import (Document, DocumentStatus, DocumentType, Event, EventStatus, LedgerSourceType, LedgerStatus,
                        OcrBatch, OcrBatchStatus, OcrRecord, PointLedger, PointLedgerDocument, PointRequest,
                        RequestType, Role, Student, UserAccount, ValidationStatus)
from app.ocr.pipeline import BATCH_FIELDS
from app.ocr.validation import MAX_POINTS, OPEN, normalize_usn, validate_records
from app.permissions import not_found
from app.services import documents as docs
from app.services import request_workflow as wf
from app.services.request_workflow import active_award

V = ValidationStatus
IN_FLIGHT = (OcrBatchStatus.QUEUED, OcrBatchStatus.PROCESSING)
RECORD_FIELDS = ("matched_student_usn", "extracted_name", "extracted_points", "validation_status",
                 "validation_message")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_dean(user: UserAccount):
    if user.role != Role.DEAN:
        raise HTTPException(403, "Only the Dean can run and review OCR")


# --- batches ----------------------------------------------------------------------------------------------------

def _new_batch(db: Session, user: UserAccount, document_id: int, metadata: dict | None) -> OcrBatch:
    # FOR UPDATE on the document serialises batch creation for one document (and waits for a concurrent delete).
    doc = docs.get_visible(db, user, document_id, lock="update")
    if doc.document_type != DocumentType.SIGNED_POINT_LIST:
        raise HTTPException(422, f"OCR runs on signed point lists only; this document is a {doc.document_type}")
    if doc.document_status != DocumentStatus.ACTIVE:
        raise HTTPException(409, f"Only ACTIVE documents can be processed; this one is {doc.document_status}")
    running = db.scalar(select(OcrBatch.ocr_batch_id).where(OcrBatch.document_id == doc.document_id,
                                                            OcrBatch.status.in_(IN_FLIGHT)))
    if running is not None:
        raise HTTPException(409, f"OCR batch {running} for this document is still running; wait for it to finish")
    batch = OcrBatch(document_id=doc.document_id, started_by=user.email, status=OcrBatchStatus.QUEUED,
                     started_at=_now(), extracted_metadata=metadata)
    db.add(batch)
    db.flush()
    return batch


def create_batch(db: Session, user: UserAccount, document_id: int, request: Request | None = None) -> OcrBatch:
    _require_dean(user)
    batch = _new_batch(db, user, document_id, None)
    audit(db, user.email, "OCR_BATCH_CREATED", "ocr_batch", batch.ocr_batch_id,
          new_values=snapshot(batch, *BATCH_FIELDS), request=request)
    return batch


def retry_batch(db: Session, user: UserAccount, batch_id: int, request: Request | None = None) -> OcrBatch:
    _require_dean(user)
    failed = db.get(OcrBatch, batch_id)
    if failed is None:
        raise not_found("OCR batch")
    docs.get_visible(db, user, failed.document_id, lock="update")      # serialises concurrent retries
    db.refresh(failed)
    if failed.status != OcrBatchStatus.FAILED:
        raise HTTPException(409, f"Only FAILED batches can be retried; this one is {failed.status}")
    latest = db.scalar(select(func.max(OcrBatch.ocr_batch_id)).where(OcrBatch.document_id == failed.document_id))
    if latest != failed.ocr_batch_id:
        raise HTTPException(409, f"This document has already been processed again (batch {latest}); "
                                 f"only its latest batch can be retried")
    batch = _new_batch(db, user, failed.document_id, {"retry_of_batch_id": failed.ocr_batch_id})
    audit(db, user.email, "OCR_BATCH_RETRIED", "ocr_batch", batch.ocr_batch_id,
          new_values={**snapshot(batch, *BATCH_FIELDS), "retry_of_batch_id": failed.ocr_batch_id,
                      "failed_error_message": failed.error_message}, request=request)
    return batch


# --- record review ----------------------------------------------------------------------------------------------

def _reviewable(db: Session, record_id: int) -> tuple[OcrRecord, OcrBatch]:
    """The record and its batch, with the batch locked so one batch's reviews (and the later CP5 commit) are
    serialised. Records can change only while their batch is in REVIEW."""
    batch_id = db.scalar(select(OcrRecord.ocr_batch_id).where(OcrRecord.ocr_record_id == record_id))
    if batch_id is None:
        raise not_found("OCR record")
    batch = db.scalar(select(OcrBatch).where(OcrBatch.ocr_batch_id == batch_id).with_for_update())
    record = db.get(OcrRecord, record_id)
    db.refresh(record)
    if batch.status != OcrBatchStatus.REVIEW:
        raise HTTPException(409, f"Batch {batch_id} is {batch.status}; records can be reviewed only while it is "
                                 f"in REVIEW")
    return record, batch


def _event_of(db: Session, batch: OcrBatch) -> Event | None:
    event_id = db.scalar(select(Document.event_id).where(Document.document_id == batch.document_id))
    return db.get(Event, event_id) if event_id is not None else None


def _revalidate(db: Session, batch: OcrBatch, exclude: int) -> list[dict]:
    records = list(db.scalars(select(OcrRecord).where(OcrRecord.ocr_batch_id == batch.ocr_batch_id)
                              .order_by(OcrRecord.ocr_record_id)))
    problems = (batch.extracted_metadata or {}).get("batch_problems", [])
    changes = validate_records(db, records, _event_of(db, batch), problems)
    db.flush()
    return [c for c in changes if c["ocr_record_id"] != exclude]


def _require_open(record: OcrRecord, action: str):
    if record.validation_status not in OPEN:
        raise HTTPException(409, f"Record is {record.validation_status}; reopen it before you {action} it")


def _audit_review(db, user, action, record, old, others, request, **extra):
    new = snapshot(record, *RECORD_FIELDS)
    if others:
        new["revalidated_records"] = others
    audit(db, user.email, action, "ocr_record", record.ocr_record_id, old_values=old, new_values={**new, **extra},
          request=request)


def correct(db: Session, user: UserAccount, record_id: int, changes: dict,
            request: Request | None = None) -> OcrRecord:
    """Apply Dean corrections to matched_student_usn / extracted_name / extracted_points, then re-validate."""
    _require_dean(user)
    record, batch = _reviewable(db, record_id)
    _require_open(record, "correct")
    if "matched_student_usn" in changes and changes["matched_student_usn"] is not None:
        usn = normalize_usn(changes["matched_student_usn"])
        if db.scalar(select(Student.usn).where(Student.usn == usn)) is None:
            raise HTTPException(422, f"No student has the USN {usn}")
        changes["matched_student_usn"] = usn
    if changes.get("extracted_points") is not None:
        changes["extracted_points"] = changes["extracted_points"].quantize(Decimal("0.01"))
    diff = {k: v for k, v in changes.items() if getattr(record, k) != v}
    if not diff:
        raise HTTPException(422, "Nothing to change: the record already has these values")
    old = snapshot(record, *RECORD_FIELDS)
    for field, value in diff.items():
        setattr(record, field, value)
    db.flush()
    others = _revalidate(db, batch, record.ocr_record_id)
    _audit_review(db, user, "OCR_RECORD_CORRECTED", record, old, others, request, corrected_fields=sorted(diff))
    return record


def confirm(db: Session, user: UserAccount, record_id: int, comment: str | None,
            request: Request | None = None) -> OcrRecord:
    """Accept the record as a candidate award for the CP5 commit. The Dean may confirm over warnings (name mismatch,
    low confidence, event mismatch); the messages stay on the record as the review history. Not an award."""
    _require_dean(user)
    record, batch = _reviewable(db, record_id)
    _require_open(record, "confirm")
    event = _event_of(db, batch)
    if record.matched_student_usn is None:
        raise HTTPException(422, "Select the matched student before confirming this record")
    if record.extracted_points is None:
        raise HTTPException(422, "Enter the points before confirming this record")
    if event is None:
        raise HTTPException(422, "The signed list is not tagged to an event, so no record of it can be confirmed")
    if active_award(db, record.matched_student_usn, event.event_id):
        raise HTTPException(409, f"{record.matched_student_usn} already has an ACTIVE award for "
                                 f"'{event.event_name}'; a bulk commit never replaces it. Dismiss this record or "
                                 f"handle it as a correction request")
    if db.scalar(select(exists().where(OcrRecord.ocr_batch_id == batch.ocr_batch_id,
                                       OcrRecord.ocr_record_id != record.ocr_record_id,
                                       OcrRecord.matched_student_usn == record.matched_student_usn,
                                       OcrRecord.validation_status == V.CONFIRMED))):
        raise HTTPException(409, f"Another record of this batch is already confirmed for "
                                 f"{record.matched_student_usn}")
    old = snapshot(record, *RECORD_FIELDS)
    record.validation_status = V.CONFIRMED
    db.flush()
    others = _revalidate(db, batch, record.ocr_record_id)
    _audit_review(db, user, "OCR_RECORD_CONFIRMED", record, old, others, request, comment=comment)
    return record


def dismiss(db: Session, user: UserAccount, record_id: int, comment: str | None,
            request: Request | None = None) -> OcrRecord:
    _require_dean(user)
    record, batch = _reviewable(db, record_id)
    _require_open(record, "dismiss")
    old = snapshot(record, *RECORD_FIELDS)
    record.validation_status = V.DISMISSED
    db.flush()
    others = _revalidate(db, batch, record.ocr_record_id)
    _audit_review(db, user, "OCR_RECORD_DISMISSED", record, old, others, request, comment=comment)
    return record


def reopen(db: Session, user: UserAccount, record_id: int, comment: str | None,
           request: Request | None = None) -> OcrRecord:
    """Undo a confirm or dismiss: the record is validated again and becomes VALID or EXCEPTION."""
    _require_dean(user)
    record, batch = _reviewable(db, record_id)
    if record.validation_status in OPEN:
        raise HTTPException(409, f"Record is {record.validation_status}; only CONFIRMED or DISMISSED records can be "
                                 f"reopened")
    old = snapshot(record, *RECORD_FIELDS)
    record.validation_status = V.EXCEPTION          # re-validation below decides VALID or EXCEPTION
    db.flush()
    others = _revalidate(db, batch, record.ocr_record_id)
    _audit_review(db, user, "OCR_RECORD_REOPENED", record, old, others, request, comment=comment)
    return record


# --- ledger commit (CP5) ----------------------------------------------------------------------------------------

def commit_batch(db: Session, user: UserAccount, batch_id: int, request: Request | None = None) -> dict:
    """Turn a fully reviewed batch's CONFIRMED records into official BULK_APPROVAL awards (CLAUDE.md 4.1, 4.6, 12).

    Runs inside the caller's write_transaction: the awards, their signed-list links, the MISSING_FROM_LIST
    resolutions, the batch's COMMITTED status and every audit entry are committed together or not at all. Nothing
    decided at extraction or confirmation time is trusted; every condition is checked again here, under locks.

    Lock order (the same prefix as the review actions, so they serialise instead of deadlocking):
      batch FOR UPDATE -> document FOR SHARE -> event FOR SHARE -> records FOR UPDATE -> students FOR SHARE
      -> matching open MISSING_FROM_LIST requests FOR UPDATE (before any award is inserted, because a Dean review
      locks a request before it writes an award) -> award inserts, which the one-ACTIVE-award index guards.
    """
    _require_dean(user)
    batch = db.scalar(select(OcrBatch).where(OcrBatch.ocr_batch_id == batch_id).with_for_update())
    if batch is None:
        raise not_found("OCR batch")
    if batch.status != OcrBatchStatus.REVIEW:
        raise HTTPException(409, f"Batch {batch_id} is {batch.status}; only a batch in REVIEW can be committed")

    doc = docs.get_visible(db, user, batch.document_id, lock="share")
    if doc.document_type != DocumentType.SIGNED_POINT_LIST:
        raise HTTPException(422, f"Only signed point lists can be committed; this document is a {doc.document_type}")
    if doc.document_status != DocumentStatus.ACTIVE:
        raise HTTPException(409, f"The signed list is {doc.document_status}; only an ACTIVE document can be committed")
    if doc.event_id is None:
        raise HTTPException(422, "The signed list is not tagged to an event, so its records cannot be awarded")
    event = db.scalar(select(Event).where(Event.event_id == doc.event_id).with_for_update(read=True))
    if event.event_status == EventStatus.DRAFT:
        raise HTTPException(409, f"'{event.event_name}' is still a DRAFT event; publish it before awarding points")
    earlier = db.scalar(select(OcrBatch.ocr_batch_id).where(OcrBatch.document_id == doc.document_id,
                                                            OcrBatch.status == OcrBatchStatus.COMMITTED))
    if earlier is not None:
        raise HTTPException(409, f"This signed list was already committed through batch {earlier}")

    records = list(db.scalars(select(OcrRecord).where(OcrRecord.ocr_batch_id == batch_id)
                              .order_by(OcrRecord.page_number, OcrRecord.row_number, OcrRecord.ocr_record_id)
                              .with_for_update()))
    unreviewed = [r for r in records if r.validation_status in OPEN]
    if unreviewed:
        listed = ", ".join(str(r.ocr_record_id) for r in unreviewed[:10]) + (" ..." if len(unreviewed) > 10 else "")
        raise HTTPException(409, f"{len(unreviewed)} record(s) have not been reviewed (still VALID or EXCEPTION: "
                                 f"{listed}); confirm or dismiss every record before committing")
    confirmed = [r for r in records if r.validation_status == V.CONFIRMED]
    if not confirmed:
        raise HTTPException(422, "No record is CONFIRMED; there is nothing to award")

    usns = sorted({r.matched_student_usn for r in confirmed if r.matched_student_usn})
    students = {s.usn: s for s in db.scalars(select(Student).where(Student.usn.in_(usns)).order_by(Student.usn)
                                             .with_for_update(read=True))}
    # Locked now, before any award exists (see the lock order above). The student locks also keep new requests
    # from being submitted for these students until the commit ends (submission locks the student row).
    db.scalars(select(PointRequest).where(
        PointRequest.event_id == event.event_id, PointRequest.usn.in_(usns),
        PointRequest.request_type == RequestType.MISSING_FROM_LIST, PointRequest.status.in_(wf.OPEN))
        .order_by(PointRequest.request_id).with_for_update()).all()
    awarded = set(db.scalars(select(PointLedger.usn).where(PointLedger.event_id == event.event_id,
                                                           PointLedger.status == LedgerStatus.ACTIVE,
                                                           PointLedger.usn.in_(usns))))
    problems = _commit_problems(confirmed, students, awarded, event)
    if problems:
        raise HTTPException(409, "The batch cannot be committed; nothing was awarded: " + "; ".join(problems))

    now, today = _now(), date.today()
    ledger_ids, resolved = [], []
    for r in confirmed:
        award = PointLedger(usn=r.matched_student_usn, event_id=event.event_id, points_awarded=r.extracted_points,
                            award_date=today, source_type=LedgerSourceType.BULK_APPROVAL, source_request_id=None,
                            source_ocr_batch_id=batch_id, approved_by=user.email, approved_at=now,
                            status=LedgerStatus.ACTIVE, supersedes_ledger_id=None)
        db.add(award)
        db.flush()                                  # a concurrent ACTIVE award fails here on the unique index
        db.add(PointLedgerDocument(ledger_id=award.ledger_id, document_id=doc.document_id, linked_at=now,
                                   linked_by=user.email))
        db.flush()
        audit(db, user.email, "LEDGER_AWARD_CREATED", "point_ledger", award.ledger_id,
              new_values={**snapshot(award, *wf.LEDGER_FIELDS), "ocr_record_id": r.ocr_record_id,
                          "document_ids": [doc.document_id]}, request=request)
        ledger_ids.append(award.ledger_id)
        resolved += wf.resolve_missing_from_list(db, user, award.usn, event.event_id, ledger_id=award.ledger_id,
                                                 ocr_batch_id=batch_id, request=request)

    result = {"awards_created": len(ledger_ids), "ledger_ids": ledger_ids,
              "requests_resolved": len(resolved), "resolved_request_ids": [q.request_id for q in resolved],
              "records_confirmed": len(confirmed),
              "records_dismissed": sum(r.validation_status == V.DISMISSED for r in records)}
    batch.status = OcrBatchStatus.COMMITTED         # records and extracted_metadata are kept as they are
    db.flush()
    audit(db, user.email, "OCR_BATCH_COMMITTED", "ocr_batch", batch_id,
          old_values={"status": OcrBatchStatus.REVIEW},
          new_values={"status": OcrBatchStatus.COMMITTED, "document_id": doc.document_id,
                      "event_id": event.event_id, **result}, request=request)
    return result


def _commit_problems(confirmed: list[OcrRecord], students: dict, awarded: set, event: Event) -> list[str]:
    """Every reason a confirmed record cannot become an award now. Any problem aborts the whole commit."""
    problems, seen = [], {}
    for r in confirmed:
        label, usn = f"record {r.ocr_record_id}", r.matched_student_usn
        if usn is None:
            problems.append(f"{label} has no matched student")
        elif usn not in students:
            problems.append(f"{label}: student {usn} does not exist")
        elif students[usn].student_status not in wf.MAY_SUBMIT:
            problems.append(f"{label}: {usn} is {students[usn].student_status}; only ACTIVE students can receive "
                            f"awards (set the student back to ACTIVE first)")
        points = r.extracted_points
        if points is None:
            problems.append(f"{label} has no points")
        elif points < 0 or points > MAX_POINTS:
            problems.append(f"{label}: {points} is not a valid points value")
        if usn is not None:
            if usn in seen:
                problems.append(f"{label}: {usn} is also confirmed in record {seen[usn]}")
            seen.setdefault(usn, r.ocr_record_id)
            if usn in awarded:
                problems.append(f"{label}: {usn} already has an ACTIVE award for '{event.event_name}'")
    return problems

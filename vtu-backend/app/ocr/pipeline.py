"""Background processing of one OCR batch: QUEUED -> PROCESSING -> REVIEW, or -> FAILED.

Runs after the request that created the batch has committed (a FastAPI background task) in its own session:
  1. claim the batch (row lock; only a QUEUED batch is claimed, so a batch is never processed twice) and commit
     PROCESSING, so the Dean can see that work has started;
  2. read the file through the storage abstraction and run the extractor, outside any transaction (it is slow);
  3. in one transaction: insert every OCR_RECORD, validate them, store the batch counts and metadata, set REVIEW,
     and write the audit entry. A failure rolls all of it back, so a batch never has a partial set of records;
  4. on any failure: FAILED with a message for the Dean (internal details go to the server log only).

Steps 3 and 4 act only on a batch that is still PROCESSING. If the Dean marked it FAILED in the meantime
(services/ocr.mark_failed, used when a run looks lost after a crash or restart), a run that finishes late is
discarded: it writes no records and does not change the batch, so a stalled batch and its retry can never both
produce records.

Nothing here touches point_ledger or point_request. OCR output is staged for Dean review, never awarded.
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.audit import audit, snapshot
from app.models import Document, Event, OcrBatch, OcrBatchStatus, OcrRecord, ValidationStatus
from app.ocr.base import Extraction, ExtractionError, Extractor
from app.ocr.validation import auto_match, check_event, parse_points, validate_records
from app.storage import Storage

log = logging.getLogger("app.ocr")
BATCH_FIELDS = ("ocr_batch_id", "document_id", "started_by", "status", "started_at", "completed_at",
                "pages_processed", "records_detected", "valid_records", "exception_count", "error_message")
UNEXPECTED = "OCR processing failed because of an unexpected server error. The batch can be retried."


class NoLongerProcessing(Exception):
    """The batch left PROCESSING while it was being extracted (it was marked FAILED); the result is discarded."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clip(value: str | None, length: int) -> str | None:
    """Fit a raw value into its column. The full text is always kept in raw_extraction."""
    return value[:length] if value else value


def _confidence(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return min(max(Decimal(str(value)), Decimal(0)), Decimal(1)).quantize(Decimal("0.0001"))


def process_batch(batch_id: int, open_session, extractor: Extractor, storage: Storage) -> None:
    with open_session() as db:
        batch = db.scalar(select(OcrBatch).where(OcrBatch.ocr_batch_id == batch_id).with_for_update())
        if batch is None or batch.status != OcrBatchStatus.QUEUED:
            log.warning("OCR batch %s is not QUEUED; not processing it", batch_id)
            db.rollback()
            return
        old = snapshot(batch, "status")
        batch.status = OcrBatchStatus.PROCESSING
        audit(db, batch.started_by, "OCR_BATCH_PROCESSING", "ocr_batch", batch_id, old_values=old,
              new_values=snapshot(batch, "status"))
        db.commit()

        try:
            doc = db.get(Document, batch.document_id)
            try:
                with storage.open(doc.storage_key) as f:
                    data = f.read()
            except Exception as exc:
                raise ExtractionError("The stored file of this document could not be read.") from exc
            extraction = extractor.extract(data, doc.mime_type)
            if not extraction.rows:
                raise ExtractionError("No participant rows were found in the document.")
            _store(db, batch_id, doc, extraction, extractor.name)
            db.commit()
        except NoLongerProcessing:
            db.rollback()
            log.warning("OCR batch %s is no longer PROCESSING; its late result was discarded", batch_id)
        except ExtractionError as exc:
            db.rollback()
            _fail(db, batch_id, str(exc))
        except Exception:
            log.exception("OCR batch %s failed", batch_id)
            db.rollback()
            _fail(db, batch_id, UNEXPECTED)


def _locked(db, batch_id: int) -> OcrBatch:
    """The batch row, locked and re-read from the database. populate_existing matters: the session keeps objects
    across commits (expire_on_commit=False), and the status may have been changed by another session meanwhile."""
    return db.scalar(select(OcrBatch).where(OcrBatch.ocr_batch_id == batch_id).with_for_update()
                     .execution_options(populate_existing=True))


def _store(db, batch_id: int, doc: Document, extraction: Extraction, engine: str) -> None:
    batch = _locked(db, batch_id)
    if batch.status != OcrBatchStatus.PROCESSING:
        raise NoLongerProcessing(batch_id)
    event = db.get(Event, doc.event_id) if doc.event_id is not None else None
    report, batch_problems = check_event(db, event, extraction.title_text)

    records = []
    for row in extraction.rows:
        confidence = _confidence(row.confidence)
        points, _ = parse_points(row.points)            # an unreadable value stays NULL; raw text keeps what was read
        record = OcrRecord(
            ocr_batch_id=batch_id, page_number=row.page_number, row_number=row.row_number,
            extracted_usn=_clip(row.usn, 20), matched_student_usn=auto_match(db, row.usn, confidence),
            extracted_name=_clip(row.name, 150), extracted_points=points, confidence_score=confidence,
            raw_extraction={**row.raw, "usn": row.usn, "name": row.name, "points": row.points,
                            "confidence": row.confidence, "structure_issue": row.structure_issue},
            validation_status=ValidationStatus.EXCEPTION)
        db.add(record)
        records.append(record)
    db.flush()
    validate_records(db, records, event, batch_problems)

    old = snapshot(batch, *BATCH_FIELDS)
    valid = sum(r.validation_status == ValidationStatus.VALID for r in records)
    batch.status = OcrBatchStatus.REVIEW
    batch.completed_at = _now()
    batch.pages_processed = extraction.pages_processed
    batch.records_detected = len(records)
    batch.valid_records = valid
    batch.exception_count = len(records) - valid
    batch.extracted_metadata = {**(batch.extracted_metadata or {}), **extraction.metadata, "engine": engine,
                                "title_text": extraction.title_text, "event_check": report,
                                "batch_problems": batch_problems}
    db.flush()
    audit(db, batch.started_by, "OCR_BATCH_REVIEW_READY", "ocr_batch", batch_id, old_values=old,
          new_values=snapshot(batch, *BATCH_FIELDS))


def _fail(db, batch_id: int, message: str) -> None:
    try:
        batch = _locked(db, batch_id)
        if batch.status != OcrBatchStatus.PROCESSING:        # already marked FAILED: keep that message
            log.warning("OCR batch %s is %s; not overwriting it with this failure", batch_id, batch.status)
            db.rollback()
            return
        old = snapshot(batch, "status", "error_message")
        batch.status = OcrBatchStatus.FAILED
        batch.error_message = message
        batch.completed_at = _now()
        db.flush()
        audit(db, batch.started_by, "OCR_BATCH_FAILED", "ocr_batch", batch_id, old_values=old,
              new_values=snapshot(batch, "status", "error_message"))
        db.commit()
    except Exception:
        log.exception("Could not mark OCR batch %s as FAILED; it stays PROCESSING", batch_id)
        db.rollback()

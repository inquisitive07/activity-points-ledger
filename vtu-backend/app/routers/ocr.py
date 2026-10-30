"""OCR batches, the Dean's review of OCR records, and the ledger commit. Dean-only. Rules live in
app/services/ocr.py, processing in app/ocr/pipeline.py. Only POST /ocr/batches/{id}/commit writes to point_ledger
and point_request, and only from the CONFIRMED records of a fully reviewed batch.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db, write_transaction
from app.models import Document, Event, OcrBatch, OcrBatchStatus, OcrRecord, Role, Student, UserAccount, \
    ValidationStatus
from app.ocr import Extractor, get_extractor, get_ocr_session_factory
from app.ocr.pipeline import process_batch
from app.permissions import not_found, require_roles
from app.schemas import OcrBatchIn, OcrBatchOut, OcrCommitOut, OcrRecordOut, OcrRecordPatch, OcrReviewIn
from app.services import ocr as svc
from app.storage import Storage, get_storage

dean = require_roles(Role.DEAN)
router = APIRouter(prefix="/ocr", tags=["ocr"], dependencies=[Depends(dean)])


def _batch_rows():
    return (select(OcrBatch, Document.file_name, Document.event_id, Event.event_name)
            .join(Document, Document.document_id == OcrBatch.document_id)
            .outerjoin(Event, Event.event_id == Document.event_id))


def _batch_out(db: Session, row) -> OcrBatchOut:
    batch, file_name, event_id, event_name = row
    counts = dict(db.execute(select(OcrRecord.validation_status, func.count())
                             .where(OcrRecord.ocr_batch_id == batch.ocr_batch_id)
                             .group_by(OcrRecord.validation_status)).all())
    fields = {f: getattr(batch, f) for f in OcrBatchOut.model_fields
              if f not in ("file_name", "event_id", "event_name", "record_counts")}
    return OcrBatchOut(**fields, file_name=file_name, event_id=event_id, event_name=event_name,
                       record_counts={s.value: counts.get(s.value, 0) for s in ValidationStatus})


def _load_batch(db: Session, batch_id: int) -> OcrBatchOut:
    row = db.execute(_batch_rows().where(OcrBatch.ocr_batch_id == batch_id)).first()
    if row is None:
        raise not_found("OCR batch")
    return _batch_out(db, row)


def _record_rows():
    return (select(OcrRecord, Student.name)
            .outerjoin(Student, Student.usn == OcrRecord.matched_student_usn))


def _record_out(row) -> OcrRecordOut:
    record, student_name = row
    fields = {f: getattr(record, f) for f in OcrRecordOut.model_fields if f != "matched_student_name"}
    return OcrRecordOut(**fields, matched_student_name=student_name)


def _load_record(db: Session, record_id: int) -> OcrRecordOut:
    return _record_out(db.execute(_record_rows().where(OcrRecord.ocr_record_id == record_id)).one())


def _schedule(background: BackgroundTasks, batch: OcrBatch, sessions, extractor: Extractor, storage: Storage):
    # Runs after the response, i.e. after the batch row has been committed.
    background.add_task(process_batch, batch.ocr_batch_id, sessions, extractor, storage)


@router.post("/batches", response_model=OcrBatchOut, status_code=202)
def create_batch(body: OcrBatchIn, request: Request, background: BackgroundTasks,
                 user: UserAccount = Depends(dean), db: Session = Depends(get_db),
                 storage: Storage = Depends(get_storage), extractor: Extractor = Depends(get_extractor),
                 sessions=Depends(get_ocr_session_factory)):
    """Queue OCR for an ACTIVE signed point list. 202: the batch is QUEUED; poll GET /ocr/batches/{id}."""
    with write_transaction(db):
        batch = svc.create_batch(db, user, body.document_id, request)
    _schedule(background, batch, sessions, extractor, storage)
    return _load_batch(db, batch.ocr_batch_id)


@router.get("/batches", response_model=list[OcrBatchOut])
def list_batches(status: OcrBatchStatus | None = None, document_id: int | None = None,
                 db: Session = Depends(get_db)):
    stmt = _batch_rows()
    if status:
        stmt = stmt.where(OcrBatch.status == status)
    if document_id is not None:
        stmt = stmt.where(OcrBatch.document_id == document_id)
    return [_batch_out(db, row) for row in db.execute(stmt.order_by(OcrBatch.ocr_batch_id.desc()))]


@router.get("/batches/{batch_id}", response_model=OcrBatchOut)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    return _load_batch(db, batch_id)


@router.get("/batches/{batch_id}/records", response_model=list[OcrRecordOut])
def list_records(batch_id: int, validation_status: ValidationStatus | None = None, db: Session = Depends(get_db)):
    if db.get(OcrBatch, batch_id) is None:
        raise not_found("OCR batch")
    stmt = _record_rows().where(OcrRecord.ocr_batch_id == batch_id)
    if validation_status:
        stmt = stmt.where(OcrRecord.validation_status == validation_status)
    rows = db.execute(stmt.order_by(OcrRecord.page_number, OcrRecord.row_number, OcrRecord.ocr_record_id))
    return [_record_out(row) for row in rows]


@router.post("/batches/{batch_id}/retry", response_model=OcrBatchOut, status_code=202)
def retry_batch(batch_id: int, request: Request, background: BackgroundTasks,
                user: UserAccount = Depends(dean), db: Session = Depends(get_db),
                storage: Storage = Depends(get_storage), extractor: Extractor = Depends(get_extractor),
                sessions=Depends(get_ocr_session_factory)):
    """Retry a FAILED batch: the failed batch is kept and a new batch for the same document is queued."""
    with write_transaction(db):
        batch = svc.retry_batch(db, user, batch_id, request)
    _schedule(background, batch, sessions, extractor, storage)
    return _load_batch(db, batch.ocr_batch_id)


@router.post("/batches/{batch_id}/commit", response_model=OcrCommitOut)
def commit_batch(batch_id: int, request: Request, user: UserAccount = Depends(dean), db: Session = Depends(get_db)):
    """Award every CONFIRMED record of a fully reviewed batch (all or nothing), resolve the matching open
    MISSING_FROM_LIST requests, and mark the batch COMMITTED. A repeated or concurrent commit gets 409."""
    with write_transaction(db):
        result = svc.commit_batch(db, user, batch_id, request)
    return OcrCommitOut(batch=_load_batch(db, batch_id), **result)


@router.patch("/records/{record_id}", response_model=OcrRecordOut)
def correct_record(record_id: int, body: OcrRecordPatch, request: Request, user: UserAccount = Depends(dean),
                   db: Session = Depends(get_db)):
    """Correct matched_student_usn, extracted_name and/or extracted_points (only the fields sent)."""
    with write_transaction(db):
        svc.correct(db, user, record_id, body.model_dump(include=body.model_fields_set), request)
    return _load_record(db, record_id)


@router.post("/records/{record_id}/confirm", response_model=OcrRecordOut)
def confirm_record(record_id: int, request: Request, body: OcrReviewIn | None = None,
                   user: UserAccount = Depends(dean), db: Session = Depends(get_db)):
    with write_transaction(db):
        svc.confirm(db, user, record_id, body.comment if body else None, request)
    return _load_record(db, record_id)


@router.post("/records/{record_id}/dismiss", response_model=OcrRecordOut)
def dismiss_record(record_id: int, request: Request, body: OcrReviewIn | None = None,
                   user: UserAccount = Depends(dean), db: Session = Depends(get_db)):
    with write_transaction(db):
        svc.dismiss(db, user, record_id, body.comment if body else None, request)
    return _load_record(db, record_id)


@router.post("/records/{record_id}/reopen", response_model=OcrRecordOut)
def reopen_record(record_id: int, request: Request, body: OcrReviewIn | None = None,
                  user: UserAccount = Depends(dean), db: Session = Depends(get_db)):
    with write_transaction(db):
        svc.reopen(db, user, record_id, body.comment if body else None, request)
    return _load_record(db, record_id)

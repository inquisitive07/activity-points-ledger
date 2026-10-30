"""Documents: upload, evidence links, archive and soft delete (CLAUDE.md 4.8, 4.9, 5.8, 5.12, 5.14, 10).

Upload ordering. The file and its metadata live in two systems that cannot share a transaction, so:
  1. everything that can be checked is checked before anything is written;
  2. the file is written to storage (atomically: complete or absent). If that fails, nothing touches the database;
  3. the document row and its audit entry are committed in one transaction. If anything in it fails, the transaction
     rolls back and the file written in step 2 is discarded, so neither an orphan file nor orphan metadata remains.
The one gap is a process crash between steps 2 and 3, which leaves an unreferenced file (never metadata without a
file); it is unreachable, because files are only served through their document row.

Evidence links only ever INSERT into point_request_document / point_ledger_document. Nothing here writes to
point_ledger or point_request, so evidence can never create or change an award.
"""
import hashlib
import logging
import unicodedata
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import BinaryIO

from fastapi import HTTPException, Request
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.audit import audit, snapshot
from app.config import settings
from app.db import write_transaction
from app.models import (Document, DocumentStatus, DocumentType, Event, LedgerStatus, OcrBatch, OcrBatchStatus,
                        PointLedger, PointLedgerDocument, PointRequest, PointRequestDocument, RequestStatus, Role,
                        UserAccount)
from app.permissions import document_scope, event_scope, ledger_scope, not_found
from app.services import request_workflow as wf
from app.storage import Storage, StorageError, new_key

log = logging.getLogger("app.documents")

# Accepted formats: the declared MIME type and the file's own signature must both name the same one.
SIGNATURES = {"application/pdf": (b"%PDF-", ".pdf"),
              "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
              "image/png": (b"\x89PNG\r\n\x1a\n", ".png")}

T = DocumentType
UPLOADABLE = {Role.STUDENT: {T.CERTIFICATE, T.PHOTO, T.OTHER},
              Role.CLUB_LEAD: {T.ATTENDANCE_SHEET, T.PHOTO, T.REPORT},
              Role.DEAN: set(T)}
# Audited document fields. storage_key is never written to the audit log.
FIELDS = ("document_id", "event_id", "document_type", "file_name", "mime_type", "file_size_bytes", "file_hash",
          "uploaded_by", "uploaded_at", "document_status")
READ_CHUNK = 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- content checks ---------------------------------------------------------------------------------------------

def read_limited(stream: BinaryIO, limit: int | None = None) -> bytes:
    """Read the whole upload, refusing (413) as soon as it exceeds the limit."""
    limit = settings.max_upload_bytes if limit is None else limit
    chunks, size = [], 0
    while chunk := stream.read(READ_CHUNK):
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"File is larger than the {limit // (1024 * 1024)} MB limit")
        chunks.append(chunk)
    return b"".join(chunks)


def detect_type(data: bytes) -> str | None:
    """The format named by the file's signature (magic bytes), or None."""
    return next((mime for mime, (magic, _) in SIGNATURES.items() if data.startswith(magic)), None)


def check_content(declared: str | None, data: bytes) -> tuple[str, str]:
    """(mime_type, extension) for an accepted file. The stored MIME type is the verified one."""
    if not data:
        raise HTTPException(422, "The file is empty")
    declared = (declared or "").split(";", 1)[0].strip().lower()
    if declared not in SIGNATURES:
        raise HTTPException(415, "Only PDF, JPEG and PNG files are accepted")
    actual = detect_type(data)
    if actual != declared:
        raise HTTPException(415, "The file's contents do not match its declared type "
                                 "(only genuine PDF, JPEG and PNG files are accepted)")
    return actual, SIGNATURES[actual][1]


def clean_file_name(raw: str | None, extension: str) -> str:
    """A display name only: never used as a path. Directory parts and control characters are removed."""
    name = unicodedata.normalize("NFC", (raw or "").replace("\\", "/")).rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if name.strip(".") == "":
        name = f"document{extension}"
    if len(name) > 255:
        suffix = PurePosixPath(name).suffix
        suffix = suffix if len(suffix) <= 16 else ""
        name = name[:255 - len(suffix)] + suffix
    return name


# --- upload -----------------------------------------------------------------------------------------------------

def upload(db: Session, storage: Storage, user: UserAccount, *, document_type: str, event_id: int | None,
           file_name: str | None, declared_type: str | None, stream: BinaryIO,
           request: Request | None = None) -> Document:
    """Validate, store the file, then commit its metadata (see the module docstring for the failure handling)."""
    allowed = UPLOADABLE.get(user.role)
    if allowed is None:
        raise HTTPException(403, "Your role cannot upload documents")
    if document_type not in allowed:
        raise HTTPException(403, f"A {user.role} account cannot upload {document_type} documents "
                                 f"(allowed: {', '.join(sorted(allowed))})")
    if user.role == Role.CLUB_LEAD and event_id is None:
        raise HTTPException(422, "Club documents must be tagged to one of your club's events")
    if event_id is not None and db.scalar(select(Event.event_id).where(Event.event_id == event_id,
                                                                       event_scope(user))) is None:
        raise not_found("Event")             # club leads' event scope is their own club's events

    data = read_limited(stream)
    mime_type, extension = check_content(declared_type, data)
    doc = Document(event_id=event_id, document_type=document_type, file_name=clean_file_name(file_name, extension),
                   storage_key=new_key(extension), mime_type=mime_type, file_size_bytes=len(data),
                   file_hash=hashlib.sha256(data).hexdigest(), uploaded_by=user.email, uploaded_at=_now(),
                   document_status=DocumentStatus.ACTIVE)

    try:
        storage.put(doc.storage_key, data)
    except Exception:
        log.exception("Storing an upload failed; no document metadata was written")
        raise HTTPException(503, "The file could not be stored. Nothing was saved; please try again.")

    try:
        with write_transaction(db):
            db.add(doc)
            db.flush()
            audit(db, user.email, "DOCUMENT_UPLOADED", "document", doc.document_id,
                  new_values=snapshot(doc, *FIELDS), request=request)
    except BaseException:
        _discard(storage, doc.storage_key)
        raise
    return doc


def _discard(storage: Storage, key: str) -> None:
    try:
        storage.discard(key)
    except StorageError:
        log.exception("Could not remove the file of a failed upload; it is unreferenced and never served")


# --- reading ----------------------------------------------------------------------------------------------------

def get_visible(db: Session, user: UserAccount, document_id: int, *, lock: str | None = None) -> Document:
    """The document if it is inside the caller's vault (document_scope), else 404. lock: None, "share", "update"."""
    stmt = select(Document).where(Document.document_id == document_id, document_scope(user))
    if lock:
        stmt = stmt.with_for_update(read=lock == "share", of=Document)
    doc = db.scalar(stmt)
    if doc is None:
        raise not_found("Document")
    return doc


def open_content(storage: Storage, doc: Document) -> BinaryIO:
    """Open an authorized document's file. Callers must have loaded `doc` through get_visible."""
    try:
        return storage.open(doc.storage_key)
    except StorageError:
        log.exception("Stored file missing or unreadable for document %s", doc.document_id)
        raise HTTPException(500, "The stored file is unavailable")


# --- evidence links ---------------------------------------------------------------------------------------------

def _attachable(db: Session, user: UserAccount, document_id: int, *, own_only: bool) -> Document:
    doc = get_visible(db, user, document_id, lock="share")      # a concurrent delete waits for this link
    if own_only and doc.uploaded_by != user.email:
        raise HTTPException(403, "You can attach only documents you uploaded")
    if doc.document_status != DocumentStatus.ACTIVE:
        raise HTTPException(409, f"Only ACTIVE documents can be attached; this one is {doc.document_status}")
    return doc


def _same_event(doc: Document, event_id: int):
    if doc.event_id is not None and doc.event_id != event_id:
        raise HTTPException(422, "The document is tagged to a different event")


def attach_to_request(db: Session, user: UserAccount, request_id: int, document_id: int,
                      request: Request | None = None) -> PointRequestDocument:
    """The requesting student attaches one of their own documents to their own open request."""
    if user.role != Role.STUDENT:
        raise HTTPException(403, "Only the requesting student can attach evidence to a request")
    req = wf.get_visible(db, user, request_id, lock=True)        # own requests only; serialised with reviews
    if req.status not in wf.OPEN:
        raise HTTPException(409, f"Request is {req.status}; evidence can be attached only while it is pending")
    doc = _attachable(db, user, document_id, own_only=True)
    _same_event(doc, req.event_id)
    before = sorted(db.scalars(select(PointRequestDocument.document_id)
                               .where(PointRequestDocument.request_id == req.request_id)))
    if doc.document_id in before:
        raise HTTPException(409, "This document is already attached to the request")
    link = PointRequestDocument(request_id=req.request_id, document_id=doc.document_id, attached_at=_now())
    db.add(link)
    db.flush()
    audit(db, user.email, "REQUEST_DOCUMENT_ATTACHED", "point_request", req.request_id,
          old_values={"document_ids": before},
          new_values={"document_ids": sorted(before + [doc.document_id]),
                      **snapshot(doc, "document_id", "document_type", "file_name", "file_hash"),
                      **snapshot(link, "attached_at")}, request=request)
    return link


def attach_to_ledger(db: Session, user: UserAccount, ledger_id: int, document_id: int,
                     request: Request | None = None) -> PointLedgerDocument:
    """Evidence for an existing award (the student who owns it, or the Dean). Inserts the link row only."""
    if user.role not in (Role.STUDENT, Role.DEAN):
        raise HTTPException(403, "Only the student who owns the award, or the Dean, can attach evidence to it")
    # FOR SHARE: the award cannot be superseded while the link is being made, and is not modified by it.
    award = db.scalar(select(PointLedger).where(PointLedger.ledger_id == ledger_id, ledger_scope(user))
                      .with_for_update(read=True, of=PointLedger))
    if award is None:
        raise not_found("Award")
    if award.status != LedgerStatus.ACTIVE:
        raise HTTPException(409, f"Evidence can be attached only to an ACTIVE award; this one is {award.status}")
    doc = _attachable(db, user, document_id, own_only=user.role == Role.STUDENT)
    _same_event(doc, award.event_id)
    # Linking makes a document visible to the award's student (document_scope), so the Dean must not link one
    # student's own upload to another student's award.
    uploader = db.get(UserAccount, doc.uploaded_by)
    if uploader.role == Role.STUDENT and uploader.student_usn != award.usn:
        raise HTTPException(422, "A student's document can only be linked to that student's own awards")
    before = sorted(db.scalars(select(PointLedgerDocument.document_id)
                               .where(PointLedgerDocument.ledger_id == award.ledger_id)))
    if doc.document_id in before:
        raise HTTPException(409, "This document is already linked to the award")
    link = PointLedgerDocument(ledger_id=award.ledger_id, document_id=doc.document_id, linked_at=_now(),
                               linked_by=user.email)
    db.add(link)
    db.flush()
    audit(db, user.email, "LEDGER_DOCUMENT_LINKED", "point_ledger", award.ledger_id,
          old_values={"document_ids": before},
          new_values={"document_ids": sorted(before + [doc.document_id]),
                      **snapshot(doc, "document_id", "document_type", "file_name", "file_hash"),
                      **snapshot(link, "linked_at", "linked_by")}, request=request)
    return link


# --- archive and soft delete ------------------------------------------------------------------------------------

def _manageable(db: Session, user: UserAccount, document_id: int) -> Document:
    doc = get_visible(db, user, document_id, lock="update")     # 404 outside the vault
    if user.role != Role.DEAN and doc.uploaded_by != user.email:
        raise HTTPException(403, "Only the uploader or the Dean can archive or delete this document")
    return doc


def _set_status(db: Session, user: UserAccount, doc: Document, target: DocumentStatus, action: str,
                request: Request | None):
    old = snapshot(doc, "document_status")
    doc.document_status = target
    db.flush()
    audit(db, user.email, action, "document", doc.document_id, old_values=old,
          new_values=snapshot(doc, "document_status"), request=request)


def archive(db: Session, user: UserAccount, document_id: int, request: Request | None = None) -> Document:
    doc = _manageable(db, user, document_id)
    if doc.document_status != DocumentStatus.ACTIVE:
        raise HTTPException(409, f"Only ACTIVE documents can be archived; this one is {doc.document_status}")
    _set_status(db, user, doc, DocumentStatus.ARCHIVED, "DOCUMENT_ARCHIVED", request)
    return doc


# A request whose evidence must stay: still under review, or decided in a way that produced/recorded points.
_EVIDENCE_KEPT = (RequestStatus.PENDING_CLUB, RequestStatus.PENDING_DEAN, RequestStatus.APPROVED,
                  RequestStatus.RESOLVED)


def deletion_blocker(db: Session, doc: Document) -> str | None:
    """Why `doc` must not be deleted, or None. The file itself is never removed either way."""
    did = doc.document_id
    if db.scalar(select(exists().where(PointLedgerDocument.document_id == did))):
        return "it is evidence for an award"
    if db.scalar(select(exists().where(PointRequestDocument.document_id == did,
                                       PointRequestDocument.request_id == PointRequest.request_id,
                                       PointRequest.status.in_(_EVIDENCE_KEPT)))):
        return "it is evidence for a request that is pending or was approved/resolved"
    if db.scalar(select(exists().where(OcrBatch.document_id == did, OcrBatch.status != OcrBatchStatus.FAILED))):
        return "it is a signed list used by OCR processing"
    return None


def soft_delete(db: Session, user: UserAccount, document_id: int, request: Request | None = None) -> Document:
    doc = _manageable(db, user, document_id)
    if doc.document_status not in (DocumentStatus.ACTIVE, DocumentStatus.ARCHIVED):
        raise HTTPException(409, f"This document is already {doc.document_status}")
    if reason := deletion_blocker(db, doc):
        raise HTTPException(409, f"This document cannot be deleted: {reason}")
    _set_status(db, user, doc, DocumentStatus.DELETED, "DOCUMENT_DELETED", request)
    return doc

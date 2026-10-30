"""Document upload, vaults and authorized file retrieval. Rules live in app/services/documents.py.

Every read starts from permissions.document_scope, so a vault is that condition applied to the one document table
and an ID outside it is a 404 exactly like an ID that does not exist. Files are reachable only through
GET /documents/{id}/content, after that check; storage keys are never accepted from or returned to clients.
"""
import re
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, aliased

from app.db import get_db, write_transaction
from app.deps import get_current_user
from app.models import (Document, DocumentStatus, DocumentType, Event, PointLedger, PointLedgerDocument, PointRequest,
                        PointRequestDocument, Role, UserAccount)
from app.permissions import document_scope, ledger_scope, not_found, request_scope, require_roles
from app.schemas import DocumentDetailOut, DocumentOut
from app.services import documents as svc
from app.storage import Storage, get_storage

router = APIRouter(prefix="/documents", tags=["documents"])
writers = require_roles(Role.STUDENT, Role.CLUB_LEAD, Role.DEAN)          # HOD is read-only: 403 on every write


def _rows(user: UserAccount):
    return (select(Document, Event.event_name).outerjoin(Event, Event.event_id == Document.event_id)
            .where(document_scope(user)))


def _out(doc: Document, event_name: str | None) -> DocumentOut:
    return DocumentOut(event_name=event_name,
                       **{f: getattr(doc, f) for f in DocumentOut.model_fields if f != "event_name"})


def _doc_out(db: Session, doc: Document) -> DocumentOut:
    return _out(doc, db.scalar(select(Event.event_name).where(Event.event_id == doc.event_id)) if doc.event_id else None)


def _in_requests(user: UserAccount, request_id: int):
    return exists().where(PointRequestDocument.document_id == Document.document_id,
                          PointRequestDocument.request_id == request_id,
                          PointRequest.request_id == PointRequestDocument.request_id, request_scope(user))


def _in_awards(user: UserAccount, ledger_id: int):
    return exists().where(PointLedgerDocument.document_id == Document.document_id,
                          PointLedgerDocument.ledger_id == ledger_id,
                          PointLedger.ledger_id == PointLedgerDocument.ledger_id, ledger_scope(user))


def _of_student(user: UserAccount, usn: str):
    """Documents about one student: uploaded from their account, or backing their requests or awards. Applied on top
    of document_scope, and the request/award links must be inside the caller's own scopes too, so the filter only
    narrows the vault: another department's USN gives an empty list, and a signed list shared by many students
    never reveals which other students it backs."""
    uploader = aliased(UserAccount)
    return or_(exists().where(uploader.email == Document.uploaded_by, uploader.student_usn == usn),
               exists().where(PointRequestDocument.document_id == Document.document_id,
                              PointRequest.request_id == PointRequestDocument.request_id, PointRequest.usn == usn,
                              request_scope(user)),
               exists().where(PointLedgerDocument.document_id == Document.document_id,
                              PointLedger.ledger_id == PointLedgerDocument.ledger_id, PointLedger.usn == usn,
                              ledger_scope(user)))


@router.post("", response_model=DocumentOut, status_code=201)
def upload(request: Request, file: UploadFile = File(...), document_type: DocumentType = Form(...),
           event_id: int | None = Form(None), user: UserAccount = Depends(writers), db: Session = Depends(get_db),
           storage: Storage = Depends(get_storage)):
    """multipart/form-data: file (PDF, JPEG or PNG, at most 10 MB), document_type, optional event_id."""
    doc = svc.upload(db, storage, user, document_type=document_type, event_id=event_id, file_name=file.filename,
                     declared_type=file.content_type, stream=file.file, request=request)
    return _doc_out(db, doc)


@router.get("", response_model=list[DocumentOut])
def list_documents(document_type: DocumentType | None = None, event_id: int | None = None,
                   status: DocumentStatus | None = None, request_id: int | None = None,
                   ledger_id: int | None = None, usn: str | None = None,
                   user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """The caller's vault. Soft-deleted documents are left out unless status=DELETED is asked for (Dean only:
    nobody else's scope contains them). usn narrows it to one student's documents (e.g. the HOD inspection view)."""
    stmt = _rows(user).where(Document.document_status == status if status
                             else Document.document_status != DocumentStatus.DELETED)
    if document_type:
        stmt = stmt.where(Document.document_type == document_type)
    if event_id is not None:
        stmt = stmt.where(Document.event_id == event_id)
    if request_id is not None:
        stmt = stmt.where(_in_requests(user, request_id))
    if ledger_id is not None:
        stmt = stmt.where(_in_awards(user, ledger_id))
    if usn and usn.strip():
        stmt = stmt.where(_of_student(user, usn.strip().upper()))
    rows = db.execute(stmt.order_by(Document.uploaded_at.desc(), Document.document_id.desc()))
    return [_out(doc, event_name) for doc, event_name in rows]


@router.get("/{document_id}", response_model=DocumentDetailOut)
def get_document(document_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.execute(_rows(user).where(Document.document_id == document_id)).first()
    if row is None:
        raise not_found("Document")
    request_ids = db.scalars(select(PointRequest.request_id)
                             .join(PointRequestDocument, PointRequestDocument.request_id == PointRequest.request_id)
                             .where(PointRequestDocument.document_id == document_id, request_scope(user))
                             .order_by(PointRequest.request_id)).all()
    ledger_ids = db.scalars(select(PointLedger.ledger_id)
                            .join(PointLedgerDocument, PointLedgerDocument.ledger_id == PointLedger.ledger_id)
                            .where(PointLedgerDocument.document_id == document_id, ledger_scope(user))
                            .order_by(PointLedger.ledger_id)).all()
    return DocumentDetailOut(**_out(*row).model_dump(), request_ids=request_ids, ledger_ids=ledger_ids)


def content_disposition(kind: str, name: str) -> str:
    """An ASCII-only filename for old clients plus the exact (percent-encoded) UTF-8 name. Neither can carry
    quotes, CR/LF or path separators into the header."""
    fallback = re.sub(r"[^A-Za-z0-9._ ()-]", "_", name).strip(" .") or "document"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def _chunks(stream, size: int = 64 * 1024):
    with stream:
        while chunk := stream.read(size):
            yield chunk


@router.get("/{document_id}/content")
def get_content(document_id: int, disposition: Literal["attachment", "inline"] = "attachment",
                user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
                storage: Storage = Depends(get_storage)):
    doc = svc.get_visible(db, user, document_id)          # authorization first; only then is storage touched
    stream = svc.open_content(storage, doc)
    return StreamingResponse(_chunks(stream), media_type=doc.mime_type, headers={
        "Content-Disposition": content_disposition(disposition, doc.file_name),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    })


@router.post("/{document_id}/archive", response_model=DocumentOut)
def archive(document_id: int, request: Request, user: UserAccount = Depends(writers), db: Session = Depends(get_db)):
    with write_transaction(db):
        doc = svc.archive(db, user, document_id, request)
    return _doc_out(db, doc)


@router.post("/{document_id}/delete", response_model=DocumentOut)
def delete(document_id: int, request: Request, user: UserAccount = Depends(writers), db: Session = Depends(get_db)):
    """Soft delete: the status becomes DELETED; the stored file is kept."""
    with write_transaction(db):
        doc = svc.soft_delete(db, user, document_id, request)
    return _doc_out(db, doc)

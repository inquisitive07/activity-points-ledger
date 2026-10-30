"""Official awards. This router only adds evidence: it never creates, changes or removes a point_ledger row."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db, write_transaction
from app.models import Role, UserAccount
from app.permissions import require_roles
from app.schemas import AttachDocumentIn, LedgerDocumentOut
from app.services import documents as svc

router = APIRouter(prefix="/ledger", tags=["ledger"])


@router.post("/{ledger_id}/documents", response_model=LedgerDocumentOut, status_code=201)
def attach_document(ledger_id: int, body: AttachDocumentIn, request: Request,
                    user: UserAccount = Depends(require_roles(Role.STUDENT, Role.DEAN)),
                    db: Session = Depends(get_db)):
    """Evidence-only: link a document to an existing ACTIVE award (the award's student or the Dean)."""
    with write_transaction(db):
        link = svc.attach_to_ledger(db, user, ledger_id, body.document_id, request)
    return LedgerDocumentOut(ledger_id=link.ledger_id, document_id=link.document_id, linked_at=link.linked_at,
                             linked_by=link.linked_by)

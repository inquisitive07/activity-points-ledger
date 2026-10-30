"""Point requests. Routers stay thin: rules, transitions and locking live in app/services/request_workflow.py."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db, write_transaction
from app.deps import get_current_user
from app.models import Event, PointLedger, PointRequest, RequestStatus, RequestType, Role, Student, UserAccount
from app.permissions import not_found, request_scope, require_roles
from app.schemas import ClubReviewIn, DeanReviewIn, RequestIn, RequestOut
from app.services import request_workflow as wf

router = APIRouter(prefix="/requests", tags=["requests"])

_award_of = (select(PointLedger.ledger_id).where(PointLedger.source_request_id == PointRequest.request_id)
             .order_by(PointLedger.ledger_id.desc()).limit(1).scalar_subquery())


def _rows(user: UserAccount):
    return (select(PointRequest, Student.name, Event.event_name, Event.club_id, _award_of)
            .join(Student, Student.usn == PointRequest.usn).join(Event, Event.event_id == PointRequest.event_id)
            .where(request_scope(user)))


def _out(row) -> RequestOut:
    req, student_name, event_name, club_id, ledger_id = row
    fields = {f: getattr(req, f) for f in RequestOut.model_fields
              if f not in ("student_name", "event_name", "club_id", "ledger_id")}
    return RequestOut(**fields, student_name=student_name, event_name=event_name, club_id=club_id,
                      ledger_id=ledger_id)


def _load(db: Session, user: UserAccount, request_id: int) -> RequestOut:
    row = db.execute(_rows(user).where(PointRequest.request_id == request_id)).first()
    if row is None:
        raise not_found("Request")
    return _out(row)


@router.post("", response_model=RequestOut, status_code=201)
def submit(body: RequestIn, request: Request, user: UserAccount = Depends(require_roles(Role.STUDENT)),
           db: Session = Depends(get_db)):
    with write_transaction(db):
        req = wf.submit(db, user, body, request)
    return _load(db, user, req.request_id)


@router.get("", response_model=list[RequestOut])
def list_requests(status: RequestStatus | None = None, request_type: RequestType | None = None,
                  event_id: int | None = None, user: UserAccount = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    stmt = _rows(user)
    if status:
        stmt = stmt.where(PointRequest.status == status)
    if request_type:
        stmt = stmt.where(PointRequest.request_type == request_type)
    if event_id is not None:
        stmt = stmt.where(PointRequest.event_id == event_id)
    return [_out(row) for row in db.execute(stmt.order_by(PointRequest.request_id.desc()))]


@router.get("/{request_id}", response_model=RequestOut)
def get_request(request_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return _load(db, user, request_id)


@router.post("/{request_id}/withdraw", response_model=RequestOut)
def withdraw(request_id: int, request: Request, user: UserAccount = Depends(require_roles(Role.STUDENT)),
             db: Session = Depends(get_db)):
    with write_transaction(db):
        wf.withdraw(db, user, request_id, request)
    return _load(db, user, request_id)


@router.post("/{request_id}/club-review", response_model=RequestOut)
def club_review(request_id: int, body: ClubReviewIn, request: Request,
                user: UserAccount = Depends(require_roles(Role.CLUB_LEAD)), db: Session = Depends(get_db)):
    with write_transaction(db):
        wf.club_review(db, user, request_id, body.action, body.comment, request)
    return _load(db, user, request_id)


@router.post("/{request_id}/dean-review", response_model=RequestOut)
def dean_review(request_id: int, body: DeanReviewIn, request: Request,
                user: UserAccount = Depends(require_roles(Role.DEAN)), db: Session = Depends(get_db)):
    with write_transaction(db):
        wf.dean_review(db, user, request_id, body.action, body.comment, request)
    return _load(db, user, request_id)

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_roles
from app.models import Event, PointRequest, RequestStatus, Role, Student, User
from app.schemas import RequestIn, ReviewIn
from app.services import request_workflow as wf

router = APIRouter(prefix="/requests", tags=["requests"])


def out(r: PointRequest, s: Student, e: Event):
    return {"id": r.id, "usn": s.usn, "student": s.name, "event_id": e.id, "event": e.title, "club_id": e.club_id,
            "request_type": r.request_type.value, "claimed_points": r.claimed_points, "reason": r.reason, "status": r.status.value,
            "reviewer_comment": r.reviewer_comment, "created_at": r.created_at.isoformat()}


def _load(db: Session, req_id: int):
    return db.execute(select(PointRequest, Student, Event).join(Student, Student.id == PointRequest.student_id)
                      .join(Event, Event.id == PointRequest.event_id).where(PointRequest.id == req_id)).one()


@router.post("", status_code=201)
def submit(body: RequestIn, user: User = Depends(require_roles(Role.STUDENT)), db: Session = Depends(get_db)):
    student = db.scalar(select(Student).where(Student.user_id == user.id))
    if not student:
        raise HTTPException(403, "No student profile is linked to this account")
    req = wf.submit(db, user, student, body)
    db.commit()
    return out(*_load(db, req.id))


@router.get("")
def list_requests(status: RequestStatus | None = None, user: User = Depends(require_roles(Role.STUDENT, Role.CLUB, Role.DEAN)),
                  db: Session = Depends(get_db)):
    stmt = select(PointRequest, Student, Event).join(Student, Student.id == PointRequest.student_id).join(Event, Event.id == PointRequest.event_id)
    if user.role == Role.STUDENT:
        stmt = stmt.where(Student.user_id == user.id)
    elif user.role == Role.CLUB:
        stmt = stmt.where(Event.club_id == user.club_id)
    if status:
        stmt = stmt.where(PointRequest.status == status)
    return [out(r, s, e) for r, s, e in db.execute(stmt.order_by(PointRequest.id.desc()))]


@router.post("/{req_id}/club-review")
def club_review(req_id: int, body: ReviewIn, user: User = Depends(require_roles(Role.CLUB)), db: Session = Depends(get_db)):
    wf.club_review(db, user, req_id, body.action, body.comment)
    db.commit()
    return out(*_load(db, req_id))


@router.post("/{req_id}/dean-review")
def dean_review(req_id: int, body: ReviewIn, user: User = Depends(require_roles(Role.DEAN)), db: Session = Depends(get_db)):
    wf.dean_review(db, user, req_id, body.action, body.comment)
    db.commit()
    return out(*_load(db, req_id))

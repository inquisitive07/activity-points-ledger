"""Student directory + summary. Scope is enforced HERE, in the query — not in the frontend."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_roles
from app.models import (Document, Event, LedgerStatus, PointLedger, PointRequirement, Role, Student, User)

router = APIRouter(prefix="/students", tags=["students"])


def visible_students(user: User):
    """The base query every student-facing endpoint must start from."""
    q = select(Student)
    if user.role == Role.DEAN:
        return q
    if user.role == Role.HOD:
        return q.where(Student.department_code == user.department_code)
    if user.role == Role.STUDENT:
        return q.where(Student.user_id == user.id)
    return q.where(False)  # club leads have no student-directory access


def summary_of(db: Session, s: Student) -> dict:
    earned = db.scalar(select(func.coalesce(func.sum(PointLedger.points), 0)).where(
        PointLedger.student_id == s.id, PointLedger.status != LedgerStatus.SUPERSEDED))
    target = db.scalar(select(PointRequirement.points_required).where(PointRequirement.entry_type == s.entry_type))
    return {"usn": s.usn, "name": s.name, "department_code": s.department_code, "entry_type": s.entry_type.value,
            "earned": int(earned), "target": target, "target_reached": earned >= target}


@router.get("")
def list_students(q: str = "", user: User = Depends(require_roles(Role.DEAN, Role.HOD, Role.STUDENT)), db: Session = Depends(get_db)):
    stmt = visible_students(user)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(Student.name).like(like) | func.lower(Student.usn).like(like))
    return [summary_of(db, s) for s in db.scalars(stmt.order_by(Student.usn))]


@router.get("/{usn}")
def student_detail(usn: str, user: User = Depends(require_roles(Role.DEAN, Role.HOD, Role.STUDENT)), db: Session = Depends(get_db)):
    s = db.scalar(visible_students(user).where(Student.usn == usn.upper()))
    if not s:  # out-of-scope and non-existent look identical (404) on purpose
        raise HTTPException(404, "Student not found")
    ledger = db.execute(select(PointLedger, Event.title).join(Event, Event.id == PointLedger.event_id)
                        .where(PointLedger.student_id == s.id).order_by(PointLedger.id)).all()
    docs = db.scalars(select(Document).where(Document.student_id == s.id)).all()
    return {**summary_of(db, s),
            "ledger": [{"id": l.id, "event": t, "points": l.points, "status": l.status.value, "source": l.source.value} for l, t in ledger],
            "documents": [{"id": str(d.id), "filename": d.filename, "type": d.doc_type.value, "purpose": d.purpose.value} for d in docs]}

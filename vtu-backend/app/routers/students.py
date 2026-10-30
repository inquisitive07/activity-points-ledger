"""Student directory and point summary. Scope is enforced in the query (app/permissions.py), not in the frontend."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db, write_transaction
from app.models import Event, PointLedger, Role, Student, UserAccount, student_point_summary as sps
from app.permissions import not_found, require_roles, student_scope
from app.schemas import LedgerRowOut, StudentDetailOut, StudentStatusIn, StudentSummaryOut
from app.services import students as svc

router = APIRouter(prefix="/students", tags=["students"])
readers = require_roles(Role.DEAN, Role.HOD, Role.STUDENT)


def summaries(user: UserAccount):
    """Visible students joined with STUDENT_POINT_SUMMARY (the view supplies required/earned/remaining)."""
    return (select(Student, sps.c.required_points, sps.c.earned_points, sps.c.remaining_points, sps.c.target_reached)
            .join(sps, sps.c.usn == Student.usn).where(student_scope(user)))


def summary_out(row) -> StudentSummaryOut:
    s = row.Student
    return StudentSummaryOut(usn=s.usn, name=s.name, department_code=s.department_code, entry_type=s.entry_type,
                             student_status=s.student_status, required_points=row.required_points,
                             earned_points=row.earned_points, remaining_points=row.remaining_points,
                             target_reached=row.target_reached)


@router.get("", response_model=list[StudentSummaryOut])
def list_students(q: str = "", user: UserAccount = Depends(readers), db: Session = Depends(get_db)):
    stmt = summaries(user)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Student.name).like(like), func.lower(Student.usn).like(like)))
    return [summary_out(row) for row in db.execute(stmt.order_by(Student.usn))]


@router.get("/{usn}", response_model=StudentDetailOut)
def student_detail(usn: str, user: UserAccount = Depends(readers), db: Session = Depends(get_db)):
    row = db.execute(summaries(user).where(Student.usn == usn.strip().upper())).first()
    if row is None:             # out of scope and non-existent look the same on purpose
        raise not_found("Student")
    ledger = db.execute(select(PointLedger, Event.event_name).join(Event, Event.event_id == PointLedger.event_id)
                        .where(PointLedger.usn == row.Student.usn).order_by(PointLedger.ledger_id)).all()
    return StudentDetailOut(
        **summary_out(row).model_dump(),
        ledger=[LedgerRowOut(ledger_id=l.ledger_id, event_id=l.event_id, event_name=name,
                             points_awarded=l.points_awarded, award_date=l.award_date, source_type=l.source_type,
                             status=l.status, supersedes_ledger_id=l.supersedes_ledger_id) for l, name in ledger])


@router.post("/{usn}/status", response_model=StudentSummaryOut)
def change_status(usn: str, body: StudentStatusIn, request: Request,
                  user: UserAccount = Depends(require_roles(Role.DEAN)), db: Session = Depends(get_db)):
    """Dean only: set ACTIVE, GRADUATED, INACTIVE or ARCHIVED. Only ACTIVE students may submit new requests; every
    status keeps login, read access, open requests and awards. Nothing is deleted."""
    with write_transaction(db):
        student = svc.change_status(db, user, usn, body.student_status, body.reason, request)
    return summary_out(db.execute(summaries(user).where(Student.usn == student.usn)).one())

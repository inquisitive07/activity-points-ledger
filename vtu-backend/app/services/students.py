"""Student lifecycle (CLAUDE.md 4.10, 18.5). Only the Dean changes student_status; records are never deleted.

Any of the four statuses can be set from any other: the source defines the values but no transition order, and the
approved rules need GRADUATED/INACTIVE -> ACTIVE ("to claim for past activity, the Dean first sets the student back
to ACTIVE"). What a status changes is decided elsewhere and only for *new* requests (request_workflow.MAY_SUBMIT):
login, read access, open requests and awards are unaffected.
"""
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.models import Role, Student, UserAccount
from app.permissions import not_found


def change_status(db: Session, user: UserAccount, usn: str, status: str, reason: str | None,
                  request: Request | None = None) -> Student:
    if user.role != Role.DEAN:
        raise HTTPException(403, "Only the Dean can change a student's status")
    student = db.scalar(select(Student).where(Student.usn == usn.strip().upper()).with_for_update())
    if student is None:
        raise not_found("Student")
    if student.student_status == status:
        raise HTTPException(409, f"The student is already {status}")
    old = student.student_status
    student.student_status = status
    db.flush()
    audit(db, user.email, "STUDENT_STATUS_CHANGED", "student", None,
          old_values={"usn": student.usn, "student_status": old},
          new_values={"usn": student.usn, "student_status": status, "reason": reason}, request=request)
    return student

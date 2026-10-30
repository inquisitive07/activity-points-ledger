"""Role guards and object-level scoping (CLAUDE.md 3 and 11.2).

Routes use `require_roles` for the coarse check, then start every query from a scope below so that object IDs
outside the caller's reach behave exactly like IDs that do not exist (404, never a hint). The scopes are SQL
conditions, so filtering happens in PostgreSQL rather than after loading rows.

    STUDENT    own student record, own requests/awards, documents they uploaded or that back their requests/awards
    CLUB_LEAD  own club's events, and requests/awards/documents attached to those events; no student directory
    HOD        students of their own department and everything attached to those students; read-only
    DEAN       everything
"""
from fastapi import Depends, HTTPException
from sqlalchemy import ColumnElement, and_, exists, false, or_, true

from app.deps import get_current_user
from app.models import (Document, DocumentStatus, Event, PointLedger, PointLedgerDocument, PointRequest,
                        PointRequestDocument, Role, Student, UserAccount)


def require_roles(*roles: Role):
    """Route guard: only the listed roles get through (everyone else: 403)."""
    allowed = {Role(r) for r in roles}

    def guard(user: UserAccount = Depends(get_current_user)) -> UserAccount:
        if user.role not in allowed:
            raise HTTPException(403, "Your role cannot access this resource")
        return user
    return guard


def not_found(what: str) -> HTTPException:
    return HTTPException(404, f"{what} not found")


# --- students ---------------------------------------------------------------------------------------------------

def student_scope(user: UserAccount) -> ColumnElement[bool]:
    """Condition on Student: which student records the caller may read."""
    match user.role:
        case Role.DEAN:
            return true()
        case Role.HOD:
            return Student.department_code == user.department_code
        case Role.STUDENT:
            return Student.usn == user.student_usn
    return false()              # club leads see students only through requests for their own events


def _usn_in_scope(usn_column, user: UserAccount) -> ColumnElement[bool]:
    """Condition: the student identified by `usn_column` is inside student_scope(user)."""
    if user.role == Role.DEAN:
        return true()
    if user.role == Role.STUDENT:
        return usn_column == user.student_usn
    if user.role == Role.HOD:
        return exists().where(Student.usn == usn_column, Student.department_code == user.department_code)
    return false()


def _event_of_own_club(event_column, user: UserAccount) -> ColumnElement[bool]:
    if user.role != Role.CLUB_LEAD:
        return false()
    return exists().where(Event.event_id == event_column, Event.club_id == user.club_id)


# --- events -----------------------------------------------------------------------------------------------------

def can_manage_event(user: UserAccount, event: Event) -> bool:
    """Club leads manage their own club's events; the Dean manages non-club events (club_id NULL)."""
    if user.role == Role.CLUB_LEAD:
        return event.club_id is not None and event.club_id == user.club_id
    return user.role == Role.DEAN and event.club_id is None


# --- requests and awards ----------------------------------------------------------------------------------------

def request_scope(user: UserAccount) -> ColumnElement[bool]:
    """Condition on PointRequest."""
    if user.role == Role.CLUB_LEAD:
        return _event_of_own_club(PointRequest.event_id, user)
    return _usn_in_scope(PointRequest.usn, user)


def ledger_scope(user: UserAccount) -> ColumnElement[bool]:
    """Condition on PointLedger."""
    if user.role == Role.CLUB_LEAD:
        return _event_of_own_club(PointLedger.event_id, user)
    return _usn_in_scope(PointLedger.usn, user)


# --- documents --------------------------------------------------------------------------------------------------

def document_scope(user: UserAccount) -> ColumnElement[bool]:
    """Condition on Document (the vaults are this condition applied to one shared table). A storage_key is never
    authorization: file access must load the Document through this scope first. Soft-deleted documents are
    visible to the Dean only."""
    if user.role == Role.DEAN:
        return true()
    backs_request = exists().where(PointRequestDocument.document_id == Document.document_id,
                                   PointRequestDocument.request_id == PointRequest.request_id, request_scope(user))
    backs_award = exists().where(PointLedgerDocument.document_id == Document.document_id,
                                 PointLedgerDocument.ledger_id == PointLedger.ledger_id, ledger_scope(user))
    if user.role == Role.STUDENT:
        own = Document.uploaded_by == user.email
    elif user.role == Role.HOD:
        own = exists().where(UserAccount.email == Document.uploaded_by, _usn_in_scope(UserAccount.student_usn, user))
    elif user.role == Role.CLUB_LEAD:
        own = _event_of_own_club(Document.event_id, user)
    else:
        return false()
    return and_(Document.document_status != DocumentStatus.DELETED, or_(own, backs_request, backs_award))


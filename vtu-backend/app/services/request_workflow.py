"""Point request workflow (CLAUDE.md 4.2-4.7 and the 18.5 state machine).

Every state change goes through `_move`, so a transition that is not in TRANSITIONS cannot happen whichever
endpoint asks for it. Functions here flush but never commit: the caller wraps them in db.write_transaction, so the
request update, the ledger rows and their audit entries are committed together or not at all.

Locking: a review locks its point_request row (SELECT ... FOR UPDATE) before reading the status, so two reviewers
of one request are serialised and the second one sees the first one's result (409). A correction approval also locks
the award it corrects. Submissions lock the student's row so one student's submissions cannot race each other.
The partial unique index (one ACTIVE award per student/event) is the final guard; db.write_transaction turns its
violation into a 409.
"""
from datetime import date, datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit, snapshot
from app.models import (Event, EventStatus, LedgerSourceType, LedgerStatus, PointLedger, PointRequest, RequestStatus,
                        RequestType, Role, Student, StudentStatus, UserAccount)
from app.permissions import event_scope, not_found, request_scope

S = RequestStatus
OPEN = (S.PENDING_CLUB, S.PENDING_DEAN)
# APPROVED, REJECTED, RESOLVED and WITHDRAWN are terminal. RESOLVED is reached only through a committed signed-list
# (bulk OCR) workflow; no endpoint in this module sets it.
TRANSITIONS = {
    S.PENDING_CLUB: {S.PENDING_DEAN, S.REJECTED, S.WITHDRAWN, S.RESOLVED},
    S.PENDING_DEAN: {S.APPROVED, S.REJECTED, S.WITHDRAWN, S.RESOLVED},
}
# Submission eligibility (owner-approved, CLAUDE.md 18.5). Both are allow-lists, so a status added later is refused
# until someone decides otherwise. PUBLISHED and COMPLETED events take new requests (CLOSED and DRAFT do not); only
# ACTIVE students may submit (GRADUATED, INACTIVE, ARCHIVED may not). Neither rule touches login or read access,
# which depend on user_account.account_status, nor requests that are already open.
ACCEPTS_REQUESTS = {EventStatus.PUBLISHED, EventStatus.COMPLETED}
MAY_SUBMIT = {StudentStatus.ACTIVE}
REVIEW_FIELDS = ("status", "club_reviewed_by", "club_reviewed_at", "club_review_comment", "dean_decided_by",
                 "dean_decided_at", "dean_decision_comment")
SUBMIT_FIELDS = ("usn", "event_id", "request_type", "claimed_points", "reason", "submitted_by",
                 "correction_of_ledger_id", "status")
LEDGER_FIELDS = ("ledger_id", "usn", "event_id", "points_awarded", "award_date", "source_type", "source_request_id",
                 "source_ocr_batch_id", "approved_by", "approved_at", "status", "supersedes_ledger_id")


def _require(user: UserAccount, role: Role, what: str):
    if user.role != role:
        raise HTTPException(403, f"Only a {role} account can {what}")


def _move(req: PointRequest, target: RequestStatus, *, stage: RequestStatus | None = None):
    """Apply one state-machine transition. `stage` pins a review to its own stage: REJECTED is reachable from both
    pending states, but the club may only act on PENDING_CLUB and the Dean only on PENDING_DEAN."""
    if (stage is not None and req.status != stage) or target not in TRANSITIONS.get(req.status, ()):
        raise HTTPException(409, f"Request is {req.status}; it cannot move to {target}")
    req.status = target


def active_award(db: Session, usn: str, event_id: int, *, lock: bool = False) -> PointLedger | None:
    stmt = select(PointLedger).where(PointLedger.usn == usn, PointLedger.event_id == event_id,
                                     PointLedger.status == LedgerStatus.ACTIVE)
    return db.scalar(stmt.with_for_update() if lock else stmt)


def get_visible(db: Session, user: UserAccount, request_id: int, *, lock: bool = False) -> PointRequest:
    stmt = select(PointRequest).where(PointRequest.request_id == request_id, request_scope(user))
    req = db.scalar(stmt.with_for_update(of=PointRequest) if lock else stmt)
    if req is None:
        raise not_found("Request")
    return req


def submit(db: Session, user: UserAccount, data, request: Request | None = None) -> PointRequest:
    _require(user, Role.STUDENT, "submit a point request")
    usn = user.student_usn                       # never taken from the request body
    student = db.scalar(select(Student).where(Student.usn == usn).with_for_update())
    if student.student_status not in MAY_SUBMIT:
        raise HTTPException(403, f"Only currently enrolled (ACTIVE) students can submit new requests; this student "
                                 f"is {student.student_status}. Existing records remain viewable.")
    # Visibility (event_scope) and eligibility are separate checks: a CLOSED event is visible but takes no new
    # requests. Requests already open on it stay reviewable; the review functions never look at event_status.
    event = db.scalar(select(Event).where(Event.event_id == data.event_id, event_scope(user)))
    if event is None:
        raise not_found("Event")
    if event.event_status not in ACCEPTS_REQUESTS:
        raise HTTPException(409, f"This event is {event.event_status} and no longer accepts new requests")
    rtype = RequestType(data.request_type)
    if rtype == RequestType.MISSING_FROM_LIST and event.club_id is None:
        raise HTTPException(422, "MISSING_FROM_LIST applies only to club events (their signed lists)")

    if rtype == RequestType.CORRECTION:
        award = db.scalar(select(PointLedger).where(PointLedger.ledger_id == data.correction_of_ledger_id,
                                                    PointLedger.usn == usn))
        if award is None:                        # someone else's award looks the same as a missing one
            raise not_found("Award")
        if award.status != LedgerStatus.ACTIVE:
            raise HTTPException(409, f"Only an ACTIVE award can be corrected; this one is {award.status}")
        if award.event_id != event.event_id:
            raise HTTPException(422, "The award being corrected belongs to a different event")
        if award.points_awarded == data.claimed_points:
            raise HTTPException(422, "claimed_points equals the current award; nothing to correct")
    elif active_award(db, usn, event.event_id):
        raise HTTPException(409, "You already have an ACTIVE award for this event. Submit a CORRECTION, or attach "
                                 "evidence to the existing award")

    if db.scalar(select(PointRequest.request_id).where(PointRequest.usn == usn,
                                                       PointRequest.event_id == event.event_id,
                                                       PointRequest.status.in_(OPEN))):
        raise HTTPException(409, "You already have an open request for this event")

    req = PointRequest(usn=usn, event_id=event.event_id, request_type=rtype, claimed_points=data.claimed_points,
                       reason=data.reason, submitted_by=user.email,
                       correction_of_ledger_id=data.correction_of_ledger_id,
                       status=S.PENDING_CLUB if event.club_id is not None else S.PENDING_DEAN)
    db.add(req)
    db.flush()
    audit(db, user.email, "REQUEST_SUBMITTED", "point_request", req.request_id,
          new_values=snapshot(req, *SUBMIT_FIELDS), request=request)
    return req


def withdraw(db: Session, user: UserAccount, request_id: int, request: Request | None = None) -> PointRequest:
    _require(user, Role.STUDENT, "withdraw a request")
    req = get_visible(db, user, request_id, lock=True)           # a student's scope is their own requests
    old = snapshot(req, "status")
    _move(req, S.WITHDRAWN)
    db.flush()
    audit(db, user.email, "REQUEST_WITHDRAWN", "point_request", req.request_id, old_values=old,
          new_values=snapshot(req, "status"), request=request)
    return req


def club_review(db: Session, user: UserAccount, request_id: int, action: str, comment: str | None,
                request: Request | None = None) -> PointRequest:
    _require(user, Role.CLUB_LEAD, "perform a club review")
    req = get_visible(db, user, request_id, lock=True)           # other clubs' requests: 404
    if action == "REJECT" and not comment:
        raise HTTPException(422, "A comment is required when rejecting")
    old = snapshot(req, *REVIEW_FIELDS)
    _move(req, S.PENDING_DEAN if action == "VERIFY" else S.REJECTED, stage=S.PENDING_CLUB)
    req.club_reviewed_by, req.club_reviewed_at, req.club_review_comment = user.email, _now(), comment
    db.flush()
    audit(db, user.email, "REQUEST_CLUB_VERIFIED" if action == "VERIFY" else "REQUEST_CLUB_REJECTED",
          "point_request", req.request_id, old_values=old, new_values=snapshot(req, *REVIEW_FIELDS), request=request)
    return req


def dean_review(db: Session, user: UserAccount, request_id: int, action: str, comment: str | None,
                request: Request | None = None) -> PointRequest:
    _require(user, Role.DEAN, "make the final decision")
    req = get_visible(db, user, request_id, lock=True)
    if action == "REJECT" and not comment:
        raise HTTPException(422, "A comment is required when rejecting")
    old = snapshot(req, *REVIEW_FIELDS)
    _move(req, S.APPROVED if action == "APPROVE" else S.REJECTED, stage=S.PENDING_DEAN)
    req.dean_decided_by, req.dean_decided_at, req.dean_decision_comment = user.email, _now(), comment
    award = _award(db, user, req, request) if action == "APPROVE" else None
    db.flush()
    new = snapshot(req, *REVIEW_FIELDS)
    if award is not None:
        new["ledger_id"] = award.ledger_id
    audit(db, user.email, "REQUEST_APPROVED" if award else "REQUEST_REJECTED", "point_request", req.request_id,
          old_values=old, new_values=new, request=request)
    return req


def resolve_missing_from_list(db: Session, user: UserAccount, usn: str, event_id: int, *, ledger_id: int,
                              ocr_batch_id: int, request: Request | None = None) -> list[PointRequest]:
    """Mark the student's open MISSING_FROM_LIST requests for this event RESOLVED, because a committed signed list
    (OCR batch) has just awarded them. Called only from the bulk ledger commit, inside its transaction. NEW_ACTIVITY
    and CORRECTION requests are left for separate review. There is no column linking a request to a BULK_APPROVAL
    award (its source_request_id must be NULL), so the link is recorded in the audit entry."""
    _require(user, Role.DEAN, "resolve requests through a signed list")
    reqs = db.scalars(select(PointRequest).where(PointRequest.usn == usn, PointRequest.event_id == event_id,
                                                 PointRequest.request_type == RequestType.MISSING_FROM_LIST,
                                                 PointRequest.status.in_(OPEN))
                      .order_by(PointRequest.request_id).with_for_update()).all()
    for req in reqs:
        old = snapshot(req, *REVIEW_FIELDS)
        _move(req, S.RESOLVED)
        req.dean_decided_by, req.dean_decided_at = user.email, _now()
        req.dean_decision_comment = f"Resolved by the committed signed list (OCR batch {ocr_batch_id})"
        db.flush()
        audit(db, user.email, "REQUEST_RESOLVED", "point_request", req.request_id, old_values=old,
              new_values={**snapshot(req, *REVIEW_FIELDS), "ledger_id": ledger_id, "ocr_batch_id": ocr_batch_id},
              request=request)
    return reqs


def _award(db: Session, user: UserAccount, req: PointRequest, request: Request | None) -> PointLedger:
    """Write the official award for an approved request (inside the caller's transaction)."""
    if req.claimed_points is None:
        raise HTTPException(409, "The request has no claimed points to award")
    now = _now()
    award = PointLedger(usn=req.usn, event_id=req.event_id, points_awarded=req.claimed_points, award_date=date.today(),
                        source_request_id=req.request_id, source_ocr_batch_id=None, approved_by=user.email,
                        approved_at=now, status=LedgerStatus.ACTIVE)
    if req.request_type == RequestType.CORRECTION:
        original = db.scalar(select(PointLedger).where(PointLedger.ledger_id == req.correction_of_ledger_id)
                             .with_for_update())
        if (original is None or original.status != LedgerStatus.ACTIVE or original.usn != req.usn
                or original.event_id != req.event_id):
            raise HTTPException(409, "The award being corrected is no longer the student's ACTIVE award for this "
                                     "event")
        original.status = LedgerStatus.SUPERSEDED     # the only change to the original row; its values are kept
        db.flush()                                    # frees the one-ACTIVE-award slot before the new row
        award.source_type, award.supersedes_ledger_id = LedgerSourceType.MANUAL_CORRECTION, original.ledger_id
    else:
        if active_award(db, req.usn, req.event_id):
            raise HTTPException(409, "The student already has an ACTIVE award for this event")
        award.source_type = LedgerSourceType.INDIVIDUAL_CLAIM
    db.add(award)
    db.flush()                                        # a concurrent duplicate fails here on the unique index
    audit(db, user.email, "LEDGER_AWARD_CREATED", "point_ledger", award.ledger_id,
          new_values=snapshot(award, *LEDGER_FIELDS), request=request)
    if award.supersedes_ledger_id is not None:
        audit(db, user.email, "LEDGER_AWARD_SUPERSEDED", "point_ledger", award.supersedes_ledger_id,
              old_values={"status": LedgerStatus.ACTIVE},
              new_values={"status": LedgerStatus.SUPERSEDED, "superseded_by_ledger_id": award.ledger_id},
              request=request)
    return award


def _now() -> datetime:
    return datetime.now(timezone.utc)

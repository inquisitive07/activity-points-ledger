"""Claim workflow rules. Routers stay thin; every state change lives here, inside one transaction."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.models import (Event, LedgerSource, LedgerStatus, PointLedger, PointRequest, RequestStatus,
                        RequestType, Student, User)

OPEN = (RequestStatus.PENDING_CLUB, RequestStatus.PENDING_DEAN)


def live_award(db: Session, student_id, event_id):
    return db.scalar(select(PointLedger).where(PointLedger.student_id == student_id, PointLedger.event_id == event_id,
                                               PointLedger.status != LedgerStatus.SUPERSEDED))


def submit(db: Session, user: User, student: Student, data) -> PointRequest:
    event = db.get(Event, data.event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    live = live_award(db, student.id, event.id)
    rtype = RequestType(data.request_type)
    if rtype == RequestType.CORRECTION:
        if not live:
            raise HTTPException(409, "There is no existing award for this event to correct")
        if live.points == data.claimed_points:
            raise HTTPException(409, "Claimed points equal the current award")
    elif live:
        raise HTTPException(409, "You already have an award for this event. Request a CORRECTION or attach evidence instead")
    if db.scalar(select(PointRequest.id).where(PointRequest.student_id == student.id, PointRequest.event_id == event.id,
                                               PointRequest.status.in_(OPEN))):
        raise HTTPException(409, "An open request for this event already exists")
    req = PointRequest(student_id=student.id, event_id=event.id, request_type=rtype, claimed_points=data.claimed_points,
                       reason=data.reason, status=RequestStatus.PENDING_CLUB if event.club_id else RequestStatus.PENDING_DEAN)
    db.add(req); db.flush()
    audit(db, user.id, "SUBMIT_CLAIM", "point_request", req.id, {"event_id": event.id, "type": rtype.value, "points": data.claimed_points})
    return req


def _locked(db: Session, req_id: int) -> PointRequest:
    req = db.scalar(select(PointRequest).where(PointRequest.id == req_id).with_for_update())
    if not req:
        raise HTTPException(404, "Request not found")
    return req


def club_review(db: Session, user: User, req_id: int, action: str, comment: str | None) -> PointRequest:
    if action not in ("VERIFY", "REJECT"):
        raise HTTPException(422, "Club leads can only VERIFY or REJECT")
    req = _locked(db, req_id)
    event = db.get(Event, req.event_id)
    if event.club_id != user.club_id:
        raise HTTPException(404, "Request not found")          # other clubs' requests are invisible
    if req.status != RequestStatus.PENDING_CLUB:
        raise HTTPException(409, f"Request is already {req.status.value}")
    req.club_reviewed_by = user.id
    req.reviewer_comment = (comment or "").strip() or req.reviewer_comment
    req.status = RequestStatus.PENDING_DEAN if action == "VERIFY" else RequestStatus.REJECTED
    audit(db, user.id, "CLUB_VERIFY" if action == "VERIFY" else "CLUB_REJECT", "point_request", req.id)
    return req


def dean_review(db: Session, user: User, req_id: int, action: str, comment: str | None) -> PointRequest:
    if action not in ("APPROVE", "REJECT"):
        raise HTTPException(422, "The Dean can only APPROVE or REJECT")
    req = _locked(db, req_id)
    if req.status != RequestStatus.PENDING_DEAN:
        raise HTTPException(409, f"Request is already {req.status.value}")
    req.dean_reviewed_by = user.id
    req.reviewer_comment = (comment or "").strip() or req.reviewer_comment
    if action == "REJECT":
        req.status = RequestStatus.REJECTED
        audit(db, user.id, "DEAN_REJECT", "point_request", req.id)
        return req
    live = live_award(db, req.student_id, req.event_id)
    if req.request_type == RequestType.CORRECTION:
        if not live:
            raise HTTPException(409, "The award being corrected no longer exists")
        live.status = LedgerStatus.SUPERSEDED
        db.flush()                                            # free the one-live-award slot first
        row = PointLedger(student_id=req.student_id, event_id=req.event_id, points=req.claimed_points, status=LedgerStatus.CORRECTION,
                          source=LedgerSource.CLAIM, source_request_id=req.id, supersedes_id=live.id)
    else:
        if live:
            raise HTTPException(409, "An award for this event was added since the request was made")
        row = PointLedger(student_id=req.student_id, event_id=req.event_id, points=req.claimed_points, status=LedgerStatus.ACTIVE,
                          source=LedgerSource.CLAIM, source_request_id=req.id)
    db.add(row)
    req.status = RequestStatus.APPROVED
    db.flush()
    audit(db, user.id, "DEAN_APPROVE", "point_request", req.id, {"ledger_id": row.id, "points": row.points})
    return req

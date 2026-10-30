from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_db, write_transaction
from app.deps import get_current_user
from app.models import Club, Event, EventStatus, PointRequest, RequestStatus, RequestType, Role, UserAccount
from app.permissions import event_scope, require_roles
from app.schemas import ClubAccountabilityOut, ClubOut, EventIn, EventOut, EventPatch, EventStatusIn
from app.services import events as svc

router = APIRouter(tags=["events"])
writers = require_roles(Role.CLUB_LEAD, Role.DEAN)          # HOD and STUDENT: 403 on every write


def _out(e: Event, club_name: str | None) -> EventOut:
    return EventOut(club_name=club_name, **{f: getattr(e, f) for f in EventOut.model_fields if f != "club_name"})


def event_out(db: Session, e: Event) -> EventOut:
    return _out(e, db.scalar(select(Club.club_name).where(Club.club_id == e.club_id)) if e.club_id else None)


@router.get("/events", response_model=list[EventOut])
def list_events(status: EventStatus | None = None, academic_year: str | None = None, club_id: int | None = None,
                user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(Event, Club.club_name).outerjoin(Club, Club.club_id == Event.club_id).where(event_scope(user))
    if status:
        stmt = stmt.where(Event.event_status == status)
    if academic_year:
        stmt = stmt.where(Event.academic_year == academic_year)
    if club_id is not None:
        stmt = stmt.where(Event.club_id == club_id)
    rows = db.execute(stmt.order_by(Event.event_date.desc(), Event.event_id.desc())).all()
    return [_out(e, club_name) for e, club_name in rows]


@router.get("/events/{event_id}", response_model=EventOut)
def get_event(event_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return event_out(db, svc.get_visible(db, user, event_id))


@router.post("/events", response_model=EventOut, status_code=201)
def create_event(body: EventIn, request: Request, user: UserAccount = Depends(writers),
                 db: Session = Depends(get_db)):
    with write_transaction(db):
        event = svc.create(db, user, body, request)
    return event_out(db, event)


@router.patch("/events/{event_id}", response_model=EventOut)
def update_event(event_id: int, body: EventPatch, request: Request, user: UserAccount = Depends(writers),
                 db: Session = Depends(get_db)):
    with write_transaction(db):
        event = svc.update(db, user, event_id, body, request)
    return event_out(db, event)


@router.post("/events/{event_id}/status", response_model=EventOut)
def change_event_status(event_id: int, body: EventStatusIn, request: Request, user: UserAccount = Depends(writers),
                        db: Session = Depends(get_db)):
    with write_transaction(db):
        event = svc.change_status(db, user, event_id, body.event_status, request)
    return event_out(db, event)


@router.get("/clubs", response_model=list[ClubOut])
def list_clubs(_: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return [ClubOut(club_id=c.club_id, club_name=c.club_name, club_email=c.club_email)
            for c in db.scalars(select(Club).order_by(Club.club_name))]


# Club accountability counts only issues (MISSING_FROM_LIST, CORRECTION), never ordinary NEW_ACTIVITY claims, and is
# calculated from point_request.status on every call (CLAUDE.md 4.5, 18.5).
ISSUE = PointRequest.request_type.in_((RequestType.MISSING_FROM_LIST, RequestType.CORRECTION))
DECIDED = (RequestStatus.APPROVED, RequestStatus.REJECTED, RequestStatus.RESOLVED)


def _issues(condition):
    return func.count(PointRequest.request_id).filter(and_(ISSUE, condition))


@router.get("/clubs/accountability", response_model=list[ClubAccountabilityOut])
def accountability(_: UserAccount = Depends(require_roles(Role.DEAN)), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Club.club_id, Club.club_name,
               _issues(PointRequest.status == RequestStatus.PENDING_CLUB).label("open_club_issues"),
               _issues(PointRequest.status == RequestStatus.PENDING_DEAN).label("pending_dean_issues"),
               _issues(PointRequest.status.in_(DECIDED)).label("resolved_issues"))
        .select_from(Club).outerjoin(Event, Event.club_id == Club.club_id)
        .outerjoin(PointRequest, PointRequest.event_id == Event.event_id)
        .group_by(Club.club_id, Club.club_name).order_by(Club.club_name))
    return [ClubAccountabilityOut(**row._mapping) for row in rows]

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.deps import get_current_user, require_roles
from app.models import Club, Event, PointRequest, RequestStatus, Role, User
from app.schemas import EventIn
from sqlalchemy import func

router = APIRouter(tags=["events"])


def event_out(e: Event, club_name: str | None):
    return {"id": e.id, "title": e.title, "club": club_name, "club_id": e.club_id, "event_date": e.event_date.isoformat(),
            "venue": e.venue, "registration_url": e.registration_url, "standard_points": e.standard_points}


@router.get("/events")
def list_events(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Event, Club.name).outerjoin(Club, Club.id == Event.club_id).order_by(Event.event_date.desc())).all()
    return [event_out(e, c) for e, c in rows]


@router.post("/events", status_code=201)
def create_event(body: EventIn, user: User = Depends(require_roles(Role.CLUB)), db: Session = Depends(get_db)):
    e = Event(club_id=user.club_id, created_by=user.id, title=body.title.strip(), event_date=body.event_date, venue=body.venue.strip(),
              registration_url=body.registration_url, standard_points=body.standard_points)
    db.add(e); db.flush()
    audit(db, user.id, "PUBLISH_EVENT", "event", e.id, {"title": e.title})
    db.commit()
    return event_out(e, db.scalar(select(Club.name).where(Club.id == e.club_id)))


@router.get("/clubs")
def list_clubs(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name} for c in db.scalars(select(Club).order_by(Club.name))]


@router.get("/clubs/accountability")
def accountability(_: User = Depends(require_roles(Role.DEAN)), db: Session = Depends(get_db)):
    """Club Accountability Matrix: open club issues / pending Dean review / resolved claims."""
    out = []
    for c in db.scalars(select(Club).order_by(Club.name)):
        counts = dict(db.execute(select(PointRequest.status, func.count()).join(Event, Event.id == PointRequest.event_id)
                                 .where(Event.club_id == c.id).group_by(PointRequest.status)).all())
        n = lambda *s: sum(counts.get(x, 0) for x in s)
        out.append({"club_id": c.id, "club": c.name, "open_club_issues": n(RequestStatus.PENDING_CLUB),
                    "pending_dean_review": n(RequestStatus.PENDING_DEAN), "resolved_claims": n(RequestStatus.APPROVED, RequestStatus.REJECTED)})
    return out

"""Event rules. Ownership and the status lifecycle are enforced here, not only in the routers."""
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit, snapshot
from app.models import Event, EventStatus, Role, UserAccount
from app.permissions import can_manage_event, event_scope, not_found
from app.schemas import EventIn, EventPatch, check_times

# DRAFT -> PUBLISHED -> COMPLETED -> CLOSED, one step at a time, never backwards.
NEXT_STATUS = {EventStatus.DRAFT: EventStatus.PUBLISHED, EventStatus.PUBLISHED: EventStatus.COMPLETED,
               EventStatus.COMPLETED: EventStatus.CLOSED}
# Details stay editable until the event has taken place; awards and requests refer to the completed record.
EDITABLE = {EventStatus.DRAFT, EventStatus.PUBLISHED}
FIELDS = ("event_name", "club_id", "description", "event_date", "start_time", "end_time", "venue",
          "registration_url", "standard_points", "academic_year", "event_status", "created_by")


def get_visible(db: Session, user: UserAccount, event_id: int, *, lock: bool = False) -> Event:
    stmt = select(Event).where(Event.event_id == event_id, event_scope(user))
    event = db.scalar(stmt.with_for_update(of=Event) if lock else stmt)
    if event is None:
        raise not_found("Event")
    return event


def get_managed(db: Session, user: UserAccount, event_id: int) -> Event:
    """The event, locked for update, if the caller may change it (404 if invisible, 403 if visible but not theirs)."""
    event = get_visible(db, user, event_id, lock=True)
    if not can_manage_event(user, event):
        raise HTTPException(403, "Only the organising club's lead (or the Dean, for non-club events) can change "
                                 "this event")
    return event


def create(db: Session, user: UserAccount, data: EventIn, request: Request | None = None) -> Event:
    if user.role not in (Role.CLUB_LEAD, Role.DEAN):
        raise HTTPException(403, "Only club leads and the Dean can create events")
    # The owner comes from the account: a club lead's own club, or NULL (non-club event) for the Dean.
    event = Event(**data.model_dump(), club_id=user.club_id if user.role == Role.CLUB_LEAD else None,
                  event_status=EventStatus.DRAFT, created_by=user.email)
    db.add(event)
    db.flush()
    audit(db, user.email, "EVENT_CREATED", "event", event.event_id, new_values=snapshot(event, *FIELDS),
          request=request)
    return event


def update(db: Session, user: UserAccount, event_id: int, patch: EventPatch, request: Request | None = None) -> Event:
    event = get_managed(db, user, event_id)
    if event.event_status not in EDITABLE:
        raise HTTPException(409, f"A {event.event_status} event can no longer be edited")
    changes = patch.model_dump(exclude_unset=True)
    try:
        check_times(changes.get("start_time", event.start_time), changes.get("end_time", event.end_time))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    changed = [f for f, v in changes.items() if getattr(event, f) != v]
    if changed:
        old = snapshot(event, *changed)
        for field in changed:
            setattr(event, field, changes[field])
        db.flush()
        audit(db, user.email, "EVENT_UPDATED", "event", event.event_id, old_values=old,
              new_values=snapshot(event, *changed), request=request)
    return event


def change_status(db: Session, user: UserAccount, event_id: int, target: str,
                  request: Request | None = None) -> Event:
    event = get_managed(db, user, event_id)
    if NEXT_STATUS.get(event.event_status) != target:
        raise HTTPException(409, f"An event cannot move from {event.event_status} to {target}")
    old = event.event_status
    event.event_status = target
    db.flush()
    audit(db, user.email, "EVENT_STATUS_CHANGED", "event", event.event_id, old_values={"event_status": old},
          new_values={"event_status": target}, request=request)
    return event

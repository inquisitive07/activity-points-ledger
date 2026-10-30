"""Read access to the append-only audit log (CLAUDE.md 3.3, 12.3). Dean only; nothing here writes."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuditLog, Role
from app.permissions import require_roles
from app.schemas import AuditOut, AuditPageOut

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_roles(Role.DEAN))])
MAX_LIMIT = 200


@router.get("", response_model=AuditPageOut)
def list_audit(action: str | None = None, entity_type: str | None = None, entity_id: int | None = None,
               user_email: str | None = None, limit: int = Query(50, ge=1, le=MAX_LIMIT),
               offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    """Newest first. The audit log grows without bound, so it is paginated: limit (1-200, default 50) and offset."""
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action.strip().upper())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type.strip())
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if user_email:
        stmt = stmt.where(AuditLog.user_email == user_email.strip().lower())
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(AuditLog.audit_id.desc()).limit(limit).offset(offset))
    items = [AuditOut(**{f: getattr(a, f) for f in AuditOut.model_fields if f != "ip_address"},
                      ip_address=str(a.ip_address) if a.ip_address is not None else None) for a in rows]
    return AuditPageOut(items=items, total=total, limit=limit, offset=offset)

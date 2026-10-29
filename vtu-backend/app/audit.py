from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(db: Session, actor_id, action: str, entity_type: str, entity_id=None, detail=None):
    db.add(AuditLog(actor_id=actor_id, action=action, entity_type=entity_type,
                    entity_id=str(entity_id) if entity_id is not None else None, detail=detail))

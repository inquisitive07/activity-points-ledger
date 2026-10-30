"""Append-only audit entries (audit_log). The caller's transaction decides whether the entry is kept, so an audit
row commits or rolls back together with the change it describes.

entity_id is BIGINT NOT NULL with no FK. Entities without a numeric key (user_account is keyed by email) use the
sentinel 0 and are identified by user_email / the JSON values instead, as scripts/rvce_correction.py already does.
"""
import ipaddress

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog

NO_ENTITY_ID = 0


def client_ip(request: Request | None) -> str | None:
    """The peer address if it is a real IP (the INET column rejects anything else, e.g. TestClient's 'testclient')."""
    host = request.client.host if request and request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def audit(db: Session, user_email: str | None, action: str, entity_type: str, entity_id: int | None = None, *,
          old_values: dict | None = None, new_values: dict | None = None, request: Request | None = None) -> None:
    db.add(AuditLog(user_email=user_email, action=action, entity_type=entity_type,
                    entity_id=NO_ENTITY_ID if entity_id is None else entity_id,
                    old_values=old_values, new_values=new_values, ip_address=client_ip(request)))

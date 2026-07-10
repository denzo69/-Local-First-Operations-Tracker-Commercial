from sqlalchemy.orm import Session

from app.models import AuditLog


def log_audit_event(
    db: Session,
    *,
    event_type: str,
    entity_type: str,
    entity_id: int,
    description: str,
) -> AuditLog:
    event = AuditLog(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
    )
    db.add(event)
    return event

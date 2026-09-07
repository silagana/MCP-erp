import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import AuditLog


def log_action(
    session: Session,
    accion: str,
    entidad: str,
    entidad_id: int | None = None,
    detalle: dict | None = None,
) -> None:
    session.add(AuditLog(
        timestamp=datetime.utcnow(),
        accion=accion,
        entidad=entidad,
        entidad_id=entidad_id,
        detalle_json=json.dumps(detalle, default=str) if detalle else None,
    ))

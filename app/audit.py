"""Audit log — every correction, approval, and agent decision is recorded."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from . import models


def log(db: Session, org_id: int, user_id: int | None, action: str,
        entity_type: str = "", entity_id: int | None = None,
        detail: dict | None = None) -> models.AuditLog:
    entry = models.AuditLog(
        org_id=org_id, user_id=user_id, action=action,
        entity_type=entity_type, entity_id=entity_id,
        detail_json=json.dumps(detail or {}, sort_keys=True, default=str))
    db.add(entry)
    db.flush()
    return entry

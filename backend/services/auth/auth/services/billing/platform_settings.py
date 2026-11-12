"""Settings Juwazi manages from its admin area, with defaults until changed."""
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from auth.database.schema.billing.billing_db import PlatformSetting


class PlatformSettings(BaseModel):
    trial_days: int = Field(default=30, ge=0, le=365)
    trial_student_limit: int = Field(default=10, ge=0, le=100_000)
    term_days: int = Field(default=120, ge=1, le=730)            # schools: one term (4 months)
    training_period_days: int = Field(default=30, ge=1, le=730)  # training: one candidate period
    exam_access_days: int = Field(default=30, ge=1, le=3650)     # exam body access, unless set per body
    # Free trial of an exam body's practice exams: once per student per exam body. 0 = no trial.
    external_trial_days: int = Field(default=10, ge=0, le=365)
    base_currency: str = Field(default="NGN", min_length=3, max_length=3)
    # Off: limits are shown but not enforced (a safe switch-over for existing organisations).
    enforce_limits: bool = False


def get_settings(session: Session) -> PlatformSettings:
    rows = session.exec(select(PlatformSetting)).all()
    stored: dict[str, Any] = {r.key: r.value for r in rows}
    known = {k: v for k, v in stored.items() if k in PlatformSettings.model_fields}
    return PlatformSettings(**known)


def update_settings(session: Session, changes: dict[str, Any], actor_id: UUID) -> PlatformSettings:
    merged = get_settings(session).model_dump() | changes
    validated = PlatformSettings(**merged)          # raises on bad values
    now = datetime.now(timezone.utc)
    existing = {r.key: r for r in session.exec(
        select(PlatformSetting).where(col(PlatformSetting.key).in_(list(changes)))
    ).all()}
    for key in changes:
        row = existing.get(key) or PlatformSetting(key=key, value=None)
        row.value = getattr(validated, key)
        row.updated_by = actor_id
        row.updated_at = now
        session.add(row)
    session.commit()
    return validated

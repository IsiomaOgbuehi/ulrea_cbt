# cbt_service/tests/helpers.py
from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, select

from cbt_service.database.models.exam import ExamModel

from .conftest import engine


def report_violation(
    client: TestClient,
    headers: dict[str, str],
    attempt_id: str | UUID,
    violation_type: str = "tab_switch",
    severity: str = "low",
    description: str | None = None,
) -> Response:
    return client.post(
        f"/api/v1/attempts/{attempt_id}/violations",
        json={
            "violation_type": violation_type,
            "severity": severity,
            "description": description,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
        headers=headers,
    )


def set_max_violations(exam_id: UUID, max_violations: int | None) -> None:
    """Directly update the seeded exam's max_violations for a specific test."""
    with Session(engine) as session:
        exam: ExamModel | None = session.exec(
            select(ExamModel).where(ExamModel.id == exam_id)
        ).first()
        assert exam is not None
        exam.max_violations = max_violations
        session.add(exam)
        session.commit()

def ensure_on_exam(attempt_id: str | UUID, item_id: str | UUID, marks: float = 1.0) -> None:
    """Answers are only accepted for questions on the attempt's exam: put this one on it."""
    from cbt_service.database.models.attempt import AttemptModel
    from cbt_service.database.models.exam import ExamItem

    with Session(engine) as session:
        attempt = session.get(AttemptModel, UUID(str(attempt_id)))
        assert attempt is not None, "attempt not found"
        iid = UUID(str(item_id))
        exists = session.exec(
            select(ExamItem).where(ExamItem.exam_id == attempt.exam_id, ExamItem.item_id == iid)
        ).first()
        if exists is None:
            session.add(ExamItem(exam_id=attempt.exam_id, item_id=iid, org_id=attempt.org_id, marks=marks))
            session.commit()
